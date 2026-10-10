# Namebeam AI Visibility Record, Edition 1 data pack

Status: {{STATUS}}. Window: {{W0}} to {{W1}} (UTC dates). Run days in the window: {{RUN_DAYS}}. Answer rows: {{ANSWERS_ROWS}}.

Counts: {{COUNTS}}

This pack holds the raw tables behind Edition 1, the metrics computed from them, the code that computes the metrics, and a script that checks every file against a SHA-256 manifest. The raw answer files they come from are in `corpus/e1/` of this repository.

## Files

| file | what it is |
|---|---|
| `{{ANSWERS_FILE}}` | One row per raw answer file: date, kind (API, SERP or AGREE), the business or segment asked about, niche, market, engine, the names the engine returned, and the SHA-256 of the raw file. Three derived columns follow: `rerun` (0, or N for a raw file named `_rN`), `headings_removed_count` and `businesses_named_no_headings` (API and AGREE rows; blank for SERP). The raw `businesses_named` column is never changed. |
| `{{SOURCES_FILE}}` | One row per URL an engine returned as a source (Perplexity sources and Google results). Columns are described in `datasets/e1/sources/README.md`. |
| `data/prompts.csv` | Every question asked in this window, word for word: its id (the slug used in the raw file names), the business or segment it belongs to, whether it is a market or category question or a question asked to see whether one named business comes up, market, niche, the SHA-256 of the text, the date it entered the run, the last date in this window, the number of run days it was asked, and which engines were asked. |
| `LICENSE` | Creative Commons Attribution 4.0 International (CC BY 4.0), the full legal code. |
| `data/siri_panel.csv` | Questions asked of Siri by hand on an iPhone: date, mode (typed or spoken), city, question, what Siri said, businesses named, sources shown. Described in `datasets/e1/siri/README.md`. |
| `data/customer_zero_*.csv` | How often our own domains appear in the sources an engine returned. Described in `datasets/e1/customer_zero/README.md`. |
| `data/segment_pages_*.csv` | For each of the 28 record pages (27 city and segment pages and the hub): how many Perplexity answers cited it, out of the answers to its own question and out of all answers with a source list, per run day and in 7 day windows. Zeros are kept. Described in `datasets/e1/customer_zero/README.md`. |
| `metrics/metrics_v2.csv` | Every metric, by window and engine: value, numerator, denominator, the unit of the denominator, and the method id. |
| `metrics/metrics_v2_methods.csv` | The definition of each method id. |
| `metrics/metrics_v2_sentences.md` | One plain sentence per metric and window, each with its denominator. |
| `metrics/metrics_v2_inputs.csv` | Size and SHA-256 of the code and data the metrics read. |
| `metrics/metrics_v2_regress.txt` | Check that this code reproduces the values published earlier (v1) and the earlier raw overlap numbers. |
| `method/` | The code that computes the metrics, in the folder layout it expects. Copied unchanged except one default file path in `gen_citation_index.py`; `PACK.json` lists the file hash before and after. |
| `PACK.json`, `MANIFEST.sha256`, `verify.py` | Pack description, file hashes, and the check. |

## How the questions were asked

Each day a program asks every engine the same fixed questions, one per business or segment in `roster/e1_roster.json`, and writes one raw file per answer. Google results are read through Bright Data. Settings, as set in `scripts/engines.py`:

| engine | how it was called | search tool | temperature |
|---|---|---|---|
| OpenAI | Chat Completions API | not switched on | provider default |
| Anthropic | Messages API, max 2,048 tokens | not switched on | 0.2 |
| Gemini | Gemini API, generateContent, model gemini-3.6-flash | not switched on | provider default |
| Perplexity, to 2026-09-27 | Sonar chat completions | web search on | not stated in the current code |
| Perplexity, from 2026-09-28 | Agent API, model perplexity/sonar | web search forced | 0.2 |
| Google | results page through Bright Data | not applicable | not applicable |

Each question is asked once a day in the same words, with no rotation. A question asked again on the same day is a rerun, kept and marked (see below). A call that fails stays in the record as a failed row: its raw file carries a status other than OK, and its row in the answers table has no names. The metrics that count answered days leave a failed API call out. The naming rate on asked days, and the persistence form in which the next day only needs to have been asked, count a failed call as a day with no name, so those two figures can fall when a call fails.

