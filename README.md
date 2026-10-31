# Namebeam AI Visibility Index

A dated public record of which local businesses AI engines (ChatGPT, Claude, Gemini and Perplexity) name when they are asked the same buyer questions. ChatGPT returned no answers from 2026-10-26 to 2026-10-29: every call was recorded as blocked on the OpenAI API quota (33 of 53 calls on 2026-10-25 were too), so those days carry three engines. ChatGPT answers resumed on 2026-10-30. Every answer is saved with its date and a SHA-256 hash of the raw file.

## MCP server (receipts-index-mcp)

This repository also contains a Model Context Protocol (MCP) server in [`mcp/`](mcp/). It is a TypeScript stdio server built on the official MCP SDK (`@modelcontextprotocol/sdk`, `McpServer`) and published to npm as `receipts-index-mcp` and to the official MCP Registry as `io.github.mrcrtr1979-droid/receipts-index`.

Tools:

- `receipts_index`: free. Calls `GET https://api.receiptsindex.com/v1/index` and returns row ids and UTC dates.
- `receipts_sample`: free. Calls `GET https://api.receiptsindex.com/v1/sample` and returns one real row labeled as a sample.
- `receipts_pricing`: free. Reads `/.well-known/x402` and `/v1/manifest` and lists the paid x402 routes and their prices (0.27 USDC per row, 1.81 USDC per day file).

The server never pays and never signs anything. Install:

```json
{ "mcpServers": { "receipts-index": { "command": "npx", "args": ["-y", "receipts-index-mcp"] } } }
```

Source, tests and the full tool reference: [`mcp/README.md`](mcp/README.md).

## Edition 1

- Window: 2026-09-01 to 2026-10-13, 42 run days (no run on 2026-10-05).
- Questions: 73. Of these, 53 were asked of all four engines and 20 of Perplexity only.
- Calls: 8,160 API calls. 6,330 were answered: 6,012 named at least one business and 318 named none. 1,830 failed and are kept as failed rows.
- Engines: Perplexity with web search on (Sonar chat completions to 2026-09-27, Agent API with a forced web search tool from 2026-09-28). OpenAI, Anthropic and Gemini through their APIs with no search tool switched on.

DOI: [10.5281/zenodo.23626831](https://doi.org/10.5281/zenodo.23626831)

Browse the tables: https://markets.namebeam.ai/edition-1/

Hugging Face dataset: https://huggingface.co/datasets/Namebeam/ai-visibility-daily-record

## Gaps in Edition 1

- Gemini returned no answers for the last 16 run days. Every Gemini call from 2026-09-27 failed because the prepaid credit ran out.
- OpenAI name lists are empty from 2026-09-01 to 2026-09-05. The raw answer text is present for those days.

## One finding

Over the whole Edition 1 window, two engines' same-day lists of businesses overlapped by 6 percent at most (Gemini and Perplexity, 280 same-day comparisons). Every other pair overlapped by 1 percent or less.

Source: `releases/edition-1/metrics/metrics_v2_sentences.md`, metric A.gated.pair, whole Edition 1 window.

## How to verify

Run these from the root of a clone of this repository. The raw answer files are in `corpus/e1/`.

Check one raw answer file against the hash recorded for it in the answers table:

```
sha256sum corpus/e1/NB-CZ-API_2026-09-10_atrix_digital_anthropic.json
grep -c bf425f6540799a10f0ea413c4d64c4f0ca56641970099f0fac24fdb694514c75 releases/edition-1/data/answers_2026-09-01_to_2026-10-13.csv
```

The first command should print `bf425f6540799a10f0ea413c4d64c4f0ca56641970099f0fac24fdb694514c75` and the file name. The second should print `1`.

Check the answers table itself against `MANIFEST.sha256`:

```
cd releases/edition-1
grep 'data/answers_2026-09-01_to_2026-10-13.csv$' MANIFEST.sha256 | sha256sum -c -
```

Expected output: `data/answers_2026-09-01_to_2026-10-13.csv: OK` (hash `398f7cc5f76a3060eba318d0231e4b32f92a3155064debb0bc68f0c9fb499f03`).

Check everything at once:

```
python3 releases/edition-1/verify.py
```

With no flags, `verify.py` reads `MANIFEST.sha256` and `PACK.json`, hashes every file listed in the manifest (33 files), prints MATCH or MISMATCH for each, and flags any file in `releases/edition-1/` that the manifest does not list. The last line reads `VERIFY MATCH` and the exit code is 0 only when everything agrees. It needs the whole `releases/edition-1/` folder and nothing outside the Python standard library.

Two optional flags go further. `--rebuild` rebuilds the answers table from the raw files in `corpus/e1/` and compares it with the manifest. `--metrics` recomputes the metrics table with the code in `releases/edition-1/method/` and compares it.

## Licence scope

CC BY 4.0 covers the compilation (selection, arrangement and structure of the tables), the code, annotations, derived columns and documentation. The raw answer text returned by OpenAI, Anthropic, Google Gemini and Perplexity, and the search result rows, are published as dated factual records and are not licensed by Namebeam; provider terms may apply.

## Disclosure

Namebeam sells AI visibility checks built on this method.

Namebeam, a Carter Enterprise property. This is the first leg.

## Earlier notes (before Edition 1)

# Namebeam AI Visibility Index

An append-only, dated, public record of one question: when someone asks an AI engine to recommend a tool that checks AI visibility for a business, does the engine say Namebeam?

This dataset is published by Namebeam (namebeam.ai), an AI visibility service that checks whether ChatGPT, Claude, Perplexity, Gemini, and Google AI name a specific business when a customer asks who to hire. Namebeam runs the same check on itself that it sells, and publishes the results here, win or lose.

## The record so far

As of 2026-07-31: five engines run, zero of five named Namebeam. That number is our starting line, not a confession. Every test is dated, every miss is logged, every fix gets re-tested, so the climb gets proven on this record.

## Files

- data/ai-visibility-index.json: the corpus. One row per engine per run. Fields: check_id, date_utc, business, niche, market, engine, prompt_variant, run, prompt_text, answer_verbatim, business_mentioned, competitors_mentioned, factor_scores.
- data/heartbeat.log: the weekly sensor log.
- .github/workflows/ai-visibility-sensor.yml: a weekly GitHub Actions heartbeat (Mondays 13:00 UTC) that keeps the record alive and opens a re-check issue.

## Rules of the record

1. Append-only. Rows are never edited or deleted.
2. No fabricated rows. A failed run is logged as a failed run.
3. Losses print the same size as wins.
4. Memory-personalized engine sessions are refused; only customer-comparable runs enter the corpus.

## The human-readable version

- Full customer-zero ledger with transcripts: https://proof.namebeam.ai
- What Namebeam is: https://namebeam.ai
- Run the same free check on your own business and get a dated receipt: https://check.namebeam.ai

Namebeam scores AI visibility on five factors: Findable, Reachable, Quotable, Bookable, Trusted. Instead of an invented score, the deliverable is the actual dated transcript of what each engine said.

Operated by Carter Enterprise LLC, Sheridan, Wyoming.
