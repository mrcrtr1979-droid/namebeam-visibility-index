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


# ---- first_three_named: a business filter on top of the answer's own structure.
# The record's extractor (e1_runner.extract_competitors) is NOT changed. Candidates come from two
# places: (1) the answer's list structure (numbered headings, a bold or plain name that leads a
# bullet, a bold name inside a sentence), which finds names in any niche without a vocabulary, and
# (2) the record's extractor, accepted only when the phrase reads as a multi-word business name.
# Every candidate then passes one filter. The word lists below are small and documented; the
# structural rules do most of the work. Rule of thumb: when unsure whether a phrase is a business,
# drop it; never invent a name (a name is returned only if the answer text itself contains it).

_CONNECTORS = {"and", "of", "the", "&", "de", "la", "du", "van", "von", "der", "den", "del", "di", "da", "le"}
# lone or headline words that never make a business name on their own
_GENERIC = set("""
homeowners homeowner companies company contractors contractor replacement replacements conditioning
factory authorized authorised certified dealer dealers installer installers services service
favorites favourites favorite tips tip search questions question warranties warranty matter matters
reviews review quotes quote rebates rebate season calculation load furnace furnaces hvac heating
cooling air plumbing roofing repair installation system systems options option best top local
well well-established established metro greater area areas county counties suburbs intown downtown midtown
north south east west northern southern eastern western central general key note notes next step
steps summary why what how when which who where expect look need needs ask about check get read
verify confirm insist compare choose find call make consider avoid require prefer request
brands brand equipment energy efficient credit credits improvement home price pricing cost costs
red flags flag process timeline typical credentials experience licensed insured
personal injury criminal civil trial family divorce dui medical malpractice cosmetic luxury private
independent practices practice facilities facility providers provider resources resource methods method
recommendation recommendations overview location locations focus vibe reputation specialty specialties
details features figures highlights pros cons popular dining fine casual upscale contemporary
northeast northwest southeast southwest ne nw se sw special occasion breakfast brunch
attorney attorneys lawyer lawyers firm firms hospital-affiliated
comprehensive online holistic patient centric track record information detox continuum defense shops
directories extras aftercare repeated eclectic standout legal specialization availability consultations
communication compatibility accreditation licensing certifications evaluate corporate litigation
high-net-worth complex full-service big winner champion business speech occupational
summer winter fall spring autumn specialized fleet high-tech modern cocktails reviews labor industries
approved references sources testimonials considerations factors full historic
""".split())
_LEAD_PREPS = set("near in on at by for with from to across around within throughout between beside".split())
_PLACE_LAST = {"city", "county", "district", "hill", "hills", "village", "heights", "falls", "township",
               "area", "neighborhood", "corridor", "downtown"}
_STREET_LAST = {"ave", "avenue", "st", "street", "rd", "road", "blvd", "boulevard", "dr", "drive", "ln",
                "lane", "hwy", "highway", "pkwy", "parkway", "way", "ct", "court", "pl", "place", "suite",
                "ste", "fwy", "expy"}
_US_STATE_ABBR = set("al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv nh nj nm "
                     "ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy dc".split())
_NUM_NAME = re.compile(r"(?<![\w$#.,:/-])\d{1,3}\s+(?:[A-Z][\w'\u2019&.-]*)(?:\s+[A-Z][\w'\u2019&.-]*){0,3}")
_TRADE = {"heating", "cooling", "plumbing", "air", "electric", "electrical", "roofing", "siding", "windows",
          "gutters", "painting", "flooring", "remodeling", "construction", "restoration", "conditioning"}
_CAP_RUN = re.compile(r"[A-Z][\w'\u2019.-]*(?:\s+(?:&|and|of|the|de|la|[A-Z][\w'\u2019.-]*))*")
_AMP_NAME = re.compile(r"(?<![\w'\u2019.-])[A-Z][A-Za-z'\u2019.-]+\s+&\s+[A-Z][A-Za-z'\u2019.-]+(?:\s+&\s+[A-Z][A-Za-z'\u2019.-]+)?")
_CHAIN_LIST = re.compile(r"\b(?:chains|shops|companies|firms|franchises|providers|clinics|practices|retailers|"
                         r"stores|dealerships|restaurants)\s*\(([^()]{3,160})\)", re.I)
