"""
E1 REFLEX CHECK-RUNNER - smallest testable slice.

Plain-language notes for a non-coder reading along are in the comments
below. In short, this script does four things:

1. Reads the roster file (roster/e1_roster.json).
2. For every row marked ACTIVE, sends prompt_variant 1, run 1, to the
   Perplexity API (one engine, one variant, one run - the smallest
   testable slice from the design document, section 7, step 1).
3. Writes one corpus row JSON file per business under corpus/e1/.
4. If the API call fails for any reason, it writes a FAILED RUN row
   instead of crashing or skipping silently, and still exits 0. A
   logged failure is a success of the protocol (see design doc section 5).

No secrets live in this file. The Perplexity API key is read only from
the PERPLEXITY_API_KEY environment variable at run time. This script
never prints, logs, or writes that key anywhere.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
# PENDING VERIFICATION: verify current model name and endpoint against
# Perplexity docs before first run. These values are best-guess placeholders
# picked at build time and may not match Perplexity's current live API.
PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"  # PENDING VERIFICATION
PERPLEXITY_MODEL = "sonar"  # PENDING VERIFICATION

# Temperature is pinned low and fixed so runs are comparable month over
# month (design doc section 3). Do not change this without documenting it.
TEMPERATURE = 0.2

ROSTER_PATH = "roster/e1_roster.json"
OUTPUT_DIR = "corpus/e1"

ENGINE_NAME = "perplexity"
RUN_CHANNEL = "api"


def slugify(business_name):
    """Turn a business name into a plain, lowercase, underscore-joined
    slug that is safe to use inside a filename."""
    slug = business_name.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")
    return slug


def load_active_roster_rows(path):
    """Read the roster file and return only the rows marked active: true.
    Rows not marked active are skipped entirely, per the design doc."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    all_rows = data.get("roster", [])
    active_rows = []
    for row in all_rows:
        if row.get("active") is True:
            active_rows.append(row)
    return active_rows


def is_business_mentioned(business_name, name_variants, response_text):
    """Case-insensitive substring match of the business name, plus any
    documented close-variant forms from the roster row, against the
    response text. Returns True if any form is found, else False."""
    if not response_text:
        return False
    haystack = response_text.lower()
    names_to_check = [business_name]
    if name_variants:
        names_to_check.extend(name_variants)
    for name in names_to_check:
        if name and name.lower() in haystack:
            return True
    return False


def call_perplexity(prompt_text, api_key):
    """Call the Perplexity chat completions endpoint with one prompt.

    Returns a tuple: (success, answer_text, sources_cited, error_detail)
    - success: True if we got a usable answer back, False otherwise
    - answer_text: the full raw response text (empty string on failure)
    - sources_cited: list of citations from the API response, or []
    - error_detail: plain description of what went wrong, empty on success
    """
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "model": PERPLEXITY_MODEL,
        "temperature": TEMPERATURE,
        "messages": [
            {"role": "user", "content": prompt_text},
        ],
    }

    try:
        response = requests.post(
            PERPLEXITY_API_URL, headers=headers, json=payload, timeout=60
        )
    except requests.exceptions.RequestException as exc:
        # Network problem, timeout, DNS failure, etc. Never include the
        # api_key variable in this message.
        return False, "", [], "request to Perplexity API failed: " + str(exc)

    if response.status_code != 200:
        detail = "API returned HTTP status " + str(response.status_code)
        # Keep the error short and plain; do not assume the body is safe
        # to print at full length.
        detail += ": " + response.text[:500]
        return False, "", [], detail

    try:
        body = response.json()
    except ValueError as exc:
        return False, "", [], "could not parse API response as JSON: " + str(exc)

    try:
        answer_text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return False, "", [], "API response did not have the expected choices/message/content shape"

    # Perplexity's API can return a top-level "citations" list. Store it
    # verbatim if present, else store an empty list. Never invent sources.
    citations = body.get("citations", [])
    if not isinstance(citations, list):
        citations = []

    return True, answer_text, citations, ""


