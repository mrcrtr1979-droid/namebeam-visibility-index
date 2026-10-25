"""
NAMEBEAM CLIENT CHECK RUNNER (dispatch only, one-off, dated).

Plain-language notes for a non-coder reading along:

1. Reads one JSON file: a list of items, each one client business and the one
   buyer question a customer would type. Shape:
   [{"check_id": "...", "client": "...", "market": "city, state",
     "niche": "...", "question": "<buyer question verbatim>"}]
2. Asks every item of every engine (perplexity, gemini, openai, anthropic by
   default) in ONE dated run. The question goes to the engine verbatim, the same
   way the daily record sends its prompt_1.
3. Writes one JSON per item per engine to client_checks/<run_date>/, hashes the
   answer text with SHA-256, and writes a RECEIPT.md and a SUMMARY.json next to
   them. This is the product behind the Namebeam Client Check: one client
   business, one buyer question, four engines, dated, SHA-256 per answer.
4. It reuses the daily record's own adapters (scripts/engines.py) and name
   extractors (scripts/e1_runner.py), so the rows are comparable with the
   record. It NEVER writes to corpus/e1, and it refuses an output folder under
   corpus/.
5. An engine that errors writes run_status FAIL with the error class and the
   redacted error text. A fabricated answer is the one unforgivable bug.
6. Optional --repeats N (default 1, which is the behaviour and the output
   described above, byte for byte). With N above 1 each engine is asked the same
   question N times in the one run. Run 1 keeps its usual file name; runs 2 to N
   are saved next to it as <check_id>_<engine>_run<k>.json, each with its own
   answer text and SHA-256. The RECEIPT then says, per engine, "named in k of N
   runs" and lists the first three businesses named in every run. A run that
   failed is counted as failed, never as "not named": the line then reads
   "k of M answered runs" and states how many runs failed. Why it exists: an
   identical request repeated on the same day changes the named businesses in
   most comparisons (status/returns/NSF_PRELIM_RESULTS_1023.md in the private
   Brain), so one answer is a weak measurement. Cost: N times the API calls per
   engine.

No secret is ever printed, logged, or written to a file. Keys are read from the
environment by the adapters; every error string passes through engines.redact().

Run from the repo root:
    python -I scripts/client_check.py --input client_checks/inputs/AGENCY_1027.json --dry-run
    python -I scripts/client_check.py --input <file> [--engines perplexity,gemini,openai,anthropic]
    python -I scripts/client_check.py --input <file> --repeats 3
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

# -I (isolated mode) does not put this script's folder on sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import e1_runner  # noqa: E402  (extractors, is_mentioned, slugify, RUN_CHANNEL_CAVEAT)
import engines as _engines_mod  # noqa: E402
from engines import ENGINES, LAST_CALL_META, redact  # noqa: E402
from extract_named_entities import extract_stated_criteria  # noqa: E402

OUT_ROOT = "client_checks"
DEFAULT_ENGINES = ("perplexity", "gemini", "openai", "anthropic")
MAX_REPEATS = 10
SCHEMA_VERSION = "v4-cc1"
ROW_TYPE = "client_check"
RUN_CHANNEL = "api"
REQUIRED_KEYS = ("check_id", "client", "market", "niche", "question")
CHECK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_STOP = {"the", "and", "of", "llc", "inc", "pc", "co", "corp", "ltd", "a", "an"}

FIRST_THREE_NOTE = (
    "first_three_named: the first three businesses the answer names, ordered by first "
    "appearance. Built from the record's name extractor (scripts/e1_runner.py "
    "extract_competitors) plus numbered headings, then filtered: place names, headings and "
    "generic phrases, sentence fragments, directories and utilities, and equipment "
    "manufacturers listed as brands (Carrier, Trane, Lennox and similar) are dropped, and so "
    "is a name that sits inside a longer name in the same list. The client, or an intake "
    "alias, is listed in its position when the answer names it. Mechanical, not a human "
    "read: an unusual business name can be missed and a look-alike can slip through. "
    "competitors_mentioned is the record's own field (client excluded) and is not altered "
    "by this rule.")


# ---------------------------------------------------------------- input


def load_items(path):
    """Read and validate the input file. Raises ValueError with a plain message."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list) or not data:
        raise ValueError("input must be a non-empty JSON list")
    seen = set()
    for i, it in enumerate(data):
        if not isinstance(it, dict):
            raise ValueError("item %d is not an object" % i)
        for k in REQUIRED_KEYS:
            if not isinstance(it.get(k), str) or not it[k].strip():
                raise ValueError("item %d missing non-empty string %r" % (i, k))
        if not CHECK_ID_RE.match(it["check_id"]):
            raise ValueError("item %d check_id %r has characters outside A-Za-z0-9._-"
                             % (i, it["check_id"]))
        aliases = it.get("aliases")
        if aliases is not None:
            if (not isinstance(aliases, list) or not all(isinstance(a, str) and len(a.strip()) >= 4 for a in aliases)):
                raise ValueError("item %d aliases must be a list of names of 4 or more characters" % i)
        if it["check_id"] in seen:
            raise ValueError("duplicate check_id %r" % it["check_id"])
        seen.add(it["check_id"])
    return data


