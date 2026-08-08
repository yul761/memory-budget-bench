# LongMemEval at an equal context budget

Every arm was given the same question set, the same answerer, the same judge, and the same number of tokens to put its memory into. What differs is what each chose to put there.

## Result

| system | 4000 tok | 16000 tok | 64000 tok |
|---|---|---|---|
| **StateCore** | 51.0% ±7.0 | 80.9% ±5.5 | 87.6% ±4.6 |
| **mem0 OSS** | 61.3% ±6.9 | 59.8% ±6.9 | 61.3% ±6.9 |
| **No memory (recency window)** | 9.3% ±4.1 | 22.7% ±5.9 | 53.6% ±7.0 |

**Ceiling** — the whole corpus in the prompt, no budget: **70.1% ±6.4** (136/194).

This is roughly the configuration the withdrawn 2026-08-05 run measured without meaning to. Read it as what the answerer can do unaided, not as a memory score.


## How much of the budget each arm could use

A low score with an underfilled budget means the system had nothing more to give; a low score with a full budget means it chose badly. They are different failures.

| system | budget | median used | median items dropped | runs that underfilled |
|---|---|---|---|---|
| StateCore | 4000 | 3955 | 46 | 0% |
| StateCore | 16000 | 15951 | 41 | 0% |
| StateCore | 64000 | 63940 | 22 | 0% |
| mem0 OSS | 4000 | 3988 | 15 | 44% |
| mem0 OSS | 16000 | 4451 | 0 | 100% |
| mem0 OSS | 64000 | 4451 | 0 | 100% |
| No memory (recency window) | 4000 | 3901 | 44 | 0% |
| No memory (recency window) | 16000 | 15895 | 39 | 0% |
| No memory (recency window) | 64000 | 63846 | 17 | 0% |
| No memory (whole corpus) | 1000000 | 102940 | 0 | 100% |

## By question type


**Budget 4000 tokens**

| question type | StateCore | mem0 OSS | No memory (recency window) |
|---|---|---|---|
| knowledge-update | 61.8% (21/34) | 67.6% (23/34) | 11.8% (4/34) |
| multi-session | 41.2% (14/34) | 85.3% (29/34) | 11.8% (4/34) |
| single-session-assistant | 96.8% (30/31) | 29.0% (9/31) | 9.7% (3/31) |
| single-session-preference | 24.1% (7/29) | 65.5% (19/29) | 3.4% (1/29) |
| single-session-user | 59.4% (19/32) | 87.5% (28/32) | 15.6% (5/32) |
| temporal-reasoning | 23.5% (8/34) | 32.4% (11/34) | 2.9% (1/34) |

**Budget 16000 tokens**

| question type | StateCore | mem0 OSS | No memory (recency window) |
|---|---|---|---|
| knowledge-update | 91.2% (31/34) | 70.6% (24/34) | 52.9% (18/34) |
| multi-session | 64.7% (22/34) | 82.4% (28/34) | 14.7% (5/34) |
| single-session-assistant | 100.0% (31/31) | 25.8% (8/31) | 19.4% (6/31) |
| single-session-preference | 69.0% (20/29) | 62.1% (18/29) | 10.3% (3/29) |
| single-session-user | 78.1% (25/32) | 87.5% (28/32) | 28.1% (9/32) |
| temporal-reasoning | 82.4% (28/34) | 29.4% (10/34) | 8.8% (3/34) |

**Budget 64000 tokens**

| question type | StateCore | mem0 OSS | No memory (recency window) |
|---|---|---|---|
| knowledge-update | 88.2% (30/34) | 73.5% (25/34) | 73.5% (25/34) |
| multi-session | 79.4% (27/34) | 82.4% (28/34) | 44.1% (15/34) |
| single-session-assistant | 100.0% (31/31) | 25.8% (8/31) | 74.2% (23/31) |
| single-session-preference | 75.9% (22/29) | 58.6% (17/29) | 41.4% (12/29) |
| single-session-user | 96.9% (31/32) | 87.5% (28/32) | 68.8% (22/32) |
| temporal-reasoning | 85.3% (29/34) | 38.2% (13/34) | 20.6% (7/34) |

## Cost

Summed from the token usage the API returned, not estimated.

| system | budget | prompt tokens | completion tokens |
|---|---|---|---|
| StateCore | 4000 | 765,934 | 87,252 |
| StateCore | 16000 | 3,094,298 | 113,425 |
| StateCore | 64000 | 12,402,551 | 100,188 |
| mem0 OSS | 4000 | 676,632 | 123,799 |
| mem0 OSS | 16000 | 886,086 | 122,891 |
| mem0 OSS | 64000 | 886,086 | 121,080 |
| No memory (recency window) | 4000 | 756,213 | 58,105 |
| No memory (recency window) | 16000 | 3,084,230 | 73,130 |
| No memory (recency window) | 64000 | 12,382,564 | 90,309 |
| No memory (whole corpus) | 1000000 | 19,946,910 | 92,146 |

## Configuration

| knob | value |
|---|---|
| dataset | `longmemeval_s.json` |
| questions scored | 194 |
| answerer | `gpt-5` |
| StateCore commit | `96b853d` |
| scope template | `personal` |
| budgets | 4000, 16000, 64000 |

## How much of each corpus survived ingest

A system that holds less of the corpus is answering a different, easier or harder question. Reported rather than hidden, and rather than discarding every question it affects.

| system | median corpus lost | worst question | questions over threshold |
|---|---|---|---|
| StateCore | 0.0% | 0.0% | 0 |
| mem0 OSS | 4.3% | 12.5% | 6 |

mem0 is measured as released (`mem0ai==2.0.17`), unmodified. It extracts nothing from some conversational sessions and then embeds an empty string, which its own API rejects — so a few percent of sessions do not make it in. That is its behaviour, not a harness failure, and patching it would mean publishing a number for something nobody runs.


## What was excluded, and why

Questions where an arm lost more than the allowed share of its corpus. Dropped for every arm, so all arms answer the same set:

- **mem0 OSS**: 6 question(s) — e.g. 0a34ad58, 1903aded, 2bf43736, 488d3006, 60d45044

## Reading these numbers

A LongMemEval score is a property of *system + configuration*, not of a system. The configuration is above; quote it with the number or the number will not reproduce.

The comparison is only meaningful where the interval excludes zero. Overlapping intervals mean the run did not distinguish the systems, whatever the point estimates say.

