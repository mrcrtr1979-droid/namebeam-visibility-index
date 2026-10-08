# E1 own-name table

One row per UTC date and engine for the one fixed question the record asks about Namebeam itself: "What is the best service to check if AI engines like ChatGPT recommend my small business?" (niche "ai visibility checks", market "US nationwide"). Built from the raw answer files in `corpus/e1/` by `scripts/own_name_rate.py`.

## Columns of own_name_daily.csv

| column | meaning |
|---|---|
| rows | raw answer files for that question, date and engine (reruns add rows) |
| rows_ok | of those, rows whose run_status is OK |
| rows_names | of those, rows that name at least one business |
| rows_named_us | rows where the engine named Namebeam in its list of businesses |
| rows_text_us | rows whose answer text contains the word Namebeam |

## Reading the counts

A day counts for an engine when it has at least one such row, so every "days" figure is a count of dates. Three different denominators are in use and they are not interchangeable: days asked (a row exists), days the run succeeded (rows_ok), and days the answer named any business (rows_names). Say which one a figure uses.

Settings: OpenAI, Anthropic and Gemini are called through their APIs with no search tool switched on by us. Perplexity is called with web search on (Sonar chat completions to 2026-09-27, the Agent API with a forced web search tool from 2026-09-28).

## Rebuild and check

    python3 -I scripts/own_name_rate.py                              # rebuild the table
    python3 -I scripts/own_name_rate.py --summary 2026-08-02 2026-10-04
    python3 -I scripts/own_name_rate.py --selftest                   # fixture test, including checks that must fail

Operated by Carter Enterprise LLC. Questions about the record: namebeam.ai.
