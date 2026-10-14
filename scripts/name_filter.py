#!/usr/bin/env python3
"""name_filter.py: NON-BUSINESS filter v2 for the derived answers columns (2026-10-14, [FABLE-PARALLEL-1013E], COS wave 3 item 1).

The engines, Claude above all, put section labels inside the lists the extractor reads as business names
("Research approaches", "Red flags to avoid", "Next steps", "Questions to ask", "Ways to research", "Research methods"),
plus bare place names ("Georgia", "Cook County") and directories or platforms ("Yelp", "Google Maps"). Heading filter v1
in build_edition.py (2026-10-09) catches the all-generic-word case; this module catches the rest.

RULES (a name is dropped when any rule fires; KEEP overrides every rule):
  H  heading label: the name, case folded and stripped of outer punctuation, is in HEADING_LABELS (hand reviewed list
     with the labels named by the COS on 2026-10-14 and the 2026-10-09 heading terms the v1 word rule misses).
  S  sentence-case label: three or more words, no word after the first is capitalised, no business marker
     (&, and, Inc, LLC, Co, Corp, Ltd, P.C, PLLC, Group, Law, digits, an apostrophe-s, a dot inside) and no
     word is a proper noun the record already knows (a token list is not kept; the rule is purely orthographic).
     Two-word sentence-case names are dropped by S only when rule P also fires (so "Home instead" style names survive
     unless they sit in heading position in the text).
  P  heading position in the raw answer: the name is a whole line on its own (after markdown markers #, *, _, -, digits
     and dots are stripped), with or without a trailing colon, AND rule S's orthographic test (sentence case, no marker)
     holds for it. A business written as "**Planet Fitness**: open 24 hours" keeps its Title Case and is never touched.
  G  place: a US state, a "<word> County" form, a city from the record's market list, "Downtown <city>", or a "City, ST"
     form (two capital letters after a comma).
  D  directory or platform: the name is in PLATFORMS, or is a bare domain (yelp.com, angi.com).

Everything is DERIVED. The raw files in corpus/e1 and the raw businesses_named column are never changed; the derived
columns businesses_named_filtered and nonbusiness_removed_count are written by build_edition.py from the build that
carries PACK.json name_filter "v2" onward (CORRECTIONS.md policy: a rule applies from a stated date forward).

Usage:
    python3 -I scripts/name_filter.py --selftest
    python3 -I scripts/name_filter.py --name "Red flags to avoid" [--text FILE]   # prints the rule that fires, or KEEP
Standard library only.
"""
import argparse
import io
import re
import sys

VERSION = 'v2'
EFFECTIVE_FROM = '2026-10-14'

# H: heading labels, case folded, outer punctuation stripped. Seed = the six labels from the COS 2026-10-14 brief plus the
# section labels seen in the 1014A samples and the 1013B-L7B label delta (see status/returns/1014B-W3-H1-STOPLIST.md).
HEADING_LABELS = set('''
research approaches|red flags to avoid|next steps|questions to ask|ways to research|research methods
research approach|research method|search methods|search method|call ahead|ask locals|red flags|red flag|next step|questions to ask them|questions to ask before hiring
how to research|how to verify|how to choose|how to find|how to evaluate|how to compare|how to check
what to look for|what to ask|what to check|what to avoid|what to consider|what to expect|what to know
things to consider|things to look for|things to check|things to avoid|key considerations|key factors|key questions
general advice|general tips|general guidance|practical tips|practical steps|action steps|steps to take|suggested steps
my recommendation|my suggestion|my advice|recommendation|recommendations|suggestions|suggestion|advice
summary|overview|conclusion|bottom line|the bottom line|final thoughts|key takeaways|takeaways|in summary
note|notes|important note|important notes|important|caveat|caveats|disclaimer|limitations|context
pros|cons|pros and cons|benefits|drawbacks|strengths|weaknesses|considerations|other considerations
alternatives|other options|additional options|additional resources|resources|local resources|online resources
verification steps|verification|due diligence|background check|background checks|credentials to check|licensing
where to look|where to search|where to start|where to find|search strategies|search tips|search approach
local directories|online directories|directories|review sites|review platforms|referral sources|referrals
word of mouth|ask locals|ask neighbors|ask for referrals|get multiple quotes|compare quotes|get quotes
questions|checklist|evaluation criteria|criteria|selection criteria|factors to consider|factors
warning signs|warning sign|signs of a scam|scam warning signs|common scams|avoid these
timeline|budget|pricing|costs|cost factors|cost considerations|insurance|warranty|licensing and insurance
'''.replace('\n', '|').split('|'))
HEADING_LABELS = {t.strip() for t in HEADING_LABELS if t.strip()}

