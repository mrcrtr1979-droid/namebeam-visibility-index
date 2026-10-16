# Stability recount kit (top-named business, day-to-day change)

Reruns the per-niche, per-engine stability numbers published for Edition 1 (window 2026-09-01 to 2026-10-13) from this repository alone. Standard library only.

Command, from the repository root (Windows: use py -3 -I -X utf8; Linux and macOS: python3 -I -X utf8):

    py -3 -I -X utf8 reproduce/stability/niche_findings.py . out.json --end 2026-10-13

Expected on a clean clone of main at or after 2026-10-15 (commit 947c3ac7): the run prints "niches 25 cells 38 window 2026-09-01 2026-10-13 latest 2026-10-13" and out.json has SHA-256 cdb9b06ba7cf9bf6046dc3f3bf158edf1ebe89409c0ed117247d356609aa8cc9. Pooled totals per engine (consecutive-day pairs with a top-named business on both days, and pairs where the top changed): Perplexity 1,244 and 582; OpenAI 464 and 250; Gemini 176 and 120; Anthropic Claude 171 and 35. Runtime about 6 minutes on a laptop (18,971 corpus files).

Definition: the top-named business of a day is the business mentioned earliest in that day's first saved answer with run_status OK (text-mention rule in mention_counts.py, candidate names through the pack's name gate); a pair is two calendar dates one day apart with a top on both; the panel is the 38 market and niche cells with a row on or before 2026-09-27, US nationwide excluded. Method note: releases/edition-1/README.md (DQ-5) and the Edition 1 page.

These three files are copies of the Brain scripts used for the published Study 1 and stability numbers (niche_findings.py, mention_counts.py, findings.py), with one line changed so niche_findings.py imports its siblings from this folder. They sit outside releases/edition-1 so MANIFEST.sha256 is unchanged.
