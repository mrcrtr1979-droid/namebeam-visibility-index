#!/usr/bin/env python3
"""Build the Namebeam citation index from the dated E1 dataset CSV. Python 3 stdlib only.
V2 gate: proper-noun filter, platform separation, corroboration tracking.

Usage:
  python3 gen_citation_index.py [--csv PATH] [--out-dir DIR]
  python3 gen_citation_index.py --selftest
"""
import argparse, csv, hashlib, json, os, re, sys, tempfile, urllib.parse
from collections import defaultdict
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
# one gate, one place: import from the 0929B panels generator where it lives in the Brain, or beside this file
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), '0929B_MARKET_PANELS'))
from gen_market_panels import is_proper_noun, is_junk, url_to_host, asc

DEFAULT_CSV = ("Brain_Kit/"
               "corpus/namebeam/Namebeam_E1_Dataset_LATEST.csv")
MIN_DATE = "2026-09-01"
DROP_SUFFIX_WORDS = {"llc", "inc", "co"}

PLATFORM_NAMES = {
    'yelp', 'airbnb', 'booking.com', 'vrbo', 'airdna', 'super lawyers', 'pricelabs',
    'wheelhouse', 'klaviyo', 'google', 'google maps', 'google business profile', 'angi',
    'angies list', 'angie\'s list', 'homeadvisor', 'home advisor', 'thumbtack', 'bbb',
    'better business bureau', 'nextdoor', 'facebook', 'instagram', 'tripadvisor',
    'trip advisor', 'zillow', 'houzz', 'avvo', 'justia', 'martindale',
    'martindale-hubbell', 'findlaw', 'lawyers.com', 'expedia', 'hotels.com', 'porch',
    'healthgrades', 'zocdoc', 'vitals', 'webmd', 'reddit', 'youtube', 'amazon', 'etsy',
    'shopify', 'wix', 'squarespace', 'clutch', 'designrush', 'expertise.com', 'upcity',
    'yellowpages', 'mapquest', 'linkedin', 'glassdoor', 'indeed', 'opentable', 'doordash',
    'grubhub', 'ubereats', 'uber eats', 'groupon', 'capterra', 'g2', 'trustpilot',
    'consumer reports',
    # v2.1 (COS 0929D): AI assistants and ad-tech products named inside answers are tools, not businesses
    'bing chat', 'google assistant', 'google ai overviews', 'apple siri', 'siri', 'alexa',
    'chatgpt', 'perplexity', 'gemini', 'claude', 'copilot', 'samsung tv',
    # v2.2 (COS 1004D): ad and analytics products named inside agency answers are tools, not businesses
    # (Comet DATA FLAG 2026-10-04: "Google Ads" counted as a business in St. Louis and San Francisco)
    'google ads', 'google analytics', 'meta ads', 'facebook ads', 'microsoft ads', 'bing ads', 'linkedin ads'
}

# v2.2 (COS 1004D): publications are sources, not businesses ("St. Louis Business Journal" leak, Comet 2026-10-04).
PUBLICATION_SUFFIXES = ('business journal',)

# v2.2 (COS 1004D): the same firm cited under its domain (Google results) and its name (AI answers).
# Only VERIFIED pairs: each carries the evidence read on the live site. Records stay separate (the Actor
# looks up by key, so a domain query must keep working); both get same_entity + alias_evidence.
VERIFIED_ALIASES = {
    'wearetgcom': ('timmermann group',
                   'wearetg.com <title> and og:site_name read "Timmermann Group" (live fetch 2026-10-04 by FABLE-COS-1004D)'),
}

