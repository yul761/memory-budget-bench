#!/usr/bin/env python3
"""Score the SAME hypotheses with mem0's judge instead of the official one.

mem0's harness (mem0ai/memory-benchmarks) does not use LongMemEval's official
evaluator. It ships a much more permissive unified judge prompt and defaults to
gpt-5 for both answering and judging. Numbers produced under that judge are NOT
comparable to numbers produced under the official evaluator -- so we report both,
and any published comparison must state which judge produced which number.

  python3 score_mem0_judge.py --run sc-lme20-001 [--judge-model gpt-5]
"""
import argparse, json, os, re, sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.abspath(__file__))
# The harness sits beside this script locally and one level up on the droplet.
for _cand in (os.path.join(ROOT, "mem0-harness"),
              os.path.join(ROOT, "..", "mem0-harness")):
    if os.path.isdir(os.path.join(_cand, "benchmarks")):
        sys.path.insert(0, os.path.abspath(_cand))
        break
else:
    sys.exit("mem0-harness not found next to or above " + ROOT)

from benchmarks.longmemeval.prompts import get_judge_prompt  # noqa: E402

from openai import OpenAI  # noqa: E402


def load_key():
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"]
    for line in open(os.path.join(ROOT, "..", "StateCore", ".env")):
        if line.startswith("MODEL_API_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("no API key")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--judge-model", default="gpt-5")
    ap.add_argument("--dataset", default=os.path.join(ROOT, "data", "longmemeval_s.json"))
    args = ap.parse_args()

    run_dir = os.path.join(ROOT, "runs", args.run)
    hyps = [json.loads(l) for l in open(os.path.join(run_dir, "hypotheses.jsonl")) if l.strip()]
    ref = {q["question_id"]: q for q in json.load(open(args.dataset))}
    client = OpenAI(api_key=load_key())

    def judge(h):
        q = ref[h["question_id"]]
        prompt = get_judge_prompt(q["question_type"], q["question_id"], q["question"],
                                  q["answer"], h["hypothesis"], q.get("question_date", ""))
        kwargs = {"model": args.judge_model,
                  "messages": [{"role": "user", "content": prompt}]}
        if not args.judge_model.startswith("gpt-5"):
            kwargs["temperature"] = 0
        out = client.chat.completions.create(**kwargs).choices[0].message.content
        tail = re.sub(r"<judge_thinking>.*?</judge_thinking>", "", out, flags=re.S).strip()
        return h["question_id"], tail.lower().rstrip(".").endswith("yes") or tail.lower() == "yes"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(judge, hyps))

    by = defaultdict(list)
    for qid, ok in results:
        by[ref[qid]["question_type"]].append(1 if ok else 0)
    overall = [v for vs in by.values() for v in vs]

    print("=" * 62)
    print("LongMemEval | run=%s | judge=mem0-style (%s)" % (args.run, args.judge_model))
    print("=" * 62)
    print("OVERALL           %5.1f%%  (%d/%d)" % (
        100.0 * sum(overall) / len(overall), sum(overall), len(overall)))
    for t in sorted(by):
        v = by[t]
        print("  %-26s %5.1f%%  (%d/%d)" % (t, 100.0 * sum(v) / len(v), sum(v), len(v)))
    print("=" * 62)

    json.dump({"judge": "mem0-style", "judge_model": args.judge_model,
               "overall": {"correct": sum(overall), "total": len(overall),
                           "accuracy": round(sum(overall) / len(overall), 4)},
               "by_type": {t: {"correct": sum(v), "total": len(v)} for t, v in by.items()},
               "labels": {qid: ok for qid, ok in results}},
              open(os.path.join(run_dir, "summary_mem0_judge.json"), "w"), indent=2)
    print("saved", os.path.join(run_dir, "summary_mem0_judge.json"))


if __name__ == "__main__":
    main()