def build_success_row(business_row, date_utc, check_id, answer_text, sources_cited):
    """Build a corpus row for a successful run.

    Field order below intentionally follows the order used in the real
    NB-0001 example row (check_id, date_utc, business, niche, market,
    prompt_variant, run, prompt_text, engine, business_mentioned,
    competitors_mentioned, factor_scores, then session_note), with the two
    new schema v2 fields (sources_cited, run_channel) inserted before
    session_note, and answer_verbatim carried over from schema v1 and
    placed last, matching where it sits in the NB-0001 example.
    """
    business_name = business_row["business"]
    mentioned = is_business_mentioned(
        business_name,
        business_row.get("variants_business_name_forms", []),
        answer_text,
    )
    session_note = (
        "competitor extraction deferred to WORK layer in slice. "
        "run_channel caveat: API results can differ from logged-in "
        "consumer apps, no memory, no personalization."
    )
    row = {
        "check_id": check_id,
        "date_utc": date_utc,
        "business": business_name,
        "niche": business_row.get("niche", ""),
        "market": business_row.get("market", ""),
        "prompt_variant": 1,
        "run": 1,
        "prompt_text": business_row["prompt_1"],
        "engine": ENGINE_NAME,
        "business_mentioned": mentioned,
        "competitors_mentioned": [],
        "factor_scores": None,
        "sources_cited": sources_cited,
        "session_note": session_note,
        "run_channel": RUN_CHANNEL,
        "answer_verbatim": answer_text,
    }
    return row


def build_failed_row(business_row, date_utc, check_id, error_detail):
    """Build a corpus row for a failed run. Same fields as a success row
    plus an explicit failure marker, per design doc section 5: a failed
    run is logged plainly, never skipped, never faked."""
    business_name = business_row["business"]
    session_note = (
        "FAILED RUN, logged per no-fabricated-rows law. error detail: "
        + error_detail
        + ". run_channel caveat: API results can differ from logged-in "
        "consumer apps, no memory, no personalization."
    )
    row = {
        "check_id": check_id,
        "date_utc": date_utc,
        "business": business_name,
        "niche": business_row.get("niche", ""),
        "market": business_row.get("market", ""),
        "prompt_variant": 1,
        "run": 1,
        "prompt_text": business_row["prompt_1"],
        "engine": ENGINE_NAME,
        "business_mentioned": None,
        "competitors_mentioned": [],
        "factor_scores": None,
        "sources_cited": [],
        "session_note": session_note,
        "run_channel": RUN_CHANNEL,
        "answer_verbatim": "",
        "failed_run": True,
    }
    return row


def main():
    # Read the key from the environment only. It is never written to
    # code, never printed, and never included in any error message.
    api_key = os.environ.get("PERPLEXITY_API_KEY", "")
    if not api_key:
        print(
            "ERROR: PERPLEXITY_API_KEY environment variable is empty or "
            "not set. Add it as a repo secret (Settings > Secrets and "
            "variables > Actions) and try again."
        )
        sys.exit(1)

    if not os.path.exists(ROSTER_PATH):
        print("ERROR: roster file not found at " + ROSTER_PATH)
        sys.exit(1)

    active_rows = load_active_roster_rows(ROSTER_PATH)
    if not active_rows:
        print("No ACTIVE rows found in the roster. Nothing to do this run.")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for business_row in active_rows:
        prompt_text = business_row["prompt_1"]
        slug = slugify(business_row["business"])
        check_id = "NB-CZ-API_" + date_utc + "_" + slug
        out_path = os.path.join(OUTPUT_DIR, check_id + ".json")

        success, answer_text, sources_cited, error_detail = call_perplexity(
            prompt_text, api_key
        )

        if success:
            row = build_success_row(
                business_row, date_utc, check_id, answer_text, sources_cited
            )
            print("Wrote corpus row: " + out_path)
        else:
            row = build_failed_row(business_row, date_utc, check_id, error_detail)
            print("Wrote FAILED RUN row: " + out_path + " (error: " + error_detail + ")")

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(row, f, indent=2)
            f.write("\n")

    # A logged failure is a success of the protocol (design doc section 5),
    # so this script always exits 0 once it has finished writing rows,
    # whether every row succeeded or some rows are failed-run rows.
    sys.exit(0)


if __name__ == "__main__":
    main()