# ---------------------------------------------------------------- matching


def _norm(s):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (s or "").lower().replace("&", " and ")).split())


def _stem(w):
    """Trailing-s fold only, so "Milestones DFW" matches "Milestone DFW"."""
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def client_named_exact(client, text, aliases=None):
    """Exact string match, case-insensitive. Same rule as the AG-PPLX-AGENCY-CHECK file.
    aliases (optional, from intake): other names buyers use for the same business, for
    example "Coolray" for "Coolray Heating and Air". The 2026-10-24 dry run showed why:
    an engine wrote "Coolray" and the full-name match said no. Each alias is matched the
    same way as the client name and must be 4 or more characters (checked at load)."""
    if not client or not text:
        return False
    low = text.lower()
    return any(n.strip().lower() in low for n in [client] + list(aliases or []) if n and n.strip())


def client_named_loose(client, text):
    """Looser flag, for a human to confirm. True when the client name appears with
    punctuation ignored (so "Doerner Saunders Daniel & Anderson" matches the comma
    spelling), or when every distinctive word of the name (a trailing s ignored,
    so "Milestones DFW" matches "Milestone DFW") sits on one line of the
    answer. It can match a look-alike, so it never replaces the exact flag."""
    if not client or not text:
        return False
    nc, nt = _norm(client), _norm(text)
    if nc and (" " + nc + " ") in (" " + nt + " "):
        return True
    toks = [_stem(t) for t in nc.split() if t not in _STOP and t != "and"]
    if not toks:
        return False
    for line in text.splitlines():
        words = {_stem(w) for w in _norm(line).split()}
        if all(t in words for t in toks):
            return True
    return False


def _business_row(client, extra_variants=None):
    return {"business": client, "variants_business_name_forms": list(extra_variants or [])}


# ---- first_three_named: a business filter on top of the record's extractor.
# The record's extractor (e1_runner.extract_competitors) is NOT changed. This block only
# filters its output and adds a few candidates it does not see (numbered headings, a
# longer "X Heating & Air" form, the client or an intake alias when named in the text).
# Rule of thumb: when unsure whether a phrase is a business, drop it; never invent a name.

_CONNECTORS = {"and", "of", "the", "&", "de", "la", "du", "van", "von"}
# lone/headline words that never make a business name on their own
_GENERIC = set("""
homeowners homeowner companies company contractors contractor replacement replacements conditioning
factory authorized authorised certified dealer dealers installer installers services service
favorites favourites favorite tips tip search questions question warranties warranty matter matters
reviews review quotes quote rebates rebate season calculation load furnace furnaces hvac heating
cooling air plumbing roofing repair installation system systems options option best top local
well well-established established metro greater area areas county suburbs intown downtown midtown
north south east west northern southern eastern western central general key note notes next step
steps summary why what how when which who where expect look need needs ask about check get read
verify confirm insist compare choose find call make consider avoid require prefer request
brands brand equipment energy efficient credit credits improvement home price pricing cost costs
red flags flag process timeline typical credentials experience licensed insured
""".split())
# a first word like these makes the phrase an instruction or heading, not a name
_LEAD_VERBS = set("""
get check ask read verify confirm insist compare choose find call make consider avoid require
prefer request look expect compare tips tip why what how when which who where do does don't
is are be this that these those there here your you i'd i'll we it its they their
""".split())
# words that mark a service business name (a name needs one more, distinctive, word besides these)
_BIZ = set("""
heating cooling air plumbing roofing roofers electric electrical mechanical services service systems
company contractors contractor construction restoration solutions group law firm attorneys lawyers
partners associates remodeling exteriors builders conditioning hvac comfort design inc llc llp pllc
pc co corp ltd
""".split())
_MFR_PHRASES = ["american standard", "owens corning", "day & night", "day and night", "carrier",
                "trane", "lennox", "rheem", "ruud", "goodman", "daikin", "york", "bryant", "gaf",
                "certainteed", "amana", "mitsubishi"]
