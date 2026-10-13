# Corrections to the Edition 1 pack

Rows are added and never removed. One row per dated change to a published pack or to the rows it reads: the date (UTC), which files or rows, what was wrong or what changed, what was done, and the commit. Rows are added at the bottom and never edited or removed. A copy of this file travels inside every build of the pack (releases/edition-1/CORRECTIONS.md) and is listed in MANIFEST.sha256, so a reader can see which corrections a given pack carries. The raw files in corpus/e1 and the name lists in the answers table are never changed by a correction; a correction states a limit, adds a derived column, or changes a build or collector rule from a stated date forward.

Format: date_utc | files or rows | what changed and why | action | commit

## Before the public release (pack status STAGED)

| date_utc | files or rows | what changed and why | action | commit |
|---|---|---|---|---|
| 2026-10-08 | releases/edition-1 (22 files) | Pack rebuilt through 2026-10-07. The OpenAI model snapshot gpt-5-mini-2025-08-07 is listed by OpenAI as deprecated (removal 2026-12-11); recorded in the method note and the dataset card. No row changed. | method note and card updated; pack rebuilt | 02f3d5f5 |
| 2026-10-09 | data/answers_*.csv, derived columns | Heading words (section titles such as Overview, Certifications) were being counted as business names. A heading filter (v1) adds the derived columns headings_removed_count and businesses_named_no_headings; a rerun marker column was added. The raw businesses_named column is unchanged. | derived columns added; raw column kept | 9bc0855f |
| 2026-10-10 | heading filter keep list | Pro Lawn (short form of Pro Lawn & Irrigation) matched the heading rule and was being removed; kept by name. | keep list entry added | e930969a |
| 2026-10-10 | LICENSE, data/prompts.csv, README counts line | The pack had no license file and no prompts table, and counts were typed in several places. CC BY 4.0 added at the repository root and in the pack; data/prompts.csv built from the raw files (id, text, SHA-256 of the text, dates asked, engines); one counts sentence computed by the build. | files added; counts computed | 1a4c4c15 |
| 2026-10-10 | README (Gemini rows 2026-09-01 to 2026-09-26) | Gemini quota-error rows in that span were not stated with their daily ranges. Stated. The sibling Hugging Face dataset now points to the canonical one. | method note updated | d9400aa6 |
| 2026-10-10 | reproduce/, EXPECTED_HEADLINES.csv, EXPECTED_INPUTS.csv, CITATION.cff | Headline numbers could not be recomputed offline from the pack alone. A reproduce kit and a citation file now travel in the pack. | files added | a865b5e2, 7ead4fdc |
| 2026-10-12 | name lists, all engines (DQ-5) | The name lists under-count the businesses an answer mentions, by different amounts per engine (Anthropic: bold spans read before plain text; other engines: a capitalisation rule). Stated in the README with per-engine counts and a text-mention recount method. Raw files and name lists unchanged. A union extractor (bold plus plain text, headings dropped, gazetteer pass) applies to collection runs dated 2026-10-14 and later; Edition 1 rows are not re-extracted. | README DQ-5 added; extractor change forward from 2026-10-14 | 0d8acef0, 2fe83d64, 29b04e5d, 73d9b313 |
| 2026-10-13 | build input, method/ code | The build copied the seven method files from a working folder without checking them against the pinned hashes; one file (gen_ai_picks_chart.py) had a one-line HTML copy change in that folder on 2026-10-12, so a build from it would ship a method file whose SHA-256 differs from the one the README quotes (611894c4...). The build now reads the method files from the pinned originals assembled by scripts/method_src.py, and this file is added to the pack. | build source pinned; CORRECTIONS.md added to the pack and manifest | (this change) |

## After the public release

(no rows yet)
