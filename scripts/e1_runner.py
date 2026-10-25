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

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

from engines import (ENGINES, LAST_CALL_META, fetch_serp, detect_ai_overview,
                     serp_diagnostics)
from agreement import build_agreement_row
from extract_named_entities import (
    extract_named_entities,
    extract_stated_criteria,
)
from extract_entities_orthographic import extract_entities_orthographic

try:
    import posthog_llm_obs as _obs
except Exception:  # pragma: no cover - observability is optional, never block a run
    _obs = None

ROSTER_PATH = "roster/e1_roster.json"
OUTPUT_DIR = "corpus/e1"
RUN_CHANNEL = "api"
SCHEMA_VERSION = "v4"

# R82 (Terry 2026-10-24, RULINGS.md R82, option B): the raw answer text of Perplexity API rows and
# of Google AI Overview rows is held in the private collector, not in this public repository.
# Counts, named-business flags, SHA-256 hashes, dates and the OpenAI and Anthropic answer text stay
# public. Reversible: PRIVATE_RAW=0 writes the text publicly again from the next run.
PRIVATE_DIR = "private_e1"
PRIVATE_RAW_ENGINES = ("perplexity",)
RAW_POLICY = "R82 2026-10-24: raw text held in the private collector"
# Full rows of this process, keyed by check_id, for rows redacted by write_row. The agreement row
# needs the answer text, which the public file no longer carries.
_FULL_IN_MEMORY = {}

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


def _sha256_text(text):
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def redact_for_public(row):
    """Returns (public_copy, full_original). The public copy holds no raw text for the held
    engines; hashes and counts come from the full text, which is computed before this runs and
    is never altered. With PRIVATE_RAW set to anything but 1, nothing is redacted."""
    if os.environ.get("PRIVATE_RAW", "1") != "1":
        return dict(row), row
    public = dict(row)
    engine = row.get("engine")
    if engine in PRIVATE_RAW_ENGINES:
        text = row.get("answer_verbatim") or ""
        public["answer_verbatim"] = ""
        public["answer_sha256"] = _sha256_text(text)
        public["answer_chars"] = len(text)
        public["answer_private"] = True
        public["raw_policy"] = RAW_POLICY
    if engine == "google_serp" and "ai_overview_text" in row:
        text = row.get("ai_overview_text") or ""
        public["ai_overview_text"] = ""
        public["ai_overview_sha256"] = _sha256_text(text)
        public["ai_overview_chars"] = len(text)
        public["ai_overview_private"] = True
        public["raw_policy"] = RAW_POLICY
    return public, row


def write_row(row, check_id):
    public, full = redact_for_public(row)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, check_id + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(public, f, indent=1, ensure_ascii=False)
    if public.get("answer_private") or public.get("ai_overview_private"):
        os.makedirs(PRIVATE_DIR, exist_ok=True)
        with open(os.path.join(PRIVATE_DIR, check_id + ".json"), "w", encoding="utf-8") as f:
            json.dump(full, f, indent=1, ensure_ascii=False)
        _FULL_IN_MEMORY[check_id] = full
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


# CHANGELOG 2026-10-13 (DQ-5): from collection runs dated 2026-10-14, extract_competitors
# returns the de-duplicated union of the bold-span names and the orthographic names, with
# headings dropped by build_edition.heading_reason. Edition 1 (through 2026-10-13) is
# unchanged: its rows keep the names they were written with, and runs dated before
# 2026-10-14 use the old rule below.
UNION_FROM = "2026-10-14"

try:
    from build_edition import heading_reason as _heading_reason
except Exception:  # pragma: no cover - the filter is optional, never block a run
    _heading_reason = None


def extract_competitors_old(business_row, text, sources):
    """Rule used through 2026-10-13: bold spans, else orthographic only when
    the bold result is empty (WO-2026-09-06-C)."""
    names = extract_named_entities(
        text, business_row["business"],
        business_row.get("variants_business_name_forms", []))
    if names:
        return names
    return extract_entities_orthographic(
        text, business_row["business"],
        business_row.get("variants_business_name_forms", []), sources)


