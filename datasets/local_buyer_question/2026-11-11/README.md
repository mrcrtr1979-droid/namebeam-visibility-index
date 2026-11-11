# Local buyer question, 28 US markets, 2026-11-11

One fixed local buyer question per market, sent 5 times per market to each of three configurations, through the vendors' APIs, on 2026-11-11. 3 sets x 28 markets x 5 runs = 420 runs, run_status OK on all 420.

## What was asked
One question per market, in the form "Who is the best dentist in San Mateo CA for a dental implant?" (the exact text is in the question column of runs.csv). The question text is the same in all three sets for each market. No system prompt or length instruction is recorded in the raw rows.

## When, and by which model
| set | model_id (as in the raw rows) | search | UTC window | CT window |
|---|---|---|---|---|
| claude_search_off | us.anthropic.claude-haiku-4-5-20251001-v1:0 | no search tool | 2026-11-11T05:22:43Z to 05:26:40Z | 2026-11-10 11:22:43PM to 11:26:40PM CT |
| gemini_search_off | gemini-3.5-flash-lite | search_grounding False | 2026-11-11T06:00:19Z to 06:06:44Z | 2026-11-11 12:00:19AM to 12:06:44AM CT |
| gemini_search_on | gemini-3.5-flash-lite | search_grounding True (Google Search tool on Vertex AI) | 2026-11-11T10:10:22Z to 10:14:29Z | 2026-11-11 4:10:22AM to 4:14:29AM CT |

The search-on set was captured about 4 hours after the search-off sets.

## Markets and trades
28 markets (market_id is state, city, trade; list in summary_by_market.csv), 12 trades: auto body repair, criminal defense attorneys, dental implant practices, dentists, estate planning attorneys, hvac, insurance agencies, orthodontists, pediatric dentists, personal injury attorneys, roofing, towing services. Five runs per market per set, 140 runs per set.

## Files
- runs.csv: one row per run, 420 rows. Columns: set, market_id, state, city, trade, model_id, search, run, utc_timestamp, question, answer_verbatim, named_business, names. answer_verbatim is the answer text as returned. names is the business names the extractor found, joined with " ; " (empty when none).
- summary_by_trade.csv, summary_by_market.csv: runs and runs naming a business per set.
- verify.py: recounts the headline figures from runs.csv; exits non-zero on a mismatch.

Not included: the search queries and grounding source lists recorded for the search-on runs, token counts, request ids and latencies. Answer text can contain characters outside ASCII; runs.csv is UTF-8.

## Rule for named_business
named_business is 1 when the extractor (tools/market_sample/extractor.py in the Namebeam Brain, called through strict_run_sets in market_sample.py) finds at least one specific business in answer_verbatim. Credentials, associations, places and advice do not count. Names come from answer_verbatim, not from the collector's own name list.

Check against a hand-labelled truth set of 645 answers (618 Gemini and 27 Claude, search-off answers only):
- Gemini: precision 1.000, recall 0.981 (606 true positive, 0 false positive, 12 missed names).
- Claude: precision 1.000, recall 1.000 (27 of 27).
- At run level the truth set gives the same 8 and 134 as the extractor.
Limits: the extractor can miss names (12 of 618 Gemini names missed in the truth set). It has not been scored on search-on answers. The extractor lists a joined heading such as "A & B" as one name.

## Figures
- claude_search_off: 8 of 140 runs name a business.
- gemini_search_off: 134 of 140 runs name a business. Runs without one: KY-union-pediatric-dentists 4 of 5, CA-san-mateo-dentists 1 of 5, NJ-red-bank-estate-planning-attorneys 1 of 5.
- gemini_search_on: 140 of 140 runs name a business.

## Caveats
One date. Five runs per market. API answers; consumer apps can differ. The extractor can miss names and has not been scored on search-on answers. The three sets were captured at different times (see table). This file records what was returned; it gives no reason why a model answered as it did and ranks no business.

## Recount
```python
import csv
rows = list(csv.DictReader(open("runs.csv", encoding="utf-8", newline="")))
print(len(rows))
for s in ("claude_search_off", "gemini_search_off", "gemini_search_on"):
    print(s, sum(1 for r in rows if r["set"] == s and r["named_business"] == "1"))
```
Expected: 420, then 8, 134, 140. `python3 verify.py` does the same and exits non-zero on a mismatch.

## Licence and citation
As the repository README states (see NOTICE.md, "Licence scope"): CC BY 4.0 covers our compilation, derived columns (named_business, names) and documentation. The raw answer text returned by each AI service is published as a dated factual record, is not licensed by us under CC BY 4.0, and each provider's own terms may apply. Cite as in CITATION.cff: Namebeam AI Visibility Record (Carter Enterprise LLC), CC BY 4.0.

Namebeam, a Carter Enterprise property.
