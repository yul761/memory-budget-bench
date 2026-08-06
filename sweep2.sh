#!/usr/bin/env bash
# Follow-on variants. Waits for sweep.sh so the two never contend for the API
# rate limit or Postgres.
#
# /v1 caps retrieve limit at 100 (contracts/src/index.ts:121). At message
# granularity that is ~20% of a LongMemEval haystack -- a structural ceiling.
# At session granularity there are only ~50 sessions, so limit=100 returns the
# WHOLE haystack: full coverage, which measures the answerer's ceiling given
# perfect recall and tells us whether retrieval is really the bottleneck.
set -uo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python

while pgrep -f "[s]weep.sh" > /dev/null; do sleep 30; done
echo "=== sweep.sh finished, starting follow-on variants ==="

run () {
  local name="$1"; shift
  echo "=== $name : $* ==="
  $PY run_longmemeval.py --n 20 --seed 42 --run-name "$name" --no-occurred-at --resume "$@" \
    2>&1 | grep -vE '^\s*"' | tail -25
  echo
}

run sweep-E-k50-session   --top-k 50  --granularity session
run sweep-F-k100-session  --top-k 100 --granularity session

echo "=== SWEEP2 COMPLETE ==="
