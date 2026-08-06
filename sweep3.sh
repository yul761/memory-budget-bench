#!/usr/bin/env bash
# Full re-run on the fixed stack. Everything from the first sweep is stale:
#   - answerer is now gpt-5 (was gpt-4o-mini), matching mem0's harness default
#   - digest no longer blows the model context on large events
#   - digest no longer discards all state when the consistency gate trips
#   - consistency retries now name the conflicting fact
# Same 20 stratified questions (seed 42), occurredAt still off, so retrieval
# config remains the only variable across variants.
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

run v2-A-k20-msg      --top-k 20  --granularity message
run v2-B-k50-msg      --top-k 50  --granularity message
run v2-C-k100-msg     --top-k 100 --granularity message
run v2-D-k20-session  --top-k 20  --granularity session
run v2-E-k50-session  --top-k 50  --granularity session

echo "=== SWEEP3 COMPLETE ==="
