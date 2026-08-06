#!/usr/bin/env bash
# Does less context answer better? The formal run retrieved 50 full sessions
# (~290k chars) and said "I don't know" on 30% of questions despite recall=1.00
# -- the evidence was present and the answerer missed it. If dilution is the
# cause, cutting top_k should RAISE accuracy, which is the opposite of the usual
# retrieval intuition.
#
# Same 34 knowledge-update questions, same seed, same everything but top_k.
set -uo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python
for k in 50 15; do
  echo "=== top_k=$k ==="
  $PY run_longmemeval.py --n 34 --seed 42 --question-type knowledge-update \
    --run-name "ab-topk-$k" --top-k "$k" --granularity session --no-occurred-at --resume \
    2>&1 | grep -vE '^\s+"' | tail -6
done
echo "=== AB TOPK COMPLETE ==="