_MFR_FILLER = set("""dealer dealers authorized authorised factory certified installer installers
comfort specialist specialists elite master contractor brand brands equipment systems and""".split())
# directories, utilities, standards bodies and local areas that are not competitors
_NOT_BUSINESS_PHRASES = {"georgia power", "atlanta gas light", "cobb emc", "north fulton",
                         "better business bureau", "consumer reports", "energy star",
                         "angie's list", "angies list"}
_NOT_BUSINESS_WORDS = {"google", "yelp", "angi", "bbb", "kudzu", "nextdoor", "facebook",
                       "homeadvisor", "thumbtack", "reddit", "nate", "ahri", "epa", "gwinnett",
                       "dekalb", "fulton", "cobb", "cherokee", "forsyth"}
_ABBREV = r"(?:St|Mt|Mr|Mrs|Ms|Dr|Co|Inc|Ltd|Jr|Sr|No|Ft|vs)"
_SENTENCE_BREAK = re.compile(r"\b(?!%s\b)[A-Za-z]{2,}[.!?]\s+\S" % _ABBREV)
_TITLE_TOKEN = re.compile(r"^[A-Z0-9][A-Za-z0-9'&.]*(?:-[A-Z0-9][A-Za-z0-9']*)*$")
_NUM_HEADING = [
    re.compile(r"^\s{0,3}#{1,6}\s*\d+[.)]\s+(.+?)\s*#*\s*$", re.M),
    re.compile(r"^\s*\*\*\d+[.)]\s+(.+?)\*\*", re.M),
]
_BIZ_TAIL = re.compile(r"\s*&\s*(?:Air|Cooling|Plumbing|Heating|Electric|Roofing)(?:\s+Conditioning)?\b")


def _tokens(name):
    return [t for t in re.split(r"[\s,;/]+", name) if t]


def _clean_candidate(name):
    n = " ".join(str(name).replace("*", " ").replace("_", " ").split())
    n = re.sub(r"\s+[-\u2013\u2014]\s+.*$", "", n)      # "Name - descriptor"
    n = re.sub(r"\s*\([^)]*\)\s*$", "", n)               # trailing "(Atlanta-based)"
    return n.strip(" \t:-.,")


def _place_words(market):
    words = set()
    for part in re.split(r",", market or ""):
        for w in _norm(part).split():
            words.add(w)
    place = getattr(e1_runner, "_PLAIN_PLACES", set())
    for p in place:
        for w in _norm(p).split():
            words.add(w)
    return words


def _is_manufacturer_only(name):
    low = " " + name.lower().replace("/", " ").replace(";", " ").replace(",", " ") + " "
    for m in sorted(_MFR_PHRASES, key=len, reverse=True):
        low = re.sub(r"(?<![a-z])%s(?![a-z])" % re.escape(m), " ", low)
    rest = [w for w in re.split(r"[\s&]+", low) if w and w not in _MFR_FILLER and w != "and"]
    had = low.strip() != name.lower().strip()
    return had and not rest


def _business_like(name, city_norm, place_words):
    """True when `name` reads as a local business. Conservative: unsure means False."""
    n = _clean_candidate(name)
    if not n or len(n) < 3 or len(n) > 80:
        return False
    if re.search(r"[:;?!\"\u201c\u201d]", n) or "\n" in n or _SENTENCE_BREAK.search(n):
        return False
    toks = _tokens(n)
    if not toks or re.search(r"(?:^|\s)\d+(?:-\d+)?(?=\s|$)", n):
        return False
    lows = [t.lower().strip(".,'") for t in toks]
    if lows[0] in _LEAD_VERBS:
        return False
    if _is_manufacturer_only(n):
        return False
    nn = _norm(n)
    if nn in _NOT_BUSINESS_PHRASES or any(w in _NOT_BUSINESS_WORDS for w in nn.split()):
        return False
    for t, lw in zip(toks, lows):
        if lw in _CONNECTORS:
            continue
        if not _TITLE_TOKEN.match(t):
            return False
    words = [w for w in nn.split() if w not in _CONNECTORS and w != "and"]
    if not words:
        return False
    has_biz = any(w in _BIZ for w in words)
    distinct = [w for w in words if w not in _GENERIC and w not in _BIZ and w not in place_words]
    if not distinct:
        # "The Air Company": a business word pair led by "The" is a name; a lone word or heading is not
        if not (lows[0] == "the" and has_biz and len(words) >= 2):
            return False
    if city_norm and nn.split()[0] == city_norm.split()[0] and not has_biz:
        return False                    # "Atlanta for Furnace Replacement", "Atlanta. They"
    if not has_biz and len(words) > 4:
        return False                    # a long capitalised phrase with no business word
    return True


