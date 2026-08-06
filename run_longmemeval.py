#!/usr/bin/env python3
"""LongMemEval runner: ingest haystack into a memory backend, retrieve, answer.

Emits `{question_id, hypothesis}` JSONL consumable by the OFFICIAL evaluator:
  python official-longmemeval/src/evaluation/evaluate_qa.py gpt-4o <hyp> <ref>

Usage:
  python3 run_longmemeval.py --backend statecore --n 20 --run-name sc-001
"""
import argparse, json, os, random, subprocess, sys, time
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adapters.statecore import StateCoreBackend
from adapters.mem0 import Mem0Backend

ROOT = os.path.dirname(os.path.abspath(__file__))


def load_env(path):
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if not v:
            continue  # empty values (e.g. OPENAI_BASE_URL=) would override SDK defaults
        os.environ.setdefault(k.strip(), v)


load_env(os.path.join(ROOT, "..", "StateCore", ".env"))

try:
    from openai import OpenAI
except ImportError:
    sys.exit("pip install openai")


def queue_pending():
    """Total waiting+active jobs across embed/classify (async enrichment)."""
    total = 0
    for q in ("embed", "classify"):
        for state in ("wait", "active", "prioritized"):
            try:
                out = subprocess.run(
                    ["docker", "exec", "statecore-redis-1", "redis-cli", "LLEN",
                     "bull:%s:%s" % (q, state)],
                    capture_output=True, text=True, timeout=15)
                total += int(out.stdout.strip() or 0)
            except Exception:
                pass
    return total


ANSWER_SYSTEM = (
    "You are a helpful assistant answering a question about a user, using only the "
    "memory excerpts provided. The excerpts come from past conversations and are "
    "prefixed with the date they occurred. Answer concisely and directly. "
    "If the memory does not contain enough information to answer, say you don't know."
)

# Step 1 of --answer-mode extract-then-answer.
#
# The 200-question run answered "I don't know" 60 times with recall at 1.00: the
# evidence was in context and went unused, and shrinking the context made it
# worse, so the problem is representation rather than volume. This pulls the
# relevant facts out of the transcript and lists them side by side -- the shape
# mem0's extracted memories already have -- to test whether that alone recovers
# the answers. It reads only the question and the retrieved context; the gold
# answer is never in scope.
EXTRACT_SYSTEM = (
    "Extract every fact from the memory excerpts that could bear on the question, "
    "including facts that only matter for comparison (earlier values, superseded "
    "values, dates, counts). Write one fact per line, each self-contained and "
    "carrying its date when known. Do not answer the question. Do not infer beyond "
    "what the excerpts state. If a value changed over time, list each value "
    "separately with its date."
)


def evidence_recall(q, retrieved_events):
    """How much of the gold evidence did retrieval actually surface?

    LongMemEval marks evidence turns with has_answer=True inside the sessions
    listed in answer_session_ids. We ingest one event per message, so we match on
    the message text being contained in the retrieved event's content.
    """
    gold = set()
    answer_sids = set(q.get("answer_session_ids") or [])
    for sid, sess in zip(q.get("haystack_session_ids") or [], q["haystack_sessions"]):
        for m in sess:
            if m.get("has_answer") or (sid in answer_sids and not answer_sids):
                gold.add(m["content"].strip())
    if not gold:
        for sid, sess in zip(q.get("haystack_session_ids") or [], q["haystack_sessions"]):
            if sid in answer_sids:
                for m in sess:
                    gold.add(m["content"].strip())
    blob = "\n".join(e.get("content", "") for e in retrieved_events)
    hit = sum(1 for g in gold if g and g[:300] in blob)
    return {"gold_turns": len(gold), "retrieved_gold": hit,
            "recall": round(hit / len(gold), 3) if gold else None}


