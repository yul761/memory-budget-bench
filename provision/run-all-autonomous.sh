#!/usr/bin/env bash
# Everything from smoke test to final report, in one detached run ON the droplet.
#
# The operator's laptop drives this over SSH but will be shut down mid-run, so
# nothing here may depend on that session staying alive: each stage is chained
# in-process and the whole script runs under setsid.
#
# Re-run of the earlier comparison under the fixed harness. The previous numbers
# were invalidated by a 2000-char cap on each retrieved item, which discarded
# ~80% of every retrieved session before the answerer saw it.
set -uo pipefail
cd /root/bench/memory-benchmarks
PY=.venv/bin/python
N=${N:-200}

log () { echo "[$(date -u +%H:%M:%S)] $*"; }

log "=== stage 1/5: smoke, both backends ==="
for b in statecore mem0; do
  url=$([ "$b" = statecore ] && echo http://localhost:3002 || echo http://localhost:8888)
  $PY run_longmemeval.py --backend "$b" --backend-url "$url" --n 3 --seed 42 \
    --run-name "smoke2-$b" --top-k 50 --granularity session --no-occurred-at --resume \
    2>&1 | grep -vE '^\s+"' | tail -6
done

# Abort rather than burn hours on a broken backend.
for b in statecore mem0; do
  n=$(wc -l < "runs/smoke2-$b/traces.jsonl" 2>/dev/null || echo 0)
  if [ "$n" -lt 3 ]; then log "SMOKE FAILED for $b ($n/3) — aborting"; exit 1; fi
done
log "smoke ok"

log "=== stage 2/5: StateCore x$N ==="
$PY run_longmemeval.py --backend statecore --backend-url http://localhost:3002 \
  --n "$N" --seed 42 --run-name final-statecore \
  --top-k 50 --granularity session --no-occurred-at --resume 2>&1 | tail -20

log "=== stage 3/5: mem0 x$N ==="
$PY run_longmemeval.py --backend mem0 --backend-url http://localhost:8888 \
  --n "$N" --seed 42 --run-name final-mem0 \
  --top-k 50 --granularity session --no-occurred-at --resume 2>&1 | tail -20

log "=== stage 4/5: scoring, both judges ==="
for r in final-statecore final-mem0; do
  $PY score.py --run "$r" --judge gpt-4o 2>&1 | grep -E 'OVERALL|single|multi|knowledge|temporal|digest success'
  $PY score_mem0_judge.py --run "$r" --judge-model gpt-5 2>&1 | grep -E 'OVERALL'
done

log "=== stage 5/5: report ==="
$PY final_report.py --statecore final-statecore --mem0 final-mem0 \
  --cost-statecore 0.26 --cost-mem0 0.09 --out /root/REPORT.md 2>&1 | tail -5

log "=== ALL DONE ==="
touch /root/ALL_DONE
