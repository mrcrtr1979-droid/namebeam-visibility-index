---
pretty_name: Namebeam AI Visibility Daily Record
license: cc-by-4.0
language:
- en
tags:
- ai-visibility
- answer-engine-optimization
- generative-engine-optimization
- local-business
- tabular
size_categories:
- 10K<n<100K
configs:
- config_name: answers
  default: true
  data_files:
  - split: train
    path: edition-1/{{ANSWERS_PATH}}
- config_name: sources
  data_files:
  - split: train
    path: edition-1/{{SOURCES_PATH}}
- config_name: prompts
  data_files:
  - split: train
    path: edition-1/data/prompts.csv
- config_name: siri
  data_files:
  - split: train
    path: edition-1/data/siri_panel.csv
- config_name: metrics
  data_files:
  - split: train
    path: edition-1/metrics/metrics_v2.csv
---

# Namebeam AI Visibility Daily Record

A dated, public record of which businesses AI engines name when people ask for a recommendation. Each row in the answers table is one question on one date to one engine. Every claim owes the future a receipt.

{{STATUS_LINE}}

Counts: {{COUNTS}}

The record is kept by [Namebeam](https://namebeam.ai/), a Carter Enterprise LLC company.

## What is in this repository

Everything is under `edition-1/`. Edition 1 covers {{W0}} to {{W1}} (UTC dates): {{RUN_DAYS}} run days, {{ANSWERS_ROWS}} answer rows.

| config | file | what it is |
|---|---|---|
| answers | `edition-1/{{ANSWERS_PATH}}` | One row per raw answer file: date, kind, the business or segment asked about, niche, market, engine, the names the engine returned, the SHA-256 of the raw file. |
| sources | `edition-1/{{SOURCES_PATH}}` | One row per URL an engine returned as a source (Perplexity sources and Google results). |
| prompts | `edition-1/data/prompts.csv` | Every question asked, word for word, with its id, the date it entered the run and the engines asked. |
| siri | `edition-1/data/siri_panel.csv` | Questions asked of Siri by hand on an iPhone, typed and spoken: what Siri said, businesses named, sources shown. |
| metrics | `edition-1/metrics/metrics_v2.csv` | Every metric by window and engine with its numerator, denominator and method id. |

The method note is `edition-1/README.md`. The license is CC BY 4.0; the full text is `edition-1/LICENSE`. The code that computes the metrics is in `edition-1/method/`. `edition-1/MANIFEST.sha256` lists the hash of every file, and `edition-1/verify.py` checks them.

## How the data is collected

1. Each day the same fixed questions are asked of Perplexity, OpenAI, Anthropic and Gemini through their APIs, and the Google results page for the same question is read.
2. Every answer is saved as a raw file in the public GitHub repository, and the answers table names that file and carries its SHA-256.
3. The record is append only. A day on which an engine failed is recorded as failed, not as zero. Losses print at the same size as wins.

Perplexity runs with web search on (Sonar chat completions to 2026-09-27, the Agent API with a forced web search from 2026-09-28). OpenAI, Anthropic and Gemini run with no search tool, so they answer from the model alone. The engines are not like for like.

## The answers table

| field | type | description |
|---|---|---|
| date | string | Run date in UTC, YYYY-MM-DD. |
| kind | string | API (an engine answer), SERP (Google results page) or AGREE (a same-day comparison row). |
| target | string | The business or segment the question was asked for. |
| niche | string | Business category. Empty on some rows. |
| market | string | Geographic area. Empty on some rows. |
| engine | string | perplexity, openai, anthropic, gemini, google_serp or agree. |
| businesses_named_count | integer | Number of names extracted. |
| businesses_named | string | The extracted names, separated by semicolons. Raw extraction: some entries are phrases or places, not businesses. |
| first_named | string | The name at the top of the extracted list. |
| source_file | string | The raw file the row was built from. |
| sha256 | string | SHA-256 of that raw file. |
| rerun | integer | 0, or N when the raw file is a same-day rerun saved with the suffix _rN. |
| headings_removed_count | integer | API and AGREE rows: how many extracted entries the heading filter (v1, 2026-10-09) removed. Blank on SERP rows. |
| businesses_named_no_headings | string | API and AGREE rows: the extracted names with section headings removed ("Overview", "Research methods" and the like). The raw businesses_named column is unchanged. Blank on SERP rows. |

## Known limits

- These are API calls, not the apps people open on a phone. A person using an app may see something different.
- The model that answered is not recorded in the rows.
- Names are raw strings found by a program. The metrics give each figure twice, raw and after a rule-based name gate, with the denominator beside it.
- OpenAI deprecated the `gpt-5-mini` snapshot (notified 2026-06-11, removed from the API 2026-12-11, replacement `gpt-5.6-terra`; OpenAI deprecations page, read 2026-10-08). The collector still reads `gpt-5-mini`. A model change will be dated in the method note before the first changed row.
- Gemini returned some answers until 2026-09-26 and none from 2026-09-27 (HTTP 402, prepayment credits depleted). Its figures are small and carry their denominators.
- The metrics read the 38 market and niche cells asked on or before 2026-09-27. Research segments added after that date are in the answers table and not in the metrics.
- The window after 2026-09-27 is short, and the Perplexity method changed on that date. A change between the two windows mixes the method change with change over time.
- The Siri rows are typed or spoken by hand on one phone. They are not a sample of Siri users.
- A count is a count. It shows what was returned on the days an engine answered. It is not a rating or a ranking.

## Examples

Perplexity on Sonar, 2026-09-01 to 2026-09-27: 2,398 of 5,486 business names (43.7 percent) returned on one day were returned again the next day. Raw names, next day answered, 38 market and niche cells.

In the same window the overlap between two consecutive days' name lists had a median of 30 percent over 832 day pairs. Raw names, both days named at least one business.

## Load the data

```python
from datasets import load_dataset

ds = load_dataset("Namebeam/ai-visibility-daily-record", "answers", split="train")
print(ds[0])
```

## Check the files

    git clone https://github.com/mrcrtr1979-droid/namebeam-visibility-index
    python3 namebeam-visibility-index/releases/edition-1/verify.py --rebuild --metrics

The last line reads `VERIFY MATCH` when every file agrees with the manifest and the tables rebuild from the raw files.

## Citation

```bibtex
@dataset{namebeam_ai_visibility_daily_record,
  author    = {{Carter Enterprise LLC}},
  title     = {Namebeam AI Visibility Daily Record},
  year      = {2026},
  publisher = {Hugging Face},
  url       = {https://huggingface.co/datasets/Namebeam/ai-visibility-daily-record}
}
```

## License

CC BY 4.0. Credit Namebeam and link the record.

## Links

- Namebeam: https://namebeam.ai/
- Public record on GitHub (append only, losses included): https://github.com/mrcrtr1979-droid/namebeam-visibility-index
- Sibling dataset with the daily CSV mirror: https://huggingface.co/datasets/Namebeam/namebeam-e1-ai-referral-index

## Maintainer

Carter Enterprise LLC, 30 N Gould St, Ste 65270, Sheridan, WY 82801. Questions about the record: namebeam.ai.