# Plain-text names only (never applied to bold spans): places, platforms and directories, street
# addresses and one-word fragments that the capitalisation rule lets through. Found by the replay
# (scripts/replay_extractor_union.py) over 2026-10-01..2026-10-11; the Edition 1 name gate drops the
# same kinds downstream, this keeps them out of the collector's raw column from 2026-10-14.
_PLAIN_PLACES = {
    "atlanta", "branson", "charlotte", "chicago", "columbus", "dallas", "denver", "fort lauderdale", "houston",
    "joplin", "kansas city", "north kansas city", "liberty", "los angeles", "miami", "nashville", "phoenix",
    "pleasanton", "san francisco", "santa cruz", "st. charles", "st. louis", "st. peters", "tyler",
    "west palm beach", "overland park", "bay area", "ozarks", "table rock lake", "branson landing",
    "silver dollar city", "east nashville", "miami-dade county", "st. charles county", "maryland", "california",
    "florida", "missouri", "texas", "illinois", "ohio", "georgia", "arizona", "colorado", "tennessee",
    "north carolina"}
_PLAIN_PLATFORMS = {
    "airbnb", "vrbo", "yelp", "youtube", "clutch", "clutch.co", "realtor.com", "homeadvisor", "angie's list",
    "angi", "super lawyers", "zillow", "homelight", "facebook", "healthcare.gov", "bbb", "avvo",
    "google business", "google maps", "nextdoor", "thumbtack", "linkedin", "reddit", "tiktok", "instagram",
    "paypal"}
_PLAIN_FRAGMENTS = {"compare", "confirm", "shadow", "website", "strip", "lodge", "customer data",
                    "budget considerations", "critical risks", "loan estimates", "jungian"}
_STATE_ABBR = re.compile(r"[,\s]+(?:[A-Z]{2})$")
_ADDRESS = re.compile(r"\d|\b(?:Rd|Road|Ave|Avenue|Dr|Blvd|St|Suite|Ste)\b\.?$|^Suite\b")


def _plain_junk(name):
    n = " ".join(str(name).split())
    key = _STATE_ABBR.sub("", n).casefold().replace("\u2019", "'")
    key = re.sub(r"'s$", "", key)
    if key in _PLAIN_PLACES or key in _PLAIN_PLATFORMS or key in _PLAIN_FRAGMENTS:
        return True
    if _ADDRESS.search(n):
        return True
    words = set(re.findall(r"[a-z.']+", key))
    return bool(words & {"airbnb", "facebook", "yelp", "google"})


def extract_competitors_union(business_row, text, sources, gazetteer=None):
    """Rule from 2026-10-14. The bold extractor keys on **bold**; Anthropic
    answers carry bold section headings ("Research methods", "Red flags"), so
    the old fallback never ran and plain-text business names such as
    "Jiffy Lube" were dropped (DQ-5). This returns the de-duplicated union of
    both extractors, bold names first, then drops headings with the Edition 1
    heading filter (build_edition.heading_reason)."""
    variants = business_row.get("variants_business_name_forms", [])
    bold = extract_named_entities(text, business_row["business"], variants)
    plain = extract_entities_orthographic(
        text, business_row["business"], variants, sources)
    bold_set = set(bold)
    out, seen = [], set()
    for n in list(bold) + list(plain):
        if n not in bold_set and _plain_junk(n):
            continue
        key = " ".join(str(n).casefold().split())
        if not key or key in seen:
            continue
        if _heading_reason is not None and _heading_reason(n) is not None:
            continue
        seen.add(key)
        out.append(n)
    # Third pass (gazetteer): names already recorded for this market x niche by any engine in
    # earlier runs, added when the whole name appears in the text (case-insensitive).
    for n in gazetteer_matches(text, gazetteer):
        key = " ".join(n.casefold().split())
        if key not in seen:
            seen.add(key)
            out.append(n)
    return out


GAZETTEER_CAP = 300
GAZETTEER_MIN_ROWS = 2


_GAZ_INDEX = {}


