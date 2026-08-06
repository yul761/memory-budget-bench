#!/usr/bin/env bash
# Only untested feature left: occurredAt. It targets temporal-reasoning (133 of
# the 500 questions), so it is worth settling before the formal run. Same 10
# temporal questions, same seed, config E -- occurredAt is the only variable.
set -uo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python
for arg in "--no-occurred-at:off" ":on"; do
  flag="${arg%%:*}"; label="${arg##*:}"
  echo "=== occurredAt=$label ==="
  $PY run_longmemeval.py --n 10 --seed 42 --question-type temporal-reasoning \
    --run-name "ab-occ-$label" --top-k 50 --granularity session --resume $flag \
    2>&1 | grep -vE '^\s*"' | tail -14
done
echo "=== AB COMPLETE ==="
