#!/usr/bin/env bash
# Bring up mem0 OSS (mem0 server + qdrant) alongside StateCore on the same host.
# Runs ON the droplet as root, after setup-droplet.sh.
#
# Fairness: mem0's own extraction model defaults to gpt-4o-mini while StateCore
# runs gpt-5-mini, so leaving the default would compare model strength as much
# as memory architecture. Both are pinned to the same models here, and the
# benchmark answerer/judge are shared by construction.
set -euo pipefail

BENCH_HOME=/root/bench
MEM0_DIR="$BENCH_HOME/mem0-harness"

: "${OPENAI_API_KEY:?OPENAI_API_KEY must be exported}"

cd "$MEM0_DIR"

cat > .env <<EOF
OPENAI_API_KEY=${OPENAI_API_KEY}
MEM0_PORT=8888
QDRANT_PORT=6333
LLM_MODEL=gpt-5-mini
EMBEDDER_MODEL=text-embedding-3-small
COLLECTION_NAME=memories
EOF

echo "=== starting mem0 oss + qdrant ==="
docker compose -p mem0 up -d
echo "waiting for mem0 health..."
for i in $(seq 1 60); do
  if curl -sf http://localhost:8888/health >/dev/null 2>&1; then
    echo "mem0 healthy after ${i}0s"
    break
  fi
  sleep 10
done

curl -sf http://localhost:8888/health || { echo "mem0 DID NOT COME UP"; docker compose -p mem0 logs --tail 40; exit 1; }

echo "=== python deps for mem0 harness ==="
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo
echo "=== mem0 READY on :8888 (qdrant :6333) ==="
echo "StateCore is on :3002 — they share the host but not the datastore."