_WEAK_BIZ = {"office", "offices", "center", "centers", "centre", "group", "partners", "associates"}
_PREP_BEFORE = re.compile(r"\b(?:near|in|around|within|across|throughout|from|to)\s+(?:the\s+)?$", re.I)
_NOUN_SUFFIX = re.compile(r"(?:tion|tions|ing|ity|ities|ment|ments|ance|ence|ness|ship|ies)$")
# a first word like these makes the phrase an instruction or heading, not a name
_LEAD_VERBS = set("""
get check ask read verify confirm insist compare choose find call make consider avoid require
prefer request look expect tips tip why what how when which who where do does don't
is are be this that these those there here your you i'd i'll we it its they their
see use using review reviews research finding choosing seek schedule interview discuss
""".split())
# first word of a heading such as "Top-Rated Firms" or "Highly Regarded Facilities"
_HEAD_ADJ = set("""
best top highly well popular local major large larger notable leading recommended other another
additional national regional great good strong trusted reputable established several some many
more most main common quick final next prominent
""".split())
# words that mark a business name (a name needs one more, distinctive, word besides these, unless
# it sits next to a place word: "Houston Roofing & Construction")
_BIZ = set("""
heating cooling air plumbing roofing roofers electric electrical mechanical services service systems
company contractors contractor construction restoration solutions group law firm attorneys lawyers
partners associates remodeling exteriors builders conditioning hvac comfort design inc llc llp pllc
pc co corp ltd offices office dental dentistry dentists dentist clinic clinics pediatrics pediatric
hospital health healthcare center centers centre studio studios recovery therapy kitchen brewing
brewery brewpub tavern bistro cafe grill bakery restaurant restaurants garage auto automotive tire
tires collision mechanics landscaping landscapes landscape nursery lawn care orthodontics
medical counsel legal realty properties insurance
""".split())
_MFR_PHRASES = ["american standard", "owens corning", "day & night", "day and night", "carrier",
                "trane", "lennox", "rheem", "ruud", "goodman", "daikin", "york", "bryant", "gaf",
                "certainteed", "amana", "mitsubishi", "hearth & home"]
# words that turn a manufacturer name into a dealer program or certification, not a business
_MFR_FILLER = set("""dealer dealers authorized authorised factory certified installer installers
comfort specialist specialists elite master contractor contractors brand brands equipment systems
and premier platinum preferred select shingle shinglemaster technologies inc""".split())
# directories, rating and licensing bodies, platforms, utilities and local areas that are not competitors
_NOT_BUSINESS_PHRASES = {"georgia power", "atlanta gas light", "cobb emc", "north fulton",
                         "better business bureau", "consumer reports", "energy star",
                         "angie's list", "angies list", "super lawyers", "best lawyers",
                         "best law firms", "chambers usa", "legal aid", "find a dentist",
                         "joint commission", "national helpline", "state bar", "legal aid society"}
_NOT_BUSINESS_WORDS = {"google", "yelp", "angi", "angie", "angies", "bbb", "kudzu", "nextdoor", "facebook",
                       "homeadvisor", "thumbtack", "reddit", "nate", "ahri", "epa", "aaa", "gwinnett",
                       "dekalb", "fulton", "cobb", "cherokee", "forsyth",
                       "avvo", "martindale", "hubbell", "justia", "findlaw", "superlawyers", "healthgrades",
                       "zocdoc", "webmd", "houzz", "carfax", "repairpal", "samhsa", "carf", "iicrc",
                       "oem", "amazon", "zillow", "expertise", "instagram", "linkedin", "tiktok", "youtube"}
