# 7-day variability sheets (IAB repeated-identical-query criterion, per question, per engine)

Built 2026-10-16 from the published Edition 1 pack (releases/edition-1/data/answers_2026-09-01_to_2026-10-13_run_status.csv). Script: variability_sheet.py (standard library, 4 unit tests: python3 -I -X utf8 reproduce/variability/variability_sheet.py --selftest). One sheet = one market, one niche, one engine, seven calendar days: the measures a reader needs to document variability across repeated identical queries in a 7-day window (the criterion in the IAB "Measuring Visibility in the AI Era" framework, 2026-08-03), with the raw file name, SHA-256 and run_status of every day so each line can be recomputed from corpus/e1.

Rule on every sheet: names as recorded in the pack (businesses_named_no_headings), top name = the entry at position 1. Failed and unnamed days are shown as rows, not dropped. The gated and text-mention rules (reproduce/stability/, reproduce/recount/) give lower change rates on the same days because they drop non-business strings; a sheet states which rule it used, and this folder uses the raw rule so that any reader can reproduce it from the pack alone.

## Sample set in out/: HVAC repair and replacement, Atlanta GA, Houston TX, Phoenix AZ; two windows; four engines (24 sheets)
| Window | Market | OpenAI: answered days, top changed of pairs | Anthropic Claude | Google Gemini | Perplexity |
|---|---|---|---|---|---|
| 2026-09-15 to 09-21 (Perplexity on Sonar) | Atlanta GA | 7 of 7, 6 of 6 | 4 of 7, 0 of 3 | 0 of 7 (HTTP 402 days) | 7 of 7, 3 of 6 |
| 2026-09-15 to 09-21 | Houston TX | 7 of 7, 6 of 6 | 6 of 7, 3 of 5 | 1 of 7, 0 of 0 | 7 of 7, 0 of 6 |
| 2026-09-15 to 09-21 | Phoenix AZ | 7 of 7, 6 of 6 | 2 of 7, 1 of 1 | 1 of 7, 0 of 0 | 7 of 7, 2 of 6 |
| 2026-10-06 to 10-12 (Perplexity on the Agent API) | Atlanta GA | 6 of 7, 5 of 5 | 3 of 7, 0 of 2 | 0 of 7 | 7 of 7, 5 of 6 |
| 2026-10-06 to 10-12 | Houston TX | 7 of 7, 5 of 6 | 7 of 7, 6 of 6 | 0 of 7 | 7 of 7, 5 of 6 |
| 2026-10-06 to 10-12 | Phoenix AZ | 7 of 7, 5 of 6 | 3 of 7, 1 of 2 | 0 of 7 | 7 of 7, 6 of 6 |
Reading, raw rule, these 24 sheets alone: OpenAI put a different business at position 1 on almost every consecutive day in all three markets in both weeks; Perplexity was steady in Houston in September (0 of 6) and changed on 5 or 6 of 6 pairs in October after its route changed; Anthropic Claude answered with a name on fewer days; Gemini had no answers in October. Small-window figures are directional; the full-record figures with the gated rule are in reproduce/stability/.

## Use
python3 -I -X utf8 reproduce/variability/variability_sheet.py --market "Houston TX" --niche "hvac repair and replacement" --engine all --start 2026-10-06 --out reproduce/variability/out
Writes .md (the sheet), .json (the numbers) and .csv (the seven days) per engine. Any cell and any 7-day start inside the edition window works; the sheet prints NO_ROW for days the pack does not hold.
