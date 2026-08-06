#!/usr/bin/env bash
# Retrieval-configuration sweep. Same 20 stratified questions (seed 42), same
# container, same answerer -- one variable changes per run so the deltas mean
# something. occurredAt stays OFF throughout; it is tested separately once a
# retrieval config is chosen.
set -uo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python

run () {
  local name="$1"; shift
  echo "=== $name : $* ==="
  $PY run_longmemeval.py --n 20 --seed 42 --run-name "$name" --no-occurred-at --resume "$@" \
    2>&1 | grep -vE '^\s*"' | tail -25
  echo
}

run sweep-A-k20-msg      --top-k 20  --granularity message
run sweep-B-k50-msg      --top-k 50  --granularity message
run sweep-C-k100-msg     --top-k 100 --granularity message
run sweep-D-k20-session  --top-k 20  --granularity session

echo "=== SWEEP COMPLETE ==="