def _gaz_rows(corpus_dir):
    """One scan of the corpus per process: {(market, niche): [(date, [names])]} for OK API rows."""
    import ast
    import glob
    if corpus_dir in _GAZ_INDEX:
        return _GAZ_INDEX[corpus_dir]
    idx = {}
    for path in glob.glob(os.path.join(corpus_dir, "NB-CZ-API_*.json")):
        try:
            d = json.load(open(path))
        except Exception:
            continue
        if d.get("run_status") != "OK":
            continue
        names = d.get("competitors_mentioned") or []
        if isinstance(names, str):
            try:
                names = ast.literal_eval(names)
            except Exception:
                names = []
        idx.setdefault((d.get("market"), d.get("niche")), []).append(
            (str(d.get("date_utc", "")), d.get("engine", ""), [str(x).strip() for x in names]))
    _GAZ_INDEX[corpus_dir] = idx
    return idx


def build_gazetteer(market, niche, corpus_dir="corpus/e1", before_date=None, cap=GAZETTEER_CAP):
    """Names recorded in competitors_mentioned, by any engine, for this market x niche in OK API rows
    dated before before_date (None = all). A name needs GAZETTEER_MIN_ROWS rows, headings and
    plain-text junk are dropped, most frequent first, capped at cap. Read from the corpus at run time."""
    counts, engines = {}, {}
    for date, engine, names in _gaz_rows(corpus_dir).get((market, niche), []):
        if before_date and date >= str(before_date):
            continue
        for n in set(names):
            counts[n] = counts.get(n, 0) + 1
            engines.setdefault(n, set()).add(engine)
    return gazetteer_from_counts(counts, cap, engines)


_GAZ_GENERIC = set("""reviews review recent read client clients fee fees structure liability cancellation installation utilities
marketplace families family find hotel hotels tire tires county kansas missouri general insurance marketing
track record martindale hubbell martindale-hubbell justia findlaw lawyers.com gaf master elite""".split())


def gazetteer_from_counts(counts, cap=GAZETTEER_CAP, engines=None):
    """A single-word name must be recorded by at least two engines and three rows (a lone engine's
    recurring heading or fragment is not a business); a multi-word name needs GAZETTEER_MIN_ROWS rows."""
    keep = []
    for n, c in counts.items():
        if c < GAZETTEER_MIN_ROWS or len(n) < 4 or "\n" in n:
            continue
        words = re.findall(r"[A-Za-z0-9.'&-]+", n)
        if len(words) == 1 and (c < 3 or (engines is not None and len(engines.get(n, ())) < 2)):
            continue
        if words and all(w.casefold() in _GAZ_GENERIC for w in words):
            continue
        if re.search(r"\b(?:reviews?|county)$|bar association|better business bureau|chamber of commerce|\bstate bar\b", n, re.I):
            continue
        if _heading_reason is not None and _heading_reason(n) is not None:
            continue
        if _plain_junk(n):
            continue
        keep.append((-c, n))
    keep.sort()
    return [n for _, n in keep[:cap]]


def gazetteer_matches(text, gazetteer):
    """Whole-name, case-insensitive matches. A single-word name must also appear capitalised exactly as
    recorded and must not appear as a plain lowercase word in the same text (so "Salvi" matches and
    "Cleaning" or "Taxes", ordinary words, do not)."""
    out = []
    for n in gazetteer or []:
        pat = r"(?<!\w)" + re.escape(n) + r"(?!\w)"
        if not re.search(pat, text or "", re.I):
            continue
        if not re.search(pat, text):          # exact case as recorded
            if " " not in n.strip():
                continue
        low = n.lower()
        if low != n and re.search(r"(?<!\w)" + re.escape(low) + r"(?!\w)", text):
            continue                           # also written as an ordinary lowercase phrase
        out.append(n)
    return out


def extract_competitors(business_row, text, sources, date_utc=None, gazetteer=None):
    """Competitor names for one OK answer. date_utc is the run date: runs
    dated before UNION_FROM keep the old rule (bold, else orthographic);
    runs from UNION_FROM, and calls with no date, use the union."""
    if date_utc and str(date_utc) < UNION_FROM:
        return extract_competitors_old(business_row, text, sources)
    return extract_competitors_union(business_row, text, sources, gazetteer)


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