# bodies that license, rate or refer: any name carrying one of these words is not a competitor
_ORG_WORDS = {"association", "board", "commission", "department", "division", "bureau", "council",
              "society", "college", "academy", "chamber", "helpline", "hotline", "locator", "referral",
              "directory", "registry", "committee", "agency", "bar", "court", "courts", "administration",
              "judiciary", "legislature"}
_LABEL_FIRST = set("""best why known services service overview location locations focus vibe
reputation specialty specialties key note tip pros cons address phone website details features
highlights pro recommendation summary years food hours price cost lead standout figure figures
what who how where when""".split())
_LEGAL_SUFFIX = {"llc", "llp", "pc", "pllc", "pa", "inc", "ltd", "lp", "plc", "dds", "dmd", "md", "esq",
                 "jr", "sr", "ii", "iii", "iv", "corp", "co", "cpa", "do", "phd", "rn"}
_ABBREV = r"(?:St|Mt|Mr|Mrs|Ms|Dr|Co|Inc|Ltd|Jr|Sr|No|Ft|vs)"
_SENTENCE_BREAK = re.compile(r"\b(?!%s\b)[A-Za-z]{2,}[.!?]\s+\S" % _ABBREV)
_TITLE_TOKEN = re.compile(r"^[A-Z0-9][A-Za-z0-9'\u2019&.]*(?:-[A-Z0-9][A-Za-z0-9'\u2019]*)*$")
_NUM_HEADING = [
    re.compile(r"^\s{0,3}#{1,6}\s*\d+[.)]\s+(.+?)\s*#*\s*$", re.M),
    re.compile(r"^\s*\*\*\d+[.)]\s+(.+?)\*\*", re.M),
]
_BIZ_TAIL = re.compile(r"\s*&\s*(?:Air|Cooling|Plumbing|Heating|Electric|Roofing)(?:\s+Conditioning)?\b")
_MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!&|~<>])")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_BULLET = re.compile(r"^(\s*)(?:[-*\u2022\u2713\u2714]|\d+[.)])\s+(.*)$")
_HEAD = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_PERSON_TOK = re.compile(r"^(?:[A-Z]\.?|[A-Z][a-z'\u2019\-]+|(?:Mc|Mac|Di|De|Le|La)[A-Z][a-z]+|O['\u2019][A-Z][a-z]+)$")


def _unescape(text):
    """Markdown escapes such as 'Dumar \\& Son' read as plain characters; model glitches such as
    '<bos>' are dropped."""
    t = _MD_ESCAPE.sub(r"\1", text or "")
    return re.sub(r"<(?:bos|eos|pad|unk)>", " ", t)


def _fold(s):
    return " ".join((s or "").replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"')
                    .replace("\u201d", '"').lower().split())


def _tokens(name):
    return [t for t in re.split(r"[\s,;/]+", name) if t]


def _words(name):
    return [w for w in _norm(name).split() if w not in _CONNECTORS and w != "and"]


def _has_biz(name):
    return any(w in _BIZ for w in _words(name))


def _suffix_tok(tok):
    return tok.lower().replace(".", "").strip(",;") in _LEGAL_SUFFIX


def _person_like(name):
    """'Mark A. Anderson', 'Dr. Sam Romano', 'Katherine Hall, DDS': two to four name tokens, no
    business word. A lawyer or dentist named alone is a listed practitioner."""
    toks = [t for t in re.split(r"[\s,]+", name or "") if t]
    toks = [t for t in toks if not _suffix_tok(t) and t.lower().strip(".") != "dr"]
    if not 2 <= len(toks) <= 4:
        return False
    if not all(_PERSON_TOK.match(t) for t in toks):
        return False
    low = [t.lower().strip(".") for t in toks]
    return not _has_biz(name) and not any(w in _GENERIC or w in _HEAD_ADJ for w in low)


def _firm_like(name):
    return _has_biz(name) or ("&" in (name or "") and len(_tokens(name)) >= 3)


def _keep_final_dot(n):
    return bool(re.search(r"(?:\b[A-Z](?:\.[A-Z])+|\b(?:Inc|Jr|Sr|Ltd|Co|Corp|Dr|St|Mt))\.$", n))