# v2.1 (COS 0929D): google_serp rows carry URLs. These hosts are booking, review or directory
# platforms that stand in front of a business, so they go to "platforms", never "businesses".
PLATFORM_HOSTS = {
    'servicetitan.com', 'getjobber.com', 'housecallpro.com', 'nerdwallet.com', 'reddit.com',
    'yelp.com', 'facebook.com', 'youtube.com', 'google.com', 'clutch.co', 'designrush.com',
    'expertise.com', 'angi.com', 'thumbtack.com', 'houzz.com', 'bbb.org', 'yellowpages.com',
    'mapquest.com', 'tripadvisor.com', 'zillow.com', 'avvo.com', 'justia.com', 'findlaw.com',
    'superlawyers.com', 'martindale.com', 'lawyers.com', 'healthgrades.com', 'zocdoc.com',
    'vitals.com', 'webmd.com', 'airbnb.com', 'vrbo.com', 'booking.com', 'expedia.com',
    'homeadvisor.com', 'nextdoor.com', 'instagram.com', 'linkedin.com', 'upcity.com',
    'opentable.com', 'doordash.com', 'grubhub.com', 'groupon.com', 'trustpilot.com',
    'amazon.com', 'wikipedia.org', 'forbes.com', 'usnews.com', 'indeed.com', 'glassdoor.com',
    'schedulicity.com', 'vagaro.com', 'mindbodyonline.com', 'square.site', 'wixsite.com',
    'glossgenius.com', 'boulevard.io', 'thryv.com', 'semrush.com', 'styleseat.com', 'fresha.com',
    'booksy.com', 'setmore.com', 'acuityscheduling.com', 'calendly.com', 'airdna.co',
}

# v2.1 (COS 0929D): two- and three-word names ending in a geographic word are neighbourhoods and landmarks
# the engines recommend for short-term-rental questions (Table Rock Lake, Green Hills, Sylvan Park,
# Bay Area). They go to "places". A business with a geographic word plus a trade word
# ("Green Hills Dental") has 3+ words and is not caught.
GEO_LAST = {
    'lake', 'park', 'hills', 'area', 'landing', 'valley', 'heights', 'district', 'square',
    'village', 'beach', 'springs', 'bay', 'island', 'point', 'points', 'ridge', 'creek',
    'river', 'downtown', 'quarter', 'loop', 'strip', 'gulch', 'plaza',
}


DEFAULT_QUARANTINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "returns", "COMET_1004", "SERP_DRIFT_QUARANTINE_2026-10-04.csv")


def registrable(host):
    parts = host.lower().split('.')
    if len(parts) >= 3 and parts[-2] in ('co', 'com', 'org', 'net') and len(parts[-1]) == 2:
        return '.'.join(parts[-3:])
    return '.'.join(parts[-2:])


def is_place(name):
    w = name.split()
    return 2 <= len(w) <= 3 and w[-1].lower().strip('.,') in GEO_LAST

# Words that alone do not make a business name. Rule text: city names, "phone", "serves",
# "certified", "repairs", "specialty", state names, "and", "or". City names are also built
# from the market column at run time. The EXTRA set is an extension: section headings that
# the engines emit inside answer lists ("Red flags to avoid", "Next steps", ...).
BASE_GENERIC = {
    "phone", "serves", "certified", "repairs", "specialty", "and", "or", "the", "of", "in",
    "for", "to", "a", "an", "county", "city", "state", "north", "south", "east", "west",
    "downtown", "area", "local", "nationwide", "us", "usa", "only",
}
STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
    "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada", "hampshire",
    "jersey", "mexico", "york", "carolina", "dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode", "island", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "wisconsin", "wyoming",
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in",
    "ks", "ky", "la", "ma", "md", "me", "mi", "mn", "mo", "ms", "mt", "nc", "nd", "ne",
    "nh", "nj", "nm", "nv", "ny", "oh", "ok", "pa", "ri", "sc", "sd", "tn", "tx", "va",
    "vt", "wa", "wi", "wv", "wy",
}
EXTRA_GENERIC = {
    "research", "methods", "method", "approaches", "approach", "red", "flags", "flag",
    "avoid", "call", "ahead", "questions", "question", "ask", "locals", "referrals",
    "resources", "ways", "get", "multiple", "quotes", "reputation", "overview", "next",
    "steps", "step", "key", "things", "evaluate", "where", "look", "check", "directories",
    "location", "certification", "certifications", "licensing", "license", "insurance",
    "specialties", "specialization", "communication", "budget", "friendly", "tips", "tip",
    "recommendation", "recommendations", "focus", "goals", "industry", "highlights",
    "availability", "appointments", "consideration", "troubleshooting", "operations",
    "features", "strengths", "warranty", "warranties", "convenience", "equipment", "perks",
    "style", "locations", "references", "consistency", "financial", "comparison", "speed",
    "parking", "hotels", "amenities", "space", "pro", "consultations", "trial", "experience",
    "standard", "test", "average", "cost", "residents", "required", "certification",
    "filing", "report", "irrigation", "lawn", "companies", "plumbing", "know", "what",
    "how", "why", "when", "who", "best", "top", "good", "great", "other", "more", "options",
    "option", "online", "reviews", "review", "ratings", "rating", "price", "pricing",
    "cost", "costs", "services", "service", "google", "maps", "search", "bar", "associations",
}
MAX_WORDS = 6
MIN_CHARS = 3


