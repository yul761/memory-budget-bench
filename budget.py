"""Fill an answerer's context budget from a retrieval payload.

The previous harness compared systems at equal *item* counts. Items are not
comparable: a StateCore session event ran ~9.8k chars while a mem0 fact ran ~145,
so "top-k 50 for both" handed one side 240x the context of the other and the
score difference measured context volume, not memory quality. Equalising on the
answerer's token budget is what makes the comparison mean anything.

Two rules here are load-bearing:

1. Items are included WHOLE, in rank order, and filling stops at the first item
   that does not fit. Never truncate mid-item. The 2000-char cap in the previous
   harness cut each retrieved session to 20% of itself, which destroyed the
   evidence while leaving retrieval recall reading 1.00 — the failure looked like
   a memory problem and was a harness one.

2. The result carries what it used and what it dropped. A prompt that silently
   contains less than the caller asked for is the same class of bug this whole
   effort exists to remove.
"""

from dataclasses import dataclass, field
from typing import Any

import tiktoken

# gpt-5 and gpt-4o share o200k_base. Counting with one encoder for every arm
# keeps the budget comparable across them, which is the entire point.
_ENC = tiktoken.get_encoding("o200k_base")


def count_tokens(text: str) -> int:
    return len(_ENC.encode(text, disallowed_special=()))


@dataclass
class FilledPrompt:
    text: str
    budget_tokens: int
    used_tokens: int
    items_included: int
    items_dropped: int
    digest_tokens: int
    facts_tokens: int
    # True when the payload had less content than the budget allowed. Not a
    # failure — it is the finding that a system had nothing more to give.
    underfilled: bool
    sections: list = field(default_factory=list)


def build_prompt(question: str, question_date: str, payload: dict[str, Any], budget_tokens: int) -> FilledPrompt:
    """Compose the answerer prompt for one question at one budget.

    `payload` is the persisted retrieval: {digest, factRegistry, events:[{content}]}.
    Order is fixed — digest, then facts, then events in rank order — so the only
    thing that varies between arms is what each system put in them.
    """
    header = "Current date: %s\n\n" % question_date
    footer = "\n\n## Question\n%s" % question

    # The question and scaffolding are not optional, so they come out of the
    # budget before anything competes for it.
    fixed_tokens = count_tokens(header) + count_tokens(footer)
    remaining = budget_tokens - fixed_tokens

    parts: list[str] = []
    sections: list[dict[str, Any]] = []
    digest_tokens = 0
    facts_tokens = 0

    digest = (payload.get("digest") or "").strip()
    if digest and remaining > 0:
        block = "## Consolidated state\n" + digest
        cost = count_tokens(block)
        if cost <= remaining:
            parts.append(block)
            remaining -= cost
            digest_tokens = cost
            sections.append({"section": "digest", "tokens": cost})

    facts = payload.get("factRegistry") or []
    if facts and remaining > 0:
        lines = []
        for fact in facts:
            text = fact if isinstance(fact, str) else (fact.get("content") or "")
            if text:
                lines.append("- " + text)
        if lines:
            block = "## Known facts\n" + "\n".join(lines)
            cost = count_tokens(block)
            if cost <= remaining:
                parts.append(block)
                remaining -= cost
                facts_tokens = cost
                sections.append({"section": "factRegistry", "tokens": cost, "items": len(lines)})

    events = payload.get("events") or []
    included = 0
    dropped = 0
    if events:
        header_block = "## Retrieved conversation excerpts\n"
        header_cost = count_tokens(header_block)
        kept: list[str] = []
        if header_cost <= remaining:
            remaining -= header_cost
            for event in events:
                content = event.get("content") if isinstance(event, dict) else str(event)
                if not content:
                    continue
                line = "- " + content
                cost = count_tokens(line + "\n")
                if cost > remaining:
                    # Whole-item rule: stop, do not cut this one down to fit.
                    dropped = len(events) - included
                    break
                kept.append(line)
                remaining -= cost
                included += 1
            if kept:
                parts.append(header_block + "\n".join(kept))
                sections.append({"section": "events", "included": included, "dropped": dropped})
            else:
                remaining += header_cost
                dropped = len(events)
        else:
            dropped = len(events)

    if not parts:
        parts.append("(no memory retrieved)")

    text = header + "\n\n".join(parts) + footer
    used = count_tokens(text)

    return FilledPrompt(
        text=text,
        budget_tokens=budget_tokens,
        used_tokens=used,
        items_included=included,
        items_dropped=dropped,
        digest_tokens=digest_tokens,
        facts_tokens=facts_tokens,
        underfilled=dropped == 0 and used < budget_tokens,
        sections=sections,
    )
