# 7-day variability sheets (IAB repeated-identical-query criterion, per question, per engine)

Built 2026-10-16 from the published Edition 1 pack (releases/edition-1/data/answers_2026-09-01_to_2026-10-13_run_status.csv). Script: variability_sheet.py (standard library, 5 unit tests: python3 -I -X utf8 reproduce/variability/variability_sheet.py --selftest). One sheet = one market, one niche, one engine, seven calendar days: the measures a reader needs to document variability across repeated identical queries in a 7-day window (the criterion in the IAB "Measuring Visibility in the AI Era" framework, 2026-08-03), with the raw file name, SHA-256 and run_status of every day so each line can be recomputed from corpus/e1.

Rule on every sheet: names as recorded in the pack (businesses_named_no_headings), top name = the entry at position 1; a pair is day d and day d+1 both answered with a name; a day with a failed row and a successful rerun uses the successful row. Failed and unnamed days are shown as rows, not dropped. The gated and text-mention rules (reproduce/stability/, reproduce/recount/) give lower change rates on the same days because they drop non-business strings (the raw lists carry headings such as "Ways to find options"); a sheet states which rule it used, and this folder uses the raw rule so that any reader can reproduce it from the pack alone.

## Sample set in out/: HVAC repair and replacement, Atlanta GA, Houston TX, Phoenix AZ; two windows; four engines (24 sheets)
| Window | Market | OpenAI: days answered of 7, top changed of pairs | Anthropic Claude | Google Gemini | Perplexity |
|---|---|---|---|---|---|
| 2026-09-15 to 09-21 (Perplexity on Sonar) | Atlanta GA | 7, 6 of 6 | 4, 0 of 2 | 0 (HTTP 402 days) | 7, 3 of 6 (Reliable Heating and Air named every day) |
| 2026-09-15 to 09-21 | Houston TX | 7, 6 of 6 | 6, 2 of 4 | 1, 0 of 0 | 7, 0 of 6 (Air Tech of Houston at position 1 all 7 days) |
| 2026-09-15 to 09-21 | Phoenix AZ | 7, 6 of 6 | 2, 0 of 0 | 1, 0 of 0 | 7, 2 of 6 (Parker and Sons named every day) |
| 2026-10-06 to 10-12 (Perplexity on the Agent API) | Atlanta GA | 6, 4 of 4 | 3, 0 of 0 | 0 | 7, 5 of 6 |
| 2026-10-06 to 10-12 | Houston TX | 7, 5 of 6 | 7, 6 of 6 | 0 | 7, 5 of 6 |
| 2026-10-06 to 10-12 | Phoenix AZ | 7, 5 of 6 | 3, 0 of 0 | 0 | 7, 6 of 6 |
Reading, raw rule, these 24 sheets alone: OpenAI put a different business at position 1 on almost every consecutive day in all three markets in both weeks; Perplexity held one Houston business at position 1 for all seven September days and kept one business in every answer in all three markets that week, then changed on 5 or 6 of 6 pairs in October after its route changed; Anthropic Claude answered with a name on fewer days; Gemini had no answers in October. Small-window figures are directional; the full-record figures with the gated rule are in reproduce/stability/.

## Atlas: every panel cell, every engine, six 7-day windows (out/variability_index_panel.csv, 912 rows; out/variability_summary_panel.csv)
python3 -I -X utf8 reproduce/variability/variability_sheet.py --atlas --panel --out reproduce/variability/out
Panel = the Edition 1 metrics panel (38 cells with an API row on or before 2026-09-27, market not US nationwide). Per engine and week: pairs and position-1 changes, pooled over the 38 cells.
| Window start | OpenAI | Anthropic Claude | Google Gemini | Perplexity |
|---|---|---|---|---|
| 2026-09-01 | 26 of 36 | 58 of 138 | 31 of 38 | 33 of 76 |
| 2026-09-08 | 123 of 196 | 62 of 134 | 63 of 76 | 125 of 228 |
| 2026-09-15 | 122 of 196 | 58 of 142 | 31 of 32 | 113 of 226 |
| 2026-09-22 | 119 of 195 | 69 of 144 | 3 of 5 | 119 of 228 |
| 2026-09-29 | 112 of 171 | 47 of 118 | 0 of 0 | 133 of 188 |
| 2026-10-06 | 125 of 204 | 68 of 140 | 0 of 0 | 165 of 228 |
Cross-check: an independent script on the same raw rule (the Edition 1 recount pass of 2026-10-16, two code paths of its own) gives the same six weeks for OpenAI and Anthropic Claude exactly, and differs by one count in four cells (Gemini weeks 1 and 3: 30 and 30 changes; Perplexity weeks 2 and 6: 124 and 164 changes), where the two scripts normalise a name differently ("and" against "&", punctuation). Same step for Perplexity in the week of 2026-09-29 (52 to 71 percent); flat OpenAI (61 to 72 percent).

## Use
python3 -I -X utf8 reproduce/variability/variability_sheet.py --market "Houston TX" --niche "hvac repair and replacement" --engine all --start 2026-10-06 --out reproduce/variability/out
Writes .md (the sheet), .json (the numbers) and .csv (the seven days) per engine. Any cell and any 7-day start inside the edition window works; the sheet prints NO_ROW for days the pack does not hold. --atlas without --panel indexes all 72 cells including the nationwide sets; --write-sheets also writes every sheet.