def normalize(name):
    s = name.casefold()
    s = re.sub(r"['\u2019.]", "", s)          # A.B. May -> ab may ; Joe's -> joes
    s = re.sub(r"[^\w\s]", " ", s)            # other punctuation -> space
    words = [w for w in s.split() if w not in DROP_SUFFIX_WORDS]
    return " ".join(words)


def make_generic_set(markets):
    g = set(BASE_GENERIC) | STATES | EXTRA_GENERIC
    for m in markets:
        for w in re.sub(r"[^\w\s]", " ", m.casefold()).split():
            g.add(w)
    return g


def keep_name(display, norm, generic):
    if len(norm) < MIN_CHARS:
        return False
    if len(display.split()) > MAX_WORDS:
        return False
    low = display.casefold()
    if "://" in low or low.startswith("www.") or low.startswith("http"):
        return False  # extension: raw URLs are citations, not business names
    words = norm.split()
    if not words or len(words) > MAX_WORDS:
        return False
    if all(w in generic for w in words):
        return False
    return True


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_platform(name, host=None):
    """Check if name or host matches a platform."""
    name_norm = name.lower().strip()
    if name_norm in PLATFORM_NAMES:
        return True
    if host and host.lower() in PLATFORM_NAMES:
        return True
    if name_norm.endswith(PUBLICATION_SUFFIXES):
        return True
    return False


