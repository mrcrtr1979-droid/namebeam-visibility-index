# E1 own-citation counter

How often an engine cited a page on one of our own properties as a source. It is computed from `datasets/e1/sources/` (every hit is a row there) and from the raw answer files in `corpus/e1/` (the denominators).

## Files

| file | contents |
|---|---|
| customer_zero_daily.csv | one row per date, engine and field, including days with zero hits |
| customer_zero_hits.csv | every hit with the cited URL, the question, the market and the rank in the engine's list |
| customer_zero_rolling.csv | rolling 7 and 30 calendar day windows for each as_of date, with days_with_data |

## Columns of customer_zero_daily.csv

| column | meaning |
|---|---|
| engine_answers | all answers by that engine that day |
| field_rows | answers that carry the source field. This is the denominator |
| rows_with_urls | of those, answers with at least one URL |
| answers_citing_ours | distinct answers with at least one URL on our properties |
| our_url_rows | URL rows on our properties |
| our_distinct_urls | distinct URLs among them |
| groups_hit | which of our properties, with counts |

Do not add the rows of two fields for one engine. A Google results page can cite a page in its organic results and in its AI Overview.

## What counts as ours

A host that is, or is a subdomain of: namebeam.ai, thereceiptsindex.com, receiptsindex.com, trunkline.money, carterenterprise.llc. A Hugging Face URL under the Namebeam account. A Datarade URL with namebeam in its path. Look-alike hosts such as notnamebeam.ai do not count.

## Counts

As of 2026-10-07, record from 2026-08-02 (no run exists for 2026-10-05):

- Perplexity: 36 of 2,735 answers cited a page of ours (2,579 of the 2,735 returned sources). 35 cited namebeam.ai/kansas-city-health-insurance, 1 cited trunkline.money.
- Google organic results: 0 of 2,727 results pages (1,648 returned URLs).
- Google AI Overview references: 0 of 188 pages that carry the field (113 returned URLs). The field exists from 2026-10-03.
- OpenAI API (no web search), Anthropic API, Gemini API: these return no source list, so there is nothing to count.

The two Kansas City health insurance questions were asked of Perplexity on 64 days. Of 129 answers, 123 returned sources and 35 cited the page. The citations come in runs: 2026-08-24 to 2026-09-01, 2026-09-20 to 2026-09-24, and on 8 of the 9 run days from 2026-09-28 to 2026-10-07. Perplexity returned errors on 2026-09-02 to 2026-09-04.

## Rebuild and check

    python3 -I scripts/customer_zero.py                  # rebuild the three files
    python3 -I scripts/customer_zero.py --check-baseline # reproduce the reference counts
    python3 -I scripts/customer_zero.py --selftest       # fixture test, including checks that must fail

The build exits with an error if a day's sources file has a different row count from the raw files.

Operated by Carter Enterprise LLC. Questions about the record: namebeam.ai.

## Segment-page citation watch (added 2026-10-09, loop S1b-1)

`scripts/segment_citations.py` runs in the same workflow step as `customer_zero.py` and writes beside it:

- `segment_pages_daily.csv`: one row per run day and watched page (27 city and segment pages and the hub, list in `roster/segment_pages.json`), zeros kept. `perplexity_answers_with_sources` is the denominator (distinct Perplexity answers that returned a source list that day); `answers_citing_page` counts answers whose source list contains the page URL; `url_rows` counts URL rows; `best_rank` is the best position in a source list; `answers_citing_data_json` counts citations of `/data/e1/<slug>.json`, kept apart from the page count. `day_status` reads `NO-PERPLEXITY-SOURCES` when Perplexity returned no source rows that day (the zero is then "no data").
- `segment_pages_rolling.csv`: for each watch-window date (from 2026-10-09), the last 7 calendar days and everything since the watch started, with `days_with_data` so a missing run day is visible.
- `segment_pages_unlisted_hits.csv`: cited namebeam.ai URLs that are not on the roster. Empty means the roster missed nothing.
- `segment_pages_probe.csv`: one row per day and page from a live GET inside GitHub Actions (HTTP status, bytes, a 16-character body hash). It is appended by date and is the only file here that cannot be rebuilt from the sources files.

Match rule: host is namebeam.ai or a subdomain of it, path lower-cased with a trailing slash and `.html` removed equals `/<slug>`. Look-alike hosts and other owners do not match. The denominator is cross-checked against `customer_zero_daily.csv`; a difference prints `DENOM-MISMATCH` and exits 2.

Verify: `python3 -I scripts/segment_citations.py --selftest` (SELFTEST PASS, includes a planted-error proof), `python3 -I scripts/segment_citations.py --readout` prints the current table.
