# Namebeam AI Visibility Index

An append-only, dated, public record of one question: when someone asks an AI engine to recommend a tool that checks AI visibility for a business, does the engine say Namebeam?

This dataset is published by Namebeam (namebeam.ai), an AI visibility service that checks whether ChatGPT, Claude, Perplexity, Gemini, and Google AI name a specific business when a customer asks who to hire. Namebeam is its own first customer. We run the same check on ourselves that we sell, and we publish the results here, win or lose.

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