def build(rows, extra_markets=(), exclude_files=frozenset()):
    """rows: iterable of dicts with the CSV columns. Returns (index_businesses, platforms, market_summary, rows_used, gate).
    exclude_files: source_file basenames quarantined by the COS (1005A: SERP query drift 09-28 to 10-03); history is kept, the index skips them."""
    n_in = len(rows)
    rows = [r for r in rows if os.path.basename(r.get("source_file", "")) not in exclude_files]
    excluded_quarantined = n_in - len(rows)
    rows = [r for r in rows
            if r.get("date", "") >= MIN_DATE and r.get("market", "").strip()
            and r.get("engine", "") != "agree"]
    generic = make_generic_set({r["market"].strip() for r in rows} | set(extra_markets))
    biz = {}
    platforms = {}
    places = {}
    seen = set()
    msum = defaultdict(lambda: {"dates": set(), "businesses": set(), "engines": set()})
    dropped_by_gate = 0

    for r in rows:
        market = r["market"].strip()
        msum[market]["dates"].add(r["date"])
        msum[market]["engines"].add(r["engine"])
        names = [n.strip() for n in r.get("businesses_named", "").split(";")]
        rank = 0
        for disp in names:
            if not disp:
                continue
            rank += 1
            norm = normalize(disp)
            engine = r["engine"]
            host = None

            if engine == "google_serp":
                # v2.1: serp names are URLs; key by registrable domain, platforms split out
                host = url_to_host(disp)
                if host is None:
                    dropped_by_gate += 1
                    continue
                reg = registrable(host)
                if reg in PLATFORM_HOSTS or reg.split('.')[0] in PLATFORM_NAMES:
                    target_dict = platforms
                else:
                    target_dict = biz
                key_norm = normalize(reg)  # v2.1b: same normalize() as names, so the Actor matches a domain query (palmbeachtan.com -> palmbeachtancom)
                if key_norm in target_dict and target_dict[key_norm]['name'] != reg:
                    key_norm = reg  # two different domains normalize alike (case.org vs co-case.org): never merge them
                disp = reg
            elif not keep_name(disp, norm, generic):
                continue
            # Check if it's a platform first (platforms bypass proper-noun gate)
            elif is_platform(disp):
                target_dict = platforms
                key_norm = norm
            else:
                # V2 gate: apply engine-specific filters for non-platforms
                # For non-serp engines: keep only if proper noun AND not junk
                if is_junk(disp) or not is_proper_noun(disp):
                    dropped_by_gate += 1
                    continue
                key_norm = norm
                target_dict = places if is_place(disp) else biz

            key = (key_norm, r["date"], market, r.get("niche", ""), engine)
            if key in seen:
                continue
            seen.add(key)

            b = target_dict.setdefault(key_norm, {"name": disp, "citations": []})
            b["citations"].append({"date": r["date"], "market": market, "niche": r.get("niche", ""),
                                   "engine": engine, "rank": rank})
            msum[market]["businesses"].add(key_norm)

    # Compute corroboration and add metadata
    for biz_dict in (biz, platforms, places):
        for b in biz_dict.values():
            b["citations"].sort(key=lambda c: (c["date"], c["market"], c["engine"], c["rank"]))
            dates = [c["date"] for c in b["citations"]]
            b["first_seen"] = dates[0]
            b["last_seen"] = dates[-1]
            b["engines"] = sorted({c["engine"] for c in b["citations"]})
            b["markets"] = sorted({c["market"] for c in b["citations"]})
            b["count"] = len(b["citations"])

            # Corroboration: true if cited by perplexity or google_serp, OR 2+ distinct engines, OR 2+ distinct dates
            engines_set = set(b["engines"])
            dates_set = set([c["date"] for c in b["citations"]])
            # v2.1 (COS 0929D): one engine repeating a name on many dates is not corroboration
            # ("Financial Tracking", anthropic x7); the sourced engines or a second engine are.
            b["corroborated"] = (
                "perplexity" in engines_set or
                "google_serp" in engines_set or
                len(engines_set) >= 2
            )

    # v2.2: cross-link verified aliases (both records must exist)
    linked = 0
    for dkey, (nkey, ev) in VERIFIED_ALIASES.items():
        if dkey in biz and nkey in biz:
            biz[dkey]["same_entity"] = nkey; biz[nkey]["same_entity"] = dkey
            biz[dkey]["alias_evidence"] = ev; biz[nkey]["alias_evidence"] = ev
            linked += 1

    summary = {}
    for m, v in sorted(msum.items()):
        ds = sorted(v["dates"])
        summary[m] = {"run_days": len(ds), "distinct_businesses": len(v["businesses"]),
                      "engines": sorted(v["engines"]), "first_date": ds[0], "last_date": ds[-1]}

    gate = {
        "version": "v2.2-tools-publications-aliases-2026-10-04",
        "rules": [
            "google_serp: key = normalize(registrable domain), display = domain",
            "other engines: keep only if not is_junk and is_proper_noun",
            "separate platforms (names and serp hosts) from businesses",
            "two- or three-word names ending in a geographic word go to places",
            "corroborated if perplexity or google_serp cited, or 2+ engines",
            "rows listed in the COS quarantine file (SERP query drift) are excluded",
            "ad/analytics products and publications (name ends in business journal) go to platforms",
            "verified domain/name aliases cross-linked with same_entity and alias_evidence"
        ],
        "businesses_total": len(biz),
        "businesses_corroborated": sum(1 for b in biz.values() if b.get("corroborated", False)),
        "platforms_total": len(platforms),
        "places_total": len(places),
        "dropped_by_gate": dropped_by_gate,
        "excluded_quarantined": excluded_quarantined,
        "aliases_linked": linked,
        "distinct_entities": len(biz) - linked
    }

    return biz, platforms, places, summary, len(rows), gate


