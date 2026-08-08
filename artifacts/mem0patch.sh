set -euo pipefail
cd /root/bench/mem0-harness
# 1. upstream branch feat/v3-pipeline was deleted; use its successor
sed -i 's|mem0ai @ git+https://github.com/mem0ai/mem0.git@feat/v3-pipeline|mem0ai @ git+https://github.com/mem0ai/mem0.git@feat/oss-add-v3-ingestion-caps|' docker/mem0/requirements.txt
# 2. current mem0ai wants filters={} rather than a top-level user_id in search()
python3 - <<'PY'
p='docker/mem0/main.py'; s=open(p).read()
old='''    params: dict[str, Any] = {"limit": req.limit}
    if req.user_id:
        params["user_id"] = req.user_id'''
new='''    params: dict[str, Any] = {"limit": req.limit}
    if req.user_id:
        params.setdefault("filters", {})["user_id"] = req.user_id'''
assert old in s, 'search param block not found'
open(p,'w').write(s.replace(old,new)); print('search patched')
PY
# 3. pin the same extraction model StateCore uses, via mem0's own config file
cat > mem0-config.yaml <<'YAML'
version: "v1.1"
llm:
  provider: openai
  config:
    model: gpt-4o-mini
    temperature: 0.1
embedder:
  provider: openai
  config:
    model: text-embedding-3-small
YAML
sed -i 's|# - ./mem0-config.yaml:/app/config.yaml:ro|- ./mem0-config.yaml:/app/config.yaml:ro|' docker-compose.yml
# 4. env
KEY=$(grep '^MODEL_API_KEY=' /root/bench/StateCore/.env | cut -d= -f2)
printf 'OPENAI_API_KEY=%s\nMEM0_PORT=8888\nQDRANT_PORT=6333\nLLM_MODEL=gpt-4o-mini\nEMBEDDER_MODEL=text-embedding-3-small\n' "$KEY" > .env
echo "=== building mem0 ==="
docker compose -p mem0 up -d --build