def _extra_candidates(text):
    """Names the record's extractor does not return: numbered headings such as
    "### 1. Conditioned Air Systems" or "**1. Cool Air Design**"."""
    out = []
    for rx in _NUM_HEADING:
        for m in rx.finditer(text or ""):
            out.append(m.group(1))
    return out


def _extend_tail(name, text):
    """Extend "One Hour Heating" to "One Hour Heating & Air" when the answer itself writes it."""
    m = re.search(re.escape(name) + _BIZ_TAIL.pattern, text or "")
    return text[m.start():m.end()] if m else name


def first_three_named(text, sources, market, date_utc, client=None, aliases=None):
    """The first three businesses an answer names, in order of first appearance. The record's
    extractor feeds it; this filters out places, headings, generic phrases, equipment
    manufacturers listed as brands and sentence fragments, and shows the client (or an intake
    alias) in its position when the answer names it. A name that is not in the answer text
    is never returned."""
    text = text or ""
    raw = list(e1_runner.extract_competitors(_business_row(""), text, sources, date_utc, None))
    raw += _extra_candidates(text)
    low = text.lower()
    city = _norm((market or "").split(",")[0])
    place_words = _place_words(market)
    keep = []
    seen = set()
    for n in raw:
        n = _extend_tail(_clean_candidate(n), text)
        key = _norm(n)
        if not key or key in seen:
            continue
        if not _business_like(n, city, place_words):
            continue
        seen.add(key)
        keep.append(n)
    own = []
    for c in [client] + list(aliases or []):
        c = (c or "").strip()
        if not c or _norm(c) in {_norm(o) for o in own}:
            continue
        m = re.search(r"(?<!\w)" + re.escape(c) + r"(?!\w)", text, re.I)
        if m:
            own.append(text[m.start():m.end()])
    own_keys = {_norm(o) for o in own}
    keep = [n for n in keep if _norm(n) not in own_keys] + own
    # position of a name: its first appearance, or the first appearance of a shorter form inside it
    def contains(longer, shorter):
        return longer != shorter and (" " + shorter + " ") in (" " + longer + " ")
    pos = {}
    for n in keep:
        p = low.find(n.lower())
        pos[_norm(n)] = p if p >= 0 else 10 ** 9
    final = []
    for n in keep:
        nn = _norm(n)
        if any(contains(_norm(m), nn) for m in keep):
            continue
        final.append(n)
    order = sorted(range(len(final)), key=lambda i: (pos[_norm(final[i])], i))
    return [final[i] for i in order][:3]


def usage_tokens(usage):
    """(input, output, total) tokens from whichever usage shape the engine returned,
    None where not stated. Never raises."""
    if not isinstance(usage, dict):
        return None, None, None
    def pick(*keys):
        for k in keys:
            v = usage.get(k)
            if isinstance(v, (int, float)):
                return int(v)
        return None
    tin = pick("prompt_tokens", "input_tokens", "promptTokenCount")
    tout = pick("completion_tokens", "output_tokens", "candidatesTokenCount")
    tot = pick("total_tokens", "totalTokenCount")
    if tot is None and tin is not None and tout is not None:
        tot = tin + tout
    return tin, tout, tot


# ---------------------------------------------------------------- one request