def _observe(row, latency_s, err):
    """One PostHog AI Observability event per finalized engine row (scripts/posthog_llm_obs.py).
    Reads the row after it is written, queues a small dict, never raises, never writes."""
    if _obs is None:
        return
    try:
        _obs.record(row, latency_s, err)
    except Exception:  # pragma: no cover
        pass


def run_engine(business_row, engine, fn, date_utc, supersedes=None):
    slug = slugify(business_row["business"])
    check_id, run_index = next_run_index(date_utc, slug, engine)
    row = base_row(business_row, date_utc, check_id, engine)
    row["run"] = run_index
    if supersedes:
        # Re-ask of a FAILED cell: say so, with the real collection time, so the row never
        # reads as collected at the date in its name.
        row["rerun_supersedes"] = supersedes
        row["rerun_collected_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    import engines as _engines
    _engines.CURRENT_QUESTION_ID[0] = slugify(business_row["business"])
    _t0 = time.monotonic()
    ok, text, sources, err = fn(business_row["prompt_1"])
    _latency = time.monotonic() - _t0
    # Forward-only audit fields: which model the provider says it served, the
    # response id and usage (Perplexity since 2026-09-28, also its search-result
    # count; OpenAI, Anthropic and Gemini from the 2026-10-13 change). Set on
    # both OK and failed rows; empty when the body never parsed.
    row["engine_meta"] = dict(LAST_CALL_META.get(engine) or {})
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
        _path = write_row(row, check_id)
        _observe(row, _latency, err)
        return _path, False
    row.update({
        "run_status": "OK",
        "business_mentioned": is_mentioned(business_row, text),
        "competitors_mentioned": extract_competitors(
            business_row, text, sources, date_utc,
            build_gazetteer(business_row.get("market"), business_row.get("niche"),
                            OUTPUT_DIR, date_utc)
            if str(date_utc) >= UNION_FROM else None),
        "engine_stated_criteria": extract_stated_criteria(text),
        "sources_cited": sources,
        "factor_scores": None,
        "answer_verbatim": text,
        "session_note": RUN_CHANNEL_CAVEAT,
    })
    _path = write_row(row, check_id)
    _observe(row, _latency, err)
    return _path, True


def extract_ai_overview(payload_text):
    """Google AI Overview from Bright Data parsed SERP JSON (documented object
    "ai_overview": texts[].snippet, references[].href/title/source).
    Added 2026-10-03 [OPUS55-COS-1003A]: the parsed JSON the collector already
    pays for carries Google's AI Overview, and it was being discarded. Returns
    (present, text, references). present is True only when the documented
    object is in the payload with text; None when the payload is not parsed
    JSON (cannot tell); False when it is parsed JSON without the object.
    Text is verbatim snippets joined by newlines, never rewritten."""
    try:
        j = json.loads(payload_text or "")
        if isinstance(j, dict) and isinstance(j.get("body"), str):
            j = json.loads(j["body"])
    except (ValueError, TypeError):
        return None, "", []
    if not isinstance(j, dict):
        return None, "", []
    aio = j.get("ai_overview")
    if isinstance(aio, list):
        aio = aio[0] if aio and isinstance(aio[0], dict) else None
    if not isinstance(aio, dict):
        return False, "", []
    parts = []

    def walk(node):
        if isinstance(node, dict):
            snip = node.get("snippet")
            if isinstance(snip, str) and snip.strip():
                parts.append(snip.strip())
            for k, v in node.items():
                if k != "snippet" and isinstance(v, (list, dict)) and k != "links":
                    walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)
    walk(aio.get("texts", []))
    refs = []
    for r in aio.get("references", []) or []:
        if isinstance(r, dict) and r.get("href"):
            refs.append({"href": r.get("href"), "title": r.get("title", ""),
                         "source": r.get("source", "")})
    text = "\n".join(parts)
    return (True if text else False), text, refs


