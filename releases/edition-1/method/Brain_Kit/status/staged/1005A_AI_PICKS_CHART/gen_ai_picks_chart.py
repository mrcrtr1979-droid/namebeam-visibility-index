#!/usr/bin/env python3
"""AI Picks Chart generator. Python 3 stdlib only, zero model calls, no network, no git.
Reuses the name gate from 0929C_ACTOR/gen_citation_index.py (normalize, keep_name, is_platform,
is_place, is_junk, is_proper_noun) and its quarantine file.

Usage:
  python3 gen_ai_picks_chart.py [--week YYYY-MM-DD] [--csv PATH] [--out-dir DIR] [--quarantine CSV]
  python3 gen_ai_picks_chart.py --selftest
Default week: latest complete Monday-Sunday week in the CSV (its Sunday is on or before the last CSV date).
"""
import argparse, csv, html, json, os, re, sys, tempfile
from collections import defaultdict
from datetime import date, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ACTOR = os.path.join(os.path.dirname(_HERE), "0929C_ACTOR")
sys.path.insert(0, _ACTOR)
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "0929B_MARKET_PANELS"))
import gen_citation_index as gci  # noqa: E402

B_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
DEFAULT_CSV = os.path.join(B_ROOT, "corpus", "namebeam", "Namebeam_E1_Dataset_LATEST.csv")
DEFAULT_OUT = os.path.join(_HERE, "out")
DEFAULT_QUARANTINE = os.path.join(B_ROOT, "status", "returns", "COMET_1004", "SERP_DRIFT_QUARANTINE_2026-10-04.csv")

ENGINES = ["openai", "gemini", "perplexity", "anthropic"]
ENGINE_LABEL = {"openai": "OpenAI", "gemini": "Gemini", "perplexity": "Perplexity", "anthropic": "Anthropic"}
PPLX_CHANGE = "2026-09-28"
MIN_DATE = "2026-09-01"
SKIP_MARKETS = {"us nationwide"}
TOP_N = 10
BANNED_WORDS = ("best", "top-rated", "guaranteed")


def week_start(d):
    x = date.fromisoformat(d)
    return (x - timedelta(days=x.weekday())).isoformat()


def week_end(ws):
    return (date.fromisoformat(ws) + timedelta(days=6)).isoformat()


def add_weeks(ws, n):
    return (date.fromisoformat(ws) + timedelta(days=7 * n)).isoformat()


def clean_text(s):
    s = s.replace("—", "-").replace("–", "-").replace("−", "-")
    return re.sub("[←-⯿☀-➿\U0001F000-\U0001FFFF️‍]", "", s)


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_quarantine(path):
    if path and os.path.exists(path):
        return frozenset(os.path.basename(r.get("source_file", "")) for r in read_csv(path) if r.get("source_file"))
    return frozenset()


# ---------------------------------------------------------------------------
# 1005B name gate. Everything here lives in this file; gen_citation_index.py is untouched.
# AIPC_GATE_OFF=1 switches the whole layer off (used to prove the selftests go RED without it).
# ---------------------------------------------------------------------------
GATE_ON = not os.environ.get("AIPC_GATE_OFF")

STATES = set("""alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho illinois
indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota mississippi missouri montana nebraska
nevada ohio oklahoma oregon pennsylvania tennessee texas utah vermont virginia washington wisconsin wyoming
al ak az ar ca co ct de fl ga hi id il in ia ks ky la ma md mi mn ms mo mt ne nv nh nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy dc
dade broward""".split())
STOP = set("and & of the on in at for inc llc co company corp ltd lp pc pllc p.c".split())
# (a) geographic / venue vocabulary (generic nouns that name a kind of place, not a business)
PLACE_VOCAB = set("""country retreat parkway pkwy beach beaches town towns mountain mountains lake lakes resort resorts center centre convention conference mall
boulevard blvd drive dr street st avenue ave road rd lane way highway hwy boardwalk cliff cliffs end side westside eastside
northside southside north south east west northeast northwest southeast southwest midtown downtown uptown county city metro
area areas region district neighborhood neighbourhood village valley hill hills heights park airport campus university
college hq headquarters zip zipcode code
 pier harbor harbour bay coast shore island canyon ridge creek river springs county""".split()) - {"code"}
