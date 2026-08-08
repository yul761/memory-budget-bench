#!/bin/bash
# Waits for the statecore retrieve to finish, then runs the answer phase for all
# ten arm-budget combinations and writes the report. Detached from any SSH
# session, so it survives the laptop being closed.
#
# The gate matters: answering on a short retrieve would produce a scored report
# built on a partial arm, which is exactly the class of silently-wrong result
# this whole effort exists to remove. If the retrieve dies short, this stops and
# leaves a marker instead of spending money on a number nobody should trust.

set -u
BENCH=/root/bench/memory-benchmarks
RETRIEVE_PID=${1:?need retrieve pid}
COMMIT=96b853d
LOG=/root/chain.log

exec >>"$LOG" 2>&1
echo "=== chain started $(date -u) watching pid $RETRIEVE_PID ==="

while kill -0 "$RETRIEVE_PID" 2>/dev/null; do sleep 120; done
echo "=== retrieve exited $(date -u) ==="

cd "$BENCH" || exit 1

DONE=$(wc -l < runs/fair/retrieve-statecore.jsonl)
if [ "$DONE" -lt 200 ]; then
  echo "BLOCKED: statecore retrieve stopped at $DONE/200. Not answering."
  echo "$DONE/200 at $(date -u)" > /root/CHAIN_BLOCKED
  exit 1
fi

# A retrieve that returns no facts is the failure mode that wasted the last run.
# Catch it here rather than in a report.
EMPTY=$(python3 -c "
import json
rows=[json.loads(l) for l in open('runs/fair/retrieve-statecore.jsonl')]
print(sum(1 for r in rows if r.get('facts_captured',0)==0))
")
if [ "$EMPTY" -gt 20 ]; then
  echo "BLOCKED: $EMPTY/200 statecore questions retrieved zero facts. Not answering."
  echo "empty=$EMPTY at $(date -u)" > /root/CHAIN_BLOCKED
  exit 1
fi
echo "gate passed: 200/200 retrieved, $EMPTY with zero facts"

export OPENAI_API_KEY=$(grep "^MODEL_API_KEY=" /root/bench/StateCore/.env | cut -d= -f2-)
COMMON="--run-name fair --n 200 --statecore-commit $COMMIT --require-arms statecore,mem0 --max-ingest-loss 0.10 --resume"

for b in 4000 16000 64000; do
  for arm in statecore mem0 recency; do
    echo "### answer arm=$arm budget=$b  $(date -u)"
    .venv/bin/python run_fair.py answer --arm "$arm" --budget "$b" $COMMON || echo "!!! failed arm=$arm budget=$b"
  done
done

echo "### answer arm=full budget=1000000 (ceiling)  $(date -u)"
.venv/bin/python run_fair.py answer --arm full --budget 1000000 $COMMON || echo "!!! failed arm=full"

echo "### report $(date -u)"
.venv/bin/python fair_report.py --run fair --budgets 4000,16000,64000 \
  --statecore-commit "$COMMIT" --out /root/FAIR-REPORT.md || echo "!!! report failed"

echo "### CHAIN DONE $(date -u)"
touch /root/CHAIN_DONE