US_STATES = set('''alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho
illinois indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota mississippi missouri montana
nebraska nevada ohio oklahoma oregon pennsylvania tennessee texas utah vermont virginia washington wisconsin wyoming'''.split())
US_STATES |= {'new hampshire', 'new jersey', 'new mexico', 'new york', 'north carolina', 'north dakota', 'rhode island',
              'south carolina', 'south dakota', 'west virginia', 'district of columbia', 'washington dc', 'washington, dc'}

# cities and areas the record asks about (e1_runner._PLAIN_PLACES, 2026-10-14) plus their common forms
PLACES = {
    'atlanta', 'branson', 'charlotte', 'chicago', 'columbus', 'dallas', 'denver', 'fort lauderdale', 'houston', 'joplin',
    'kansas city', 'north kansas city', 'liberty', 'los angeles', 'miami', 'nashville', 'phoenix', 'pleasanton',
    'san francisco', 'santa cruz', 'st. charles', 'st charles', 'st. louis', 'st louis', 'saint louis', 'st. peters',
    'st peters', 'tyler', 'west palm beach', 'overland park', 'bay area', 'the bay area', 'ozarks', 'the ozarks',
    'table rock lake', 'branson landing', 'silver dollar city', 'east nashville', 'miami-dade county', 'miami-dade',
    'st. charles county', 'cook county', 'broward county', 'palm beach county', 'harris county', 'dallas county',
    'maricopa county', 'fulton county', 'davidson county', 'alameda county', 'santa cruz county', 'jackson county',
    'clay county', 'jasper county', 'smith county', 'mecklenburg county', 'franklin county', 'south florida',
    'southern california', 'northern california', 'central florida', 'north texas', 'east texas', 'midwest',
    'united states', 'usa', 'us', 'nationwide'}

# directories and platforms (e1_runner._PLAIN_PLATFORMS and gen_citation_index PLATFORM_NAMES, 2026-10-14)
PLATFORMS = {
    'airbnb', 'vrbo', 'yelp', 'youtube', 'clutch', 'clutch.co', 'realtor.com', 'homeadvisor', 'home advisor',
    "angie's list", 'angies list', 'angi', 'super lawyers', 'zillow', 'homelight', 'facebook', 'healthcare.gov',
    'bbb', 'better business bureau', 'avvo', 'google business', 'google business profile', 'google maps', 'google',
    'google reviews', 'nextdoor', 'thumbtack', 'linkedin', 'reddit', 'tiktok', 'instagram', 'paypal', 'yellow pages',
    'yellowpages', 'houzz', 'porch', 'bark', 'expertise.com', 'expertise', 'findlaw', 'justia', 'martindale',
    'martindale-hubbell', 'lawyers.com', 'nolo', 'healthgrades', 'zocdoc', 'vitals', 'webmd', 'care.com',
    'trustpilot', 'glassdoor', 'indeed', 'craigslist', 'booking.com', 'tripadvisor', 'airdna', 'apartments.com',
    'redfin', 'trulia', 'loopnet', 'crexi', 'bankrate', 'nerdwallet', 'lendingtree', 'consumer reports',
    'chamber of commerce', 'local chamber of commerce', 'state bar', 'state bar association', 'bar association',
    'nar', 'national association of realtors', 'gaf', 'owens corning', 'certainteed', 'chatgpt', 'perplexity', 'gemini',
    'bing', 'apple maps', 'waze', 'twitter', 'x'}

KEEP = {'pro lawn & irrigation', 'pro lawn'}   # same override as build_edition.KEEP_NAMES

MARKER = re.compile(r"(&|\band\b|\binc\b|\bllc\b|\bco\b|\bcorp\b|\bltd\b|\bp\.?c\b|\bpllc\b|\bgroup\b|\blaw\b|\bfirm\b|"
                    r"\bassociates\b|\bpartners\b|\bservices\b|\d|'s\b|\w\.\w)", re.I)
CITY_ST = re.compile(r',\s*[A-Z]{2}$')
DOMAIN = re.compile(r'^[a-z0-9][a-z0-9.-]*\.(com|org|net|gov|co|io|ai)$', re.I)
COUNTY = re.compile(r'^[A-Z][A-Za-z.\- ]+ County(,\s*[A-Z]{2})?$')
DOWNTOWN = re.compile(r'^(downtown|greater|metro|north|south|east|west|central)\s+[A-Z][a-z]+$', re.I)
LINE_MARK = re.compile('^[\\s#*_\\-\\u2022\\d.\\)\\(]+|[\\s*_:]+$')


def key(name):
    return ' '.join(str(name).replace('\u2019', "'").casefold().split()).strip(' :*-.,#')


def words(name):
    return [w for w in re.split(r'\s+', ' '.join(str(name).split())) if w]


