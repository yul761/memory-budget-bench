cd /root/bench/memory-benchmarks
export OPENAI_API_KEY=$(grep "^MODEL_API_KEY=" ../StateCore/.env | cut -d= -f2-)
COMMON="--run-name fair --n 200 --statecore-commit 4357479 --require-arms statecore,mem0 --max-ingest-loss 0.10 --resume"
for b in 4000 16000 64000; do
  for arm in statecore mem0 recency; do
    echo "### answer arm=$arm budget=$b"
    .venv/bin/python run_fair.py answer --arm $arm --budget $b $COMMON
  done
done
echo "### answer arm=full budget=1000000 (ceiling)"
.venv/bin/python run_fair.py answer --arm full --budget 1000000 $COMMON
echo "### ALL ANSWER DONE"