# small gazetteer of place names that appeared in the data (neighborhoods, landmarks, nearby towns, campuses)
GAZETTEER = set("""rio del mar bonny doon steamer belle meade segundo el hillsboro green aptos capitola soquel felton davenport
la ut westgate-none hollywood brentwood pasadena glendale burbank sandwich hermosa venice malibu cherokee brookside plaza
ben lomond boulder henry cowell roaring camp ano a\u00f1o nuevo seascape basin depot blacks selva seabright live oak pleasure
point twin opal natural bridges lighthouse wilder ranch pasatiempo corralitos watsonville brookdale hermon zayante swanton
scotts valley lorenzo taneycomo riverfront brighton pajaro dunes wharf field midtown buckhead decatur marietta sandy springs dunwoody vinings""".split())
GAZETTEER.discard("westgate-none")
GAZETTEER -= {"hollywood", "plaza", "green"}  # keep ambiguous brand-like words out; "green hills" handled as phrase below
GAZ_PHRASES = ("green hills", "lake resort", "rio del mar", "big basin", "new brighton")
# attractions and events (not businesses)
ATTRACTION_WORDS = set("opry museum fest festival stadium arena amphitheater amphitheatre aquarium zoo speedway".split())
ATTRACTION_PHRASES = ("hall of fame",)
# (b) generic-phrase lexicon: trade words, descriptors, and institutional words. A name made ONLY of these is not a business.
INSTITUTIONAL = set("""code codes association council marketplace registrar notice fest festival board commission department
bureau authority society federation chamber commerce noa aca institute coalition approval approvals license licensing""".split())
GENERIC_LEX = INSTITUTIONAL | set("""air conditioning ac a/c hvac heating cooling heat furnace plumbing plumber plumbers roofing roofer roofers roof
backflow testing test repair repairs service services public works muay thai brazilian jiu jitsu jiu-jitsu karate martial
mma boxing kickboxing grappling highest rated top best somatic experiencing therapy trial lawyers lawyer attorney attorneys law legal firm injury accident personal tan tanning
spray tech technology digital agency marketing advertising seo web internet consulting insurance health building acceptance
contractors contractor construction remodeling painting painters inspection inspections permit permits
partner partners network kpis kpi what look google facebook meta ai hoa any gym gyms studio studios product products approval
approvals instant book hurricane zone centre excellence isshinryu taekwondo judo aikido kenpo capoeira""".split())
# manufacturer brands (product makers, not local businesses). Last-layer gazetteer, counted separately from the rules.
MANUFACTURERS = ("owens corning", "certainteed", "gaf", "tamko", "malarkey", "iko roofing", "atlas roofing", "carrier hvac", "trane")
# short explicit denylist, LAST layer only (publications and the like the rules cannot see)
DENYLIST = {"ad age", "active colorado", "life skills", "little dragons", "little ninjas", "internal family systems",
            "mindfulness-based stress reduction", "owning your own shadow", "high-velocity hurricane zone", "ibhs fortified roof", "compassionate inquiry", "deloitte digital", "mckinsey digital"}
# (d) first names, used only in the "US nationwide, online" market to drop individual authors and teachers
FIRST_NAMES = set("""rupert teal doug laurie adyashanti tara debbie jack peter gabor brene deepak eckhart marianne wayne louise oprah byron katie thich pema ram dan david
michael john james robert mary linda susan karen lisa jennifer sarah emily jessica amanda ashley anne anna maria jane
joe tony mark steve steven paul richard thomas daniel matthew andrew joseph brian kevin jason jeff jeffrey scott eric
chris christopher stephen sean tim timothy gary larry ken kenneth ronald donald george edward alan adam ben benjamin
bessel judith elizabeth kristin neff rick hanson sharon pema dalai alan watts carl eckhart joan helen caroline
stephanie nicole heather rachel laura amy angela melissa brenda kim julie joanne diane carol ruth sandra donna nancy
patricia barbara jim jimmy bill billy bob bobby tom tommy dave ed eddie rob ron russ sam sammy ted tod todd tyler
charles chuck greg gregory harold henry jerry keith lawrence lee leo martin neil norman philip phil ray raymond roger
ryan samuel terry vincent walter wendy""".split())
BUSINESS_WORDS = set("""platform timer true chasers academy institute press media group labs lab app apps studios studio network
collective school foundation center centre inc llc company co corp books publishing publishers publisher health wellness
therapy counseling coaching community online digital""".split())

_PUNCT = ".,;:()[]{}\"'!?"


def _words(s):
    s = s.replace("’", "").replace("'", "")
    return [w for w in (x.strip(_PUNCT).lower() for x in s.split()) if w]


def _tokens(s):
    """Content tokens for alias merging: words (hyphens split) minus corporate stopwords."""
    out = []
    for w in _words(s):
        for p in re.split(r"[-/]", w) if w not in GENERIC_LEX else [w]:
            p = p.strip(_PUNCT)
            if p and p not in STOP:
                out.append(p)
    return out


_ABBR = {"inc", "co", "corp", "ltd", "llc", "st", "mt", "jr", "sr", "dr", "mr", "mrs", "ms", "lp", "pc", "pllc", "bros", "assoc", "ft", "no", "vs"}


def is_fragment(disp):
    """Sentence fragment: a sentence break inside the name ("KPIs. What")."""
    for m in re.finditer(r"([A-Za-z]+)\.\s+[A-Z]", disp):
        if m.group(1).lower() not in _ABBR and len(m.group(1)) >= 3:
            return True
    return False


def _mk_tokens(market):
    return set(_words(market.replace(",", " ")))


def _covered(w, vocab):
    if w in vocab:
        return True
    if "-" in w or "/" in w:
        parts = [p for p in re.split(r"[-/]", w) if p]
        return len(parts) > 1 and all(p in vocab for p in parts)
    return False


def is_place_name(disp, market):
    """(a) neighborhood, street, landmark, region: every word is a market token, place noun, direction or gazetteer name."""
    ws = _words(disp)
    if not ws:
        return False
    low = " ".join(ws)
    if any(p in low for p in GAZ_PHRASES):
        return True
    vocab = PLACE_VOCAB | GAZETTEER | _mk_tokens(market) | STOP
    return all(_covered(w, vocab) for w in ws)


