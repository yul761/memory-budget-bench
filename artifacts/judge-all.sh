#!/bin/bash
# Runs the official LongMemEval judge over every arm-budget answer set, then
# rebuilds the report. The answer phase writes hypotheses only; judging is a
# separate step that chain.sh omitted, which is why the report's result table
# came out empty.
set -u
BENCH=/root/bench/memory-benchmarks
COMMIT=96b853d
exec >>/root/judge.log 2>&1
cd "$BENCH" || exit 1

export OPENAI_API_KEY=$(grep "^MODEL_API_KEY=" /root/bench/StateCore/.env | cut -d= -f2-)
echo "=== judge started $(date -u) ==="

for d in runs/fair/answers/*/; do
  name=$(basename "$d")
  if [ -f "$d/hypotheses.jsonl.eval-results-gpt-4o" ]; then
    echo "### $name already judged, skipping"
    continue
  fi
  echo "### judging $name  $(date -u)"
  .venv/bin/python score.py --run "fair/answers/$name" || echo "!!! judge failed: $name"
done

echo "### report $(date -u)"
.venv/bin/python fair_report.py --run fair --budgets 4000,16000,64000 \
  --statecore-commit "$COMMIT" --out /root/FAIR-REPORT.md || echo "!!! report failed"

echo "### JUDGE DONE $(date -u)"
touch /root/JUDGE_DONE
