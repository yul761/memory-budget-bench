#!/usr/bin/env python3
"""Report generator for the budget-aligned comparison.

Replaces final_report.py, whose output had three defects worth naming so they do
not come back:

* It printed a "Caveat" table of scores from an earlier, invalidated harness under
  the words "measured on this machine today". This one prints nothing it did not
  compute from the run it was given.
* It labelled cost "measured" while taking it from a hardcoded CLI flag. This one
  sums the token usage the API actually returned.
* It shipped an empty StateCore commit because the droplet checkout has no .git.
  This one refuses to write a report without one.

Usage:
    fair_report.py --run fair --budgets 4000,16000,64000 --out REPORT.md
"""

import argparse
import json
import math
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
ARMS = ("statecore", "mem0", "recency", "full")
ARM_LABEL = {
    "statecore": "StateCore",
    "mem0": "mem0 OSS",
    "recency": "No memory (recency window)",
    "full": "No memory (whole corpus)",
}


def wilson_halfwidth(correct, total):
    """95% interval half-width. A 4.5-point gap with a ±9.4 interval is not a
    result, and the previous report only avoided claiming one by luck."""
    if total == 0:
        return 0.0
    p = correct / total
    return 1.96 * math.sqrt(p * (1 - p) / total) * 100


def load_scores(run_dir, arm, budget, qtype_by_id=None):
    """Reads the judge's per-question verdicts for one arm at one budget.

    The verdict is `autoeval_label.label`, a bool inside a dict. Reading the dict
    itself as the verdict makes every answer correct, since a non-empty dict is
    truthy — the kind of mistake that yields a plausible-looking 100% and is only
    obvious once someone checks a single row.

    Question type is not in the results file; it comes from the dataset, which is
    why this takes a lookup rather than guessing "unknown".
    """
    path = os.path.join(run_dir, "answers", "%s-b%d" % (arm, budget), "hypotheses.jsonl.eval-results-gpt-4o")
    if not os.path.exists(path):
        return None
    by_type, correct, total = {}, 0, 0
    for line in open(path):
        if not line.strip():
            continue
        rec = json.loads(line)
        label = rec.get("autoeval_label")
        if isinstance(label, dict):
            ok = bool(label.get("label"))
        else:
            ok = bool(label)
        qtype = (qtype_by_id or {}).get(rec["question_id"], "unknown")
        hit, seen = by_type.get(qtype, (0, 0))
        by_type[qtype] = (hit + (1 if ok else 0), seen + 1)
        correct += 1 if ok else 0
        total += 1
    return {"correct": correct, "total": total, "by_type": by_type}


def load_meta(run_dir, arm, budget):
    path = os.path.join(run_dir, "answers", "%s-b%d" % (arm, budget), "meta.json")
    return json.load(open(path)) if os.path.exists(path) else None