_STREET = {"rd", "road", "st", "street", "ave", "avenue", "blvd", "boulevard", "dr", "drive", "ln", "lane", "hwy", "highway", "pkwy", "parkway"}


def is_street_address(disp):
    ws = _words(disp)
    return 1 <= len(ws) <= 3 and ws[-1] in _STREET and not any(w in GENERIC_LEX for w in ws)


def is_agency_acronym(disp):
    """State agency style: "FL DBPR" (state abbreviation plus a consonant-only acronym)."""
    ws = disp.split()
    return (len(ws) == 2 and ws[0].isupper() and ws[0].lower() in STATES and len(ws[0]) == 2
            and re.fullmatch(r"[A-Z]{3,6}", ws[1]) is not None and not re.search("[AEIOU]", ws[1]))


def is_attraction(disp):
    ws = _words(disp)
    low = " ".join(ws)
    return any(w in ATTRACTION_WORDS for w in ws) or any(p in low for p in ATTRACTION_PHRASES)


def is_generic_phrase(disp, market):
    """(b) no brand: sentence fragment, or every word is a trade/descriptor/institutional word, market token or state."""
    if is_fragment(disp):
        return True
    ws = _words(disp)
    if not ws:
        return True
    if ws[-1] in ("association", "council", "commission", "department", "authority", "code", "codes", "university"):
        return True
    if is_agency_acronym(disp):
        return True
    content = [w for w in ws if w not in STOP]
    vocab = GENERIC_LEX | STOP
    # a short phrase of market/state + trade word ("Atlanta HVAC") is generic; a longer one ("Air Tech of Houston",
    # "Joplin Roofing & Remodeling") reads like a real business name unless an institutional word is present
    if len(content) <= 2 or any(w in INSTITUTIONAL for w in ws):
        vocab = vocab | _mk_tokens(market) | STATES
    return all(_covered(w, vocab) for w in ws)


def is_manufacturer(disp):
    low = " ".join(_words(disp))
    return any(low == m or low.startswith(m + " ") for m in MANUFACTURERS)


def is_person(disp, market):
    """(d) individual authors and teachers, applied only to the nationwide online market."""
    if "nationwide" not in market.casefold():
        return False
    ws = disp.replace("’", "").split()
    if ws and ws[0].strip(".").lower() in ("dr", "prof", "professor"):
        ws = ws[1:]
    if not 2 <= len(ws) <= 3:
        return False
    low = [w.strip(_PUNCT).lower() for w in ws]
    if any(w in BUSINESS_WORDS for w in low):
        return False
    if not all(re.fullmatch(r"[A-Z][A-Za-z]*\.?", w) for w in ws):
        return False
    return low[0] in FIRST_NAMES


def is_rating(disp):
    """A lawyer rating, not a firm: Martindale-Hubbell, "Hubbell AV", "AV-rated"."""
    ws = _words(disp)
    return any("martindale" in w for w in ws) or (bool(ws) and ws[-1] in ("av", "av-rated"))


def extra_reject(disp, market):
    """Returns the reason a name is rejected by the 1005B gate, or None."""
    if not GATE_ON:
        return None
    if is_place_name(disp, market) or is_street_address(disp):
        return "place"
    if is_attraction(disp):
        return "attraction"
    if is_rating(disp):
        return "rating"
    if is_generic_phrase(disp, market):
        return "generic"
    if is_person(disp, market):
        return "person"
    if is_manufacturer(disp):
        return "manufacturer"
    if " ".join(_words(disp)) in DENYLIST:
        return "denylist"
    return None


ALIAS_OLD = bool(os.environ.get("AIPC_ALIAS_OLD"))  # proves the selftest RED on the pre-FIX-1 subset rule
MERGE_LOG = []
_ALIAS_SUFFIX = set("""lodge beauty bar academy resort hotel spa inn suites group""".split())
_PSTOP = {"and", "&", "inc", "llc", "co", "company", "corp", "ltd", "lp", "pc", "pllc", "p.c", "the"}


def _ptokens(s):
    out = []
    for w in _words(s):
        if w in GENERIC_LEX:
            out.append(w)
            continue
        out.extend(p for p in re.split(r"[-/]", w) if p and p not in _PSTOP)
    return out


def _is_subsequence(a, b):
    it = iter(b)
    return all(x in it for x in a)


def valid_alias(short, long_, market):
    """FIX 1. The shorter name is an ordered subsequence of the longer one, and every extra token in the longer
    name is a location qualifier or business-type word ("of <town>", "on <street>", Lodge, Academy, Insurance...).
    Never when the shorter name sits after "of", "at" or "|" in the longer one (person-at-brokerage pattern)."""
    a, b = _ptokens(short), _ptokens(long_)
    if not a or not _is_subsequence(a, b):
        return False
    ll = long_.lower().replace("\u2019", "")
    sl = short.lower().replace("\u2019", "").strip()
    if re.search(r"(\bof|\bat|\|)\s*" + re.escape(sl), ll):
        return False
    # leading extra words: only a possessive publication prefix ("Forbes' Warm Nashville Home")
    first = long_.split()[0] if long_.split() else ""
    i0 = b.index(a[0])
    lead, rest = b[:i0], b[i0:]
    if lead and not (len(lead) == 1 and first[-1:] in ("\u2019", "'")):
        return False
    ra, extras, j = list(a), [], 0
    for x in rest:
        if j < len(ra) and x == ra[j]:
            j += 1
        else:
            extras.append(x)
    if not extras:
        return True
    if extras[0] == "on" and len(extras) <= 4:
        return True
    vocab = _mk_tokens(market) | STATES | PLACE_VOCAB | GAZETTEER | GENERIC_LEX | _ALIAS_SUFFIX | {"of", "in", "st"}
    return all(_covered(x, vocab) for x in extras)


