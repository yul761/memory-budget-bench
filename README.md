# memory-budget-bench

Tooling for comparing [StateCore](https://github.com/yul761/StateCore) against
other long-term memory systems on LongMemEval, at an equal context budget.

This exists as its own repository because a benchmark result is only as
trustworthy as the harness that produced it. If the numbers are published, the
thing that produced them has to be readable too.

## Who maintains this

StateCore does. One of the systems measured here is ours, which is a reason to
read our numbers sceptically, and we would rather say so than have you find out.

What follows from it, concretely:

- **Results that go against us stay in the repository.** `withdrawn/` holds a
  comparison we ran, published, and then retracted after finding the harness had
  handed StateCore the entire corpus. It is kept with a notice explaining exactly
  what was wrong with it, because a benchmark maintained by an interested party is
  worth nothing if the failures quietly disappear.
- **The harness is the argument.** Every guarantee in the list below exists
  because we got it wrong first. If you disagree with how something is measured,
  the code is here and the disagreement is about something specific.
- **Every run leaves its evidence on disk.** `runs/<name>/retrievals/<arm>/<qid>.json`
  is exactly what each system handed the answerer. You do not have to take a
  reported score on trust; you can read what produced it.
- **The controls are not decoration.** The `recency` and `full` arms exist to make
  it possible for StateCore to look unnecessary. A comparison that cannot produce
  that outcome is not measuring anything.

If you find a way this harness favours StateCore, open an issue. That is a more
useful contribution than a competing benchmark.

## Why "at an equal context budget"

An earlier run of this comparison was withdrawn. It gave both systems `top-k 50`,
which sounds fair and is not: a StateCore session event ran ~9.8k characters and
a mem0 fact ~145, so equal item counts handed one side roughly 240x the context
of the other. Worse, the haystack held about 50 sessions, so `top-k 50` selected
*everything* — the prompt came to 1.44x the entire corpus, and the score measured
how well the answerer reads a transcript rather than how well the memory system
retrieves.

This harness gives every arm the same number of **answerer tokens** and lets each
fill them with whatever it has. That is a question a memory system can actually
lose: given one session's worth of room, did you pick the right session?

## Arms

| arm | what it is |
|---|---|
| `statecore` | StateCore's digest, fact registry and retrieved events |
| `mem0` | mem0 OSS's extracted facts |
| `recency` | No memory system: the most recent raw sessions, to the same budget |
| `full` | No memory system: the entire corpus, no budget — the ceiling |

The two control arms are the point. Without `recency`, a score cannot separate
"the memory layer works" from "the answerer read the transcript". Without `full`,
there is no way to say how much headroom is left.

## Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Phase 1 — build each arm's memory and capture what it would retrieve.
.venv/bin/python run_fair.py retrieve --arm statecore --n 200 --run-name fair
.venv/bin/python run_fair.py retrieve --arm mem0      --n 200 --run-name fair
.venv/bin/python run_fair.py retrieve --arm recency   --n 200 --run-name fair
.venv/bin/python run_fair.py retrieve --arm full      --n 200 --run-name fair

# Phase 2 — answer at each budget. Retrieval is not repeated.
for arm in statecore mem0 recency; do
  for b in 4000 16000 64000; do
    .venv/bin/python run_fair.py answer --arm $arm --budget $b --n 200 --run-name fair
  done
done
.venv/bin/python run_fair.py answer --arm full --n 200 --run-name fair   # ceiling

# Phase 3 — score and report.
.venv/bin/python score.py --run fair/answers/statecore-b16000 --judge gpt-4o
.venv/bin/python fair_report.py --run fair --budgets 4000,16000,64000
```

Splitting retrieval from answering means sweeping budgets costs almost no wall
clock, and — more usefully — `runs/<name>/retrievals/<arm>/<qid>.json` holds
exactly what each system handed the answerer, for anyone who wants to check the
result rather than take it.

## What the harness guarantees

- **Nothing is truncated mid-item.** Items go in whole, in rank order, and filling
  stops at the first one that does not fit. An earlier version capped each
  retrieved item at 2000 characters, which cut every session to a fifth of itself
  while retrieval recall still read 1.00 — the failure looked like a memory
  problem and was a harness one.
- **The budget is never exceeded**, asserted per question rather than assumed.
- **A question is scored only if every arm ingested its corpus completely.** An
  earlier run silently lost 4.8% of one system's corpus to swallowed exceptions.
  Ingest now retries, records failures with their cause, and a question any arm
  could not fully hold is dropped for all arms and listed in the report.
- **Cost is the token usage the API returned**, not an estimate.
- **The report refuses to be written without a StateCore commit.** A score that
  cannot be traced to a build is not reproducible.

`test_budget.py` guards the filling rules. Run `.venv/bin/python -m pytest -q`.

## Fetching the dataset

LongMemEval is third-party data under its own licence and is not vendored here.
Download `longmemeval_s.json` from
[xiaowu0162/LongMemEval](https://github.com/xiaowu0162/LongMemEval) and place it
at `data/longmemeval_s.json`.

## Upstream repositories

Cloned for reference and comparison rather than vendored, so their history and
licences stay theirs. Revisions used:

| repo | revision |
|---|---|
| [mem0ai/memory-benchmarks](https://github.com/mem0ai/memory-benchmarks) | `4b61c5d` |
| [xiaowu0162/LongMemEval](https://github.com/xiaowu0162/LongMemEval) | `9e0b455` |
| [snap-research/locomo](https://github.com/snap-research/locomo) | `3eb6f2c` |

### How mem0 is configured, and what was changed

**mem0 is measured as released: `mem0ai==2.0.17`, unmodified.**

Its own benchmark harness pins `mem0ai @ git+…@feat/v3-pipeline`, an unreleased
feature branch that no longer exists upstream. Substituting its successor
(`feat/oss-add-v3-ingestion-caps`, which resolves to 2.0.6) would mean publishing
a number for a build nobody runs — and that branch adds an extraction cap the
released version does not have. The pin is therefore moved to the current
release.

Two changes to mem0's **harness** (not to mem0 itself) were needed, both because
that harness was written against an API revision that has since moved:

- `search()` is called with `limit=…`, but the parameter is named `top_k`;
  `limit` is swallowed by `**kwargs`, so every search silently returned the
  default 20 however many were requested. This is not cosmetic — it is the entire
  reason an earlier comparison gave mem0 20 items against StateCore's 50.
- `search()` rejects a top-level `user_id` and requires `filters={…}`.

Neither touches extraction, storage or ranking.

**What was deliberately not fixed:** mem0 extracts nothing from some
conversational sessions and then embeds an empty string, which the OpenAI API
rejects, so a few percent of sessions fail to ingest. It is deterministic, not a
transient error, and it is mem0's behaviour as shipped. Patching it would produce
a number for something nobody runs. The loss is measured and reported per arm
instead.

## Reading a result

A LongMemEval score is a property of *system + configuration*, not of a system.
The same StateCore build measured on the same machine has scored 30 points apart
across retrieval configurations alone. Quote the configuration with the number,
or the number will not reproduce.

A difference is only a result where the confidence interval excludes zero.
Overlapping intervals mean the run did not distinguish the systems, whatever the
point estimates look like.

## History

`run_longmemeval.py` and `final_report.py` produced the withdrawn 2026-08-05
comparison. They are kept unchanged so it stays possible to see what was actually
run; `run_fair.py` and `fair_report.py` supersede them.