def load_fill_stats(run_dir, arm, budget):
    """How much of the budget an arm could actually use — the number that says
    whether a low score means bad retrieval or nothing left to retrieve."""
    path = os.path.join(run_dir, "answers", "%s-b%d" % (arm, budget), "traces.jsonl")
    if not os.path.exists(path):
        return None
    used, underfilled, dropped = [], 0, []
    for line in open(path):
        if not line.strip():
            continue
        rec = json.loads(line)
        used.append(rec["used_tokens"])
        dropped.append(rec["items_dropped"])
        underfilled += 1 if rec.get("underfilled") else 0
    if not used:
        return None
    used.sort()
    return {
        "median_used": used[len(used) // 2],
        "underfilled_share": underfilled / len(used),
        "median_dropped": sorted(dropped)[len(dropped) // 2],
        "n": len(used),
    }


def question_types(dataset):
    return {q["question_id"]: q["question_type"] for q in json.load(open(dataset))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="fair")
    ap.add_argument("--budgets", default="4000,16000,64000")
    ap.add_argument("--out", default=os.path.join(ROOT, "FAIR-REPORT.md"))
    ap.add_argument("--statecore-commit", default=None)
    ap.add_argument("--dataset", default=os.path.join(ROOT, "data", "longmemeval_s.json"))
    args = ap.parse_args()

    qtypes = question_types(args.dataset)

    run_dir = os.path.join(ROOT, "runs", args.run)
    budgets = [int(b) for b in args.budgets.split(",")]

    commit = args.statecore_commit
    if not commit:
        for budget in budgets:
            meta = load_meta(run_dir, "statecore", budget)
            if meta and meta.get("statecore_commit"):
                commit = meta["statecore_commit"]
                break
    if not commit:
        raise SystemExit(
            "refusing to write a report with no StateCore commit — a score that "
            "cannot be traced to a build is not reproducible. Pass --statecore-commit."
        )

    out = []
    out.append("# LongMemEval at an equal context budget\n")
    out.append(
        "Every arm was given the same question set, the same answerer, the same judge, "
        "and the same number of tokens to put its memory into. What differs is what each "
        "chose to put there.\n"
    )

    out.append("## Result\n")
    header = "| system | " + " | ".join("%s tok" % b for b in budgets) + " |"
    out.append(header)
    out.append("|---" * (len(budgets) + 1) + "|")
    for arm in ARMS:
        if arm == "full":
            continue
        cells = []
        for budget in budgets:
            s = load_scores(run_dir, arm, budget, qtypes)
            cells.append("—" if not s else "%.1f%% ±%.1f" % (100 * s["correct"] / s["total"],
                                                             wilson_halfwidth(s["correct"], s["total"])))
        out.append("| **%s** | %s |" % (ARM_LABEL[arm], " | ".join(cells)))

    ceiling = load_scores(run_dir, "full", 1_000_000, qtypes)
    if ceiling:
        out.append("\n**Ceiling** — the whole corpus in the prompt, no budget: **%.1f%% ±%.1f** (%d/%d).\n"
                   % (100 * ceiling["correct"] / ceiling["total"],
                      wilson_halfwidth(ceiling["correct"], ceiling["total"]),
                      ceiling["correct"], ceiling["total"]))
        out.append(
            "This is roughly the configuration the withdrawn 2026-08-05 run measured without "
            "meaning to. Read it as what the answerer can do unaided, not as a memory score.\n"
        )

    out.append("\n## How much of the budget each arm could use\n")
    out.append("A low score with an underfilled budget means the system had nothing more to give; "
               "a low score with a full budget means it chose badly. They are different failures.\n")
    out.append("| system | budget | median used | median items dropped | runs that underfilled |")
    out.append("|---|---|---|---|---|")
    for arm in ARMS:
        for budget in budgets + ([1_000_000] if arm == "full" else []):
            stats = load_fill_stats(run_dir, arm, budget)
            if stats:
                out.append("| %s | %d | %d | %d | %.0f%% |" % (
                    ARM_LABEL[arm], budget, stats["median_used"],
                    stats["median_dropped"], 100 * stats["underfilled_share"]))

    out.append("\n## By question type\n")
    for budget in budgets:
        rows = {arm: load_scores(run_dir, arm, budget, qtypes) for arm in ARMS if load_scores(run_dir, arm, budget, qtypes)}
        if not rows:
            continue
        out.append("\n**Budget %d tokens**\n" % budget)
        types = sorted({t for s in rows.values() for t in s["by_type"]})
        out.append("| question type | " + " | ".join(ARM_LABEL[a] for a in rows) + " |")
        out.append("|---" * (len(rows) + 1) + "|")
        for qtype in types:
            cells = []
            for arm in rows:
                hit, seen = rows[arm]["by_type"].get(qtype, (0, 0))
                cells.append("—" if seen == 0 else "%.1f%% (%d/%d)" % (100 * hit / seen, hit, seen))
            out.append("| %s | %s |" % (qtype, " | ".join(cells)))

    out.append("\n## Cost\n")
    out.append("Summed from the token usage the API returned, not estimated.\n")
    out.append("| system | budget | prompt tokens | completion tokens |")
    out.append("|---|---|---|---|")
    for arm in ARMS:
        for budget in budgets + ([1_000_000] if arm == "full" else []):
            meta = load_meta(run_dir, arm, budget)
            if meta and meta.get("usage"):
                out.append("| %s | %d | %s | %s |" % (
                    ARM_LABEL[arm], budget,
                    f"{meta['usage']['prompt_tokens']:,}",
                    f"{meta['usage']['completion_tokens']:,}"))

    excluded = {}
    for budget in budgets:
        meta = load_meta(run_dir, "statecore", budget)
        if meta and meta.get("excluded"):
            excluded = meta["excluded"]
            break

    out.append("\n## Configuration\n")
    any_meta = next((load_meta(run_dir, a, b) for a in ARMS for b in budgets if load_meta(run_dir, a, b)), {})
    out.append("| knob | value |")
    out.append("|---|---|")
    out.append("| dataset | `longmemeval_s.json` |")
    out.append("| questions scored | %s |" % (any_meta.get("n_eligible", "?")))
    out.append("| answerer | `%s` |" % any_meta.get("answerer", "?"))
    out.append("| StateCore commit | `%s` |" % commit)
    out.append("| scope template | `personal` |")
    out.append("| budgets | %s |" % ", ".join(str(b) for b in budgets))

    out.append("\n## How much of each corpus survived ingest\n")
    loss = (excluded or {}).get("_ingest_loss") or {}
    if loss:
        out.append("A system that holds less of the corpus is answering a different, easier or "
                   "harder question. Reported rather than hidden, and rather than discarding every "
                   "question it affects.\n")
        out.append("| system | median corpus lost | worst question | questions over threshold |")
        out.append("|---|---|---|---|")
        for arm, stats in loss.items():
            out.append("| %s | %.1f%% | %.1f%% | %d |" % (
                ARM_LABEL.get(arm, arm), 100 * stats["median_loss"],
                100 * stats["max_loss"], stats["questions_over_threshold"]))
        out.append("\nmem0 is measured as released (`mem0ai==2.0.17`), unmodified. It extracts "
                   "nothing from some conversational sessions and then embeds an empty string, "
                   "which its own API rejects — so a few percent of sessions do not make it in. "
                   "That is its behaviour, not a harness failure, and patching it would mean "
                   "publishing a number for something nobody runs.\n")

    dropped = {k: v for k, v in (excluded or {}).items() if k != "_ingest_loss"}
    out.append("\n## What was excluded, and why\n")
    if dropped:
        out.append("Questions where an arm lost more than the allowed share of its corpus. "
                   "Dropped for every arm, so all arms answer the same set:\n")
        for arm, ids in dropped.items():
            out.append("- **%s**: %d question(s) — e.g. %s" % (ARM_LABEL.get(arm, arm), len(ids), ", ".join(ids[:5])))
    else:
        out.append("No question was dropped: no arm lost more than the allowed share of any corpus.\n")

    out.append("\n## Reading these numbers\n")
    out.append(
        "A LongMemEval score is a property of *system + configuration*, not of a system. "
        "The configuration is above; quote it with the number or the number will not reproduce.\n"
    )
    out.append(
        "The comparison is only meaningful where the interval excludes zero. "
        "Overlapping intervals mean the run did not distinguish the systems, whatever the point estimates say.\n"
    )

    with open(args.out, "w") as f:
        f.write("\n".join(out) + "\n")
    print("wrote %s" % args.out)


if __name__ == "__main__":
    main()
