# Text-mention recount, Edition 1 (answers_recount_v1)

Built 2026-10-16T06:31:46Z from the published pack (answers table sha256 398f7cc5..., run_status file sha256 8adfbd0b...) and corpus/e1. Output: out/answers_recount_v1.csv (12,180 rows, the pack's columns plus ten recount columns; sha256 eca511ff...), out/recount_summary.json. Script: text_mention_recount.py (standard library; the name rule is mention_counts.py, the same module the explorer uses). Selftest: python3 -I -X utf8 reproduce/recount/text_mention_recount.py --selftest.

## What the recount is
The collector recorded a list of names per answer at the time (businesses_named). DQ-5 in the pack README says those lists under-count on every engine, by different amounts (Anthropic's bold-span reader misses plain-text names; the capitalisation rule misses names at line starts). This file adds a second count per row: the gated business candidates of the row's market and niche cell whose name appears in the answer text (case-insensitive, whole word, full multi-word name; single words only when capitalised and not common). Both counts sit side by side; the original columns are unchanged.

## Row status (12,180 rows)
| recount_status | rows | meaning |
|---|---|---|
| RECOUNTED | 5,333 | API row, run_status OK, in one of the 64 local-market cells where the gate found at least one candidate |
| NO_GATED_CANDIDATES | 997 | API row, run_status OK, in one of the 8 US nationwide question sets: the gate (built for local-market names) found no candidate, so the text recount is blind there and the row is marked, not counted as zero |
| FAILED | 1,830 | API row with a failed call (run_status FAILED), nothing to count |
| NOT_APPLICABLE | 4,020 | Google result rows and engine agreement rows |

## Per-engine before and after (RECOUNTED rows, window 2026-09-01 to 2026-10-13)
| Engine | OK rows recounted | Recorded names, sum (before) | Rows with a recorded name | Text mentions of gated candidates, sum (after) | Rows with at least one text mention | Rows where after > before | Rows where after < before |
|---|---|---|---|---|---|---|---|
| Perplexity | 1,667 | 7,889 | 1,667 | 5,041 | 1,566 | 152 | 1,074 |
| OpenAI | 1,669 | 6,879 | 1,389 | 2,294 | 818 | 156 | 1,250 |
| Gemini | 323 | 3,057 | 319 | 1,986 | 320 | 34 | 268 |
| Anthropic Claude | 1,674 | 3,061 | 1,179 | 295 | 219 | 11 | 1,121 |
Reading the two counts: they measure different things. "Before" is every string the collector recorded, which includes phrases that are not businesses ("Compare options", "Better Business Bureau (BBB)", "Popular chains in most areas"). "After" counts only names that passed the pack's business gate, so it is lower on most rows by construction. The rows where after > before are the DQ-5 cases: a business named in the text that the recorded list missed (example, Anthropic, Pleasanton home care: recorded "Contact local resources; I'd recommend getting 2-3 consultations", text mentions Comfort Keepers, Pleasanton Senior Center, Visiting Angels).

## DQ-5 replication and the gate (OK rows dated 2026-09-01 to 2026-10-11, local-market cells)
The README printed, from the raw files: answers whose recorded list has a business in the cell's explorer top 10, and answers whose text contains one. Anthropic 48 and 195; OpenAI 330 and 553; Perplexity 1,142 and 1,250; Gemini 215 and 305 (OK answers 1,904; 1,898; 1,828; 342, nationwide included).
| Engine | OK rows (local cells) | Recorded list has a top-10 candidate | Text has a top-10 candidate (explorer order) | Text has a candidate in any engine's own top 10 (diagnostic) |
|---|---|---|---|---|
| Anthropic Claude | 1,584 | 4 | 192 | 206 |
| OpenAI | 1,579 | 331 | 598 | 760 |
| Perplexity | 1,537 | 1,277 | 1,400 | 1,426 |
| Gemini | 323 | 222 | 295 | 316 |
GATE (set for this build: Anthropic text count at least 195 on the explorer-order top 10): FAIL, 192. A second run with the candidate window cut at 2026-10-11 (the README's cut-off) gave 186. The diagnostic union definition gives 206. The README's 195 was produced by an earlier explorer build whose exact top-10 construction is not recorded in the pack; this build reproduces the direction and the size (the recorded list carries a top-10 Anthropic business on 4 answers, the text on 192) but not the exact figure. The gate verdict is printed as FAIL; whether the union definition is the intended one is a maintainer decision, not changed here. The Anthropic list figure (4 versus the README's 48) is a stricter list-match in this build (canon or stem equality against gated candidates) than the README's string match.

## Limits
- Blind on the 8 nationwide question sets (997 OK rows): no gated candidates. A nationwide gate is a separate build.
- The candidate set per cell comes from the recorded lists of all engines in the window; a business that no engine's list ever recorded cannot be found in the text by this rule (the DQ-5 ceiling).
- Shared name rule with the explorer (mention_counts.py): a defect there moves both the same way; the name-gate spot audit in the pack README (86 of 100 gated names are operating businesses) is the check on it.
- One pass; a second code path has not recounted this file.
