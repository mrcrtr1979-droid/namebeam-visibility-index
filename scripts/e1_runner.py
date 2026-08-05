"""
E1 CHECK-RUNNER, schema v4, multi-engine.

Plain-language notes for a non-coder reading along:

1. Reads the roster (roster/e1_roster.json).
2. For every ACTIVE row, asks the SAME dated question of EVERY configured
   engine, and writes ONE corpus row per engine. That is the change: v3 asked
   one engine, v4 asks all of them, which is what makes engine DISAGREEMENT
   measurable. When two engines name different businesses for the same
   question, at most one is right, and only someone running both can see it.
3. Also fetches the Google results page through Bright Data and writes a
   separate SERP row holding the organic top results. That is the other half
   of the rank-to-citation question: who ranks on Google versus who the
   engines actually name.
4. Any failure is LOGGED as a FAILED row and the script still exits 0. A
   logged failure is a success of the protocol. A fabricated row is the one
   unforgivable bug.

No secret is ever printed, logged, or written into a corpus row.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

from engines import ENGINES, fetch_serp, detect_ai_overview
from extract_named_entities import (
    extract_named_entities,
    extract_stated_criteria,
)

ROSTER_PATH = "roster/e1_roster.json"
OUTPUT_DIR = "corpus/e1"
RUN_CHANNEL = "api"
SCHEMA_VERSION = "v4"

RUN_CHANNEL_CAVEAT = ("run_channel caveat: API results can differ from "
                      "logged-in consumer apps, no memory, no personalization.")


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower())
    return s.strip("_")


def load_active_roster(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [r for r in data.get("roster", []) if r.get("active") is True]


def write_row(row, check_id):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, check_id + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(row, f, indent=1, ensure_ascii=False)
    return path


def base_row(business_row, date_utc, check_id, engine):
    return {
        "check_id": check_id,
        "date_utc": date_utc,
        "business": business_row["business"],
        "niche": business_row.get("niche", ""),
        "market": business_row.get("market", ""),
        "row_type": business_row.get("row_type", "business"),
        "prompt_variant": 1,
        "run": 1,
        "prompt_text": business_row["prompt_1"],
        "engine": engine,
        "run_channel": RUN_CHANNEL,
        "schema_version": SCHEMA_VERSION,
    }


def is_mentioned(business_row, text):
    if not text:
        return False
    hay = text.lower()
    names = [business_row["business"]] + list(
        business_row.get("variants_business_name_forms", []))
    for n in names:
        if n and n.lower() in hay:
            return True
    return False


def run_engine(business_row, engine, fn, date_utc):
    slug = slugify(business_row["business"])
    check_id = "NB-CZ-API_%s_%s_%s" % (date_utc, slug, engine)
    row = base_row(business_row, date_utc, check_id, engine)
    ok, text, sources, err = fn(business_row["prompt_1"])
    if not ok:
        row.update({
            "run_status": "FAILED",
            "business_mentioned": None,
            "competitors_mentioned": [],
            "engine_stated_criteria": [],
            "sources_cited": [],
            "factor_scores": None,
            "answer_verbatim": "",
            "session_note": "FAILED RUN, logged per no-fabricated-rows law. "
                            "error detail: %s. %s" % (err, RUN_CHANNEL_CAVEAT),
        })
        return write_row(row, check_id), False
    row.update({
        "run_status": "OK",
        "business_mentioned": is_mentioned(business_row, text),
        "competitors_mentioned": extract_named_entities(
            text, business_row["business"],
            business_row.get("variants_business_name_forms", [])),
        "engine_stated_criteria": extract_stated_criteria(text),
        "sources_cited": sources,
        "factor_scores": None,
        "answer_verbatim": text,
        "session_note": RUN_CHANNEL_CAVEAT,
    })
    return write_row(row, check_id), True


def run_serp(business_row, date_utc):
    """Writes a SERP row: who RANKS on Google for the same question.
    Paired with the engine rows, this is the rank-to-citation delta."""
    slug = slugify(business_row["business"])
    check_id = "NB-CZ-SERP_%s_%s" % (date_utc, slug)
    row = base_row(business_row, date_utc, check_id, "google_serp")
    row["run_channel"] = "serp"
    ok, html, organic, err = fetch_serp(business_row["prompt_1"])
    if not ok:
        row.update({
            "run_status": "FAILED",
            "business_mentioned": None,
            "organic_top_results": [],
            "ai_overview_detected": None,
            "session_note": "FAILED RUN, logged per no-fabricated-rows law. "
                            "error detail: %s" % err,
        })
        return write_row(row, check_id), False
    row.update({
        "run_status": "OK",
        "business_mentioned": is_mentioned(business_row, html),
        "organic_top_results": organic,
        # None means "could not tell", and that is preserved on purpose.
        # Never coerce an unknown to False; an unknown is not a negative.
        "ai_overview_detected": detect_ai_overview(html),
        "session_note": ("Organic results extracted mechanically from the "
                         "returned results page. Bright Data does not "
                         "document an AI Overview field, so ai_overview_"
                         "detected is BEST EFFORT and may be null. Do not "
                         "publish it as a measurement."),
    })
    return write_row(row, check_id), True


def main():
    date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        roster = load_active_roster(ROSTER_PATH)
    except (OSError, ValueError) as exc:
        print("ERROR: could not read roster: %s" % exc)
        return 0

    engines = dict(ENGINES)
    available = [k for k in engines if os.environ.get(
        {"perplexity": "PERPLEXITY_API_KEY", "gemini": "GEMINI_API_KEY"}[k], "").strip()]
    serp_on = bool(os.environ.get("BRIGHTDATA_API_KEY", "").strip())

    print("E1 v4 run %s | roster %d active | engines available: %s | serp: %s"
          % (date_utc, len(roster), ", ".join(available) or "NONE", serp_on))
    if not available and not serp_on:
        print("ERROR: no engine keys set. Add repo secrets under "
              "Settings > Secrets and variables > Actions. Nothing written.")
        return 0

    wrote = ok_n = fail_n = 0
    for br in roster:
        for name, fn in engines.items():
            _, good = run_engine(br, name, fn, date_utc)
            wrote += 1
            ok_n += 1 if good else 0
            fail_n += 0 if good else 1
        if serp_on:
            _, good = run_serp(br, date_utc)
            wrote += 1
            ok_n += 1 if good else 0
            fail_n += 0 if good else 1

    print("rows written: %d (ok %d, failed %d)" % (wrote, ok_n, fail_n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
