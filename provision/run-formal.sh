#!/usr/bin/env bash
# Formal benchmark: StateCore vs mem0 OSS, same host, same runner, same sample.
# Runs ON the droplet.
#
# Sequenced, not parallel: both systems drive the same OpenAI account, so running
# them together would have them competing for the same rate limit and make the
# latency numbers meaningless.
#
# n=200 is the sample size, not 20: at n=20 the 95% interval on a difference
# between two systems is ±31 points, which cannot support any comparative claim.
# At n=200 it is ±9.8.
set -uo pipefail
cd /root/bench/memory-benchmarks

N=${N:-200}
TOPK=${TOPK:-50}
GRAN=${GRAN:-session}
OCC=${OCC:-}          # set to "--no-occurred-at" to disable
PY=.venv/bin/python

echo "=== config: n=$N top_k=$TOPK gran=$GRAN occurredAt=${OCC:-on} ==="

echo
echo "=== [1/2] StateCore ==="
$PY run_longmemeval.py --backend statecore --backend-url http://localhost:3002 \
  --n "$N" --seed 42 --run-name "formal-statecore" \
  --top-k "$TOPK" --granularity "$GRAN" --resume $OCC 2>&1 | tail -40

echo
echo "=== [2/2] mem0 OSS ==="
$PY run_longmemeval.py --backend mem0 --backend-url http://localhost:8888 \
  --n "$N" --seed 42 --run-name "formal-mem0" \
  --top-k "$TOPK" --granularity "$GRAN" --resume $OCC 2>&1 | tail -40

echo
echo "=== FORMAL RUN COMPLETE ==="
wc -l runs/formal-statecore/hypotheses.jsonl runs/formal-mem0/hypotheses.jsonl 2>/dev/null