def build_answer_prompt(question, question_date, retrieved):
    parts = []
    if retrieved.get("digest"):
        parts.append("## Consolidated state\n" + retrieved["digest"])
    facts = retrieved.get("factRegistry") or []
    if facts:
        parts.append("## Known facts\n" + "\n".join(
            "- %s" % (json.dumps(f, ensure_ascii=False) if isinstance(f, dict) else f)
            for f in facts[:40]))
    events = retrieved.get("events") or []
    if events:
        # No per-event truncation. A 2000-char cap was fine when one event was one
        # chat message; at session granularity the median event is ~9.8k chars, so
        # it silently dropped 80% of every retrieved session -- including the turns
        # that answered the question. Retrieval recall stayed at 1.00 while the
        # answerer never saw the evidence, which made the failure look like a
        # memory problem rather than a harness one. Systems whose retrieval unit is
        # already compact (mem0's extracted facts) were barely affected, so the cap
        # also biased the comparison.
        parts.append("## Retrieved conversation excerpts\n" + "\n".join(
            "- %s" % e["content"] for e in events))
    if not parts:
        parts.append("(no memory retrieved)")
    return "Current date: %s\n\n%s\n\n## Question\n%s" % (
        question_date, "\n\n".join(parts), question)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="statecore", choices=["statecore", "mem0"])
    ap.add_argument("--backend-url", default=None, help="override backend base URL")
    ap.add_argument("--dataset", default=os.path.join(ROOT, "data", "longmemeval_s.json"))
    ap.add_argument("--n", type=int, default=20, help="0 = all")
    ap.add_argument("--question-type", default=None,
                    help="restrict to one type, e.g. temporal-reasoning (for A/B tests)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--granularity", default="message", choices=["message", "session"])
    # gpt-5 matches what mem0's own harness answers with, so a StateCore-vs-mem0
    # comparison is not confounded by one side getting a weaker reader. It also
    # removes the "small model lost the needle in a long context" explanation for
    # low scores, which gpt-4o-mini could not rule out.
    ap.add_argument("--answerer", default="gpt-5")
    ap.add_argument("--answer-mode", default="direct",
                    choices=["direct", "extract-then-answer"],
                    help="extract-then-answer pulls question-relevant facts out of the "
                         "retrieved transcript first, then answers from that list")
    ap.add_argument("--run-name", required=True)
    ap.add_argument("--digest-wait", type=int, default=180)
    ap.add_argument("--no-digest", action="store_true")
    ap.add_argument("--no-occurred-at", action="store_true",
                    help="do not send occurredAt; historical time lives only in content text")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    out_dir = os.path.join(ROOT, "runs", args.run_name)
    os.makedirs(out_dir, exist_ok=True)
    hyp_path = os.path.join(out_dir, "hypotheses.jsonl")
    trace_path = os.path.join(out_dir, "traces.jsonl")

    done = set()
    if args.resume and os.path.exists(hyp_path):
        done = {json.loads(l)["question_id"] for l in open(hyp_path) if l.strip()}
        print("resuming, %d already done" % len(done))

    data = json.load(open(args.dataset))

    if args.question_type:
        data = [q for q in data if q["question_type"] == args.question_type]
        if not data:
            sys.exit("no questions of type " + args.question_type)
        # Same seed picks the same subset across A/B runs, so the only thing that
        # differs between them is the flag under test.
        random.Random(args.seed).shuffle(data)
        if args.n:
            data = data[:args.n]

    # Stratified sample so every question_type is represented.
    if args.n and args.n < len(data):
        by_type = defaultdict(list)
        for q in data:
            by_type[q["question_type"]].append(q)
        rng = random.Random(args.seed)
        for v in by_type.values():
            rng.shuffle(v)
        types = sorted(by_type)
        picked, i = [], 0
        while len(picked) < args.n:
            t = types[i % len(types)]
            if by_type[t]:
                picked.append(by_type[t].pop())
            i += 1
            if all(not by_type[t] for t in types):
                break
        data = picked
    print("running %d questions: %s" % (len(data), dict(Counter(q["question_type"] for q in data))))

    if args.backend == "mem0":
        backend = Mem0Backend(**({"base_url": args.backend_url} if args.backend_url else {}))
    else:
        backend = StateCoreBackend(**({"base_url": args.backend_url} if args.backend_url else {}))
    api_key = next((os.environ[k] for k in ("OPENAI_API_KEY", "MODEL_API_KEY",
                                            "MODEL_CHAT_API_KEY") if os.environ.get(k)), None)
    if not api_key:
        sys.exit("no OpenAI key found in env (OPENAI_API_KEY / MODEL_API_KEY)")
    client = OpenAI(api_key=api_key)

    meta = {
        "run_name": args.run_name, "backend": args.backend, "started": datetime.now().isoformat(),
        "dataset": os.path.basename(args.dataset), "n": len(data), "seed": args.seed,
        "top_k": args.top_k, "granularity": args.granularity, "answerer": args.answerer,
        "digest_enabled": not args.no_digest,
        "answer_mode": args.answer_mode,
        "occurred_at": not args.no_occurred_at,
        "statecore_commit": subprocess.run(
            ["git", "-C", os.path.join(ROOT, "..", "StateCore"), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True).stdout.strip(),
    }
    json.dump(meta, open(os.path.join(out_dir, "meta.json"), "w"), indent=2)
    print(json.dumps(meta, indent=2))

    hyp_f = open(hyp_path, "a")
    trace_f = open(trace_path, "a")
    t_start = time.time()

    for idx, q in enumerate(data, 1):
        qid = q["question_id"]
        if qid in done:
            continue
        t0 = time.time()
        scope_id = None
        rec = {"question_id": qid, "question_type": q["question_type"], "idx": idx}
        try:
            scope_id = backend.create_scope("lme-" + qid)
            rec["scope_id"] = scope_id

            ing = backend.ingest_sessions(scope_id, q["haystack_sessions"],
                                          q["haystack_dates"], args.granularity,
                                          send_occurred_at=not args.no_occurred_at)
            rec["ingest"] = ing
            t_ing = time.time()

            drained = backend.wait_for_embeddings(ing["events"], queue_pending, wait_s=1800)
            rec["queue_drained"] = drained
            t_drain = time.time()

            if args.no_digest:
                rec["digest_ok"] = None
            else:
                ok, detail = backend.run_digest(scope_id, wait_s=args.digest_wait)
                rec["digest_ok"] = ok
                if not ok:
                    rec["digest_error"] = str(detail)[:200]
            t_dig = time.time()

            retrieved = backend.search(scope_id, q["question"], args.top_k)
            rec["retrieved_events"] = len(retrieved.get("events") or [])
            rec["retrieved_digest"] = bool(retrieved.get("digest"))
            # The state layer's own contribution, separate from raw event recall:
            # a ~120-word digest plus the fact registry. Recorded so a failure can
            # be attributed to the state layer or to event retrieval, not just to
            # "the answer was wrong".
            rec["digest_chars"] = len(retrieved.get("digest") or "")
            rec["fact_registry_n"] = len(retrieved.get("factRegistry") or [])
            rec["retrieval_mode"] = (retrieved.get("retrieval") or {}).get("mode")
            # Retrieval recall against the gold evidence sessions: without this a
            # wrong answer is ambiguous between "did not retrieve" and "retrieved
            # but reasoned badly", which are entirely different fixes.
            rec["evidence"] = evidence_recall(q, retrieved.get("events") or [])
            rec["total_haystack_msgs"] = sum(len(s) for s in q["haystack_sessions"])
            t_ret = time.time()

            prompt = build_answer_prompt(q["question"], q["question_date"], retrieved)

            if args.answer_mode == "extract-then-answer":
                ex = {"model": args.answerer,
                      "messages": [{"role": "system", "content": EXTRACT_SYSTEM},
                                   {"role": "user", "content": prompt}]}
                if args.answerer.startswith("gpt-5"):
                    ex["max_completion_tokens"] = 4096
                else:
                    ex["temperature"] = 0
                    ex["max_tokens"] = 1024
                facts = (client.chat.completions.create(**ex)
                         .choices[0].message.content or "").strip()
                rec["extracted_facts_chars"] = len(facts)
                prompt = ("Current date: %s\n\n## Facts recalled from memory\n%s\n\n"
                          "## Question\n%s" % (q["question_date"], facts, q["question"]))

            kwargs = {"model": args.answerer,
                      "messages": [{"role": "system", "content": ANSWER_SYSTEM},
                                   {"role": "user", "content": prompt}]}
            if args.answerer.startswith("gpt-5"):
                # Reasoning models reject temperature and use max_completion_tokens.
                # Headroom above 512: the budget covers reasoning tokens too, and a
                # too-small cap returns an empty answer rather than an error.
                kwargs["max_completion_tokens"] = 4096
            else:
                kwargs["temperature"] = 0
                kwargs["max_tokens"] = 512
            comp = client.chat.completions.create(**kwargs)
            hypothesis = (comp.choices[0].message.content or "").strip()

            rec["timings"] = {
                "ingest_s": round(t_ing - t0, 1), "drain_s": round(t_drain - t_ing, 1),
                "digest_s": round(t_dig - t_drain, 1), "retrieve_s": round(t_ret - t_dig, 1),
                "answer_s": round(time.time() - t_ret, 1), "total_s": round(time.time() - t0, 1),
            }
            rec["hypothesis"] = hypothesis
            rec["prompt_chars"] = len(prompt)

            hyp_f.write(json.dumps({"question_id": qid, "hypothesis": hypothesis}) + "\n")
            hyp_f.flush()
            print("[%d/%d] %-26s %-24s digest=%s ev=%d %.0fs" % (
                idx, len(data), qid, q["question_type"], rec.get("digest_ok"),
                rec["retrieved_events"], rec["timings"]["total_s"]), flush=True)
        except Exception as e:
            rec["error"] = "%s: %s" % (type(e).__name__, e)
            print("[%d/%d] %s FAILED %s" % (idx, len(data), qid, rec["error"]), flush=True)
        finally:
            if scope_id:
                backend.delete_scope(scope_id)
            trace_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            trace_f.flush()

    print("\ntotal wall clock: %.1f min -> %s" % ((time.time() - t_start) / 60, hyp_path))


if __name__ == "__main__":
    main()
