# Cost estimate (1012D-X4), computed 2026-10-13

## Prices
Claude Haiku 4.5: standard $1 per MTok input, $5 per MTok output. Message Batches API: $0.50 input, $2.50 output
(50 percent discount on both). Source: https://platform.claude.com/docs/en/about-claude/pricing (the docs.claude.com
address redirects there), fetched 2026-10-13.

## Candidate counts (local clone of main, corpus/e1, 17,958 files, answers 2026-08-02 to 2026-10-12)
| Set | Names |
|---|---|
| Raw unique (name, market, niche), recorded lists plus bolded answer-text names | 15,551 |
| of which deterministic pre-filter marks NOT_BUSINESS (no API cost) | 1,140 |
| Sent to Haiku | 14,411 |
| Text rule gate output (mention_counts.py candidates, OK answers, 74 pairs): name-pair rows | 2,237 |
| Text rule gate output: unique canonical names | 2,072 |

The raw set is the right labeling target because the gate output is already filtered; the labels also cover names the
gate drops. Cost for the gate set only is shown below.

## Token estimate per name
Input about 33 tokens (110 characters of item text plus system prompt spread over 40 names, at about 3.6 characters
per token; the dry-run JSONL is 1,585,207 characters of input). Output about 35 tokens (JSON object with index, label
and a reason of up to 12 words). These are estimates; the API reports exact usage per batch.

## Cost
| Run | Names | Input tokens | Output tokens | Batch cost |
|---|---|---|---|---|
| One-time backfill (raw set) | 14,411 | about 476,000 | about 504,000 | $0.24 + $1.26 = about $1.50 |
| Backfill with 50 percent safety margin | 14,411 | 714,000 | 756,000 | about $2.25 |
| Gate set only | 2,237 | 74,000 | 78,000 | about $0.23 |
| Weekly (about 1,000 new names) | 1,000 | 33,000 | 35,000 | about $0.10 |
| Monthly (4.3 weeks) | 4,300 | 142,000 | 151,000 | about $0.45 |

Weekly volume basis: new (name, market, niche) keys first seen per week over the last six weeks were 996, 723, 1,113,
1,361, 2,093 and 1,399 (newest first); the three most recent average about 940.

## Against the credits
Backfill about $1.50 of the $1,000 promotional credit (0.15 percent). The $200 monthly credit covers about 440 times
the monthly need. Cost is not the constraint; label quality is. Assumption to verify on first live run: token counts
(read usage from the batch results) and that Haiku follows the JSON-only format (unparsed items are retried next run).