def run_serp(business_row, date_utc):
    """Writes a SERP row: who RANKS on Google for the same question.
    Paired with the engine rows, this is the rank-to-citation delta.

    WO-2026-09-06-C: google_serp is NOT an answer engine and carries no
    competitors_mentioned field at all (see base_row/write_row above -- this
    function never calls extract_competitors). It fetches a Google RESULTS
    PAGE, not a conversational answer, so there is no answer text to run a
    named-entity extractor over; organic_top_results (search-result listings)
    is the honest analogue, and it is a different measurement, ranking, not
    naming. Any published "share of rows naming a competitor, by engine"
    figure must exclude google_serp from its denominator entirely rather
    than reporting it as a 0-of-N extraction failure. Measured 2026-09-06:
    1,270 of 1,270 google_serp rows have no competitors_mentioned field,
    by design, in every one of them."""
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
            "ai_overview_present": None,
            "ai_overview_text": "",
            "ai_overview_references": [],
            "session_note": "FAILED RUN, logged per no-fabricated-rows law. "
                            "error detail: %s" % err,
        })
        return write_row(row, check_id), False
    diag = serp_diagnostics(html, organic)
    try:
        _aio = extract_ai_overview(html)
    except Exception:  # never let the AIO read cost the SERP row
        _aio = (None, "", [])
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
        # 2026-10-03: Google's AI Overview text and cited references, read from
        # the documented ai_overview object of the parsed SERP JSON. Additive
        # fields; every existing field above is unchanged.
        "ai_overview_present": _aio[0],
        "ai_overview_text": _aio[1],
        "ai_overview_references": _aio[2],
        "session_note": ("Organic results extracted mechanically from the "
                         "returned results page. Bright Data does not "
                         "document an AI Overview field, so ai_overview_"
                         "detected is BEST EFFORT and may be null. Do not "
                         "publish it as a measurement."),
    })
    return write_row(row, check_id), True


# RE-RUN ONLY THE FAILED CELLS (2026-10-24, [SONNET-BEDROCK-EXPIRY-1024]). A "cell" is one
# business x one engine on one date. On 2026-10-24 sixteen Anthropic cells FAILED when the
# Bedrock session token expired mid-run. These optional env vars (the workflow's manual-run
# inputs) re-ask ONLY those cells; a normal scheduled or manual run sets none of them and
# behaves exactly as before.
#   E1_ONLY_ENGINES   comma list of engines to ask (or "serp"); every other engine and the SERP
#                     are skipped. Required whenever E1_ONLY_FAILED is set.
#   E1_ONLY_FAILED    1/true: ask a cell only if its row for the date is FAILED and no OK row
#                     exists for it yet, so a repeat dispatch is idempotent. Never touches an
#                     existing row: the answer lands as the next _rN file, the FAILED row stays.
#   E1_DATE_UTC       YYYY-MM-DD, only with E1_ONLY_FAILED: the date whose failed cells to
#                     re-ask (default today). Never later than today. Rows keep that date in
#                     their name and date_utc, and carry rerun_collected_at_utc with the real
#                     collection time plus rerun_supersedes (the FAILED row's check_id).
_KNOWN_ONLY = ("perplexity", "gemini", "openai", "anthropic", "serp")


def _truthy(v):
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def parse_rerun_options(env, today_utc):
    """Returns (opts, error). opts is None for a normal run. Never raises."""
    only = [x.strip().lower() for x in str(env.get("E1_ONLY_ENGINES", "")).split(",")
            if x.strip()]
    failed = _truthy(env.get("E1_ONLY_FAILED"))
    date = str(env.get("E1_DATE_UTC", "")).strip()
    if not only and not failed and not date:
        return None, ""
    bad = [x for x in only if x not in _KNOWN_ONLY]
    if bad:
        return None, "E1_ONLY_ENGINES has unknown engine(s): %s" % ", ".join(bad)
    if failed and not only:
        return None, "E1_ONLY_FAILED requires E1_ONLY_ENGINES (name the engine(s) to re-ask)"
    if date:
        if not failed:
            return None, "E1_DATE_UTC is only allowed together with E1_ONLY_FAILED"
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            return None, "E1_DATE_UTC must be YYYY-MM-DD"
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return None, "E1_DATE_UTC is not a real date"
        if date > today_utc:
            return None, "E1_DATE_UTC is in the future"
    return {"only": only, "failed": failed, "date": date or today_utc}, ""


