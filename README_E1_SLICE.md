# E1 Reflex Check-Runner, smallest slice: install steps for Terry

This is the smallest testable version of the E1 check-runner (design doc
section 7, step 1). It runs ONE business (Namebeam itself), against ONE
engine (Perplexity), ONE prompt variant, ONE run. You trigger it by hand
from the GitHub website. Nothing here runs on a schedule yet.

## 1. Where each file goes

Everything in this bundle goes into your existing repo:
github.com/mrcrtr1979-droid/namebeam-visibility-index

Copy the folders over exactly as they are, keeping the same paths:

- `.github/workflows/e1-check-runner.yml` goes into `.github/workflows/`
  in your repo (create that folder path if it does not already exist).
- `scripts/e1_runner.py` goes into a `scripts/` folder in your repo.
- `roster/e1_roster.json` goes into a `roster/` folder in your repo.
- `corpus/e1/.gitkeep` goes into a `corpus/e1/` folder in your repo. This
  is where the new check rows will land after each run.

If you are using the GitHub website's "Add file > Upload files" screen,
you can drag the whole unzipped folder in and GitHub will preserve the
paths for you.

## 2. Add your Perplexity API key as a repo secret

Creating and funding a Perplexity API key is a money decision, and that
decision is yours alone. Nobody should provision or pay for that key
without you saying so first.

Once you have a Perplexity API key in hand:

1. Go to your repo on github.com.
2. Click Settings (top menu of the repo, not your account settings).
3. In the left sidebar, click Secrets and variables, then Actions.
4. Click "New repository secret."
5. Name: `PERPLEXITY_API_KEY`
6. Value: paste your actual key.
7. Click "Add secret."

The workflow reads this secret directly into the run; the key is never
written into any file in this bundle and never shows up in any log.

## 3. How to trigger the workflow manually

1. Go to your repo on github.com.
2. Click the Actions tab (top menu of the repo).
3. In the left sidebar, click "E1 Check Runner."
4. Click the "Run workflow" button (it is on the right side, may need a
   small dropdown click first), then click the green "Run workflow"
   button that appears.
5. Wait a minute or two, then refresh the page. You will see the run
   listed with a status icon (a spinning circle while running, a green
   check when done, a red X if something failed).

## 4. What success looks like

- The workflow run shows a green check.
- A new file appears under `corpus/e1/` in your repo, named something
  like `NB-CZ-API_2026-08-01_namebeam.json`.
- Opening that file, you should see one JSON object with fields like
  `check_id`, `date_utc`, `business`, `business_mentioned`, and
  `answer_verbatim` filled in with real data from the API call.
- If the Perplexity API call itself failed for some reason (bad key,
  API outage, rate limit), you will still get a new file, but it will
  say `"failed_run": true` and describe the error in `session_note`.
  That is expected behavior, not a bug: a logged failure is treated as
  a success of the process, because nothing gets faked or hidden.

## 5. The honest-labeling reminder

Every row this runner writes is stamped `"run_channel": "api"`. That
means it came from a script calling the Perplexity API directly, not
from a real logged-in consumer session in a browser. API answers can
differ from what a logged-in ChatGPT, Claude, Perplexity, or Gemini
account would say, because the API has no memory, no personalization,
and sometimes different search/grounding behavior than the consumer app.

Please never present an "api" row to a customer as if it were a
consumer-UI capture. If a receipt or report is built from these rows, it
should say plainly which channel produced it.

## 6. Items still PENDING VERIFICATION

These are placeholders that need to be checked and confirmed against
Perplexity's current documentation before you trust the output for real
customer-facing use:

- The exact Perplexity API endpoint URL used in `scripts/e1_runner.py`
  (`PERPLEXITY_API_URL`).
- The exact current Perplexity model name used in `scripts/e1_runner.py`
  (`PERPLEXITY_MODEL`).
- Current Perplexity API pricing (not included in this slice at all;
  see the design doc's cost model section for the placeholder math).

Everything else in this bundle (the workflow trigger, the roster file
format, the corpus row writer, and the failed-run handling) has been
checked to run and produce valid output as of this build.
