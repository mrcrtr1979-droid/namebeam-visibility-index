# AI-ingestion lag probe

How many days pass between publishing one true, dated fact on a Namebeam page and the day each AI engine states it? This folder holds the daily record that answers that question for each cohort. The design of every cohort is fixed in advance in `prereg/` of this repository, and the commit that adds a cohort file is its registration time.

## How a cohort runs

1. Before the first probe, `prereg/cohort-NN.json` fixes the probe questions (each with its SHA-256), the engines and their settings, the sampling, the decision rule, the controls and the publication plan. The fact itself is not in that file. It holds salted SHA-256 commitments to the fact text and to the pattern that detects it, so the number cannot be read off this repository before publication and the published fact can be checked against what was fixed in advance.
2. Every day, `scripts/ingestion_probe.py` asks each engine each probe question once, with the same call settings as the daily E1 run (`scripts/engines.py`), and saves every answer word for word under `raw/<date>/`. Failed calls are saved as failures.
3. On the publication day the fact text, its pattern and the salt are committed in `prereg/cohort-NN.reveal.json`. `python3 -I scripts/ingestion_probe.py --check-reveal prereg/cohort-NN.reveal.json --cohort prereg/cohort-NN.json` prints `REVEAL MATCH` when they equal the commitments.
4. Every saved answer, including the ones saved before publication, is then scored from the committed files. Anyone can rerun the scoring: `python3 -I scripts/ingestion_probe.py --score`.

## Probes

- Anchored: names Namebeam and asks for the fact.
- Unanchored: asks the same question with no brand. A hit needs the fact and either the Namebeam name or a namebeam.ai link.
- Negative control: a true fact that stays unpublished until the cohort ends. Any hit means the detector or a leak is wrong.
- Positive control: a fact that is already published on a page an engine is known to cite. It shows whether an engine and probe can register a published fact at all.

## Files

| file | what it is |
|---|---|
| `ingestion_probe.csv` | One row per call: time, date, phase (baseline before publication, post after), probe, engine, the model the provider reported, search setting, prompt SHA-256, ok, HTTP status, error class, answer length and SHA-256, number of citations, whether a namebeam.ai URL was cited, whether the answer names Namebeam, the raw file and the workflow run id. Append only. |
| `ingestion_presence.csv` | One row per daily check of the page: HTTP status and SHA-256 of the live page, whether the page carries the fact (after the reveal), whether the sitemap lists it, and the closest Wayback Machine snapshot. |
| `ingestion_scores.csv` | Derived from the raw answers each run: the pattern result and the hit per row. Blank until the pattern is revealed. |
| `ingestion_lag.csv` | Derived: per engine and probe, baseline hits, the first named date under the decision rule, and days to first named. |
| `raw/<date>/` | Every answer as returned, with the prompt, the model id, the citations and the error text of a failed call. |

## Limits

- One sample per engine and probe per day. The engines are not deterministic, so a single day can miss a fact an engine would state on another try. The decision rule asks for two probed days in a row.
- The OpenAI and Anthropic calls run without a search tool, as in the E1 run, so they show what the model holds, not what it can find on the web. Perplexity runs with web search forced.
- An engine can state a fact it read somewhere other than the page. The unanchored probe and the citations help tell these apart; they do not prove where a fact came from.
- Results may stay at zero for the whole cohort. Zeros are printed like any other result.

Operated by Carter Enterprise LLC. Questions about the record: namebeam.ai.