def sentence_case(name):
    """True when the name has two or more words and no word after the first starts with a capital letter."""
    w = words(name)
    if len(w) < 2:
        return False
    return all(not x[:1].isupper() for x in w[1:])


def has_marker(name):
    return bool(MARKER.search(str(name)))


def heading_position(name, text):
    """True when the name is a whole line of the answer on its own (markdown markers stripped), colon or not."""
    if not text:
        return False
    k = key(name)
    for line in str(text).splitlines():
        s = LINE_MARK.sub('', line.strip())
        s = LINE_MARK.sub('', s)
        if key(s) == k:
            return True
    return False


def reason(name, text=''):
    """The rule letter that drops the name (H, S, P, G, D) or None to keep it."""
    k = key(name)
    if not k or k in KEEP:
        return None
    if k in HEADING_LABELS:
        return 'H'
    if DOMAIN.match(k) or k in PLATFORMS:
        return 'D'
    kk = CITY_ST.sub('', str(name).strip()).casefold().strip()
    if k in US_STATES or kk in US_STATES or kk in PLACES or k in PLACES or COUNTY.match(str(name).strip()) \
            or DOWNTOWN.match(str(name).strip()) or (CITY_ST.search(str(name).strip()) and len(words(name)) <= 3 and not has_marker(name)):
        return 'G'
    sc = sentence_case(name) and not has_marker(name)
    if sc and len(words(name)) >= 3:
        return 'S'
    if sc and heading_position(name, text):
        return 'P'
    return None


def apply(names, text=''):
    """Returns (kept list, removed count)."""
    kept = [n for n in names if reason(n, text) is None]
    return kept, len(names) - len(kept)


# ------------------------------------------------------------------ selftest
DROP_CASES = [
    ('Research approaches', '', 'H'), ('Red flags to avoid', '', 'H'), ('Next steps', '', 'H'), ('Questions to ask', '', 'H'),
    ('Ways to research', '', 'H'), ('Research methods', '', 'H'), ('Georgia', '', 'G'), ('Cook County', '', 'G'),
    ('Yelp', '', 'D'), ('Google Maps', '', 'D'), ('angi.com', '', 'D'), ('Miami, FL', '', 'G'), ('Downtown Chicago', '', 'G'),
    ('Things you should verify before signing', '', 'S'),
    ('Local permit requirements', '', 'S'),
    ('Permit requirements', 'Some text\n**Permit requirements:**\nmore text', 'P'),
    ('Research Approaches:', '', 'H'), ('St. Louis', '', 'G'), ('Better Business Bureau', '', 'D'),
]
KEEP_CASES = [
    'Jung Platform', 'Planet Fitness', "Jeni's Splendid Ice Creams", 'Advertising Savants', 'Home Care Pulse',
    'Roaring Camp', 'John Foy & Associates', 'Salvi, Schostok & Pritchard P.C', 'Extreme Roofing Inc', 'Pro Lawn',
    'BBS Insurance', 'A-1 Property Services', 'Home instead', 'Flatline Contracting',
]


def selftest():
    fails = []

    def chk(name, ok):
        print(('  ok   ' if ok else '  FAIL ') + name)
        if not ok:
            fails.append(name)
    for n, t, r in DROP_CASES:
        chk('drop %-45s rule %s' % (repr(n), r), reason(n, t) == r)
    for n in KEEP_CASES:
        chk('keep %-45s' % repr(n), reason(n, '') is None)
    # RED proofs
    chk('RED: a Title Case business followed by a colon in the text is kept (list item, not a heading)',
        reason('Planet Fitness', '**Planet Fitness**: open 24 hours') is None)
    chk('RED: a two-word sentence-case name NOT in heading position is kept', reason('Home instead', 'Home instead is a franchise.') is None)
    chk('RED: the same two-word name on its own line is dropped by P', reason('Home instead', 'Options:\n**Home instead**\nmore') == 'P')
    chk('apply() counts removals', apply(['Yelp', 'Planet Fitness', 'Next steps'], '') == (['Planet Fitness'], 2))
    chk('at least 20 cases', len(DROP_CASES) + len(KEEP_CASES) >= 20)
    print('SELFTEST ' + ('FAIL: ' + ', '.join(fails) if fails else 'PASS') + ' (%d cases)' % (len(DROP_CASES) + len(KEEP_CASES) + 5))
    return not fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--name')
    ap.add_argument('--text', help='file with the raw answer text, for rule P')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if a.name:
        text = io.open(a.text, encoding='utf-8').read() if a.text else ''
        print(reason(a.name, text) or 'KEEP')
        return 0
    ap.print_help()
    return 1


if __name__ == '__main__':
    sys.exit(main())