def cell_files(date_utc, slug, engine):
    """All corpus files of one cell: the base row and any _rN, in run order."""
    base = "NB-CZ-API_%s_%s_%s" % (date_utc, slug, engine)
    out = [base] + ["%s_r%d" % (base, n) for n in range(2, 60)]
    paths = [os.path.join(OUTPUT_DIR, c + ".json") for c in out]
    found = []
    for c, pth in zip(out, paths):
        if os.path.exists(pth):
            found.append((c, pth))
        elif c != base:
            break
    return found


def failed_cell_check_id(date_utc, slug, engine):
    """check_id of the cell's latest FAILED row if the cell has no OK row, else None."""
    last_failed = None
    for cid, pth in cell_files(date_utc, slug, engine):
        try:
            with open(pth, "r", encoding="utf-8") as f:
                status = json.load(f).get("run_status")
        except (OSError, ValueError):
            continue
        if status == "OK":
            return None
        if status == "FAILED":
            last_failed = cid
    return last_failed


def main():
    """Run the collector, then send the queued PostHog events (best-effort, in a finally so a
    crash mid-run still reports the calls that did happen). The return value is untouched."""
    try:
        return _main()
    finally:
        if _obs is not None:
            try:
                _obs.flush()
            except Exception:  # pragma: no cover
                pass


def _main():
    date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rerun, rerun_err = parse_rerun_options(os.environ, date_utc)
    if rerun_err:
        print("ERROR: %s. Nothing written." % rerun_err)
        return 2  # a bad manual input fails the run loudly instead of passing silently
    if rerun:
        date_utc = rerun["date"]
    try:
        roster = load_active_roster(ROSTER_PATH)
    except (OSError, ValueError) as exc:
        print("ERROR: could not read roster: %s" % exc)
        return 0

    engines = dict(ENGINES)
    # The env var for every engine follows one convention: <ENGINE>_API_KEY.
    # v8 lesson: an inline dict here listed only two engines, so adding an
    # adapter to ENGINES without touching this line crashed the whole run
    # with a KeyError before a single call was made (run #9, 2026-08-05).
    # Deriving the name means a new adapter can never be forgotten here.
    available = [k for k in engines
                 if os.environ.get("%s_API_KEY" % k.upper(), "").strip()
                 or (k == "gemini" and os.environ.get("GEMINI_API_KEY_FREE", "").strip())]
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
    if len(roster) > 1 and not rerun:
        offset = sum(ord(c) for c in date_utc) % len(roster)
        roster = roster[offset:] + roster[:offset]
        print("roster rotated by %d for %s" % (offset, date_utc))

    wrote = ok_n = fail_n = 0
    if rerun:
        print("RERUN MODE | date %s | engines %s | only failed cells: %s"
              % (date_utc, ",".join(rerun["only"]), rerun["failed"]))
    for br in roster:
        engine_rows = []
        # OPTIONAL PER-ENTRY LIMITS (2026-10-08, [SONNET-RECORD-1008E-S1], S1-7). A roster
        # entry may carry "engines": [..names..] to be asked of those engines only, and
        # "serp": false to skip the Google results page. Neither key means what it always
        # meant: every available engine plus the SERP. An empty list asks no engine.
        only = br.get("engines")
        for name, fn in engines.items():
            if isinstance(only, list) and name not in only:
                continue
            supersedes = None
            if rerun:
                if name not in rerun["only"]:
                    continue
                if rerun["failed"]:
                    supersedes = failed_cell_check_id(date_utc, slugify(br["business"]), name)
                    if not supersedes:
                        continue
                    print("rerun cell: %s %s (supersedes %s)" % (slugify(br["business"]), name, supersedes))
            path, good = run_engine(br, name, fn, date_utc, supersedes)
            if good:
                # The public file for a redacted row has no answer text, so the agreement uses
                # the full in-memory copy written by this run (see write_row).
                with open(path, "r", encoding="utf-8") as _f:
                    _disk = json.load(_f)
                engine_rows.append(_FULL_IN_MEMORY.get(os.path.basename(path)[:-len(".json")], _disk))
            wrote += 1
            ok_n += 1 if good else 0
            fail_n += 0 if good else 1
        if serp_on and br.get("serp") is not False and (not rerun or "serp" in rerun["only"]):
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