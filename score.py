#!/usr/bin/env python3
"""Score a run with the OFFICIAL LongMemEval judge and print a breakdown.

Wraps official-longmemeval/src/evaluation/evaluate_qa.py (same prompts, same
judge model, same abstention rule) so the number stays comparable to published
LongMemEval results.

  python3 score.py --run sc-lme20-001 [--judge gpt-4o]
"""
import argparse, json, os, subprocess, sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--judge", default="gpt-4o", choices=["gpt-4o", "gpt-4o-mini"])
    ap.add_argument("--dataset", default=os.path.join(ROOT, "data", "longmemeval_s.json"))
    args = ap.parse_args()

    run_dir = os.path.join(ROOT, "runs", args.run)
    hyp = os.path.join(run_dir, "hypotheses.jsonl")
    if not os.path.exists(hyp):
        sys.exit("no hypotheses at " + hyp)

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        for line in open(os.path.join(ROOT, "..", "StateCore", ".env")):
            if line.startswith("MODEL_API_KEY="):
                key = line.split("=", 1)[1].strip()
    env = dict(os.environ, OPENAI_API_KEY=key)

    print("scoring %s with official evaluator (judge=%s)..." % (args.run, args.judge))
    r = subprocess.run(
        [os.path.join(ROOT, ".venv", "bin", "python"),
         os.path.join(ROOT, "official-longmemeval", "src", "evaluation", "evaluate_qa.py"),
         args.judge, hyp, args.dataset],
        env=env, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-3000:]); sys.exit(r.stderr[-3000:])

    res_file = hyp + ".eval-results-" + args.judge
    labels = [json.loads(l) for l in open(res_file) if l.strip()]
    qtype = {q["question_id"]: q["question_type"] for q in json.load(open(args.dataset))}

    traces = {}
    tp = os.path.join(run_dir, "traces.jsonl")
    if os.path.exists(tp):
        for l in open(tp):
            t = json.loads(l)
            traces[t["question_id"]] = t

    by = defaultdict(list)
    for e in labels:
        by[qtype[e["question_id"]]].append(1 if e["autoeval_label"]["label"] else 0)
    overall = [v for vs in by.values() for v in vs]

    meta = json.load(open(os.path.join(run_dir, "meta.json")))
    print("\n" + "=" * 62)
    print("LongMemEval  |  run=%s  backend=%s  commit=%s" % (
        args.run, meta.get("backend"), meta.get("statecore_commit")))
    print("answerer=%s  judge=%s  top_k=%s  granularity=%s" % (
        meta.get("answerer"), args.judge, meta.get("top_k"), meta.get("granularity")))
    print("=" * 62)
    print("OVERALL           %5.1f%%  (%d/%d)" % (
        100.0 * sum(overall) / len(overall), sum(overall), len(overall)))
    print("-" * 62)
    for t in sorted(by):
        v = by[t]
        print("  %-26s %5.1f%%  (%d/%d)" % (t, 100.0 * sum(v) / len(v), sum(v), len(v)))

    if traces:
        ok = [t for t in traces.values() if not t.get("error")]
        dg = [t for t in ok if t.get("digest_ok")]
        tot = [t["timings"]["total_s"] for t in ok if "timings" in t]
        print("-" * 62)
        print("  digest success            %d/%d" % (len(dg), len(ok)))
        print("  failed questions          %d" % sum(1 for t in traces.values() if t.get("error")))
        if tot:
            print("  median wall clock/question %.0fs" % sorted(tot)[len(tot) // 2])
            for k in ("ingest_s", "drain_s", "digest_s", "retrieve_s", "answer_s"):
                vs = sorted(t["timings"][k] for t in ok if "timings" in t)
                print("    %-12s median %6.1fs" % (k, vs[len(vs) // 2]))
    print("=" * 62)

    summary = {"run": args.run, "judge": args.judge, "meta": meta,
               "overall": {"correct": sum(overall), "total": len(overall),
                           "accuracy": round(sum(overall) / len(overall), 4)},
               "by_type": {t: {"correct": sum(v), "total": len(v),
                               "accuracy": round(sum(v) / len(v), 4)} for t, v in by.items()}}
    json.dump(summary, open(os.path.join(run_dir, "summary.json"), "w"), indent=2)
    print("saved", os.path.join(run_dir, "summary.json"))


if __name__ == "__main__":
    main()
