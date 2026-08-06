#!/usr/bin/env bash
# Create the benchmark droplet, ship code to it, and provision it.
# Run LOCALLY. Requires: doctl authenticated, ~/statecore110 SSH key.
set -euo pipefail

NAME=${NAME:-statecore-bench}
SIZE=${SIZE:-s-4vcpu-8gb}
REGION=${REGION:-sfo3}
IMAGE=${IMAGE:-ubuntu-24-04-x64}
SSH_KEY_ID=${SSH_KEY_ID:-57309208}   # statecore110
KEY=${KEY:-$HOME/statecore110}

HERE="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

if doctl compute droplet list --format Name --no-header | grep -qx "$NAME"; then
  echo "droplet $NAME already exists"
else
  echo "=== creating $NAME ($SIZE, $REGION) ==="
  doctl compute droplet create "$NAME" \
    --size "$SIZE" --region "$REGION" --image "$IMAGE" \
    --ssh-keys "$SSH_KEY_ID" --wait --format ID,Name,PublicIPv4
fi

IP=$(doctl compute droplet list --format Name,PublicIPv4 --no-header \
     | awk -v n="$NAME" '$1==n{print $2}')
echo "IP: $IP"

echo "=== waiting for ssh ==="
for i in $(seq 1 60); do
  ssh -i "$KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
      root@"$IP" true 2>/dev/null && break
  sleep 5
done

SSH="ssh -i $KEY -o StrictHostKeyChecking=no root@$IP"
$SSH "mkdir -p /root/bench"

echo "=== shipping StateCore (source only) ==="
rsync -az --delete -e "ssh -i $KEY -o StrictHostKeyChecking=no" \
  --exclude node_modules --exclude .git --exclude dist --exclude .turbo \
  --exclude benchmark-results \
  "$ROOT/StateCore/" root@"$IP":/root/bench/StateCore/

echo "=== shipping runner (no dataset, no venv) ==="
rsync -az --delete -e "ssh -i $KEY -o StrictHostKeyChecking=no" \
  --exclude data --exclude .venv --exclude runs \
  --exclude mem0-harness --exclude official-locomo \
  "$HERE/" root@"$IP":/root/bench/memory-benchmarks/

echo "=== provisioning ==="
$SSH "bash /root/bench/memory-benchmarks/provision/setup-droplet.sh"

cat <<EOF

=== droplet ready ===
  ssh -i $KEY root@$IP
  destroy: doctl compute droplet delete $NAME --force
EOF