def _tidy(n):
    n = " ".join(str(n).replace("*", " ").replace("_", " ").split())
    n = re.sub(r"^\d+[.)]\s+", "", n)                    # "1. Name"
    n = re.sub(r"[\u2713\u2714]", " ", n).strip()
    n = re.sub(r"[:;,\s]+$", "", n)
    if n.endswith(".") and not _keep_final_dot(n):
        n = n[:-1]
    return n.strip()


def _split_commas(n):
    """'Eli Gabay, Solomon, Sherman, Gabay, Briskin & Sherman' -> the firm; 'Beasley, Allen, Crow &
    Miles' stays one law-firm name; 'Flanagan Law, P.C.' keeps its suffix; 'A & B, C D, E F' is a
    list of three names; 'Name, Place' keeps the name."""
    segs = [s.strip() for s in re.split(r",\s+", n) if s.strip()]
    if len(segs) <= 1:
        return [n]
    suffix = ""
    while len(segs) > 1 and all(_suffix_tok(t) for t in segs[-1].split()):
        suffix = ", " + segs[-1] + suffix
        segs.pop()
    if len(segs) == 1:
        return [segs[0] + suffix]
    if _person_like(segs[0]):
        rest = ", ".join(segs[1:])
        if rest[:1].isupper():
            return _split_commas(rest + suffix) if not suffix else [rest + suffix]
        return [segs[0] + suffix]
    if len(segs) == 2:                                    # "PV Heating, Cooling & Plumbing"
        w0 = _norm(segs[0]).split()
        w1 = _norm(segs[1]).split()
        if w0 and w1 and w0[-1] in _TRADE and w1[0] in _TRADE and not suffix:
            return [n]
    singles = all(len(_tokens(s)) == 1 for s in segs[:-1])
    if singles and ("&" in segs[-1] or re.search(r"\band\b", segs[-1])):
        return [", ".join(segs) + suffix]
    if len(segs) == 2:
        x, y = segs
        if _firm_like(x) and not _firm_like(y):
            return [x + suffix]
        if _firm_like(y) and not _firm_like(x):
            return [y + suffix]
    return [s + (suffix if i == len(segs) - 1 else "") for i, s in enumerate(segs)]


def _clean_names(raw):
    """One raw span (a heading, a bold span, a bullet lead) -> the business names it carries."""
    n = _tidy(raw)
    if not n:
        return []
    # trailing "(note)": a person's firm in the brackets wins, else the brackets are dropped
    m = re.search(r"\s*\(([^()]*)\)\s*$", n)
    if m:
        head, inner = n[:m.start()].strip(), m.group(1).strip()
        n = inner if (_person_like(head) and _firm_like(inner) and not _person_like(inner)) else head
    n = re.sub(r"\s*\([^()]*\)", "", n).strip()
    n = re.sub(r"\s*\([^()]*$", "", n).strip()           # an unclosed bracket
    # "Name - descriptor"; "Person - Firm"
    parts = [p.strip() for p in re.split(r"\s+[-\u2013\u2014]\s+|\s*[\u2013\u2014]\s*", n) if p.strip()]
    if parts:
        n = parts[1] if (len(parts) > 1 and _person_like(parts[0]) and parts[1][:1].isupper()
                         and _firm_like(parts[1])) else parts[0]
    # "Person of Firm", "Person at Firm"
    m = re.match(r"^(.+?)\s+(?:of|at)\s+(.+)$", n)
    if m and _person_like(m.group(1)) and _firm_like(m.group(2)):
        n = m.group(2)
    n = _tidy(n)
    low_words = [w for w in re.findall(r"[A-Za-z']+", n) if w[0].islower() and w not in _CONNECTORS and w != "to"]
    if len(n.split()) >= 5 and low_words:                # a bold sentence: take the names inside it
        runs = []
        for m in _CAP_RUN.finditer(n):
            r = re.sub(r"(?:\s+(?:&|and|of|the|de|la))+$", "", m.group(0)).strip()
            if len(r.split()) >= 2 and ("&" in r or _has_biz(r)):
                runs.append(_tidy(r))
        return runs
    alts = [a.strip() for a in re.split(r"\s+/\s+|\s+or\s+(?=[A-Z0-9])", n) if a.strip()]
    alts = alts[:1] + [a for a in alts[1:] if _firm_like(a)]    # "SERVPRO of X / Lemoyne": drop place alternates
    out = []
    for a in alts:
        out.extend(_tidy(x) for x in _split_commas(a))
    if len(out) > 1 and any(_firm_like(x) for x in out):
        out = [x for x in out if not _person_like(x)]
    return [x for x in out if x]


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


