#!/usr/bin/env python3
"""Budget-aligned LongMemEval comparison.

Replaces run_longmemeval.py, which produced the withdrawn 2026-08-05 numbers.
That script is left in place as the record of what was run; this one is a
separate file so the break is explicit rather than a silent mutation of history.

What changed and why:

* Systems are compared at an equal **answerer token budget**, not an equal item
  count. A StateCore session event ran ~9.8k chars and a mem0 fact ~145, so
  "top-k 50 for both" gave one side 240x the context and the score measured
  context volume. See budget.py.

* Two control arms. Without them a score cannot distinguish "the memory layer
  works" from "gpt-5 read the transcript": at top-k 50 over a ~50-session
  haystack, retrieval selected everything and the prompt came to 1.44x the whole
  corpus.

* Retrieval is persisted before any answering. Budgets are then swept offline,
  which costs almost no wall clock and — more importantly — leaves on disk
  exactly what each system handed the answerer, for anyone who wants to check.

* A question is only scored if EVERY arm ingested its corpus completely. One arm
  quietly holding 95% of the corpus is not a comparison.

Phases:
    run_fair.py retrieve --arm statecore --n 200
    run_fair.py answer   --arm statecore --n 200 --budget 4000
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime

from openai import OpenAI

from adapters.mem0 import Mem0Backend
from adapters.statecore import StateCoreBackend
from budget import build_prompt, count_tokens

ROOT = os.path.dirname(os.path.abspath(__file__))

ARMS = ("statecore", "mem0", "recency", "full")


def queue_pending():
    """Waiting + active embed/classify jobs.

    StateCore enriches asynchronously, so retrieving before the queue drains
    measures a half-indexed store. Lives here rather than in the adapter because
    it reaches for the deployment's Redis container, which is a property of how
    the benchmark is hosted, not of the API.
    """
    total = 0
    for queue in ("embed", "classify"):
        for state in ("wait", "active", "prioritized"):
            try:
                out = subprocess.run(
                    ["docker", "exec", "statecore-redis-1", "redis-cli", "LLEN",
                     "bull:%s:%s" % (queue, state)],
                    capture_output=True, text=True, timeout=15)
                total += int(out.stdout.strip() or 0)
            except Exception:
                pass
    return total

ANSWER_SYSTEM = (
    "You answer strictly from the supplied memory. If the memory does not "
    "contain the answer, say you do not know. Be concise and specific."
)

# Enough to hold an entire haystack; the ceiling arm is deliberately unbounded.
CEILING_BUDGET = 1_000_000


# --------------------------------------------------------------------------
# sampling — identical across arms so the comparison is over the same questions
# --------------------------------------------------------------------------
def load_questions(dataset, n, seed):
    data = json.load(open(dataset))
    if n and n < len(data):
        by_type = defaultdict(list)
        for q in data:
            by_type[q["question_type"]].append(q)
        rng = random.Random(seed)
        for v in by_type.values():
            rng.shuffle(v)
        types = sorted(by_type)
        picked, i = [], 0
        while len(picked) < n:
            t = types[i % len(types)]
            if by_type[t]:
                picked.append(by_type[t].pop())
            i += 1
            if all(not by_type[t] for t in types):
                break
        data = picked
    return data


def payload_path(run_dir, arm, qid):
    return os.path.join(run_dir, "retrievals", arm, qid + ".json")


# --------------------------------------------------------------------------
# phase 1 — retrieve
# --------------------------------------------------------------------------
def haystack_events(q, newest_first=True):
    """The raw corpus as retrieval-shaped items, for the two control arms."""
    sessions = q["haystack_sessions"]
    dates = q.get("haystack_dates") or [""] * len(sessions)
    items = []
    for session, date in zip(sessions, dates):
        text = "\n".join("%s: %s" % (m.get("role", "user"), m["content"]) for m in session)
        items.append({"content": text, "createdAt": date})
    # A no-memory product keeps the most recent conversation, so the recency arm
    # must be offered the newest first.
    items.sort(key=lambda it: it["createdAt"], reverse=newest_first)
    return items


def retrieve_one(arm, backend, q, args):
    """Returns (payload, meta). Payload is what the arm hands the answerer."""
    qid = q["question_id"]
    meta = {"question_id": qid, "question_type": q["question_type"], "arm": arm}
    t0 = time.time()

    if arm in ("recency", "full"):
        payload = {"digest": None, "factRegistry": [], "events": haystack_events(q)}
        meta.update(ingest={"events": len(payload["events"]), "errors": 0}, digest_ok=None,
                    timings={"total_s": round(time.time() - t0, 1)})
        return payload, meta

    scope_id = backend.create_scope("lme-" + qid)
    meta["scope_id"] = scope_id
    try:
        ing = backend.ingest_sessions(scope_id, q["haystack_sessions"], q["haystack_dates"],
                                      args.granularity, send_occurred_at=not args.no_occurred_at)
        meta["ingest"] = ing
        t_ing = time.time()

        meta["queue_drained"] = backend.wait_for_embeddings(ing["events"], queue_pending, wait_s=1800)
        t_drain = time.time()

        ok, detail = backend.run_digest(scope_id, wait_s=args.digest_wait)
        meta["digest_ok"] = ok
        if not ok:
            meta["digest_error"] = str(detail)[:200]
        t_dig = time.time()

        # Ask for far more than any budget can consume: the point is to capture
        # everything the system would offer, then let the budget decide offline.
        retrieved = backend.search(scope_id, q["question"], args.retrieve_k)
        payload = {
            "digest": retrieved.get("digest"),
            "factRegistry": retrieved.get("factRegistry") or [],
            "events": [{"content": e.get("content", ""), "createdAt": e.get("createdAt", "")}
                       for e in (retrieved.get("events") or [])],
        }
        meta["retrieval_mode"] = (retrieved.get("retrieval") or {}).get("mode")
        # Everything the system had, or everything we were allowed to ask for?
        # The two are indistinguishable downstream unless it is recorded here.
        meta["capture_capped"] = len(payload["events"]) >= args.retrieve_k
        meta["timings"] = {
            "ingest_s": round(t_ing - t0, 1), "drain_s": round(t_drain - t_ing, 1),
            "digest_s": round(t_dig - t_drain, 1), "retrieve_s": round(time.time() - t_dig, 1),
            "total_s": round(time.time() - t0, 1),
        }
        return payload, meta
    finally:
        backend.delete_scope(scope_id)


def phase_retrieve(args):
    run_dir = os.path.join(ROOT, "runs", args.run_name)
    os.makedirs(os.path.join(run_dir, "retrievals", args.arm), exist_ok=True)
    data = load_questions(args.dataset, args.n, args.seed)
    print("arm=%s questions=%d %s" % (args.arm, len(data),
                                      dict(Counter(q["question_type"] for q in data))), flush=True)

    backend = None
    if args.arm == "statecore":
        backend = StateCoreBackend(**({"base_url": args.backend_url} if args.backend_url else {}))
    elif args.arm == "mem0":
        backend = Mem0Backend(**({"base_url": args.backend_url} if args.backend_url else {}))

    meta_path = os.path.join(run_dir, "retrieve-%s.jsonl" % args.arm)
    seen = set()
    if args.resume and os.path.exists(meta_path):
        seen = {json.loads(l)["question_id"] for l in open(meta_path) if l.strip()}
        print("resuming, %d already retrieved" % len(seen), flush=True)

    meta_f = open(meta_path, "a")
    t_start = time.time()
    for idx, q in enumerate(data, 1):
        qid = q["question_id"]
        if qid in seen:
            continue
        try:
            payload, meta = retrieve_one(args.arm, backend, q, args)
            with open(payload_path(run_dir, args.arm, qid), "w") as f:
                json.dump(payload, f, ensure_ascii=False)
            meta["events_captured"] = len(payload["events"])
            meta["digest_chars"] = len(payload.get("digest") or "")
            meta["facts_captured"] = len(payload.get("factRegistry") or [])
        except Exception as exc:
            meta = {"question_id": qid, "arm": args.arm,
                    "error": "%s: %s" % (type(exc).__name__, str(exc)[:300])}
            print("[%d/%d] %s FAILED %s" % (idx, len(data), qid, meta["error"]), flush=True)
        meta_f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        meta_f.flush()
        if "error" not in meta:
            print("[%d/%d] %-26s ev=%-4d facts=%-3d %.0fs" % (
                idx, len(data), qid, meta.get("events_captured", 0),
                meta.get("facts_captured", 0), meta.get("timings", {}).get("total_s", 0)), flush=True)
    print("\nretrieve wall clock: %.1f min" % ((time.time() - t_start) / 60), flush=True)


# --------------------------------------------------------------------------
# phase 2 — answer at a budget
# --------------------------------------------------------------------------
def eligible_questions(run_dir, arms, data, max_loss=0.10):
    """Questions usable for comparison, plus what each arm lost getting there.

    Requiring a perfect ingest from every arm sounds right and is not workable:
    mem0 as released fails to ingest roughly 5% of sessions — it extracts nothing
    from some small talk and then embeds an empty string, which its API rejects.
    That is deterministic, not a blip, so an all-or-nothing rule would discard
    most of the dataset and measure nothing.

    A question is dropped when an arm lost more than `max_loss` of its corpus,
    which is a real hole. Below that the question is kept and the loss is
    reported, because "mem0 held 95% of this corpus" is a finding to publish, not
    a reason to publish nothing.
    """
    usable = {}
    loss = {}
    for arm in arms:
        path = os.path.join(run_dir, "retrieve-%s.jsonl" % arm)
        if not os.path.exists(path):
            return set(), {"missing_arm": arm}
        ok = set()
        arm_loss = []
        for line in open(path):
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("error"):
                continue
            ing = rec.get("ingest") or {}
            total = ing.get("events") or 0
            errors = ing.get("errors") or 0
            share = (errors / total) if total else 0.0
            arm_loss.append(share)
            if share <= max_loss:
                ok.add(rec["question_id"])
        usable[arm] = ok
        if arm_loss:
            arm_loss.sort()
            loss[arm] = {
                "median_loss": round(arm_loss[len(arm_loss) // 2], 4),
                "max_loss": round(arm_loss[-1], 4),
                "questions_over_threshold": sum(1 for x in arm_loss if x > max_loss),
            }

    all_ids = {q["question_id"] for q in data}
    eligible = set.intersection(*usable.values()) & all_ids
    excluded = {arm: sorted(all_ids - ids)[:10] for arm, ids in usable.items() if all_ids - ids}
    excluded["_ingest_loss"] = loss
    return eligible, excluded


def phase_answer(args):
    run_dir = os.path.join(ROOT, "runs", args.run_name)
    data = load_questions(args.dataset, args.n, args.seed)
    by_id = {q["question_id"]: q for q in data}

    required = [a.strip() for a in args.require_arms.split(",") if a.strip()]
    eligible, excluded = eligible_questions(run_dir, required, data, args.max_ingest_loss)
    if excluded:
        print("excluded (incomplete ingest): %s" % json.dumps(excluded)[:400], flush=True)
    print("answering %d/%d questions at budget=%d for arm=%s"
          % (len(eligible), len(data), args.budget, args.arm), flush=True)

    api_key = next((os.environ[k] for k in ("OPENAI_API_KEY", "MODEL_API_KEY") if os.environ.get(k)), None)
    if not api_key:
        sys.exit("no OpenAI key in env")
    client = OpenAI(api_key=api_key)

    out_dir = os.path.join(run_dir, "answers", "%s-b%d" % (args.arm, args.budget))
    os.makedirs(out_dir, exist_ok=True)
    hyp_path = os.path.join(out_dir, "hypotheses.jsonl")
    trace_path = os.path.join(out_dir, "traces.jsonl")

    done = set()
    if args.resume and os.path.exists(hyp_path):
        done = {json.loads(l)["question_id"] for l in open(hyp_path) if l.strip()}

    hyp_f, trace_f = open(hyp_path, "a"), open(trace_path, "a")
    cost = {"prompt_tokens": 0, "completion_tokens": 0}
    t_start = time.time()

    for idx, q in enumerate(sorted(eligible), 1):
        if q in done:
            continue
        question = by_id[q]
        path = payload_path(run_dir, args.arm, q)
        if not os.path.exists(path):
            continue
        payload = json.load(open(path))

        filled = build_prompt(question["question"], question["question_date"], payload, args.budget)
        # The invariant, asserted rather than assumed.
        assert filled.used_tokens <= args.budget, "budget exceeded: %d > %d" % (filled.used_tokens, args.budget)

        kwargs = {"model": args.answerer,
                  "messages": [{"role": "system", "content": ANSWER_SYSTEM},
                               {"role": "user", "content": filled.text}]}
        if args.answerer.startswith("gpt-5"):
            kwargs["max_completion_tokens"] = 4096
        else:
            kwargs["temperature"] = 0
            kwargs["max_tokens"] = 512

        comp = client.chat.completions.create(**kwargs)
        hypothesis = (comp.choices[0].message.content or "").strip()
        usage = getattr(comp, "usage", None)
        if usage:
            cost["prompt_tokens"] += usage.prompt_tokens or 0
            cost["completion_tokens"] += usage.completion_tokens or 0

        hyp_f.write(json.dumps({"question_id": q, "hypothesis": hypothesis}) + "\n")
        hyp_f.flush()
        trace_f.write(json.dumps({
            "question_id": q, "question_type": question["question_type"], "arm": args.arm,
            "budget_tokens": args.budget, "used_tokens": filled.used_tokens,
            "items_included": filled.items_included, "items_dropped": filled.items_dropped,
            "digest_tokens": filled.digest_tokens, "facts_tokens": filled.facts_tokens,
            "underfilled": filled.underfilled, "sections": filled.sections,
            "hypothesis": hypothesis,
        }, ensure_ascii=False) + "\n")
        trace_f.flush()
        if idx % 10 == 0 or idx == len(eligible):
            print("[%d/%d] used=%d/%d dropped=%d" % (idx, len(eligible), filled.used_tokens,
                                                      args.budget, filled.items_dropped), flush=True)

    meta = {
        "arm": args.arm, "budget": args.budget, "answerer": args.answerer,
        "n_eligible": len(eligible), "excluded": excluded,
        "usage": cost,
        "statecore_commit": args.statecore_commit or git_commit(),
        "finished": datetime.now().isoformat(),
        "wall_clock_min": round((time.time() - t_start) / 60, 1),
    }
    json.dump(meta, open(os.path.join(out_dir, "meta.json"), "w"), indent=2)
    print("\n%s" % json.dumps(meta["usage"]), flush=True)


def git_commit():
    """Recorded so a number can be traced to a build. The droplet checkout has no
    .git (create-droplet.sh rsyncs with --exclude .git), which is why the previous
    report shipped an empty commit field — pass --statecore-commit there."""
    try:
        out = subprocess.run(["git", "-C", os.path.join(ROOT, "..", "StateCore"), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["retrieve", "answer"])
    ap.add_argument("--arm", required=True, choices=ARMS)
    ap.add_argument("--run-name", default="fair")
    ap.add_argument("--dataset", default=os.path.join(ROOT, "data", "longmemeval_s.json"))
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--backend-url", default=None)
    ap.add_argument("--granularity", default="session", choices=["message", "session"])
    ap.add_argument("--no-occurred-at", action="store_true")
    ap.add_argument("--digest-wait", type=int, default=180)
    # StateCore's frozen /v1 contract caps `limit` at 100. That is comfortably
    # above this dataset's ~50 sessions, but the cap is real, so capture_capped
    # below records when a run actually hits it rather than letting a silent
    # top-100 truncation pass for "that was everything".
    ap.add_argument("--retrieve-k", type=int, default=100,
                    help="how much to capture, not how much to use; budgets decide that offline")
    ap.add_argument("--budget", type=int, default=16000)
    ap.add_argument("--answerer", default="gpt-5")
    ap.add_argument("--statecore-commit", default=None)
    # Which arms must have ingested a question completely for it to be scored.
    # Defaults to all four: a question one arm only partly holds is not comparable
    # across arms, so it is dropped everywhere rather than silently scored. Narrow
    # this only for smoke tests.
    ap.add_argument("--require-arms", default=",".join(ARMS),
                    help="comma-separated arms that must have complete ingest")
    # Above this share of a question's corpus lost at ingest, the question is
    # dropped for every arm. Below it, the question is kept and the loss reported.
    ap.add_argument("--max-ingest-loss", type=float, default=0.10)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    if args.arm == "full":
        args.budget = CEILING_BUDGET

    (phase_retrieve if args.phase == "retrieve" else phase_answer)(args)


if __name__ == "__main__":
    main()