def location_variant(short, long_, market):
    """True when the longer name is the shorter (chain) name plus a pure location qualifier ("on South Broadway",
    "of Pleasanton", "St. Peters"), so the chain name is the display. Business-type extras (Lodge, Academy) are not."""
    a, b = _ptokens(short), _ptokens(long_)
    if not a or not _is_subsequence(a, b) or b.index(a[0]) != 0:
        return False
    ra, extras, j = a, [], 0
    for x in b:
        if j < len(ra) and x == ra[j]:
            j += 1
        else:
            extras.append(x)
    if not extras:
        return False
    if extras[0] == "on" and len(extras) <= 4:
        return True
    vocab = _mk_tokens(market) | STATES | PLACE_VOCAB | GAZETTEER | {"of", "in", "st"}
    return all(_covered(x, vocab) for x in extras)


def merge_aliases(cell_by_engine, market="", week=""):
    """(c) Same market and week: if one name's tokens are a subset of exactly one longer name (or the unique
    largest of a nested chain), they are the same business. Keeps the longer display, unions the days."""
    info = {}
    for eng, cell in cell_by_engine.items():
        for key, (disp, _days) in cell["named"].items():
            cur = info.get(key)
            if cur is None or len(disp) > len(cur[0]):
                info[key] = (disp, frozenset(_tokens(disp)))
    target = {}
    keys = list(info)
    for s in keys:
        ts = info[s][1]
        if len(ts) < 2:
            continue
        sup = [t for t in keys if t != s and (ts < info[t][1] or (ts == info[t][1] and len(info[t][0]) > len(info[s][0])))]
        if not ALIAS_OLD:
            sup = [t for t in sup if valid_alias(info[s][0], info[t][0], market)]
        if not sup:
            continue
        big = max(sup, key=lambda t: (len(info[t][1]), len(info[t][0])))
        if all(info[t][1] <= info[big][1] for t in sup):
            target[s] = big
    def resolve(k):
        seen = set()
        while k in target and k not in seen:
            seen.add(k)
            k = target[k]
        return k
    moved = 0
    chain_name = {}
    for s in list(target):
        t = resolve(s)
        if t == s:
            continue
        MERGE_LOG.append((market, week, info[s][0], info[t][0]))
        if not ALIAS_OLD and location_variant(info[s][0], info[t][0], market):
            if t not in chain_name or len(info[s][0]) < len(chain_name[t]):
                chain_name[t] = info[s][0]
        for eng, cell in cell_by_engine.items():
            if s in cell["named"]:
                _d, days = cell["named"].pop(s)
                cell["named"].setdefault(t, (info[t][0], set()))[1].update(days)
                moved += 1
    for eng, cell in cell_by_engine.items():  # one display per business: the longer name
        for key in list(cell["named"]):
            d, days = cell["named"][key]
            want = chain_name.get(key) or (info[key][0] if key in info else d)
            if want != d:
                cell["named"][key] = (want, days)
    return moved


def aggregate(rows, exclude_files=frozenset()):
    """Returns (data, stats). data[market][week][engine] = {'answered': set(dates), 'named': {key: (display, set(dates))}}"""
    stats = {"rows_in": len(rows), "agree_skipped": 0, "serp_skipped": 0, "quarantined": 0, "gate_dropped": 0, "gate_extra": 0, "alias_merged": 0}
    use = []
    for r in rows:
        eng = r.get("engine", "")
        if eng == "agree" or r.get("kind", "").upper() == "AGREE":
            stats["agree_skipped"] += 1
            continue
        if eng == "google_serp":
            stats["serp_skipped"] += 1
            continue
        if eng not in ENGINES:
            continue
        if os.path.basename(r.get("source_file", "")) in exclude_files:
            stats["quarantined"] += 1
            continue
        mk = r.get("market", "").strip()
        if not mk or mk.casefold() in SKIP_MARKETS or r.get("date", "") < MIN_DATE:
            continue
        use.append(r)
    generic = gci.make_generic_set({r["market"].strip() for r in use})
    data = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {"answered": set(), "named": {}})))
    for r in use:
        mk, d, eng = r["market"].strip(), r["date"], r["engine"]
        cell = data[mk][week_start(d)][eng]
        raw = [n.strip() for n in r.get("businesses_named", "").split(";") if n.strip()]
        if raw:
            cell["answered"].add(d)
        for disp in raw:
            disp = re.sub(r"\s*\([^)]*\)\s*$", "", disp) or disp  # 1005B: "GJEL Accident Attorneys (Pleasanton Office)" is GJEL
            norm = gci.normalize(disp)
            if not gci.keep_name(disp, norm, generic):
                continue
            if gci.is_platform(disp):
                continue
            if gci.is_junk(disp) or not gci.is_proper_noun(disp):
                stats["gate_dropped"] += 1
                continue
            if gci.is_place(disp):
                continue
            if extra_reject(disp, mk):
                stats["gate_extra"] += 1
                continue
            cell["named"].setdefault(norm, (disp, set()))[1].add(d)
    if GATE_ON:
        for mk in data:
            for ws in data[mk]:
                stats["alias_merged"] += merge_aliases(data[mk][ws], mk, ws)
    return data, stats