def _digits_ok(toks):
    """'10 Barrel Brewing', '314 Roofing Solutions', 'A-1 Automotive', 'Alpha 4 Real Roofing', '4D
    Construction'. A year or a phone-style number is not a name."""
    for i, t in enumerate(toks):
        if not re.search(r"\d", t):
            continue
        if re.match(r"^\d{1,3}$", t):
            if (i == 0 and len(toks) >= 2) or len(toks) >= 3:
                continue
            return False
        if re.match(r"^[A-Za-z]{1,2}-?\d{1,2}$|^\d{1,2}[A-Za-z]{1,2}$", t):
            continue
        return False
    return True


def _business_like(name, city_norm, place_words, strict=False):
    """True when `name` reads as a business. Conservative: unsure means False. strict=True (a
    phrase found inside prose rather than in a list position) also needs a business word or '&'."""
    n = _tidy(name)
    if not n or len(n) < 3 or len(n) > 90:
        return False
    n = n.replace('"', "")
    if re.search(r"[:;?!\u201c\u201d]", n) or "\n" in n or _SENTENCE_BREAK.search(n):
        return False
    if re.search(r"https?:|www\.|\.(?:com|org|net|gov|edu|io)\b", n, re.I):
        return False
    toks = _tokens(n)
    if not toks or not _digits_ok(toks):
        return False
    lows = [t.lower().strip(".,'\u2019") for t in toks]
    first = re.split(r"[-\u2013]", lows[0])[0]
    if first in _LEAD_VERBS or first in _HEAD_ADJ or lows[0] in _LEAD_PREPS:
        return False
    if re.match(r"^[A-Z]{2}$", toks[-1]) and lows[-1] in _US_STATE_ABBR and lows[-1] not in _LEGAL_SUFFIX \
            and (len(lows) < 2 or lows[-2] not in _CONNECTORS):
        return False                    # "HVAC Post Falls ID": a search phrase ending in a state code
    if lows[-1] in _STREET_LAST or (lows[-1] in _PLACE_LAST and len(toks) <= 2):
        return False
    if _is_manufacturer_only(n):
        return False
    nn = _norm(n)
    if nn in _NOT_BUSINESS_PHRASES or any(p in nn for p in _NOT_BUSINESS_PHRASES):
        return False
    nwords = nn.split()
    if any(w in _NOT_BUSINESS_WORDS for w in nwords) or any(w in _ORG_WORDS for w in nwords):
        return False
    for ti, (t, lw) in enumerate(zip(toks, lows)):
        if lw in _CONNECTORS or (lw == "to" and ti > 0 and len(toks[ti - 1]) == 1):
            continue                    # "A to Z Pediatric Dentistry"
        if not _TITLE_TOKEN.match(t) and not re.match(r"^[a-z]{1,3}[A-Z]", t):
            return False
    if lows[-1] in _CONNECTORS or lows[0] in ("and", "of", "&"):
        return False
    words = [w for w in nwords if w not in _CONNECTORS and w != "and"]
    if not words:
        return False
    if len(toks) == 1:
        if re.match(r"^[A-Z]{2,5}s?$", toks[0]):
            return False                # DMV, BCBAs, FACDL: an acronym, not a business
        if lows[0] in place_words or lows[0] in _GENERIC or lows[0] in _LABEL_FIRST:
            return False
        if _NOUN_SUFFIX.search(lows[0]) and lows[0] not in _BIZ:
            return False                # "Certifications", "Availability": an ordinary noun
    has_biz = any(w in _BIZ for w in words)
    has_place = any(w in place_words for w in words)
    has_city = any(w in city_norm.split() for w in words)
    distinct = [w for w in words if w not in _GENERIC and w not in _BIZ and w not in place_words]
    if not distinct and has_biz and re.match(r"^[A-Z]{2}$", toks[0]) and toks[0].lower() in _US_STATE_ABBR \
            and len(toks) >= 3:
        distinct = [toks[0].lower()]    # "AZ Injury Law": a state code used as part of the name
    if not distinct:
        # "Houston Roofing & Construction", "The Air Company": business words next to a place word or
        # led by "The" are a name; a lone word or a heading is not
        nbiz = sum(1 for w in words if w in _BIZ and w not in _WEAK_BIZ)
        if not ((nbiz >= 1 and has_city and len(words) >= 2) or (nbiz >= 2 and lows[0] == "the")):
            return False
    amp = "&" in n or " and " in n.lower()
    if not has_biz and len(words) > (9 if amp else 5):
        return False                    # a long capitalised phrase with no business word
    if strict and len(toks) < 2:
        return False
    if strict and not (has_biz or "&" in toks):
        return False
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