def write_outputs(biz, platforms, places, summary, rows_used, source_sha, out_dir, gate):
    os.makedirs(out_dir, exist_ok=True)
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    idx = {
        "generated_utc": gen, "source_sha256": source_sha, "rows_used": rows_used,
        "gate": gate, "businesses": biz, "platforms": platforms, "places": places
    }
    p1 = os.path.join(out_dir, "citation_index.json")
    p2 = os.path.join(out_dir, "market_summary.json")
    with open(p1, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=True, separators=(",", ":"))
    with open(p2, "w", encoding="utf-8") as f:
        json.dump({"generated_utc": gen, "source_sha256": source_sha, "markets": summary}, f,
                  ensure_ascii=True, indent=1)
    return p1, p2


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def selftest():
    fx = []
    cols = ["date", "kind", "target", "niche", "market", "engine", "businesses_named_count",
            "businesses_named", "first_named", "source_file", "sha256"]

    def row(date, market, engine, names, niche="plumber"):
        return dict(zip(cols, [date, "API", "t", niche, market, engine, str(len(names.split(";"))),
                               names, "", "f.json", "0" * 64]))
    # Acme Plumbing LLC named on 3 dates
    fx.append(row("2026-09-01", "St. Louis MO", "openai", "Acme Plumbing LLC;Phone;Bob's Pipes Inc."))
    fx.append(row("2026-09-02", "St. Louis MO", "openai", "Bob's Pipes;Acme Plumbing, LLC"))
    fx.append(row("2026-09-03", "St. Louis MO", "gemini", "Acme Plumbing;Serves St. Louis;Missouri"))
    # drop: agree engine, old date, empty market
    fx.append(row("2026-09-03", "St. Louis MO", "agree", "Ghost Co"))
    fx.append(row("2026-08-30", "St. Louis MO", "openai", "Old Date Plumbing"))
    fx.append(row("2026-09-03", "", "openai", "No Market Plumbing"))
    # drop rules: short, generic, too many words, city, state
    fx.append(row("2026-09-04", "Kansas City MO", "perplexity",
                  "AB;Kansas City;Certified;Repairs;Specialty;and;or;One Two Three Four Five Six Seven;"
                  "Kansas City Missouri;Ryan Lawn Care"))
    # same business twice in one answer (dedupe), different engines same day
    fx.append(row("2026-09-05", "Kansas City MO", "anthropic", "Ryan Lawn Care;Ryan Lawn Care"))
    fx.append(row("2026-09-05", "Kansas City MO", "google_serp", "Ryan Lawn Care"))
    # V2 fixtures: proper-noun gate, platforms, corroboration
    fx.append(row("2026-09-06", "St. Louis MO", "openai", "Red flags to avoid"))  # should drop (not proper noun)
    fx.append(row("2026-09-06", "St. Louis MO", "openai", "Yelp;ABC Plumbing Inc"))  # Yelp to platforms, ABC to biz
    fx.append(row("2026-09-07", "St. Louis MO", "openai", "SingleCite Business"))  # single cite, single date
    fx.append(row("2026-09-08", "St. Louis MO", "perplexity", "Perplexity Cited Business"))  # corroborated via engine
    # filler to reach more rows
    for i in range(11):
        fx.append(row("2026-09-%02d" % (6 + i % 5), "Denver CO", "openai", "Filler Co %d Services" % i))

    # normalization
    assert normalize("Acme Plumbing, LLC") == "acme plumbing"
    assert normalize("Bob's Pipes Inc.") == "bobs pipes"
    assert normalize("A.B. May Co") == "ab may"
    assert normalize("  Zehl  &  Associates ") == "zehl associates"

    fx.append(row("2026-09-09", "Branson MO", "openai", "Table Rock Lake;Green Hills Dental Group"))
    fx.append(row("2026-09-09", "Branson MO", "google_serp", "https://www.ryanlawncare.com/services;https://book.servicetitan.com/abc?x=1"))
    fx.append(row("2026-09-10", "Denver CO", "anthropic", "Repeat Fragment Works"))
    fx.append(row("2026-09-11", "Denver CO", "anthropic", "Repeat Fragment Works"))
    fx.append(row("2026-09-12", "Denver CO", "google_serp", "https://case.org/a;https://co-case.org/b"))
    biz, platforms, places, summary, used, gate = build(fx)
    assert "caseorg" in biz and "co-case.org" in biz, "two domains that normalize alike stay separate"
    # v2.1 checks
    assert "table rock lake" in places and "table rock lake" not in biz, "geo two-word name goes to places"
    assert "green hills dental group" in biz, "three-word business with geo word stays a business"
    assert "ryanlawncarecom" in biz and biz["ryanlawncarecom"]["corroborated"] is True and biz["ryanlawncarecom"]["name"] == "ryanlawncare.com", "serp host is a corroborated business keyed by normalize(domain)"
    assert "servicetitancom" in platforms and "servicetitancom" not in biz, "booking host is a platform"
    assert biz["repeat fragment works"]["corroborated"] is False, "one engine on two dates is not corroboration"

    # v2 gate checks
    assert "Red flags to avoid".lower() not in biz, "advice phrase should be dropped"
    assert "yelp" in platforms or any("yelp" in k.lower() for k in platforms.keys()), "Yelp should be in platforms"
    assert "abc plumbing" in biz or any("abc plumbing" in k.lower() for k in biz.keys()), "ABC Plumbing should be in biz"

    single_cite = biz.get("singlecite business", None)
    if single_cite is None:
        for k in biz.keys():
            if "singlecite" in k.lower():
                single_cite = biz[k]
                break
    assert single_cite is not None, "SingleCite Business should exist in biz"
    assert single_cite.get("corroborated") == False, "single-cite single-date should have corroborated=false"

    perp_cite = biz.get("perplexity cited business", None)
    if perp_cite is None:
        for k in biz.keys():
            if "perplexity" in k.lower():
                perp_cite = biz[k]
                break
    if perp_cite is not None:
        assert perp_cite.get("corroborated") == True, "perplexity-cited should have corroborated=true"

    # row filter: 3 dropped rows (agree, old date, empty market)
    assert used >= 17, used
    # shape with corroboration
    acme = biz.get("acme plumbing")
    assert acme is not None
    assert set(acme.keys()) == {"name", "citations", "first_seen", "last_seen", "engines", "markets", "count", "corroborated"}
    c0 = acme["citations"][0]
    assert set(c0.keys()) == {"date", "market", "niche", "engine", "rank"}
    # 3 dates -> count 3, display name kept from first sight
    a = acme
    assert a["count"] == 3, a["count"]
    assert a["name"] == "Acme Plumbing LLC", a["name"]
    assert a["first_seen"] == "2026-09-01" and a["last_seen"] == "2026-09-03"
    assert a["engines"] == ["gemini", "openai"] and a["markets"] == ["St. Louis MO"]
    assert a["citations"][0]["rank"] == 1 and a["citations"][1]["rank"] == 2
    # corroborated due to multiple engines
    assert a.get("corroborated") == True, "multi-engine should be corroborated"

    # dropped rows never appear
    for bad in ("ghost", "old date plumbing", "no market plumbing"):
        assert bad not in biz and bad not in platforms, bad
    # drop rules
    fx_url = build([row("2026-09-09", "Denver CO", "openai", "https://example.com/x;Real Roofing")])[:2]
    biz_url = fx_url[0]
    assert "real roofing" in biz_url and len(biz_url) == 1, "URL should be dropped, Real Roofing kept"
    for bad in ("ab", "kansas city", "certified", "repairs", "specialty", "and", "or", "phone",
                "serves st louis", "missouri", "kansas city missouri"):
        assert bad not in biz and bad not in platforms, bad
    assert not any(len(k.split()) > 6 for k in biz.keys())

    # v2.2 gate: tools and publications to platforms; verified alias cross-linked
    fx22 = [row("2026-09-08", "St. Louis MO", "openai", "Google Ads;St. Louis Business Journal;Timmermann Group"),
            row("2026-09-20", "St. Louis MO", "google_serp", "https://www.wearetg.com/"),
            row("2026-09-21", "St. Louis MO", "openai", "Atomic Dust Studio")]
    b22, p22 = build(fx22)[:2]
    assert "google ads" in p22 and "google ads" not in b22, "Google Ads is a tool"
    assert "st louis business journal" in p22 and "st louis business journal" not in b22, "publication"
    assert b22["timmermann group"].get("same_entity") == "wearetgcom", b22.get("timmermann group")
    assert b22["wearetgcom"].get("same_entity") == "timmermann group"
    assert "same_entity" not in b22.get("atomic dust studio", {}), "no alias without evidence"

    # dedupe within an answer; two engines same day are two citations
    # Note: google_serp "Ryan Lawn Care" is skipped in v2 gate because it's not a URL (url_to_host returns None)
    r = biz.get("ryan lawn care")
    assert r is not None
    assert r["count"] == 2 and r["engines"] == ["anthropic", "perplexity"], r
    # market summary
    s = summary["St. Louis MO"]
    assert s["run_days"] >= 1 and "openai" in s["engines"]
    assert s["distinct_businesses"] >= 2 and s["first_date"] <= "2026-09-08"

    # 1005A: quarantined source files are excluded
    qrows = [row("2026-09-29", "Dallas TX", "google_serp", "https://www.zehllaw.com/truck", niche="pi")]
    qrows[0]["source_file"] = "NB-CZ-SERP_q.json"
    b2, _, _, _, _, g2 = build(fx + qrows, exclude_files=frozenset({"NB-CZ-SERP_q.json"}))
    assert g2["excluded_quarantined"] == 1, g2
    assert not any("zehllaw" in k for k in b2), "quarantined row leaked"
    b3, _, _, _, _, g3 = build(fx + qrows)
    assert g3["excluded_quarantined"] == 0
    assert any("zehllaw" in k for k in b3), "control: the same row must be kept when not quarantined"

    # output files with new keys
    with tempfile.TemporaryDirectory() as d:
        p1, p2 = write_outputs(biz, platforms, places, summary, used, "f" * 64, d, gate)
        j = json.load(open(p1))
        expected_keys = {"generated_utc", "source_sha256", "rows_used", "businesses", "platforms", "places", "gate"}
        assert set(j.keys()) == expected_keys, f"Expected {expected_keys}, got {set(j.keys())}"
        assert j["rows_used"] >= 17 and j["source_sha256"] == "f" * 64
        assert "version" in j["gate"] and j["gate"]["version"] == "v2.2-tools-publications-aliases-2026-10-04"
        m = json.load(open(p2))
        assert "St. Louis MO" in m["markets"]

    print("selftest OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=DEFAULT_CSV)
    ap.add_argument("--out-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--exclude-csv", default=DEFAULT_QUARANTINE, help="quarantine CSV with a source_file column; missing file = exclude nothing")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return
    sha = sha256_file(a.csv)
    rows = read_csv(a.csv)
    excl = frozenset()
    if a.exclude_csv and os.path.exists(a.exclude_csv):
        excl = frozenset(os.path.basename(r.get("source_file", "")) for r in read_csv(a.exclude_csv) if r.get("source_file"))
    print("quarantine file:    %s (%d source files)" % (a.exclude_csv, len(excl)))
    biz, platforms, places, summary, used, gate = build(rows, exclude_files=excl)
    print("excluded (quarantine): %d" % gate["excluded_quarantined"])
    p1, p2 = write_outputs(biz, platforms, places, summary, used, sha, a.out_dir, gate)
    cites = sum(b["count"] for b in biz.values())
    plat_cites = sum(b["count"] for b in platforms.values())
    dates = sorted({c["date"] for b in biz.values() for c in b["citations"]} |
                   {c["date"] for b in platforms.values() for c in b["citations"]})
    print("csv rows read:      %d" % len(rows))
    print("rows used:          %d" % used)
    print("businesses kept:    %d" % len(biz))
    print("platforms found:    %d" % len(platforms))
    print("citations (biz):    %d" % cites)
    print("citations (plat):   %d" % plat_cites)
    print("markets:            %d" % len(summary))
    print("dropped by gate:    %d" % gate["dropped_by_gate"])
    print("corroborated biz:   %d" % gate["businesses_corroborated"])
    print("date range:         %s to %s (%d run days)" % (dates[0], dates[-1], len(dates)))
    print("citation_index.json %.2f MB" % (os.path.getsize(p1) / 1e6))
    print("source_sha256:      %s" % sha)
    print("top 10 most-cited businesses:")
    for k, b in sorted(biz.items(), key=lambda kv: (-kv[1]["count"], kv[0]))[:10]:
        print("  %5d  %s  [%d markets, %d engines, corr=%s]" % (b["count"], b["name"], len(b["markets"]), len(b["engines"]), b["corroborated"]))
    print("top 10 most-cited platforms:")
    for k, b in sorted(platforms.items(), key=lambda kv: (-kv[1]["count"], kv[0]))[:10]:
        print("  %5d  %s  [%d markets, %d engines]" % (b["count"], b["name"], len(b["markets"]), len(b["engines"])))


if __name__ == "__main__":
    main()
