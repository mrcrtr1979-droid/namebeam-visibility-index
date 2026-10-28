# receipts-index-mcp

An MCP server (stdio) for the Receipts Index API at https://api.receiptsindex.com, published by Carter Enterprise LLC.

The Receipts Index is the Namebeam record: dated daily rows of which US local businesses AI engines (OpenAI, Perplexity, Gemini, Anthropic, Google AI Overviews) name for fixed local questions. Each row is one check run on one engine on one UTC date, with the businesses the engine named and the sources it showed.

This server reads the free parts of the API and lists what the paid parts cost. It never pays and never signs anything.

## Tools

| Tool | Calls | Cost | Returns |
|---|---|---|---|
| `receipts_index` | `GET /v1/index` | free | Row ids and UTC dates (no row content), row count, first and last date, the dates that have rows. Optional `start` (integer, 1 or more) and `limit` (integer, 1 to 500) page through row ids. |
| `receipts_sample` | `GET /v1/sample` | free | One real record row labeled as a sample, its row id, the paid route and price for that row, the row as fields and as CSV. No parameters. |
| `receipts_pricing` | `GET /.well-known/x402`, `GET /v1/manifest` | free | The paid endpoints with prices in USDC, as the API lists them. |

## Paid endpoints (as listed by the API)

Paid routes use x402 with USDC on Base mainnet. Row and day files come back as CSV with a header line. A row id or date that does not exist returns 404 and is not charged.

| Route | Price (USDC) | Returns |
|---|---|---|
| `GET /v1/row/{row_id}` | 0.27 | One CSV row |
| `GET /v1/day/{YYYY-MM-DD}` | 1.81 | All rows for one UTC date |
| `GET /v1/corpus` | 540 | The full corpus as one CSV, per call |
| `GET /v1/feed/founding-partner` | 200 | The full corpus per call. Listed at 200 USDC per month; access is priced per call |

Use `receipts_index` to pick a valid row id or date, `receipts_sample` to see the row shape, then buy with any x402 client. This server does not include a wallet or a payment client. Prices are read from the API at call time, so `receipts_pricing` is the current source.

Row fields: date_utc, check_id, business, niche, market, engine, prompt_variant, run, business_mentioned, competitors_named, sources_cited, run_channel, flags.

## Install

Requires Node.js 18 or newer.

Claude Desktop or any client that reads an `mcpServers` block:

```json
{
  "mcpServers": {
    "receipts-index": {
      "command": "npx",
      "args": ["-y", "receipts-index-mcp"]
    }
  }
}
```

Claude Code:

```
claude mcp add receipts-index -- npx -y receipts-index-mcp
```

## Develop

```
npm install
npm test
```

The tests run against recorded fixtures in `fixtures/` (see `fixtures/PROVENANCE.txt`) and make no network calls.

## Limits

- Row content, day files and the corpus are paid. The free tools return ids, dates, one sample row and prices only.
- The data is a record of what the named AI engines answered on the dates shown. It makes no claim about why an engine answered as it did.
- Source and contact: Carter Enterprise LLC, https://api.receiptsindex.com. License: MIT.

Namebeam, a Carter Enterprise property. This is the first leg.