def _label_set(t):
    """Bold field labels: bold text ending in a colon that repeats in the answer ("Best for:",
    "Why them:"), once per listed business."""
    counts = {}
    for m in _BOLD.finditer(t):
        inner = m.group(1).strip()
        after = t[m.end():m.end() + 1]
        if inner.endswith(":") or after == ":":
            k = _fold(inner.rstrip(": "))
            counts[k] = counts.get(k, 0) + 1
    return {k for k, c in counts.items() if c >= 2}


def _is_label(inner, labels):
    k = _fold(inner.rstrip(": "))
    first = re.split(r"[^a-z]+", k)[0] if k else ""
    return k in labels or first in _LABEL_FIRST


def _structure_candidates(t, city, place_words):
    """(raw span, line index) in reading order: numbered headings that name a business (not a
    category that lists businesses beneath it), bold or plain leads of bullets, bold names in
    sentences. A person's firm named right after a bold person ("**Eli Gabay**, Solomon, Sherman")
    is added."""
    labels = _label_set(t)
    lines = t.split("\n")
    kinds = []
    for ln in lines:
        h = _HEAD.match(ln)
        b = _BULLET.match(ln)
        kinds.append(("h", len(h.group(1)), h.group(2)) if h else ("b", len(b.group(1)), b.group(2)) if b
                     else ("p", 0, ln.strip()))

    def lead_bold(content):
        m = re.match(r"^(?:\d+[.)]\s+)?\*\*(.+?)\*\*(.*)$", content)
        return (m.group(1), m.group(2)) if m else None

    def names_below(i, level):
        for j in range(i + 1, len(lines)):
            kj = kinds[j]
            if kj[0] == "h" and kj[1] <= level:
                return False
            if kj[0] == "h":
                if re.match(r"^\*\*.+\*\*$", kj[2].strip()):
                    return True
                continue
            lb = lead_bold(kj[2])
            if lb and not _is_label(lb[0], labels) and not lb[0].strip().endswith(":") \
                    and _business_like(_clean_names(lb[0])[0] if _clean_names(lb[0]) else "", city, place_words) \
                    and not _person_like((_clean_names(lb[0]) or [""])[0]):
                return True
            if kj[0] == "b" and not lb and kj[2][:1].isupper():
                lead = re.split(r"\s+[\u2013\u2014-]\s+|:\s|\s\(", kj[2])[0]
                cl = _clean_names(lead)
                if cl and _business_like(cl[0], city, place_words):
                    return True
        return False

    out = []
    firm_lvl = None                                      # level of the last accepted numbered heading
    for i, (kind, lvl, content) in enumerate(kinds):
        child = "child" if firm_lvl is not None else False
        if kind == "h":
            if firm_lvl is not None and lvl <= firm_lvl:
                firm_lvl = None
            c = content.strip()
            numbered = re.match(r"^(?:\*\*)?\d+[.)]\s+(.*)$", c)
            if numbered:
                if not names_below(i, lvl):
                    out.append((numbered.group(1), i, False))
                    firm_lvl = lvl
            elif re.match(r"^\*\*.+\*\*:?$", c):
                one = len(_tokens(re.sub(r"[*:]", " ", c))) < 2
                out.append((c, i, True if one else False))   # an un-numbered bold heading; a single word needs a business word
            continue
        lb = lead_bold(content)
        lead_firm = False                               # the line leads with a bold firm (not a person, not a label)
        if lb and kind in ("b", "p"):
            inner, tail = lb
            it = _tokens(inner.strip())
            lead_firm = not _is_label(inner, labels) and (
                not _person_like(inner.strip()) or (len(it) >= 3 and not re.search(r"\b(?:Dr|[A-Z])\.", inner)))
            if not _is_label(inner, labels):
                firm = None
                if _person_like(inner.strip()):
                    m = re.match(r"^\s*,\s+([A-Z][^\u2013\u2014(\n]*?)(?:\s+[\u2013\u2014-]\s|\s*[(:.]\s|\s*$)", tail)
                    if m:
                        firm = m.group(1)
                if firm:
                    out.append((firm, i, False))        # "**Eli Gabay**, Solomon, Sherman, ...": the firm, not the lawyer
                else:
                    out.append((inner, i, child))
            rest = tail
        elif kind == "b":
            lead = re.split(r"\s+[\u2013\u2014-]\s+|:\s|\s\(|\s*[\u2013\u2014]\s*", content)[0]
            if lead[:1].isupper() and (len(_tokens(lead)) >= 2 or re.search(r"[A-Za-z]", lead) and lead != lead.title().split()[0]):
                out.append((lead, i, child))            # a plain bullet lead: one word alone is not enough
            rest = content
        else:
            rest = content
        for m in _BOLD.finditer(rest):
            inner = m.group(1)
            if lead_firm and _person_like(inner.strip()):
                continue                                # a lawyer named inside a bullet about a firm
            if _is_label(inner, labels) or inner.strip().endswith(":") or rest[m.end():m.end() + 1] == ":":
                continue                            # a bold field label inside a sentence
            out.append((inner, i, False))
    return out