def week_ranking(wk):
    """wk = data[market][week]. Returns list of (key, display, score, per_engine_days) sorted."""
    biz = {}
    for eng in ENGINES:
        for key, (disp, days) in wk[eng]["named"].items():
            b = biz.setdefault(key, [disp, {}])
            b[1][eng] = len(days)
    out = [(k, v[0], sum(v[1].values()), v[1]) for k, v in biz.items()]
    out.sort(key=lambda t: (-t[2], -len(t[3]), t[1].casefold()))
    return out


def build_chart(data, week):
    wend = week_end(week)
    prev = add_weeks(week, -1)
    pplx_flag = prev < PPLX_CHANGE <= wend
    markets = {}
    for mk in sorted(data):
        weeks = data[mk]
        if week not in weeks:
            continue
        rank_cache = {}

        def top(ws):
            if ws not in rank_cache:
                rank_cache[ws] = week_ranking(weeks[ws])[:TOP_N] if ws in weeks else []
            return rank_cache[ws]

        cur = top(week)
        if not cur:
            continue
        first_week = min(weeks)
        prev_rank = {t[0]: i + 1 for i, t in enumerate(top(prev))}
        denom = {e: len(weeks[week][e]["answered"]) for e in ENGINES}
        prev_denom = {e: len(weeks[prev][e]["answered"]) if prev in weeks else 0 for e in ENGINES}
        entries = []
        for i, (key, disp, score, per) in enumerate(cur):
            rank = i + 1
            on, ws, peak = 0, week, rank
            while ws >= first_week and any(t[0] == key for t in top(ws)):
                on += 1
                ws = add_weeks(ws, -1)
            w = first_week
            while w < week:
                for j, t in enumerate(top(w)):
                    if t[0] == key:
                        peak = min(peak, j + 1)
                w = add_weeks(w, 1)
            pw = weeks[prev] if prev in weeks else None
            engs = {}
            for e in ENGINES:
                named = per.get(e, 0)
                pn = len(pw[e]["named"][key][1]) if pw and key in pw[e]["named"] else 0
                flag = None
                if denom[e] == 0:
                    status = "no answers"
                else:
                    status = "answered"
                if e == "perplexity" and pplx_flag:
                    flag = "method change"
                delta = None
                if status == "answered" and prev_denom[e] > 0 and flag is None:
                    delta = named - pn
                engs[e] = {"days_named": None if status == "no answers" else named,
                           "days_answered": denom[e], "status": status,
                           "last_week_days_named": pn if prev_denom[e] > 0 else None,
                           "last_week_days_answered": prev_denom[e],
                           "change_in_days": delta, "flag": flag}
            entries.append({"rank": rank, "business": clean_text(disp),
                            "last_week_rank": prev_rank.get(key, "NEW"),
                            "weeks_on_chart": on, "peak_rank": peak,
                            "engine_days_named": score, "engines": engs})
        markets[mk] = {"days_answered_by_engine": denom,
                       "last_week_days_answered_by_engine": prev_denom,
                       "top10": entries}
    return {"title": "AI Picks Chart, week of %s" % week, "week": week, "week_start": week, "week_end": wend,
            "last_week_start": prev, "perplexity_method_change_date": PPLX_CHANGE,
            "perplexity_method_change_flag": pplx_flag, "markets": markets}