def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def run_one(item, engine, fn, date_utc, run_index=1, repeats=1):
    """One item x one engine -> the row dict. Never raises, never invents an answer.
    run_index is the same-day repeat number (the record's own "run" field); repeats is
    how many were requested. repeats is written to the row only when it is above 1, so
    a default run keeps the exact row it always had."""
    br = _business_row(item["client"])
    row = {
        "check_id": item["check_id"],
        "date_utc": date_utc,
        "business": item["client"],
        "niche": item["niche"],
        "market": item["market"],
        "row_type": ROW_TYPE,
        "prompt_variant": 1,
        "run": run_index,
        "prompt_text": item["question"],
        "engine": engine,
        "run_channel": RUN_CHANNEL,
        "schema_version": SCHEMA_VERSION,
    }
    if repeats > 1:
        row["repeats_requested"] = repeats
    # call_gemini picks its model from this id; a client check is never on the
    # continuity panel, so it uses the free-tier rest model and its own pacing.
    _engines_mod.CURRENT_QUESTION_ID[0] = e1_runner.slugify(item["check_id"])
    LAST_CALL_META.pop(engine, None)
    err_class = None
    try:
        ok, text, sources, err = fn(item["question"])
    except Exception as exc:  # an adapter should not raise; if one does, log it
        ok, text, sources = False, "", []
        err = redact("%s: %s" % (type(exc).__name__, exc))
        err_class = type(exc).__name__
    row["engine_meta"] = dict(LAST_CALL_META.get(engine) or {})
    if ok and not (text or "").strip():
        ok, err = False, "EmptyAnswer: engine returned ok with no answer text"
        err_class = "EmptyAnswer"
    if not ok:
        err = redact(err)
        if err_class is None:
            err_class = "QuotaBlocked" if str(err).startswith("QUOTA: ") else "EngineError"
        row.update({
            "run_status": "FAIL",
            "error_class": err_class,
            "error_detail": err,
            "business_mentioned": None,
            "competitors_mentioned": [],
            "engine_stated_criteria": [],
            "sources_cited": [],
            "factor_scores": None,
            "answer_verbatim": "",
            "answer_sha256": None,
            "client_named": None,
            "client_named_loose": None,
            "first_three_named": [],
            "session_note": "FAILED RUN, logged per no-fabricated-rows law. %s"
                            % e1_runner.RUN_CHANNEL_CAVEAT,
        })
        return row
    row.update({
        "run_status": "OK",
        "business_mentioned": e1_runner.is_mentioned(br, text),
        "competitors_mentioned": e1_runner.extract_competitors(br, text, sources, date_utc, None),
        "engine_stated_criteria": extract_stated_criteria(text),
        "sources_cited": sources,
        "factor_scores": None,
        "answer_verbatim": text,
        "answer_sha256": sha256_text(text),
        "client_named": client_named_exact(item["client"], text, item.get("aliases")),
        "client_named_loose": client_named_loose(item["client"], text),
        "first_three_named": first_three_named(text, sources, item["market"], date_utc,
                                               item["client"], item.get("aliases")),
        "session_note": e1_runner.RUN_CHANNEL_CAVEAT,
    })
    return row


# ---------------------------------------------------------------- outputs


def pick_run_dir(root, date_utc):
    """Same-day re-runs ACCUMULATE: a second run gets <date>_r2, never an overwrite."""
    base = os.path.join(root, date_utc)
    if not os.path.exists(base):
        return base
    n = 2
    while os.path.exists("%s_r%d" % (base, n)):
        n += 1
    return "%s_r%d" % (base, n)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
        f.write("\n")


def _cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def answer_filename(check_id, engine, run_index=1):
    """Run 1 keeps the name it has always had; run k above 1 gets a _run<k> suffix."""
    if run_index == 1:
        return "%s_%s.json" % (check_id, engine)
    return "%s_%s_run%d.json" % (check_id, engine, run_index)


def named_in_text(k, answered, failed, requested):
    if answered == 0:
        return "no answer: all %d runs failed" % requested
    if failed == 0:
        return "named in %d of %d runs" % (k, requested)
    return ("named in %d of %d answered runs (%d of %d runs FAILED, not counted as not named)"
            % (k, answered, failed, requested))


def named_in(runs, requested):
    """The repeat tally for one engine on one item. runs = that engine's rows for the item.
    A FAIL row is a failed call: it is counted under failed and never under not named.
    k is counted over OK rows only, so the honest denominator is `answered`."""
    answered = [r for r in runs if r["run_status"] == "OK"]
    failed = len(runs) - len(answered)
    exact = sum(1 for r in answered if r["client_named"] is True)
    loose = sum(1 for r in answered if r["client_named_loose"] is True)
    return {"requested": requested, "answered": len(answered), "failed": failed,
            "exact": exact, "loose": loose,
            "text_exact": named_in_text(exact, len(answered), failed, requested),
            "text_loose": named_in_text(loose, len(answered), failed, requested)}


