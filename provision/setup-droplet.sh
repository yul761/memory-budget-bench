#!/usr/bin/env bash
# Bootstrap a throwaway DigitalOcean droplet to run the LongMemEval benchmark.
# Runs ON the droplet as root. Idempotent enough to re-run after a reboot.
set -euo pipefail

BENCH_HOME=/root/bench
export DEBIAN_FRONTEND=noninteractive

echo "=== [1/5] base packages ==="
# A fresh droplet runs cloud-init's unattended-upgrades for the first minutes and
# holds the dpkg lock; installing immediately fails outright.
for i in $(seq 1 60); do
  if ! fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 \
     && ! fuser /var/lib/apt/lists/lock >/dev/null 2>&1; then
    break
  fi
  echo "  waiting for apt lock ($i)…"
  sleep 10
done
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg python3 python3-venv python3-pip rsync jq

echo "=== [2/5] docker ==="
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
docker --version

echo "=== [3/5] StateCore stack ==="
cd "$BENCH_HOME/StateCore"
docker compose -p statecore -f docker-compose.local.yml build api worker migrate
docker compose -p statecore -f docker-compose.local.yml run --rm migrate
docker compose -p statecore -f docker-compose.local.yml up -d api worker
sleep 10
curl -sf http://localhost:3002/health | jq -r '.status, .model.model, .retrieve.useEmbeddings'

echo "=== [4/5] python runner ==="
cd "$BENCH_HOME/memory-benchmarks"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q openai backoff numpy tqdm

echo "=== [5/5] dataset ==="
mkdir -p data
if [ ! -s data/longmemeval_s.json ]; then
  curl -sL -o data/longmemeval_s.json \
    "https://huggingface.co/datasets/xiaowu0162/longmemeval/resolve/main/longmemeval_s"
fi
ls -la data/
python3 -c "import json;d=json.load(open('data/longmemeval_s.json'));print('dataset ok:',len(d),'questions')"

echo
echo "=== READY ==="
echo "smoke:  cd $BENCH_HOME/memory-benchmarks && .venv/bin/python run_longmemeval.py --n 5 --run-name smoke --resume"
echo "full :  nohup .venv/bin/python run_longmemeval.py --n 0 --run-name sc-lme500 --resume > runs/full.log 2>&1 &"