def first_three_named(text, sources, market, date_utc, client=None, aliases=None):
    """The first three businesses an answer names, in order of first appearance. The answer's own
    structure feeds it (numbered headings, bold or plain names leading bullets, bold names in
    sentences), with the record's extractor as a second source for multi-word names in prose; this
    filters out places, headings, field labels, advice lines, directories and rating bodies,
    equipment manufacturers listed as brands and sentence fragments, and shows the client (or an
    intake alias) in its position when the answer names it. A name that is not in the answer text is
    never returned."""
    text = _unescape(text or "")
    low = _fold(text)
    city = _norm((market or "").split(",")[0])
    place_words = _place_words(market)

    plain = re.sub(r"https?://\S+|www\.\S+", " ", text)      # links do not count as prose

    def lower_word(w):
        """True when the answer also writes this single word in lower case (an ordinary word)."""
        return re.search(r"(?<![A-Za-z0-9'\-])%s(?![A-Za-z0-9'\-])" % re.escape(w.lower()), plain) is not None

    raw = []                                    # (name, from_structure)
    for span, _, kind in _structure_candidates(text, city, place_words):
        for n in _clean_names(span):
            if kind == "child" and _person_like(n):
                continue                                # a practitioner listed under a firm heading
            raw.append((n, "heading" if kind is True else True))
    for m in _CHAIN_LIST.finditer(text):                # "national chains (Firestone, Midas, Jiffy Lube)"
        for part in re.split(r",\s*|\s+and\s+|\s+or\s+", m.group(1)):
            part = _tidy(part)
            if part[:1].isupper():
                raw.append((part, True))
    for m in _AMP_NAME.finditer(text):
        name = m.group(0)
        before = text[max(0, m.start() - 40):m.start()]
        pm = re.search(r"(?<![\w'\u2019.-])([A-Z][A-Za-z'\u2019.-]+),\s+$", before)
        if pm and not re.search(r"[A-Z][\w'\u2019.-]*\s+$", before[:pm.start()]):
            name = pm.group(1) + ", " + name
        raw.append((name, False))
    for m in _NUM_NAME.finditer(text):
        raw.append((m.group(0).strip(), False))
    for n in list(e1_runner.extract_competitors(_business_row(""), text, sources, date_utc, None)) \
            + _extra_candidates(text):
        for c in _clean_names(n):
            raw.append((c, False))
    keep, seen, weak = [], set(), []
    n_struct = len({_norm(n) for n, st in raw if st is True and _business_like(n, city, place_words)})
    for n, struct in raw:
        if struct is False and _person_like(n):
            weak.append(n)                      # a person found only in prose: only inside a list of names
            continue
        if struct is False and n_struct >= 3:
            tk = _tokens(n)
            if not (2 <= len(tk) <= 4 and _norm(n).split()[-1] in _BIZ and _norm(n).split()[-1] not in _WEAK_BIZ
                    and " and " not in " " + _fold(n) + " "):
                continue                        # a list-structured answer: its list is the answer, not prose asides
        n = _extend_tail(n, text)
        key = _norm(n)
        if not key or key in seen:
            continue
        if not _business_like(n, city, place_words, strict=(struct is not True)):
            if struct is False and len(_tokens(n)) >= 2 and _business_like(n, city, place_words):
                weak.append(n)                  # no business word: only accepted inside a list of names
            continue
        if struct is False and _PREP_BEFORE.search(text[max(0, low.find(_fold(n)) - 16):max(0, low.find(_fold(n)))]):
            continue                            # "near the Perimeter Center area": a place in prose
        if len(_tokens(n)) == 1 and lower_word(n):
            continue                            # an ordinary lower-case word in the answer, not a name
        if _fold(n) not in low:
            continue                            # never return a name the answer does not contain
        seen.add(key)
        keep.append(n)
    for n in weak:                                      # "Coolray (...), One Hour Heating & Air (...), and Aire Serv"
        key, p = _norm(n), low.find(_fold(n))
        if key in seen or p < 0 or _fold(n) not in low:
            continue
        for k in keep:
            e = low.find(_fold(k)) + len(_fold(k))
            gap = low[e:p]
            if e <= p and len(gap) <= 60 and not re.search(r"[.!?\n]", gap) and re.search(r"(?:,|\band)\s+(?:and\s+)?$", gap):
                seen.add(key)
                keep.append(n)
                break
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

    # position of a name: its first appearance
    def contains(longer, shorter):
        return longer != shorter and (" " + shorter + " ") in (" " + longer + " ")
    pos = {}
    for n in keep:
        p = low.find(_fold(n))
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
                 "SHA-256 of Answer Text | Request Cost (tokens in/out/total) | Served Model | Rail | File |")
        L.append("|---|---|---|---|---|---|---|---|---|")
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
            rail = meta.get("rail") or "n/a"  # which key or project answered (never a key value)
            L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % tuple(
                _cell(x) for x in (eng, status, named, first3, sha, cost, model, rail, fname)))
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
                 "SHA-256 of Answer Text | Request Cost (tokens in/out/total) | Served Model | Rail | File |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for eng in engines_used:
            for r in runs_by.get((it["check_id"], eng), []):
                status, named, first3, sha, cost, model = _run_cells(r)
                rail = (r.get("engine_meta") or {}).get("rail") or "n/a"  # which key or project answered (never a key value)
                fname = answer_filename(it["check_id"], eng, r["run"])
                L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % tuple(
                    _cell(x) for x in (eng, r["run"], status, named, first3, sha, cost, model, rail, fname)))
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
