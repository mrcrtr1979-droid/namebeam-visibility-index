# Category bank, software, 2026-11-01

30 software buying questions, each sent 12 times on 2026-11-01 to claude-haiku-5-5 with the web search tool, through the API (two runs: 1135Z with 10 repeats, 1157Z with 2). 360 answers, run_status OK on all 360.

Prompt: the question, then "Answer in at most 150 words and name specific companies with their websites."

## Files
- software_answers.csv: one row per answer. answer_text is the whole answer (the longest is 1,367 characters); answer_sha256 is the SHA-256 of answer_text in UTF-8; named_domains lists the web addresses written in the answer text (www. removed, lowercased, first seen order).
- software_counts.csv: one row per question and address: answers_with_address is the number of the 12 answers that wrote that address.

## Counts used in Namebeam data post 3
- 1,604 addresses written across the 360 answers (4.46 per answer), 182 distinct.
- 239 question and address pairs: 70 in all 12 answers, 53 in exactly 1.
- No question returned the same list in all 12 answers.
Script: tools/cpeo/j3_compute.py in the Namebeam Brain; every number above can be recounted from these two files.

## Licence
Same scope as the repository README: CC BY 4.0 covers the compilation, derived columns and documentation. The raw answer text returned by Anthropic is published as a dated factual record and is not licensed by Namebeam; provider terms may apply.

Namebeam, a Carter Enterprise property. This is the first leg.