def render_html(chart):
    h = html.escape
    wk = chart["week_start"]
    P = []
    P.append("<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">")
    P.append("<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">")
    P.append("<title>%s</title>" % h(chart["title"]))
    P.append("<style>body{font-family:system-ui,Arial,sans-serif;max-width:60rem;margin:0 auto;padding:1rem;line-height:1.45;color:#1a1a1a;background:#fff}"
             "table{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem;font-size:.95rem}"
             "th,td{border:1px solid #888;padding:.35rem .5rem;text-align:left;vertical-align:top}"
             "th{background:#eee}.box{border:2px solid #333;padding:.75rem 1rem;margin:1rem 0}"
             "caption{text-align:left;font-weight:bold;padding:.25rem 0}</style></head><body>")
    P.append("<h1>%s</h1>" % h(chart["title"]))
    P.append("<p>Which local businesses AI engines named most, market by market, from %s to %s, with movement against the week of %s.</p>"
             % (h(wk), h(chart["week_end"]), h(chart["last_week_start"])))
    P.append("<div class=\"box\"><h2>Methodology</h2><ul>")
    P.append("<li>Score is engine-days named: for each AI engine (OpenAI, Gemini, Perplexity, Anthropic), the number of distinct days that engine named the business in that market during the week, added across engines.</li>")
    P.append("<li>Every count carries its denominator: the days that engine answered with at least one name in that market this week. A cell reads days named / days answered.</li>")
    P.append("<li>Movement is compared with last week, engine by engine. NEW means the business was not in last week's ten.</li>")
    P.append("<li>Our Perplexity collection method changed on %s (a different API path). Perplexity counts are not compared across that date and carry the flag \"method change\" when the comparison would cross it.</li>" % h(PPLX_CHANGE))
    P.append("<li>Gemini returned no names after 2026-09-26. Where an engine returned no names in the week, the cell reads \"no answers\", which is not a zero.</li>")
    P.append("<li>Platforms, directories, places and non-business phrases are removed before counting. Google search results are a separate book and are not in this chart.</li>")
    P.append("<li>Most of each score comes from Perplexity. Our OpenAI and Anthropic calls run through their APIs without web search, and they rarely name a local business. The apps people use can answer differently.</li>")
    P.append("<li><strong>Businesses do not pay to appear and cannot pay to change their position.</strong></li></ul>")
    if chart["perplexity_method_change_flag"]:
        P.append("<p><strong>Flag for this week:</strong> Perplexity method change. Perplexity movement is not shown for this week.</p>")
    P.append("</div>")
    for mk, m in chart["markets"].items():
        den = m["days_answered_by_engine"]
        P.append("<section><h2>%s</h2>" % h(clean_text(mk)))
        P.append("<p>Days answered this week: %s.</p>" % h(", ".join(
            "%s %d" % (ENGINE_LABEL[e], den[e]) if den[e] else "%s no answers" % ENGINE_LABEL[e] for e in ENGINES)))
        P.append("<table><caption>Ten most named in %s</caption><thead><tr><th scope=\"col\">Rank</th><th scope=\"col\">Business</th>"
                 "<th scope=\"col\">Last week</th><th scope=\"col\">Weeks on chart</th><th scope=\"col\">Peak</th>"
                 "<th scope=\"col\">Engine-days</th>%s</tr></thead><tbody>" % (
                     h(clean_text(mk)), "".join("<th scope=\"col\">%s</th>" % ENGINE_LABEL[e] for e in ENGINES)))
        for en in m["top10"]:
            cells = []
            for e in ENGINES:
                x = en["engines"][e]
                if x["status"] == "no answers":
                    cells.append("no answers")
                    continue
                t = "%d of %d" % (x["days_named"], x["days_answered"])
                if x["flag"]:
                    t += " (%s)" % x["flag"]
                elif x["change_in_days"] is not None:
                    t += " (%+d)" % x["change_in_days"]
                cells.append(t)
            P.append("<tr><th scope=\"row\">%d</th><td>%s</td><td>%s</td><td>%d</td><td>%d</td><td>%d</td>%s</tr>" % (
                en["rank"], h(en["business"]), h(str(en["last_week_rank"])), en["weeks_on_chart"], en["peak_rank"],
                en["engine_days_named"], "".join("<td>%s</td>" % h(c) for c in cells)))
        P.append("</tbody></table></section>")
    P.append("</body></html>\n")
    return clean_text("\n".join(P))


def latest_complete_week(rows):
    ds = [r["date"] for r in rows if r.get("date")]
    mx = max(ds)
    ws = week_start(mx)
    if week_end(ws) > mx:
        ws = add_weeks(ws, -1)
    return ws


def run(rows, week, out_dir, exclude_files):
    data, stats = aggregate(rows, exclude_files)
    chart = build_chart(data, week)
    chart["stats"] = stats
    os.makedirs(out_dir, exist_ok=True)
    pj, ph = os.path.join(out_dir, "chart.json"), os.path.join(out_dir, "chart.html")
    with open(pj, "w", encoding="utf-8") as f:
        json.dump(chart, f, indent=2, ensure_ascii=True)
    with open(ph, "w", encoding="utf-8") as f:
        f.write(render_html(chart))
    return chart, pj, ph


def _row(d, eng, mk, names, src="f.json", kind="API"):
    return {"date": d, "kind": kind, "target": "t", "niche": "roofing", "market": mk, "engine": eng,
            "businesses_named_count": str(len(names)), "businesses_named": ";".join(names),
            "first_named": names[0] if names else "", "source_file": src, "sha256": ""}


def _gate_selftest():
    """1005B: cases (a) place, (b) generic phrase, (c) alias merge, (d) person filter. Returns a list of failure messages."""
    fails = []
    wk = "2026-09-21"
    with tempfile.TemporaryDirectory() as d:
        def top(mk, per_engine_names, tag):
            rows = [_row(day, eng, mk, names) for eng, day, names in per_engine_names]
            ch, _, _ = run(rows, wk, os.path.join(d, tag), frozenset())
            return {x["business"]: x for x in ch["markets"].get(mk, {"top10": []})["top10"]}
        mk = "Testville MO"
        # (a) place: market-token plus place-noun names, none of them in the gazetteer
        t = top(mk, [("perplexity", "2026-09-21", ["Pine Roofing", "Testville Downtown", "North Testville", "Testville Convention Center", "Testville Mall"])], "a")
        for bad in ("Testville Downtown", "North Testville", "Testville Convention Center", "Testville Mall"):
            if bad in t:
                fails.append("(a) place survived: " + bad)
        if "Pine Roofing" not in t:
            fails.append("(a) real business lost: Pine Roofing")
        # (b) generic phrase: trade words, institutional words, sentence fragment
        t = top(mk, [("perplexity", "2026-09-21", ["Pine Roofing", "Plumbing Repair Services", "Missouri Building Code", "Roofing Contractors Association",
                                                   "Testville HVAC", "Heating Tips. Next"])], "b")
        for bad in ("Plumbing Repair Services", "Missouri Building Code", "Roofing Contractors Association", "Testville HVAC", "Heating Tips. Next"):
            if bad in t:
                fails.append("(b) generic phrase survived: " + bad)
        if "Pine Roofing" not in t:
            fails.append("(b) real business lost: Pine Roofing")
        # (c) alias merge: subset names are one business with the union of days; two unrelated supersets block the merge
        t = top(mk, [("perplexity", "2026-09-21", ["Delta Plumbing", "Echo Tan"]), ("perplexity", "2026-09-22", ["Delta Plumbing & Heating", "Echo Tan East"]),
                     ("perplexity", "2026-09-23", ["Delta Plumbing", "Echo Tan West"])], "c")
        if "Delta Plumbing" in t or "Delta Plumbing & Heating" not in t:
            fails.append("(c) alias not merged into the longer name: %s" % sorted(t))
        elif t["Delta Plumbing & Heating"]["engines"]["perplexity"]["days_named"] != 3:
            fails.append("(c) merged days not the union: %s" % t["Delta Plumbing & Heating"]["engines"]["perplexity"])
        if not all(n in t for n in ("Echo Tan", "Echo Tan East", "Echo Tan West")):
            fails.append("(c) ambiguous alias was merged: %s" % sorted(t))
        # FIX 1: a national brand is not folded into a person who works there
        t = top(mk, [("perplexity", "2026-09-21", ["Coldwell Banker", "Jane Doe of Coldwell Banker"]),
                     ("perplexity", "2026-09-22", ["Coldwell Banker"])], "c2")
        if "Coldwell Banker" not in t or "Jane Doe of Coldwell Banker" not in t:
            fails.append("(c) brand merged into person-at-brokerage: %s" % sorted(t))
        elif t["Jane Doe of Coldwell Banker"]["engines"]["perplexity"]["days_named"] != 1:
            fails.append("(c) days leaked into the person: %s" % t["Jane Doe of Coldwell Banker"]["engines"]["perplexity"])
        # FIX 2: chain displays as the chain name; a lawyer rating is not a business
        t = top(mk, [("perplexity", "2026-09-21", ["Delta Roofing", "Hubbell AV", "Martindale-Hubbell Rated"]),
                     ("perplexity", "2026-09-22", ["Delta Roofing on Main St"])], "f2")
        if "Delta Roofing" not in t or "Delta Roofing on Main St" in t:
            fails.append("(c) chain not displayed as chain name: %s" % sorted(t))
        elif t["Delta Roofing"]["engines"]["perplexity"]["days_named"] != 2:
            fails.append("(c) chain days not unioned: %s" % t["Delta Roofing"]["engines"]["perplexity"])
        for bad in ("Hubbell AV", "Martindale-Hubbell Rated"):
            if bad in t:
                fails.append("(a) rating survived: " + bad)
        rows_w = [_row("2026-09-21", "perplexity", mk, ["Delta Roofing"])]
        chw, _, _ = run(rows_w, wk, os.path.join(d, "wk"), frozenset())
        if chw.get("week") != wk:
            fails.append("(week) chart.json top-level week missing: %r" % chw.get("week"))
        # (d) person filter: nationwide market only
        t = top("US nationwide, online", [("openai", "2026-09-21", ["Tara Brach", "Jung Platform"])], "d1")
        if "Tara Brach" in t or "Jung Platform" not in t:
            fails.append("(d) person survived in nationwide market or platform lost: %s" % sorted(t))
        t = top(mk, [("perplexity", "2026-09-21", ["Tara Brach", "Doug Buenz"])], "d2")
        if "Tara Brach" not in t or "Doug Buenz" not in t:
            fails.append("(d) named professional lost in a local market: %s" % sorted(t))
    return fails


def selftest():
    mk = "Testville MO"
    A, Bz, G = "Alpha Roofing", "Beta Roofing", "Gamma Roofing"
    rows = []
    # week 2026-09-14: Beta 3 openai days, Alpha 1
    for d in ("2026-09-14", "2026-09-15", "2026-09-16"):
        rows.append(_row(d, "openai", mk, [Bz, A] if d == "2026-09-14" else [Bz]))
    rows.append(_row("2026-09-14", "perplexity", mk, [Bz, A]))
    rows.append(_row("2026-09-14", "gemini", mk, [Bz]))
    # week 2026-09-21: Alpha 3 openai + 1 perplexity, Beta 1, Gamma NEW 1
    for d in ("2026-09-21", "2026-09-22", "2026-09-23"):
        rows.append(_row(d, "openai", mk, [A, Bz, G] if d == "2026-09-21" else [A]))
    rows.append(_row("2026-09-21", "perplexity", mk, [A, Bz, G]))
    rows.append(_row("2026-09-21", "gemini", mk, [A]))
    # week 2026-09-28: Alpha again, gemini no names, perplexity after change
    for d in ("2026-09-28", "2026-09-29"):
        rows.append(_row(d, "openai", mk, [A, Bz]))
    rows.append(_row("2026-09-28", "perplexity", mk, [A]))
    rows.append(_row("2026-09-30", "gemini", mk, []))
    # traps
    rows.append(_row("2026-09-21", "agree", mk, ["Agreeonly Roofing"] * 1, kind="AGREE"))
    rows.append(_row("2026-09-21", "google_serp", mk, ["Serponly Roofing"], kind="SERP"))
    rows.append(_row("2026-09-22", "openai", mk, ["Quarantine Roofing"] * 1, src="NB-CZ-Q.json"))
    qset = frozenset({"NB-CZ-Q.json"})
    wk21, wk28 = "2026-09-21", "2026-09-28"
    with tempfile.TemporaryDirectory() as d:
        ch, pj, ph = run(rows, wk21, d, qset)
        t = ch["markets"][mk]["top10"]
        names = [x["business"] for x in t]
        assert "Agreeonly Roofing" not in names, "agree row leaked"
        assert "Serponly Roofing" not in names, "google_serp row leaked"
        assert "Quarantine Roofing" not in names, "quarantined row leaked"
        assert ch["stats"]["agree_skipped"] == 1 and ch["stats"]["serp_skipped"] == 1 and ch["stats"]["quarantined"] == 1, ch["stats"]
        by = {x["business"]: x for x in t}
        assert by[A]["rank"] == 1 and by[A]["last_week_rank"] == 2, by[A]
        assert by[Bz]["rank"] == 2 and by[Bz]["last_week_rank"] == 1, by[Bz]
        assert by[G]["last_week_rank"] == "NEW" and by[G]["weeks_on_chart"] == 1, by[G]
        assert by[A]["weeks_on_chart"] == 2 and by[A]["peak_rank"] == 1, by[A]
        assert by[Bz]["peak_rank"] == 1 and by[Bz]["weeks_on_chart"] == 2
        assert by[A]["engines"]["openai"]["days_named"] == 3 and by[A]["engines"]["openai"]["days_answered"] == 3
        assert not ch["perplexity_method_change_flag"] and by[A]["engines"]["perplexity"]["flag"] is None
        # quarantine control: without exclusion the row is kept
        ch0, _, _ = run(rows, wk21, os.path.join(d, "ctl"), frozenset())
        assert "Quarantine Roofing" in [x["business"] for x in ch0["markets"][mk]["top10"]], "control failed"
        # cross 09-28
        ch2, pj2, ph2 = run(rows, wk28, os.path.join(d, "w28"), qset)
        a2 = {x["business"]: x for x in ch2["markets"][mk]["top10"]}[A]
        assert ch2["perplexity_method_change_flag"] and a2["engines"]["perplexity"]["flag"] == "method change"
        assert a2["engines"]["perplexity"]["change_in_days"] is None
        assert a2["engines"]["openai"]["change_in_days"] == -1, a2["engines"]["openai"]
        assert a2["engines"]["gemini"]["status"] == "no answers" and a2["engines"]["gemini"]["days_named"] is None
        assert a2["weeks_on_chart"] == 3 and a2["last_week_rank"] == 1
        page = open(ph2, encoding="utf-8").read()
        assert "method change" in page and "no answers" in page
        assert "Businesses do not pay to appear and cannot pay to change their position." in page
        assert "AI Picks Chart, week of 2026-09-28" in page
        for p in (ph, ph2):
            s = open(p, encoding="utf-8").read()
            assert "—" not in s and "–" not in s, "em/en dash in html"
            assert not re.search("[\U0001F000-\U0001FFFF☀-➿]", s), "emoji"
            for w in BANNED_WORDS:
                assert w not in s.lower(), "hype word: " + w
        assert clean_text("a — b – c") == "a - b - c"
        # 1005B alias: a trailing parenthetical is the same business (GJEL Accident Attorneys (Pleasanton Office))
        rows_a = [_row("2026-09-21", "gemini", mk, ["Delta Law"]), _row("2026-09-22", "gemini", mk, ["Delta Law (Testville Office)"])]
        cha, _, _ = run(rows_a, wk21, os.path.join(d, "alias"), frozenset())
        ta = cha["markets"][mk]["top10"]
        assert len(ta) == 1 and ta[0]["engines"]["gemini"]["days_named"] == 2, ta
    fails = _gate_selftest()
    assert not fails, "1005B gate selftest failures: " + "; ".join(fails)
    print("selftest OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week")
    ap.add_argument("--csv", default=DEFAULT_CSV)
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--quarantine", default=DEFAULT_QUARANTINE)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return
    rows = read_csv(a.csv)
    week = a.week or latest_complete_week(rows)
    if date.fromisoformat(week).weekday() != 0:
        sys.exit("--week must be a Monday")
    excl = load_quarantine(a.quarantine)
    chart, pj, ph = run(rows, week, a.out_dir, excl)
    print("week %s | markets %d | quarantine files %d | stats %s" % (week, len(chart["markets"]), len(excl), chart["stats"]))
    print(pj)
    print(ph)


if __name__ == "__main__":
    main()
