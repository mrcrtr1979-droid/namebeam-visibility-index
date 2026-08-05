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

from engines import (ENGINES, fetch_serp, detect_ai_overview,
                     serp_diagnostics)
from agreement import build_agreement_row
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


def next_run_index(date_utc, slug, engine):
    """Same-day re-runs must ACCUMULATE, never overwrite.

    Found the hard way on 2026-08-05: the identical roster was run twice in
    one day and the second run silently overwrote the first. The only reason
    the comparison survived was that a workflow had already committed the
    earlier rows to git. That comparison produced the most valuable finding
    the corpus has: across 22 rows ZERO answers were byte-identical between
    runs, and 4.5 percent flipped the named verdict.

    Destroying that data by default is the opposite of what this corpus is
    for. Run 1 keeps the historical filename so nothing already committed
    breaks; run 2 and beyond get a _rN suffix.
    """
    base = "NB-CZ-API_%s_%s_%s" % (date_utc, slug, engine)
    if not os.path.exists(os.path.join(OUTPUT_DIR, base + ".json")):
        return base, 1
    n = 2
    while os.path.exists(os.path.join(OUTPUT_DIR, "%s_r%d.json" % (base, n))):
        n += 1
    return "%s_r%d" % (base, n), n


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
    check_id, run_index = next_run_index(date_utc, slug, engine)
    row = base_row(business_row, date_utc, check_id, engine)
    row["run"] = run_index
    ok, text, sources, err = fn(business_row["prompt_1"])
    if not ok:
        # A refusal for QUOTA is not the engine failing to answer. It is us
        # not being allowed to ask. Counting it as FAILED would publish a
        # false statement about the engine, so it gets its own status and
        # must be excluded from any failure rate or denominator.
        quota = str(err or "").startswith("QUOTA: ")
        row.update({
            "run_status": "QUOTA_BLOCKED" if quota else "FAILED",
            "business_mentioned": None,
            "competitors_mentioned": [],
            "engine_stated_criteria": [],
            "sources_cited": [],
            "factor_scores": None,
            "answer_verbatim": "",
            "session_note": (
                "%s, logged per no-fabricated-rows law. error detail: %s. %s"
                % ("QUOTA BLOCKED, not an engine failure" if quota
                   else "FAILED RUN", err, RUN_CHANNEL_CAVEAT)),
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
    n = 2
    while os.path.exists(os.path.join(OUTPUT_DIR, check_id + ".json")):
        check_id = "NB-CZ-SERP_%s_%s_r%d" % (date_utc, slug, n)
        n += 1
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
    diag = serp_diagnostics(html, organic)
    row.update({
        # An extraction failure is NOT a finding. If organic is empty, this
        # row is marked EXTRACTION_FAILED so it can be excluded from any
        # published denominator (doctrine 122). It is never a silent zero.
        "run_status": "OK" if organic else "EXTRACTION_FAILED",
        "serp_diagnostics": diag,
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

    # ROSTER ROTATION. Free engine tiers run out of quota partway through a
    # roster. On 2026-08-05 Gemini answered the FIRST business and returned
    # 429 for the other twelve, which means roster order silently decided who
    # got engine coverage and the same business would have won every day.
    # Rotating the start position by date spreads coverage evenly over time
    # without spending a cent. Deterministic, so a run is still reproducible.
    if len(roster) > 1:
        offset = sum(ord(c) for c in date_utc) % len(roster)
        roster = roster[offset:] + roster[:offset]
        print("roster rotated by %d for %s" % (offset, date_utc))

    wrote = ok_n = fail_n = 0
    for br in roster:
        engine_rows = []
        for name, fn in engines.items():
            path, good = run_engine(br, name, fn, date_utc)
            if good:
                import json as _j
                with open(path, "r", encoding="utf-8") as _f:
                    engine_rows.append(_j.load(_f))
            wrote += 1
            ok_n += 1 if good else 0
            fail_n += 0 if good else 1
        if serp_on:
            _, good = run_serp(br, date_utc)
            wrote += 1
            ok_n += 1 if good else 0
            fail_n += 0 if good else 1

        # DW-1 falls out of the run for free. Only written when at least two
        # engines actually answered, so it never fabricates a comparison.
        if len(engine_rows) >= 2:
            ag = build_agreement_row(br, date_utc, engine_rows)
            cid = "NB-CZ-AGREE_%s_%s" % (date_utc, slugify(br["business"]))
            _n = 2
            while os.path.exists(os.path.join(OUTPUT_DIR, cid + ".json")):
                cid = "NB-CZ-AGREE_%s_%s_r%d" % (
                    date_utc, slugify(br["business"]), _n)
                _n += 1
            ag["check_id"] = cid
            write_row(ag, cid)
            wrote += 1
            ok_n += 1

    print("rows written: %d (ok %d, failed %d)" % (wrote, ok_n, fail_n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
