# Name labeler (1012D-X4)

Labels every candidate name found in the saved answers as BUSINESS, NOT_BUSINESS or UNSURE, so non-business names
(for example "Labor Day", "Google Business", "Ad Club") stop counting as businesses (G3) and bolded names that the
recorded lists missed are captured (M602).

## Files
- scripts/label_names.py : stdlib plus urllib only. Reads ANTHROPIC_API_KEY from the environment.
- .github/workflows/label-names.yml : manual run only (workflow_dispatch, default max_names 200; no schedule). Key comes from secret
  ANTHROPIC_API_KEY_CREDIT. Labels are committed on a new branch labels/names-<stamp> and opened as a PR. It never
  pushes to main.
- corpus/labels/name_labels.csv : output, append only. Columns: name, market, niche, label, reason, model, labeled_at.

## How it works
1. Candidates: for each corpus/e1/*.json answer file, every name in competitors_mentioned plus every bolded span in
   answer_verbatim that looks like a proper name (this second source is the M602 fix). Unique key is
   (name with legal suffix and leading "the" removed, market, niche).
2. Deterministic pre-filter: a deny list and phrase patterns (holidays, platforms, directories, associations,
   sentence fragments). Matches are written as NOT_BUSINESS with model "prefilter" and no API call.
3. Everything else goes to Claude Haiku 4.5 (claude-haiku-4-5-20251001) through the Message Batches API at the 50
   percent batch discount, 40 names per request, each item carrying its market and niche as context.
4. Rows are appended. A key already in the CSV is skipped, so reruns only pay for new names. Names the model does not
   answer stay unlabeled and retry next run. Corpus JSON files are only read.

## Commands
    python3 -I scripts/label_names.py --selftest
    python3 -I scripts/label_names.py --count   --repo .
    python3 -I scripts/label_names.py --dry-run --repo . --out /tmp      # writes batch_dryrun.jsonl, no API call
    ANTHROPIC_API_KEY=... python3 -I scripts/label_names.py --run --repo . [--max-names 500]

## Setup (hands step, gate word only)
Repository secret ANTHROPIC_API_KEY_CREDIT must exist. First live run: start workflow label-names with max_names 200 to
check labels by eye, then run again with 0 for the full backfill.

## Notes
- A batch finishes within 24 hours (usually under one hour). The job waits up to 6 hours; if it times out, no rows
  are written and the next run resubmits.
- The text-rule script (branch mention-rule-src, scripts/mention_counts.py) is not modified. Joining the labels to it
  (drop NOT_BUSINESS before ranking) is a separate step.
