#!/usr/bin/env python3
"""Cross-variant comparison table for a sweep.

The point of the sweep is the relationship between retrieval coverage and
accuracy, so recall is reported next to accuracy for every variant -- an
accuracy number alone cannot distinguish "did not retrieve the evidence" from
"retrieved it and answered badly", and those need different fixes.

  python3 compare.py v2-A-k20-msg v2-B-k50-msg ...
  python3 compare.py --prefix v2-
"""
import argparse, json, math, os, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
TYPES = ["single-session-user", "single-session-assistant", "single-session-preference",
         "multi-session", "knowledge-update", "temporal-reasoning"]


def load(run):
    d = os.path.join(ROOT, "runs", run)
    tp = os.path.join(d, "traces.jsonl")
    if not os.path.exists(tp):
        return None
    rows = [json.loads(l) for l in open(tp) if l.strip()]
    ok = [r for r in rows if not r.get("error")]
    meta = json.load(open(os.path.join(d, "meta.json")))
    sp = os.path.join(d, "summary.json")
    summary = json.load(open(sp)) if os.path.exists(sp) else None
    mp = os.path.join(d, "summary_mem0_judge.json")
    mem0_judge = json.load(open(mp)) if os.path.exists(mp) else None
    rec = [(r.get("evidence") or {}).get("recall") or 0 for r in ok]
    return {
        "run": run, "meta": meta, "summary": summary, "mem0_judge": mem0_judge,
        "rows": rows, "ok": ok,
        "recall": sum(rec) / len(rec) if rec else 0,
        "zero_recall": sum(1 for x in rec if x == 0),
        "digest_ok": sum(1 for r in ok if r.get("digest_ok")),
        "n": len(ok),
        "median_s": sorted(r["timings"]["total_s"] for r in ok if "timings" in r)[len(ok) // 2] if ok else 0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="*")
    ap.add_argument("--prefix")
    args = ap.parse_args()

    runs = args.runs
    if args.prefix:
        runs = sorted(d for d in os.listdir(os.path.join(ROOT, "runs"))
                      if d.startswith(args.prefix)
                      and os.path.isdir(os.path.join(ROOT, "runs", d)))
    if not runs:
        sys.exit("no runs given")

    data = [d for d in (load(r) for r in runs) if d]
    if not data:
        sys.exit("no usable runs")

    m = data[0]["meta"]
    print("answerer=%s  judge=gpt-4o (official)  commit=%s  n=%s  seed=%s"
          % (m.get("answerer"), m.get("statecore_commit"), m.get("n"), m.get("seed")))
    print()
    hdr = "%-22s %-6s %-8s %-8s %-9s %-8s %-16s %s" % (
        "variant", "top_k", "gran", "recall", "zero-rec", "digest",
        "acc (official)", "acc (mem0 judge)")
    print(hdr)
    print("-" * len(hdr))

    def pct(summary):
        if not summary:
            return "—"
        o = summary["overall"]
        n = o["total"]
        # 95% CI on a proportion; without it a 5-point gap at n=20 reads as a
        # result when it is noise.
        half = 1.96 * math.sqrt(max(o["accuracy"] * (1 - o["accuracy"]), 1e-9) / n) * 100
        return "%.1f%% ±%.0f (%d/%d)" % (100 * o["accuracy"], half, o["correct"], n)

    for d in data:
        print("%-22s %-6s %-8s %-8.2f %-9s %-8s %-16s %s" % (
            d["run"], d["meta"].get("top_k"), d["meta"].get("granularity")[:7],
            d["recall"], "%d/%d" % (d["zero_recall"], d["n"]),
            "%d/%d" % (d["digest_ok"], d["n"]),
            pct(d["summary"]), pct(d["mem0_judge"])))

    scored = [d for d in data if d["summary"]]
    if scored:
        print()
        print("by question type (correct/total)")
        w = max(len(d["run"]) for d in scored)
        print("  %-28s %s" % ("", "  ".join(d["run"][:14].ljust(14) for d in scored)))
        for t in TYPES:
            cells = []
            for d in scored:
                e = d["summary"]["by_type"].get(t)
                cells.append((("%d/%d" % (e["correct"], e["total"])) if e else "—").ljust(14))
            print("  %-28s %s" % (t, "  ".join(cells)))

    print()
    print("median wall clock/question: " + ", ".join(
        "%s=%.0fs" % (d["run"].split("-")[1] if "-" in d["run"] else d["run"], d["median_s"]) for d in data))
    _ = w if scored else None


if __name__ == "__main__":
    main()