def build_receipt(run_dir_name, date_utc, input_path, input_sha, items, rows, engines_used, repeats=1):
    if repeats > 1:
        return build_receipt_repeats(run_dir_name, date_utc, input_path, input_sha, items, rows,
                                     engines_used, repeats)
    by = {(r["check_id"], r["engine"]): r for r in rows}
    ok = sum(1 for r in rows if r["run_status"] == "OK")
    L = []
    L.append("# Namebeam Client Check receipt, %s" % date_utc)
    L.append("")
    L.append("- Run folder: %s" % run_dir_name)
    L.append("- Input: %s (SHA-256 %s)" % (input_path, input_sha))
    L.append("- Engines: %s" % ", ".join(engines_used))
    L.append("- Items: %d | Requests: %d | OK: %d | FAIL: %d"
             % (len(items), len(rows), ok, len(rows) - ok))
    L.append("- Method: each question sent verbatim to each engine, one dated run, API channel "
             "(no memory, no personalization; results can differ from logged-in consumer apps). "
             "SHA-256 is of the answer text as saved (UTF-8). Full answer text is in the per-answer "
             "JSON file named below, not repeated here.")
    L.append("- Client Appears in Answer: exact = the client string, case-insensitive, in the answer. "
             "loose = punctuation-insensitive match or every distinctive word on one line; a human "
             "confirms it. " + FIRST_THREE_NOTE)
    L.append("- Request Cost: tokens as returned by the engine (in / out / total). No price is applied here.")
    L.append("")
    for n, it in enumerate(items, 1):
        L.append("## %d. %s" % (n, it["check_id"]))
        L.append("")
        L.append("- **Question:** %s" % it["question"])
        L.append("- **Target Client:** %s (%s, %s)" % (it["client"], it["market"], it["niche"]))
        L.append("")
        L.append("| Engine | Run Status | Client Appears (exact / loose) | First Three Businesses Named | "
                 "SHA-256 of Answer Text | Request Cost (tokens in/out/total) | Served Model | File |")
        L.append("|---|---|---|---|---|---|---|---|")
        for eng in engines_used:
            r = by.get((it["check_id"], eng))
            if r is None:
                continue
            fname = "%s_%s.json" % (it["check_id"], eng)
            if r["run_status"] == "OK":
                tin, tout, tot = usage_tokens((r.get("engine_meta") or {}).get("usage"))
                cost = "%s / %s / %s" % tuple("n/a" if v is None else v for v in (tin, tout, tot))
                named = "%s / %s" % ("yes" if r["client_named"] else "no",
                                     "yes" if r["client_named_loose"] else "no")
                first3 = "; ".join(r["first_three_named"]) or "none extracted"
                sha = "`%s`" % r["answer_sha256"]
                status = "OK"
            else:
                cost, named, first3, sha = "n/a", "n/a", "n/a", "n/a"
                status = "FAIL (%s)" % r.get("error_class")
            meta = r.get("engine_meta") or {}
            model = meta.get("served_model") or meta.get("requested_model") or "n/a"
            L.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % tuple(
                _cell(x) for x in (eng, status, named, first3, sha, cost, model, fname)))
        L.append("")
    return "\n".join(L) + "\n"


def _run_cells(r):
    """The per-answer cells shared by the receipt rows: (status, named, first3, sha, cost, model)."""
    if r["run_status"] == "OK":
        tin, tout, tot = usage_tokens((r.get("engine_meta") or {}).get("usage"))
        cost = "%s / %s / %s" % tuple("n/a" if v is None else v for v in (tin, tout, tot))
        named = "%s / %s" % ("yes" if r["client_named"] else "no",
                             "yes" if r["client_named_loose"] else "no")
        first3 = "; ".join(r["first_three_named"]) or "none extracted"
        sha = "`%s`" % r["answer_sha256"]
        status = "OK"
    else:
        cost, named, first3, sha = "n/a", "n/a", "n/a", "n/a"
        status = "FAIL (%s)" % r.get("error_class")
    meta = r.get("engine_meta") or {}
    model = meta.get("served_model") or meta.get("requested_model") or "n/a"
    return status, named, first3, sha, cost, model


