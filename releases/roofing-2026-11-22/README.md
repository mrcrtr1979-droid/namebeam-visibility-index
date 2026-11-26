# Namebeam residential roofing word mentions

One row represents one saved residential roofing API answer marked OK from 2026-09-27 through 2026-11-22, inclusive. The date column preserves the collector's recorded date. The file pools OpenAI, Gemini and Anthropic answers from three markets. Its unit is an answer. The page uses all 420 saved answers as the denominator for each word group. Each answer can count once per group and can count in several groups.

The recorded model is the archive's served-model label. The value "none recorded" preserves missing model metadata. The answer ID is the lowercase hex SHA-256 of the UTF-8 compact JavaScript JSON serialization (JSON.stringify, no whitespace between elements) of the array [date, engine, exact saved answer text]. The CSV contains the digest and the flags. Raw text and market labels are omitted. (Checked 2026-11-26.)

## Flag rules

The recount uses case-insensitive regex searches of the saved text. Each column is 1 if its pattern matches at least once and 0 otherwise. The patterns below reproduce the original recount's rules. (Checked 2026-11-26.)

| Column | Regex |
| --- | --- |
| reviews | `\b(reviews?\|ratings?\|rated\|stars?)\b` |
| license | `\blicen[sc]\w*` |
| insurance | `\b(insured\|insurance\|bonded\|liability)\b` |
| warranty | `\b(warrant\w*\|guarantee\w*)\b` |
| estimate | `\b(written estimates?\|estimates?\|quotes?)\b` |
| years | `\b(years? (in business\|of experience)\|experience\|established)\b` |

## OpenAI split and limits

Namebeam switched its own OpenAI collector to gpt-5.6-terra on 2026-11-10. The page reports 113 of 114 OpenAI answers (99.1%) with a review or rating mention through 2026-11-09 and 33 of 39 (84.6%) from 2026-11-10 onward. The full table pools both periods and gives each saved answer equal weight. The before-and-after counts describe different periods and cannot isolate the effect of the collector switch.

A flag records wording. It cannot establish whether the answer's statements are true, whether a criterion is useful for choosing a roofer, or how an engine orders businesses. Broad matches can capture terms outside their intended context. The CSV lets readers sum the released flags. Rechecking the flags against the original wording requires the saved answers.

License. The compilation is licensed CC BY 4.0. Raw answer text is excluded from that license.

Source: Namebeam, "What do AI answers say to check when you hire a roofer?", roofing sample 2026-09-27 to 2026-11-22, https://markets.namebeam.ai/what-ai-checks/roofing/ (read 2026-11-26 by the laptop seat).

## Reproduce the percentages

Run these four commands with Python 3 from the directory containing the CSV. The first checks the denominator, the second prints all six pooled counts and percentages, and the last two print the OpenAI review splits. This recipe needs only the CSV and this README. (Checked 2026-11-26.)

```
python -c "import csv; r=list(csv.DictReader(open('roofing_sample.csv', newline='', encoding='utf-8'))); print(len(r)); assert len(r)==420"
python -c "import csv; r=list(csv.DictReader(open('roofing_sample.csv', newline='', encoding='utf-8'))); keys='reviews license insurance warranty estimate years'.split(); print('\n'.join('{}: {}/{} ({:.1f}%)'.format(k,n,len(r),100*n/len(r)) for k in keys for n in [sum(int(x[k]) for x in r)]))"
python -c "import csv; r=[x for x in csv.DictReader(open('roofing_sample.csv', newline='', encoding='utf-8')) if x['engine']=='openai' and x['date']<'2026-11-10']; n=sum(int(x['reviews']) for x in r); print('{}/{} ({:.1f}%)'.format(n,len(r),100*n/len(r)))"
python -c "import csv; r=[x for x in csv.DictReader(open('roofing_sample.csv', newline='', encoding='utf-8')) if x['engine']=='openai' and x['date']>='2026-11-10']; n=sum(int(x['reviews']) for x in r); print('{}/{} ({:.1f}%)'.format(n,len(r),100*n/len(r)))"
```

The expected pooled percentages in column order are 97.4%, 93.1%, 98.3%, 91.9%, 91.2% and 75.0%. The companion verifier checks the same counts and percentages. (Checked 2026-11-26.)
