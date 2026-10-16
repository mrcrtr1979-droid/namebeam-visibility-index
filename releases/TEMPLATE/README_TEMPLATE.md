# Namebeam AI Visibility Record, Edition [N] (release template)

This file is the reusable README for every edition from Edition 2 on. It follows the seven questions a reviewer asked on 2026-10-09 and accepted answers to on 2026-10-15 (exact prompts; engine and model versions; dates; regions and languages; sampling and scoring method; the underlying answers; reuse terms), in that order, each as its own section with a stable anchor. Replace every [FILL] before release; delete this paragraph. Edition 1 values are shown as worked examples so the next build copies the shape, not the numbers. ASCII characters throughout; every number carries its window and denominator on the same line.

Edition [N], released [YYYY-MM-DD]. Window [YYYY-MM-DD] to [YYYY-MM-DD] (UTC), [D] run days (no run on [dates, or "none missed"]). [R] dated rows: [A] AI engine answer rows ([A_ok] answered, [A_fail] failed calls kept as rows and labeled in run_status), [S] Google result pages, [G] engine agreement rows. [M] US local markets plus [Q_nat] nationwide question sets; [Q] questions ([Q_all] asked in the same words on every run day to [E] engines; [Q_added] added on [date], to [which engines]). Manifest sha256 [MANIFEST_SHA] timestamped in Bitcoin block [BLOCK] (OpenTimestamps). Corrections to any earlier edition are in CORRECTIONS.md and never change released rows.
Worked example, Edition 1: released 2026-10-15; 2026-09-01 to 2026-10-13; 42 run days (no run on 2026-10-05); 12,180 rows: 8,160 engine rows (6,330 answered, 1,830 failed), 2,010 Google result pages, 2,010 agreement rows; 25 markets plus 2 nationwide sets; 73 questions (47 on all 42 days to four engines, 26 added 2026-10-08: 6 to all four, 20 to Perplexity alone); manifest c682ee36...; block 971625.

## 1. Exact prompts {#prompts}
Every question is in data/prompts.csv with its prompt_id, the prompt text, its sha256, first_asked, last_asked_in_window, days_asked_in_window, engines_in_window, google_results and agreement_check. The text is asked verbatim, one call per engine per day, with no system prompt beyond [FILL: the collector's fixed preamble, quoted, or "none"]. Questions added or retired inside the window are listed here with the date: [FILL list or "none"].

## 2. Engine and model versions {#engines}
| Engine | Route | Model string requested | Web search | Temperature | In force |
|---|---|---|---|---|---|
| OpenAI | Chat Completions API | [FILL; code default gpt-5.2-mini on main 16e6ee9b, 2026-10-16; a repository variable may override and the row records the requested string] | [FILL] | 0.2 | [dates] |
| Anthropic Claude | Messages API | [FILL; code default claude-haiku-4-5 on main 16e6ee9b] | [FILL] | 0.2 | [dates] |
| Google Gemini | Gemini API generateContent | gemini-3.6-flash on the 20-question continuity panel (the prompt ids that ran all 42 Edition 1 days on Gemini); gemini-3.5-flash-lite on every other question; free tier, paced (PR #32, from 2026-10-16). State the opening run day: [FILL] | not switched on | provider default | [dates] |
| Perplexity | Agent API, model perplexity/sonar | perplexity/sonar | forced | 0.2 | [dates] |
Rules: the rows record the model string requested, not the model that answered, except where the API returns the served model (Perplexity engine_meta from 2026-09-28). Any change of route, model string or search setting inside the window splits the window (Edition 1: WA and WB at 2026-09-27/28) and is reported apart. Deprecations announced by a provider that fall inside or after the window are listed here with the provider's date: [FILL; Edition 1 example: gpt-5-mini-2025-08-07 removal 2026-12-11].

## 3. Dates {#windows}
Window [start] to [end] (UTC). Run days [D]; days with no run and why: [FILL]. Engine-days with no answer on any row, by engine, with the HTTP reason: [FILL; Edition 1 example: Gemini 17 days HTTP 402 and 429; OpenAI 5 days extractor returned nothing; Perplexity 3 days HTTP 401]. Sub-windows if a route changed: [FILL or "none"]. Collection time of day: [FILL, UTC].

## 4. Regions and languages {#regions}
Markets: [M] US local markets, listed in data/prompts.csv (market column), plus [Q_nat] nationwide question sets. All prompts are in English; no locale or location header is sent beyond the city and state in the prompt text; Google result pages are fetched for [FILL: location setting] through [FILL: route, e.g. Bright Data SERP API]. Engines are not told a user location.

## 5. Sampling and scoring method {#metrics}
Sampling: one call per engine per question per run day; the earliest saved answer per engine-question-day is the row used by every metric (reruns kept and marked, rerun column). This is API sampling, not real-user sampling. Metrics (metrics/metrics_v2.csv, recomputable with verify.py --rebuild --metrics): name rate = answered cell days with at least one gated business name / answered cell days, per engine, per window; next-day persistence; same-day list overlap; the stability and agreement counts from reproduce/stability/. Scoring of names: raw lists as recorded (businesses_named), heading filter (businesses_named_no_headings), the name gate (deny set, quarantine, alias merge, 1005A gate; spot audit [FILL: n of 100 gated names are operating businesses]) and, from Edition 1.1, the text-mention recount side table (reproduce/recount/). Panel rule for metrics: cells with an API row on or before [FILL cutoff], market not nationwide.

## 6. Archived answers {#answers}
Every answer row in data/answers_[start]_to_[end].csv carries source_file (the raw JSON in corpus/e1/) and the sha256 of that file; run_status and run_status_reason per row; the Google result pages and the agreement rows likewise. MANIFEST.sha256 lists every file in the pack; verify.py checks the pack, rebuilds the tables from the corpus and recomputes the metrics (expected: VERIFY MATCH, [FILL] files). The manifest is timestamped with OpenTimestamps (MANIFEST.sha256.ots, Bitcoin block [FILL]). The raw text is public and unchanged; no row is edited after release (corrections are new files plus a CORRECTIONS.md line).

## 7. Terms {#license}
CC BY 4.0 on the compilation, the code and the annotations (name tables, metrics, status columns). The raw answer text is a dated factual record of what each engine returned and is published as such under each provider's terms; Namebeam grants no licence on it and it may not be used to train models that compete with the provider (OpenAI Services Agreement, Anthropic Commercial Terms D.4, Gemini API Additional Terms, Perplexity API Terms 2.1 and 2.3.1; see NOTICE.md and status/returns/1016-VERIFY.md in the Brain for the clause dates). Free-tier Gemini rows from [FILL date]: Google states it may use and review unpaid-tier prompts and responses (Gemini API Additional Terms, "How Google Uses Your Data", effective 2026-03-23). Credit: CITATION.cff. Quote any row with its date and sha256.

## Known gaps and corrections
[FILL: the DQ list for this edition, each with the count and window, same shape as Edition 1 README "Known gaps and how to read them".] CORRECTIONS.md rows that touch this edition: [FILL or "none"].

## Check the pack
python3 -I -X utf8 releases/edition-[N]/verify.py --rebuild --metrics
