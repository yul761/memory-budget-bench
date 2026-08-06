#!/usr/bin/env python3
"""Produce the publishable StateCore vs mem0 report.

Every number carries the configuration that produced it. A LongMemEval score is
not a property of a memory system -- the same StateCore build scored 35% and
65.5% on this machine today purely from retrieval-config changes -- so a bare
number invites a reproduction that fails and reads as overstatement.

  python3 final_report.py --statecore formal-statecore --mem0 formal-mem0 \
      [--out REPORT.md]
"""
import argparse, json, math, os, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
TYPES = ["single-session-user", "single-session-assistant", "single-session-preference",
         "multi-session", "knowledge-update", "temporal-reasoning"]


def load(run):
    d = os.path.join(ROOT, "runs", run)
    if not os.path.isdir(d):
        sys.exit("missing run dir: " + d)
    rows = [json.loads(l) for l in open(os.path.join(d, "traces.jsonl")) if l.strip()]
    ok = [r for r in rows if not r.get("error")]

    def maybe(name):
        p = os.path.join(d, name)
        return json.load(open(p)) if os.path.exists(p) else None

    t = sorted(r["timings"]["total_s"] for r in ok if "timings" in r)
    return {
        "run": run, "meta": json.load(open(os.path.join(d, "meta.json"))),
        "official": maybe("summary.json"), "mem0_judge": maybe("summary_mem0_judge.json"),
        "rows": rows, "ok": ok, "n": len(ok),
        "errors": len(rows) - len(ok),
        "digest_ok": sum(1 for r in ok if r.get("digest_ok")),
        "digest_na": sum(1 for r in ok if r.get("digest_ok") is None),
        "median_s": t[len(t) // 2] if t else 0,
    }


def ci(acc, n):
    return 1.96 * math.sqrt(max(acc * (1 - acc), 1e-9) / n) * 100 if n else 0


def cell(summary):
    if not summary:
        return "—"
    o = summary["overall"]
    return "**%.1f%%** ±%.1f (%d/%d)" % (
        100 * o["accuracy"], ci(o["accuracy"], o["total"]), o["correct"], o["total"])


def type_cell(summary, t):
    if not summary:
        return "—"
    e = summary.get("by_type", {}).get(t)
    if not e:
        return "—"
    return "%.1f%% (%d/%d)" % (100 * e["correct"] / e["total"], e["correct"], e["total"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--statecore", default="formal-statecore")
    ap.add_argument("--mem0", default="formal-mem0")
    ap.add_argument("--out", default=os.path.join(ROOT, "REPORT.md"))
    ap.add_argument("--cost-statecore", type=float, default=None)
    ap.add_argument("--cost-mem0", type=float, default=None)
    args = ap.parse_args()

    sc, m0 = load(args.statecore), load(args.mem0)
    meta = sc["meta"]
    L = []

    L.append("# LongMemEval: StateCore vs mem0 OSS\n")
    L.append("Both systems were driven by the **same runner**, over the **same question "
             "sample**, with the **same answerer and judges**. The memory system is the "
             "only thing that differs.\n")

    L.append("## Result\n")
    L.append("| system | official judge (gpt-4o) | mem0-style judge (gpt-5) |")
    L.append("|---|---|---|")
    L.append("| **StateCore** | %s | %s |" % (cell(sc["official"]), cell(sc["mem0_judge"])))
    L.append("| **mem0 OSS** | %s | %s |" % (cell(m0["official"]), cell(m0["mem0_judge"])))
    L.append("")
    if sc["official"] and m0["official"]:
        a, b = sc["official"]["overall"], m0["official"]["overall"]
        diff = 100 * (a["accuracy"] - b["accuracy"])
        se = math.sqrt(a["accuracy"] * (1 - a["accuracy"]) / a["total"]
                       + b["accuracy"] * (1 - b["accuracy"]) / b["total"]) * 100
        L.append("Difference (official judge): **%+.1f points**, 95%% CI ±%.1f. %s\n" % (
            diff, 1.96 * se,
            "The interval excludes zero, so the gap is not noise."
            if abs(diff) > 1.96 * se else
            "**The interval includes zero — this gap is not statistically distinguishable.**"))

    L.append("## By question type (official judge)\n")
    L.append("| question type | StateCore | mem0 OSS |")
    L.append("|---|---|---|")
    for t in TYPES:
        L.append("| %s | %s | %s |" % (t, type_cell(sc["official"], t), type_cell(m0["official"], t)))
    L.append("")

    L.append("## Cost and latency\n")
    L.append("| | StateCore | mem0 OSS |")
    L.append("|---|---|---|")
    L.append("| median wall clock / question | %.0fs | %.0fs |" % (sc["median_s"], m0["median_s"]))
    if args.cost_statecore and args.cost_mem0:
        L.append("| measured OpenAI cost / question | $%.3f | $%.3f |"
                 % (args.cost_statecore, args.cost_mem0))
    L.append("| questions completed | %d | %d |" % (sc["n"], m0["n"]))
    L.append("| runtime errors | %d | %d |" % (sc["errors"], m0["errors"]))
    sd = "%d/%d" % (sc["digest_ok"], sc["n"])
    L.append("| digest / state layer produced | %s | n/a (no state layer) |" % sd)
    L.append("")

    L.append("## Configuration (all of it)\n")
    L.append("| knob | value |")
    L.append("|---|---|")
    for k, label in [("dataset", "dataset"), ("n", "sample size"), ("seed", "seed"),
                     ("granularity", "ingest granularity"), ("top_k", "retrieval top-k"),
                     ("answerer", "answerer model"), ("occurred_at", "occurredAt enabled"),
                     ("statecore_commit", "StateCore commit")]:
        if k in meta:
            L.append("| %s | `%s` |" % (label, meta[k]))
    L.append("| StateCore internal model | `gpt-4o-mini` |")
    L.append("| mem0 internal model | `gpt-4o-mini` |")
    L.append("| embedder (both) | `text-embedding-3-small` |")
    L.append("| official judge | `gpt-4o-2024-08-06`, LongMemEval `evaluate_qa.py` |")
    L.append("| mem0-style judge | `gpt-5`, mem0 harness `JUDGE_PROMPT` |")
    L.append("| host | single DigitalOcean s-4vcpu-8gb, both systems |")
    L.append("")

    L.append("## Reproduction notes\n")
    L.append("Deviations a reader would otherwise trip over:\n")
    L.append("- **mem0's published Docker config does not build.** Its harness pins "
             "`mem0ai @ git+.../mem0.git@feat/v3-pipeline`; that branch no longer exists "
             "upstream. Tested against `feat/oss-add-v3-ingestion-caps` instead.")
    L.append("- **mem0's server code needed a one-line compatibility fix**: current mem0ai "
             "rejects a top-level `user_id` in `search()` and requires `filters={...}`. "
             "Extraction, storage and ranking are untouched.")
    L.append("- **Internal model is gpt-4o-mini for both, not each project's default.** "
             "StateCore normally runs gpt-5-mini, but gpt-5 reasoning models reject the "
             "`temperature`/`top_p` that mem0's client always sends, and gpt-4o-mini rejects "
             "StateCore's `reasoning_effort`. Parity mattered more than matching either default.")
    L.append("- Scores are **not comparable to either project's published numbers**, which "
             "use different answerers, judges and dataset variants.")
    L.append("")

    L.append("## Caveat on interpreting these numbers\n")
    L.append("A LongMemEval score is a property of *system + configuration*, not of the "
             "system. The same StateCore build measured on this machine today scored:\n")
    L.append("| config | score |")
    L.append("|---|---|")
    L.append("| message granularity, top-k 20 | 35% |")
    L.append("| message granularity, top-k 100 | 50% |")
    L.append("| session granularity, top-k 50 | 65.5% |")
    L.append("\nA 30-point spread from retrieval configuration alone. Quote the config with "
             "the number, or the number will not reproduce.\n")

    open(args.out, "w").write("\n".join(L) + "\n")
    print("\n".join(L))
    print("\nsaved", args.out)


if __name__ == "__main__":
    main()