These are API calls, not the apps people open on a phone. The rows do not record which model answered. The model strings are set in the code or in repository variables: OpenAI read `gpt-5-mini` on 2026-08-06, and Anthropic uses `claude-haiku-4-5` unless a variable overrides it. Because Perplexity is the one engine with a search tool switched on, the engines are not like for like, and no figure here compares them as if they were.

## Windows

- WA: 2026-09-01 to 2026-09-27, Perplexity on Sonar chat completions.
- WB: 2026-09-28 to {{W1}}, Perplexity on the Agent API. Perplexity retired Sonar chat completions on 2026-09-27 and the collector moved to the Agent API, so WA and WB are reported apart. Any change between them mixes the method change with change over time.
- WE: the whole Edition 1 window. For Perplexity it mixes both methods and is marked that way in the table.

Days with no run are absent from every table, not counted as zero. Days with no run in this window: {{GAPS}}.

2026-10-05 has no raw file for any question or engine. It is a gap, not a day of zero answers, and it is absent from every table.

The roster grew during the window, so the number of raw files per day changes on 2026-10-08, from 282 to 338. Six dentist and orthodontist segments (Atlanta, Dallas, Phoenix), added to `roster/e1_roster.json` in commit e81cbdeb (2026-10-07 UTC), are asked of the four API engines, Google and the agreement check: 24 API, 6 SERP and 6 AGREE files a day. Twenty real estate research segments (real estate agents, property management, mortgage lenders and title companies in Fort Lauderdale, Miami, St. Charles, St. Louis and West Palm Beach), added in commit 980286d1 (2026-10-08), are asked of Perplexity alone, with no Google rows: 20 API files a day. That is 56 more files and 50 more question ids a day. No earlier question was dropped or changed. These rows are kept in the answers table; under the panel rule below their 26 cells are left out of the metrics.

## Metrics

Every metric names its method and its denominator in `metrics/metrics_v2.csv`. The main ones:

- Persistence: of the business names returned on one day, the share also returned the next day, pooled over cells. Two forms are given: the next day must have named someone, or the next day needs just to have been asked. Each is computed on raw names and on gated names (below).
- List overlap: the overlap (Jaccard) of the full name lists on two consecutive days when both days named someone, as a mean and a median, raw and gated.
- Churn: the mean of one minus the list overlap over consecutive answered days where either day has a gated name. A pair with one empty day counts as full churn.
- Concentration: the mean over cells of H, the sum of squared shares of named days, and of 1/H, the number of equally common names the cell behaves like.
- Name rate: the share of answered days on which at least one name survived the gate.
- Naming rate on asked days: the share of asked days on which the engine returned at least one business name.
- Coin-flip share: the share of businesses named on 20 to 80 percent of answered days, so one day's answer is close to a coin flip.
- Engine agreement: the mean overlap of two engines' name lists on the same day, raw and gated.

A cell is a market plus a niche. US nationwide questions are left out of the cell metrics. September has 38 cells. API rows count; SERP and AGREE rows do not. The metrics read just the cells that were asked on or before 2026-09-27 (the 38 cells of September), in every window. Research segments added to the roster after that date are in the answers table and are left out of the metrics, so a change between WA and WB is not also a change of panel. {{PANEL}} A list of SERP files from 2026-10-04 that were held out is in `method/`; it removes no API rows.

## The name gate

Engines return strings, and some are not businesses: places, directories, trade words, sentence fragments, and several spellings of one name. The gate is rule-based code with word lists. It drops places, directories and platforms, generic trade words and sentence fragments, and merges variants of one name. It is in `method/Brain_Kit/status/staged/1005A_AI_PICKS_CHART/gen_ai_picks_chart.py` (SHA-256 611894c48c21a512bc856463c2e4b6b7a33155375aebcce71a036e820a4edde6). Every persistence and overlap metric is given twice, raw and gated, and the two can differ a lot: for Perplexity in WA, persistence is 0.626 gated and 0.437 raw.

## Name lists in the answers table