def build_receipt_repeats(run_dir_name, date_utc, input_path, input_sha, items, rows, engines_used, repeats):
    """Receipt for --repeats above 1: per item, a tally table (named in k of N runs, per
    engine) then one row per run (status, first three businesses, SHA-256, file)."""
    runs_by = {}
    for r in rows:
        runs_by.setdefault((r["check_id"], r["engine"]), []).append(r)
    for v in runs_by.values():
        v.sort(key=lambda r: r["run"])
    ok = sum(1 for r in rows if r["run_status"] == "OK")
    L = []
    L.append("# Namebeam Client Check receipt, %s" % date_utc)
    L.append("")
    L.append("- Run folder: %s" % run_dir_name)
    L.append("- Input: %s (SHA-256 %s)" % (input_path, input_sha))
    L.append("- Engines: %s" % ", ".join(engines_used))
    L.append("- Items: %d | Requests: %d | OK: %d | FAIL: %d | Repeats per engine: %d"
             % (len(items), len(rows), ok, len(rows) - ok, repeats))
    L.append("- Method: each question sent verbatim to each engine, %d times in one dated run, API channel "
             "(no memory, no personalization; results can differ from logged-in consumer apps). "
             "The %d requests per engine per question are identical, and every run is saved and hashed on "
             "its own. SHA-256 is of the answer text as saved (UTF-8). Full answer text is in the per-answer "
             "JSON file named below, not repeated here. Run 1 keeps the usual file name; run k above 1 adds "
             "_run<k> before .json." % (repeats, repeats))
    L.append("- Named in k of N runs: counted over the runs that returned an answer. A run that FAILED is "
             "counted as failed and never as not named; when any run failed the line says so.")
    L.append("- Client Appears in Answer: exact = the client string, or any alias given at intake, case-insensitive, in the answer. "
             "loose = punctuation-insensitive match or every distinctive word on one line; a human "
             "confirms it. " + FIRST_THREE_NOTE)
    L.append("- Request Cost: tokens as returned by the engine (in / out / total), per run. No price is applied here.")
    L.append("")
    for n, it in enumerate(items, 1):
        L.append("## %d. %s" % (n, it["check_id"]))
        L.append("")
        L.append("- **Question:** %s" % it["question"])
        L.append("- **Target Client:** %s (%s, %s)" % (it["client"], it["market"], it["niche"]))
        L.append("")
        L.append("| Engine | Client Named, Exact | Client Named, Loose | Runs Answered | Runs Failed |")
        L.append("|---|---|---|---|---|")
        for eng in engines_used:
            runs = runs_by.get((it["check_id"], eng))
            if not runs:
                continue
            t = named_in(runs, repeats)
            L.append("| %s | %s | %s | %s | %s |" % tuple(
                _cell(x) for x in (eng, t["text_exact"], t["text_loose"], t["answered"], t["failed"])))
        L.append("")
        L.append("| Engine | Run | Run Status | Client Appears (exact / loose) | First Three Businesses Named | "
                 "SHA-256 of Answer Text | Request Cost (tokens in/out/total) | Served Model | File |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for eng in engines_used:
            for r in runs_by.get((it["check_id"], eng), []):
                status, named, first3, sha, cost, model = _run_cells(r)
                fname = answer_filename(it["check_id"], eng, r["run"])
                L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % tuple(
                    _cell(x) for x in (eng, r["run"], status, named, first3, sha, cost, model, fname)))
        L.append("")
    return "\n".join(L) + "\n"


def build_summary(run_dir_name, date_utc, input_path, input_sha, items, rows, engines_used, repeats=1):
    per_engine = {}
    for e in engines_used:
        er = [r for r in rows if r["engine"] == e]
        per_engine[e] = {
            "requests": len(er),
            "ok": sum(1 for r in er if r["run_status"] == "OK"),
            "fail": sum(1 for r in er if r["run_status"] != "OK"),
            "client_named_exact": sum(1 for r in er if r["client_named"] is True),
            "client_named_loose": sum(1 for r in er if r["client_named_loose"] is True),
            "served_models": sorted({str((r.get("engine_meta") or {}).get("served_model")
                                         or (r.get("engine_meta") or {}).get("requested_model"))
                                     for r in er if r["run_status"] == "OK"}),
        }
    by = {(r["check_id"], r["engine"]): r for r in rows if r["run"] == 1}
    runs_by = {}
    for r in rows:
        runs_by.setdefault((r["check_id"], r["engine"]), []).append(r)
    out_items = []
    for it in items:
        d = {"check_id": it["check_id"], "client": it["client"], "market": it["market"],
             "question": it["question"], "engines": {}}
        for e in engines_used:
            r = by.get((it["check_id"], e))
            if r is None:
                continue
            if repeats > 1:
                runs = sorted(runs_by[(it["check_id"], e)], key=lambda x: x["run"])
                d["engines"][e] = {
                    "named_in": named_in(runs, repeats),
                    "runs": [{"run": x["run"], "file": answer_filename(it["check_id"], e, x["run"]),
                              "run_status": x["run_status"], "client_named": x["client_named"],
                              "client_named_loose": x["client_named_loose"],
                              "answer_sha256": x["answer_sha256"],
                              "first_three_named": x["first_three_named"],
                              "error_class": x.get("error_class")} for x in runs]}
                continue
            d["engines"][e] = {"run_status": r["run_status"], "client_named": r["client_named"],
                               "client_named_loose": r["client_named_loose"],
                               "answer_sha256": r["answer_sha256"],
                               "first_three_named": r["first_three_named"],
                               "error_class": r.get("error_class")}
        out_items.append(d)
    out = {"run_date_utc": date_utc, "run_folder": run_dir_name,
           "schema_version": SCHEMA_VERSION, "input_path": input_path, "input_sha256": input_sha,
           "engines": list(engines_used), "items": len(items), "requests": len(rows),
           "ok": sum(1 for r in rows if r["run_status"] == "OK"),
           "fail": sum(1 for r in rows if r["run_status"] != "OK"),
           "per_engine": per_engine, "results": out_items}
    if repeats > 1:
        out["repeats_per_engine"] = repeats      # key absent on a default run, so its summary is unchanged
    return out


