# E1 sources dataset

One CSV per UTC date, named `sources_<YYYY-MM-DD>.csv`. Each row is one URL that an engine returned as a source for one dated question, in the order the engine returned it. The rows are derived from the raw answer files in `corpus/e1/`, which stay unedited.

## Columns

| column | meaning |
|---|---|
| date | UTC date of the run |
| engine | perplexity or google_serp |
| run_channel | api for Perplexity, serp for Google results pages |
| market | city and state of the question |
| niche | business category of the question |
| prompt_text | the exact question that was asked |
| url | the URL as the engine returned it |
| domain | host of the URL in lower case, with a leading www removed |
| field | where the URL came from in the raw file |
| check_id | the raw answer file the row came from, without `.json` |
| rank | position of the URL in the engine's list, starting at 1 |

## Where the URLs come from

| field | engine | what it is |
|---|---|---|
| sources_cited | perplexity | sources Perplexity returned with the answer (web search on) |
| organic_top_results | google_serp | organic results on the Google results page |
| ai_overview_references | google_serp | links listed inside a Google AI Overview |

The other engines in the record (OpenAI API without web search, Anthropic API, Gemini API) return no source list, so they produce no rows here. URLs written inside the text of an answer are not rows in this dataset.

## Counts and denominators

As of 2026-10-07, from the raw files for 2026-09-27 to 2026-10-07 (10 run days, 470 answers per engine):

- Perplexity: 7,020 URLs across 468 of 470 answers.
- Google organic results: 3,835 URLs across 446 of 470 results pages.
- Google AI Overview references: 1,132 URLs across 113 of the 188 pages that carry the field. The field was first recorded on 2026-10-03.

The full record starts on 2026-08-02. No run exists for 2026-10-05.

## Rebuild and check

    python3 -I scripts/build_sources.py --all          # rebuild every day file
    python3 -I scripts/build_sources.py --census       # counts by engine and field, with denominators
    python3 -I scripts/build_sources.py --selftest     # fixture test, including checks that must fail

The build prints a warning if a raw file holds URLs in a field the builder does not know, or if a day with source-capable answers produces no rows.

Operated by Carter Enterprise LLC. Questions about the record: namebeam.ai.
