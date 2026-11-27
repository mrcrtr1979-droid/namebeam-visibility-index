# Source-share record, first release 2026-11-27

Which websites Perplexity and Gemini cite in their answers to 21 buyer questions asked every run day, as a weekly table, the sites whose share just moved, and the measured chance that each move is noise. The page that presents it: https://namebeam.ai/source-share/2026-11-27/

Perplexity data run from 2026-08-13 to 2026-11-26. Gemini data run from 2026-10-17 to 2026-11-19 (links resolved), and the Gemini record ends there: every Gemini call from 2026-11-20 to 2026-11-26 failed with HTTP 402.

## Files

- tools/source_share_watch.py: the tripwire. Reads corpus/namebeam/citation_corpus.csv; for one engine compares each site's share of answers in the newest 7 days with the 28 days before; fires at |z| of 3 or more. Standard library only, no model calls.
- tools/source_share_null.py: the calibration. A vectorised copy of the tripwire proven equal to it, placebo records made by shuffling whole days within a collector mode, a known-truth test, persistence, power and a changepoint test for one site. Needs numpy.
- corpus/namebeam/citation_corpus.csv: one row per saved answer; columns date_utc, check_id, niche (question set), market, engine, run, flags, sources_cited, run_channel. The business and competitor columns of the full corpus are left out.
- corpus/namebeam/gemini_source_resolution.csv: each Gemini redirect link and the page it resolved to, read 2026-11-27. Links that expired before they were resolved have no page and their answers are left out of every Gemini number.
- files/: every output of the release (weekly CSVs, first-fire lists, fires, calibration outputs and the summary CALIBRATION_1127.md).
- SHA256SUMS: a hash of every file in this folder.

## Reproduce

From this folder:

    python3 -I tools/source_share_watch.py --selftest
    python3 -I tools/source_share_watch.py --engine perplexity --backtest     (equals files/SOURCE_SHARE_BACKTEST_1127.txt)
    python3 -I tools/source_share_watch.py --engine gemini --backtest         (equals files/SOURCE_SHARE_BACKTEST_GEMINI_1127.txt)
    python3 -I tools/source_share_watch.py --engine perplexity --series out.csv --since 2026-08-24   (equals files/SOURCE_SHARE_PERPLEXITY_1127.csv)
    python3 -I tools/source_share_watch.py --engine gemini --series out.csv   (equals files/SOURCE_SHARE_GEMINI_1127.csv)
    python3 -I tools/source_share_null.py --selftest
    python3 -I tools/source_share_null.py --engine perplexity --equal
    python3 -I tools/source_share_null.py --engine perplexity --perms 2000 --jobs 12   (seeded: the same --perms and --jobs reproduce the counts)

## What it does not tell you

- Gemini has no readable answers from 2026-09-08 to 2026-10-16 (767 answers, expired links) and none after 2026-11-19.
- The Perplexity collector changed mode on 2026-09-27 (flag agent-fast); sources per answer fell from about 20 to about 10. The tools never compare across the change.
- The Gemini model version is not recorded. Persistence is measured to 14 days only. ChatGPT has 21 answers in the corpus and is not tracked.