# ---------------------------------------------------------------- main


def under_corpus(path):
    p = os.path.abspath(path).replace("\\", "/")
    return "/corpus/" in p + "/" or p.endswith("/corpus")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Namebeam Client Check runner (dispatch only).")
    ap.add_argument("--input", required=True, help="JSON list of client check items")
    ap.add_argument("--engines", default=",".join(DEFAULT_ENGINES),
                    help="comma list from: " + ",".join(DEFAULT_ENGINES))
    ap.add_argument("--dry-run", action="store_true", help="build the request list, call nothing")
    ap.add_argument("--run-date", default=None, help="override the UTC run date (YYYY-MM-DD)")
    ap.add_argument("--outdir", default=OUT_ROOT, help="output folder; the dated run folder goes under it (default client_checks)")
    ap.add_argument("--repeats", type=int, default=1,
                    help="ask each engine the same question this many times in the one run "
                         "(default 1 = one answer, today's behaviour; max %d). N times the API calls." % MAX_REPEATS)
    args = ap.parse_args(argv)
    if not 1 <= args.repeats <= MAX_REPEATS:
        print("ERROR: --repeats must be a whole number from 1 to %d" % MAX_REPEATS)
        return 2
    repeats = args.repeats

    if under_corpus(args.outdir):
        print("ERROR: refusing to write under corpus/. Client checks never touch the daily record.")
        return 2
    try:
        items = load_items(args.input)
    except (OSError, ValueError) as exc:
        print("ERROR: could not read input: %s" % exc)
        return 2
    wanted = [e.strip() for e in args.engines.split(",") if e.strip()]
    unknown = [e for e in wanted if e not in ENGINES]
    if not wanted or unknown:
        print("ERROR: unknown or empty engines %r; available: %s" % (unknown or wanted, ",".join(ENGINES)))
        return 2
    wanted = list(dict.fromkeys(wanted))
    date_utc = args.run_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_utc):
        print("ERROR: --run-date must be YYYY-MM-DD")
        return 2
    with open(args.input, "rb") as f:
        input_sha = hashlib.sha256(f.read()).hexdigest()

    requests_list = [(it, e, k) for it in items for e in wanted for k in range(1, repeats + 1)]
    run_dir = pick_run_dir(args.outdir, date_utc)
    run_dir_name = run_dir.replace(os.sep, "/")
    print("client_check %s | input %s | items %d | engines %s%s | requests %d | run folder %s"
          % (date_utc, args.input, len(items), ",".join(wanted),
             " | repeats %d" % repeats if repeats > 1 else "", len(requests_list), run_dir))
    keys = {"perplexity": ("PERPLEXITY_API_KEY",), "gemini": ("GEMINI_API_KEY_FREE", "GEMINI_API_KEY"),
            "openai": ("OPENAI_API_KEY",), "anthropic": ("ANTHROPIC_API_KEY",)}
    for e in wanted:
        present = any(os.environ.get(k, "").strip() for k in keys.get(e, ()))
        print("  engine %-10s key %s" % (e, "present" if present else "MISSING"))
    if args.dry_run:
        if repeats > 1:
            print("DRY RUN: no engine called, nothing written. %d items x %d engines x %d repeats = %d requests."
                  % (len(items), len(wanted), repeats, len(requests_list)))
        else:
            print("DRY RUN: no engine called, nothing written. %d items x %d engines = %d requests."
                  % (len(items), len(wanted), len(requests_list)))
        return 0

    os.makedirs(run_dir, exist_ok=True)
    rows = []
    for it, eng, k in requests_list:
        row = run_one(it, eng, ENGINES[eng], date_utc, run_index=k, repeats=repeats)
        write_json(os.path.join(run_dir, answer_filename(it["check_id"], eng, k)), row)
        rows.append(row)
        print("  %s %-10s %s%s%s" % (it["check_id"], eng, "run %d " % k if repeats > 1 else "",
                                     row["run_status"],
                                     "" if row["run_status"] == "OK" else " " + str(row.get("error_class"))))
    with open(os.path.join(run_dir, "RECEIPT.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(build_receipt(run_dir_name, date_utc, args.input, input_sha, items, rows, wanted, repeats))
    write_json(os.path.join(run_dir, "SUMMARY.json"),
               build_summary(run_dir_name, date_utc, args.input, input_sha, items, rows, wanted, repeats))
    ok = sum(1 for r in rows if r["run_status"] == "OK")
    print("files written: %d answers + RECEIPT.md + SUMMARY.json (ok %d, fail %d)"
          % (len(rows), ok, len(rows) - ok))
    return 0


if __name__ == "__main__":
    sys.exit(main())
