#!/usr/bin/env python3
"""Build a publishable markdown report for a run, under BOTH judges.

Every number is stamped with the knobs that produced it (dataset variant,
answerer, judge, top-k, commit) because LongMemEval scores are only comparable
when those match. See findings in REPORT header.

  python3 report.py --run sc-lme20-001
"""
import argparse, json, os
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
ORDER = ["single-session-user", "single-session-assistant", "single-session-preference",
         "multi-session", "knowledge-update", "temporal-reasoning"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    args = ap.parse_args()
    d = os.path.join(ROOT, "runs", args.run)

    meta = json.load(open(os.path.join(d, "meta.json")))
    official = json.load(open(os.path.join(d, "summary.json"))) \
        if os.path.exists(os.path.join(d, "summary.json")) else None
    mem0j = json.load(open(os.path.join(d, "summary_mem0_judge.json"))) \
        if os.path.exists(os.path.join(d, "summary_mem0_judge.json")) else None

    traces = [json.loads(l) for l in open(os.path.join(d, "traces.jsonl"))]
    ok = [t for t in traces if not t.get("error")]

    L = []
    L.append("# LongMemEval — %s\n" % args.run)
    L.append("## Setup\n")
    L.append("| knob | value |")
    L.append("|---|---|")
    for k in ("backend", "statecore_commit", "dataset", "n", "answerer",
              "top_k", "granularity", "digest_enabled", "occurred_at", "seed"):
        if k in meta:
            L.append("| %s | `%s` |" % (k, meta[k]))
    L.append("")
    L.append("## Accuracy\n")
    L.append("Same hypotheses, two judges. These are **not interchangeable** — "
             "mem0's judge is materially more permissive than the official one, "
             "so a number is only meaningful next to the judge that produced it.\n")
    L.append("| question type | official judge | mem0-style judge |")
    L.append("|---|---|---|")

    def cell(summary, t):
        if not summary:
            return "—"
        by = summary.get("by_type", {})
        if t not in by:
            return "—"
        e = by[t]
        n = e.get("total")
        c = e.get("correct")
        return "%.0f%% (%d/%d)" % (100.0 * c / n, c, n) if n else "—"

    types = [t for t in ORDER if (official and t in official.get("by_type", {}))
             or (mem0j and t in mem0j.get("by_type", {}))]
    for t in types:
        L.append("| %s | %s | %s |" % (t, cell(official, t), cell(mem0j, t)))
    for s, label in ((official, "official"), (mem0j, "mem0")):
        pass
    o = official["overall"] if official else None
    m = mem0j["overall"] if mem0j else None
    L.append("| **OVERALL** | %s | %s |" % (
        "**%.1f%% (%d/%d)**" % (100 * o["accuracy"], o["correct"], o["total"]) if o else "—",
        "**%.1f%% (%d/%d)**" % (100 * m["accuracy"], m["correct"], m["total"]) if m else "—"))
    L.append("")

    L.append("## Pipeline health\n")
    dg = sum(1 for t in ok if t.get("digest_ok"))
    L.append("- questions completed: **%d/%d** (%d errored)"
             % (len(ok), len(traces), len(traces) - len(ok)))
    L.append("- digest produced a state snapshot: **%d/%d**" % (dg, len(ok)))
    if dg < len(ok):
        L.append("  - when digest fails its consistency gate the whole digest is dropped "
                 "(no degraded fallback), so those questions are answered from retrieved "
                 "events alone, with no state layer.")
    ev = [t.get("retrieved_events", 0) for t in ok]
    if ev:
        L.append("- retrieved events/question: median %d" % sorted(ev)[len(ev) // 2])
    tm = [t["timings"] for t in ok if "timings" in t]
    if tm:
        L.append("\n### Latency (median per question)\n")
        L.append("| stage | seconds |")
        L.append("|---|---|")
        for k in ("ingest_s", "drain_s", "digest_s", "retrieve_s", "answer_s", "total_s"):
            vs = sorted(x[k] for x in tm)
            L.append("| %s | %.1f |" % (k, vs[len(vs) // 2]))
        L.append("\n`drain_s` is the async embed+classify queue draining — it dominates "
                 "wall clock and is bounded by the gpt-5-mini RPM limit, not by StateCore.")

    out = os.path.join(d, "REPORT.md")
    open(out, "w").write("\n".join(L) + "\n")
    print("\n".join(L))
    print("\nsaved", out)


if __name__ == "__main__":
    main()
