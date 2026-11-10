# SHADOW REPORT

Date: 2026-11-10
Method: Parallel GitHub Actions matrix workflow, dry-run mode (E1_DRY_RUN=1)

CRITICAL: Rows under shadow/ are never mixed into the public series. This is a 
test run only. The method note date is the date of this shadow run, not the date 
these rows would have been collected in production.

## Completeness

Engine | OK | Failed | Quota Blocked | Expected | Completeness %
-------|-------|--------|---------------|----------|---------------
perplexity | 0 | 0 | 0 | 93 | 0.0
gemini | 72 | 1 | 0 | 73 | 98.6
openai | 0 | 0 | 0 | 73 | 0.0
anthropic | 73 | 0 | 0 | 73 | 100.0
serp | 0 | 0 | 0 | 73 | 0.0

## Served Models

### gemini
- gemini-3.5-flash-lite: 53 rows
- gemini-3.6-flash: 19 rows

### anthropic
- anthropic.claude-haiku-4-5-20251001-v1:0: 73 rows

## Cost Per Answer (list-price estimate)

Engine | Per Answer ($) | Total ($) | OK Count
-------|----------------|-----------|----------
perplexity | N/A | N/A | 0
gemini | 0.0004 | 0.0308 | 72
openai | N/A | N/A | 0
anthropic | 0.0013 | 0.0934 | 73

Note: Costs are list-price estimates. Perplexity costs from usage.cost.total_cost; 
others computed from tokens times rates in prices.json. Gemini output tokens include 
thoughtsTokenCount when present.

## Wall Time

Shard | Wall Minutes
------|-------------
perplexity | 0
gemini | 20.2
openai | 0
anthropic | 3.0
serp | 0

Parallel run wall time (max): 20.2 min
Serial estimate (sum): 23.2 min
Speedup: 1.1x

## Agreement Rows

Buildable (2+ engines with answer text): 72
Not buildable (< 2 engines or missing text): 1

Agreement rows are built in memory only and are never written to the corpus.

## Five Comparison Fields

The packet requested these five fields:
1. Completeness: see table above
2. Served model per engine: see distribution above
3. Failures: see completeness table (Failed and Quota Blocked columns)
4. Wall time: parallel max vs serial sum shown above
5. Cost per answer by engine: see cost table above