The engines often put section headings inside the lists that the collector reads as business names, for example "Overview", "Research methods" or "Red flags to avoid". The raw files and the raw `businesses_named` column keep them as written. The derived column `businesses_named_no_headings` drops a name when every word of it is in the heading word list of the name gate code (`BASE_GENERIC` and `EXTRA_GENERIC` in `gen_citation_index.py`), when it matches a fixed, hand-reviewed list of headings found in the rows to 2026-10-09 (`HEADING_TERMS` in `scripts/build_edition.py`), or when it holds a line break. A business name that the word rule would catch is kept by name. {{HEADINGS}} The metrics do not read this column; their own name gate is described above.

Rerun files: a question asked again on the same day is saved as a new raw file ending `_r2`, `_r3` and so on, and every one is kept. Rerun files in this window: {{RERUNS}}.

Google rows: {{EXTRACTION_FAILED}}

## Limits

- The record covers local business questions in the markets in the roster. It does not describe all businesses or all questions.
- Names are strings returned by the engines and read by a program. A name returned is not a recommendation by us and not a claim that the business is good.
- OpenAI, Anthropic and Gemini ran without a search tool, so their answers come from the model alone.
- OpenAI lists the model snapshot `gpt-5-mini-2025-08-07` as deprecated. OpenAI notified developers on 2026-06-11 and removes it from the API on 2026-12-11, with `gpt-5.6-terra` as its recommended replacement (OpenAI deprecations page, read 2026-10-08). The collector still reads `gpt-5-mini`. When the model is changed, the change will be dated in this note before the earliest changed row, so a shift in the OpenAI rows is not read as a shift in the businesses named.
- Gemini returned some answers until 2026-09-26 and none from 2026-09-27 (the account returned HTTP 402, prepayment credits depleted). Gemini metrics carry their own denominators and are small.
- WB is a short window. Read its values with their denominators.
- Siri rows are typed or spoken by hand on one phone, for a few questions on a few days. They are not a sample of Siri users.
- Nothing here shows why a list changed. The record shows what was returned on which day.

## How this record differs from another published record

Another open record of a similar kind of measurement is the Zenodo deposit "AI Recommendation Calibration Study 2026" (record 21611003, published 2026-07-26). Its record page, read 2026-10-09, describes 4,800 model responses collected between May and July 2026 from four engines called through their APIs, and states: "All published files are aggregates. No business names, competitor names, cited sources, verbatim AI response text ... are included."

This pack is built differently on three points, each of which you can check in the files:

- Unit of publication. This pack has one row per raw answer file, with the names the engine returned and the SHA-256 of the raw file. The raw answer files, including the verbatim answer text, are in the public repository under `corpus/e1`.
- Sources. `{{SOURCES_FILE}}` lists every URL an engine returned as a source for the engines that return a source list.
- Dates and place. This pack covers US markets from {{W0}} to {{W1}}, with a daily collection run ({{RUN_DAYS}} run days); the other record covers the UK between May and July 2026.

The two records use different engines, countries, dates and question sets. Their numbers are not comparable and neither replicates the other. Other records of this kind may exist that were not read for this note.

## License and where to get it

The data and text in this pack are licensed under CC BY 4.0 (`LICENSE`): you may copy, share and adapt them for any purpose, including commercial use, if you give credit. Credit line: Namebeam AI Visibility Record, Edition 1 (Carter Enterprise LLC), CC BY 4.0. Use without credit, for example white-label use inside client reports, needs a separate commercial license from Carter Enterprise LLC; ask at namebeam.ai.

The canonical copy of this pack on Hugging Face is the dataset `Namebeam/ai-visibility-daily-record` (https://huggingface.co/datasets/Namebeam/ai-visibility-daily-record), folder `edition-1/`. The dataset `Namebeam/namebeam-e1-ai-referral-index` carries the daily LATEST files of the same record. The raw answer files are in this repository under `corpus/e1/`.

## Check the pack

    python3 verify.py              # every file against MANIFEST.sha256, prints MATCH or MISMATCH
    python3 verify.py --rebuild    # rebuilds the answers table from corpus/e1 and compares
    python3 verify.py --metrics    # recomputes the metrics table with the code in method/ and compares

Run these from `releases/edition-1/` in a clone of this repository. The last line reads `VERIFY MATCH` when everything agrees.

Operated by Carter Enterprise LLC. Questions about the record: namebeam.ai.
