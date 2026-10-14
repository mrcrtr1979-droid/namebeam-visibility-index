#!/usr/bin/env python3
"""DQ-5 counts for Edition 1 (lane 1013E-W1): a saved, reproducible recount of the three DQ-5 columns.

For each engine, over API answers with run_status OK in the window:
  (a) ok           = number of OK answers (one per raw file)
  (b) recorded_top10 = answers whose recorded name list (competitors_mentioned, as written in the raw file) holds a
                       business that is in that question's top 10 list (case-insensitive equality)
  (c) text_top10   = answers whose answer_verbatim contains a top 10 business name as a whole name
                       (case-insensitive; the characters before and after the name must not be letters or digits)
  days             = distinct OK answer days for the engine (the denominator used for day-level counts)

The top 10 list per market x niche question is built with the rule of mention_counts.py (1012B-L9), ported here
without shelling out: the candidate names come from the Edition 1 name gate (releases/edition-1/method, the
1005A gate module and the build_edition heading filter, both inside this repo), the day counts are whole-name
text mentions on distinct OK answer days, and the top 10 are ranked by the highest engine day count (ties: more
engines, then name). A question enters the top 10 set when it has at least --min-answers OK answer days
summed over the four engines.

Standard library, no third-party packages (the gate modules are repo files). Reads the corpus; writes nothing.

    python3 -I scripts/dq5_counts.py --corpus corpus/e1 --start 2026-09-01 --end 2026-10-11 [--top 10 --min-answers 20]
    python3 -I scripts/dq5_counts.py --selftest
"""
import sys, os, re, json, glob, unicodedata, argparse, tempfile, shutil, itertools
from collections import defaultdict

sys.dont_write_bytecode = True
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_DIR = None  # set by main() before read_corpus runs

# ------------------------------------------------------------------ name gate and mention rule (ported from mention_counts.py)
ENG = ['openai', 'perplexity', 'gemini', 'anthropic']
LAB = {'openai': 'OpenAI', 'anthropic': 'Anthropic', 'perplexity': 'Perplexity', 'gemini': 'Gemini'}
RUN_DENY = {'spoliation letter', 'board certification', 'manual d', 'epa 608', 'fort worth', 'houston lawyer referral service',
            'georgia power', 'av preeminent'}
NON_BUSINESS = {'labor day', 'google business', 'google business profile', 'ad club'}  # G3 deny set
SUFFIX = {'law', 'firm', 'offices', 'office', 'llp', 'llc', 'pllc', 'pc', 'p', 'c', 'pa', 'inc', 'co', 'company'}
# generic phrases: kept out of business rankings, reported in a separate guard list
GUARD_EXACT = {'google maps', 'google', 'yelp', 'bing', 'bing maps', 'apple maps', 'facebook', 'nextdoor', 'reddit', 'angi', 'angies list',
               'homeadvisor', 'thumbtack', 'bbb', 'better business bureau', 'avvo', 'findlaw', 'justia', 'nolo', 'lawyers com',
               'super lawyers', 'martindale hubbell', 'martindale', 'best lawyers', 'national chains', 'national chain', 'chains',
               'zillow', 'realtor com', 'trulia', 'redfin', 'tripadvisor', 'healthgrades', 'zocdoc', 'vitals', 'groupon',
               'consumer reports', 'angi leads', 'expertise com', 'houzz', 'porch', 'airbnb', 'vrbo', 'booking com', 'mapquest',
               'chamber of commerce', 'local chamber of commerce', 'state bar', 'lawyer referral service', 'yellow pages', 'yp',
               'editors choice', 'editor choice', 'new patient specials', 'june gloom', 'part d', 'preserve evidence', 'table rock', 'table rock lake', 'st charles', 'lake st louis', 'hurricane zone', 'ruby hill', 'short north', 'music row', 'northern coast', 'seacliff pier', 'nisene marks', 'san francisco', 'santa cruz',
               'cars com', 'carfax', 'tire rack', 'google reviews', 'google business', 'linkedin', 'instagram', 'tiktok', 'youtube'}
GUARD_RE = re.compile(r'\b(bar association|bar foundation|bar of|trial lawyers|association of|lawyer referral|referral service|'
                      r'chamber of commerce|national chains?|local chains?|directory|directories|review sites?|rating sites?|'
                      r'near me|top rated|best of|search results|state bar|board of|department of|attorney general|'
                      r'statute of limitations|liability|compensation|compliance|medicare|medicaid|certifications?|warranty|price ratio|'
                      r'hurricane zone|hvhz|miami dade|location and hours|office hours|area agency|uv beds?|memorial day|labor day|fourth of july|july 4th|'
                      r'(?:north|south|east|west)ern (?:california|texas|missouri|illinois)|bay area|tri valley|silicon valley|'
                      r'highway \d+)\b|\b(?:rd|ave|blvd|hwy)$')
COMMON_ONE = set('''the best top good great local first premier elite prime reliable quality family expert experts national american
united central north south east west city state county metro summit pioneer liberty freedom eagle patriot champion legacy
advanced precision superior express rapid quick fast action ace pro pros trusted certified licensed professional pro service
services company companies team group associates partners pointe point point peak star stars sun sunny solar gold golden
blue red green white black new old modern classic heritage direct total complete all affordable budget discount value
red flags overview summary notes tips pricing price cost costs'''.split())


def clean(s):
    s = s.replace('\u2013', '-').replace('\u2014', '-').replace('\u2019', "'").replace('\u2018', "'")
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'\s+', ' ', s).strip()


def norm_text(s):
    """Lowercase ASCII text with possessives dropped, '&' as 'and', punctuation as single spaces."""
    t = clean(s).casefold()
    t = re.sub(r"'s\b", '', t)
    t = t.replace('&', ' and ').replace('.', '').replace("'", '')
    t = re.sub(r'[^a-z0-9 ]', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()


def stem(name):
    """Normalised name with legal suffix words (and an initial 'the') removed."""
    w = norm_text(name).split()
    while len(w) > 1 and w[-1] in SUFFIX:
        w.pop()
    if len(w) > 1 and w[0] == 'the':
        w = w[1:]
    return ' '.join(w)


def skeleton(k):
    """A stem without middle initials, used just to see that two spellings are one name."""
    w = k.split()
    if len(w) > 2:
        w = [w[0]] + [t for t in w[1:-1] if len(t) > 1] + [w[-1]]
    return w


def canon(disp):
    t = norm_text(disp)
    if t in RUN_DENY or t in NON_BUSINESS:
        return None
    c = stem(disp)
    if c in RUN_DENY or c in NON_BUSINESS or not c:
        return None
    return c


FUNC = {'for', 'by', 'your', 'our', 'in', 'on', 'with', 'to', 'of', 'at', 'from', 'and', 'or', 'a', 'an', 'the', 'is', 'near', 'how', 'what'}
VERBS = {'preserve', 'document', 'join', 'pick', 'confirm', 'ask', 'verify', 'check', 'get', 'call', 'avoid', 'compare', 'request', 'review', 'choose', 'contact', 'notable', 'beware', 'around'}
INSTITUTION = {'board', 'administration', 'commission', 'society', 'council', 'association', 'department', 'bureau', 'regulation', 'roc'}


def is_guard(name):
    c = stem(name)
    w = c.split()
    if c in GUARD_EXACT or GUARD_RE.search(c):
        return True
    if len(w) > 1 and (w[0] in FUNC or w[-1] in FUNC or w[0] in VERBS) or w[0] in {'your', 'for', 'filter'}:
        return True  # sentence fragment, not a name
    if 'based' in w or raw_has_based(name):
        return True
    return bool(INSTITUTION & set(w)) and len(w) > 1 and w[0] not in {'pleasanton'} and not re.search(r'\b(dental|law|roofing|heating|air|plumbing)\b', c)


def raw_has_based(name):
    return bool(re.search(r'-based\b', clean(name), re.I))


def make_matcher(displays):
    """Compile one whole-word regex over the stems of the display variants. Single-word stems are kept when
    they are not common words and are at least 4 letters; those are matched against the original case (capitalised)."""
    multi, single = set(), set()
    for d in displays:
        s = stem(d)
        if not s or s in COMMON_ONE:
            continue
        if ' ' in s:
            multi.add(s)
        elif len(s) >= 4 and re.match(r'^[A-Za-z]', clean(d)):
            single.add(clean(d).split()[0].strip(".,;:()'\"") if len(clean(d).split()) == 1 else s)
    pats = [r'(?<![a-z0-9])' + r'\s+(?:[a-z]\s+)?'.join(map(re.escape, m.split())) + r'(?![a-z0-9])' for m in sorted(multi, key=len, reverse=True)]
    rx = re.compile('|'.join(pats)) if pats else None
    srx = None
    if single:
        sp = ['(?<![A-Za-z0-9])' + re.escape(x[:1].upper() + x[1:]) + r"(?![A-Za-z0-9])" for x in sorted(single)]
        srx = re.compile('|'.join(sp))
    return rx, srx


class Ans(object):
    """One OK answer with its normalised and cleaned text computed once."""
    __slots__ = ('date', 'raw', 'nt', 'ct')

    def __init__(self, date, text):
        self.date, self.raw = date, text or ''
        self.ct = clean(self.raw)
        self.nt = norm_text(self.raw)


def _asAns(x):
    return x if isinstance(x, Ans) else Ans(x[0], x[1])


def mentions(text, matcher):
    a = _asAns(('', text)) if not isinstance(text, Ans) else text
    rx, srx = matcher
    if rx and rx.search(a.nt):
        return True
    if srx and srx.search(a.ct):
        return True
    return False


def named_days(answers, matcher):
    """answers = [(date, text)] or [Ans] for OK rows of one engine; returns the set of dates on which any answer mentions."""
    out = set()
    for x in answers:
        a = _asAns(x)
        if a.date not in out and a.raw and mentions(a, matcher):
            out.add(a.date)
    return out


def lowercase_rows(answers, variants):
    """(rows mentioning, rows where the name appears in lowercase running text). A real business name is not written in
    lowercase; a phrase such as 'fee structure' or 'general liability' is, and it is a topic word, not a business."""
    stems = {stem(v) for v in variants if stem(v)}
    pats = []
    for st in stems:
        toks = [r'(?:and|&)' if t == 'and' else re.escape(t) for t in st.split()]
        pats.append(r'(?<![A-Za-z0-9])' + r"['\u2019.,]?\s+".join(toks) + r'(?![A-Za-z0-9])')
    if not pats:
        return 0, 0
    rx = re.compile('|'.join(pats), re.I)
    n = low = 0
    for x in answers:
        a = _asAns(x)
        hits = [m.group(0) for m in rx.finditer(a.ct)]
        if hits:
            n += 1
            if any(h[:1].islower() for h in hits):
                low += 1
    return n, low


def is_generic_by_usage(answers, variants):
    n, low = lowercase_rows(answers, variants)
    return low >= 3 and low * 100 >= n * 15


# ------------------------------------------------------------------ corpus + gate
def load_gate(repo):
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.join(repo, 'scripts'))
    import build_edition as be
    M = os.path.join(repo, 'releases/edition-1/method/Brain_Kit/status/staged')
    sys.path.insert(0, os.path.join(M, '1005A_AI_PICKS_CHART'))
    import gen_ai_picks_chart as g
    q = g.load_quarantine(os.path.join(M, '..', '..', 'returns', 'COMET_1004', 'SERP_DRIFT_QUARANTINE_2026-10-04.csv'))
    return be, g, q


def candidates(ok_rows, g, quar):
    """Gate output -> {canon: set(display variants)} over all engines of one pair."""
    del g.MERGE_LOG[:]
    data, _ = g.aggregate(ok_rows, quar)
    var = defaultdict(set)
    for mk in data:
        for ws in data[mk]:
            for e in data[mk][ws]:
                for key, (disp, days) in data[mk][ws][e]['named'].items():
                    c = canon(disp)
                    if c:
                        var[c].add(disp)
    for (mk, wk, short, long_) in g.MERGE_LOG:  # recorded display variants from the alias merge
        c = canon(long_)
        if c:
            var[c].add(short)
    return var


def build_pair(mk, ni, cell, g, quar):
    ok = [r for r in cell if r['status'] == 'OK']
    asked = {e: sorted({r['date'] for r in ok if r['engine'] == e}) for e in ENG}
    var = candidates(ok, g, quar)
    ans = {e: [Ans(r['date'], r['answer']) for r in ok if r['engine'] == e] for e in ENG}
    allans = [a for e in ENG for a in ans[e]]
    cnt, guard, raw = {}, {}, {}
    for c, vs in var.items():
        allv = vs | {c}
        m = make_matcher(allv)
        per = {e: named_days(ans[e], m) for e in ENG}
        if not any(per.values()):
            continue
        if is_guard(c) or any(is_guard(v) for v in vs if stem(v) == c) or is_generic_by_usage([a for a in allans if mentions(a, m)], allv):
            guard[c] = (c, {e: len(per[e]) for e in ENG}, sorted(vs))
        else:
            raw[c] = (allv, per)
    # one business, one row: a name whose words sit inside a longer candidate is the same business (its mentions are the
    # union of both); a single word merges just when its counts equal the longer name's on every engine
    keys = sorted(raw, key=lambda k: (-len(k.split()), k))
    root = {k: k for k in keys}

    def find(k):
        while root[k] != k:
            k = root[k]
        return k
    PRE = {'law', 'offices', 'office', 'of', 'firm', 'attorney', 'attorneys', 'the', 'dr', 'mr', 'mrs', 'ms', 'forbes'}

    def inside(a, b):
        return _inside(a.split(), b.split()) or _inside(skeleton(a), skeleton(b))

    def _inside(wa, wb):
        if len(wa) >= len(wb):
            return False
        if wb[:len(wa)] == wa:
            return True  # business name followed by a place or qualifier
        if wb[-len(wa):] == wa:
            return len(wa) == 1 or set(wb[:-len(wa)]) <= PRE  # a lone surname is checked by the equal-counts test below
        return False
    for a in keys:
        longer = [b for b in keys if inside(a, b)]
        if not longer:
            continue
        if len(a.split()) == 1:
            longer = [b for b in longer if all(len(raw[a][1][e]) == len(raw[b][1][e]) for e in ENG)]
            if not longer:
                continue
        for b in longer:
            ra, rb = find(a), find(b)
            if ra != rb:
                root[ra] = rb
    groups = defaultdict(list)
    for k in keys:
        groups[find(k)].append(k)
    for r_, mem in groups.items():
        allv = set().union(*[raw[k][0] for k in mem])
        per = {e: set().union(*[raw[k][1][e] for k in mem]) for e in ENG}
        mem_multi = [k for k in mem if len(k.split()) > 1] or mem
        shortest = min(mem_multi, key=lambda k: (len(k.split()), len(k), k))
        disp = min([v for v in raw[shortest][0] if stem(v) == shortest and v != v.lower()] or [v for v in allv if v != v.lower()] or [shortest.title()], key=lambda x: (len(x), x))
        cnt[r_] = (disp, {e: len(per[e]) for e in ENG}, sorted(allv))
    return asked, cnt, guard


def tops(cnt, e):
    rk = sorted((c for c in cnt if cnt[c][1][e] > 0), key=lambda c: (-cnt[c][1][e], c))
    if not rk:
        return {'top': [], 'withties': [], 'top1': [], 'top1_strict': []}
    cut = cnt[rk[4]][1][e] if len(rk) >= 5 else cnt[rk[-1]][1][e]
    best = cnt[rk[0]][1][e]
    return {'top': rk[:5], 'withties': [c for c in rk if cnt[c][1][e] >= cut],
            'top1': [c for c in rk if cnt[c][1][e] == best], 'top1_strict': rk[:1]}




# ------------------------------------------------------------------ DQ-5 counting (lane 1013E-W1)
def read_corpus(repo, be, W0, W1):
    """Gate rows for the window: API rows with status, answer text, and recorded names (heading filter applied)."""
    rows = be.answers_rows(CORPUS_DIR)
    out = []
    for r in rows:
        if r['kind'] != 'API' or r['engine'] not in ENG or not (W0 <= r['date'] <= W1):
            continue
        j = json.load(open(os.path.join(CORPUS_DIR, r['source_file']), encoding='utf-8'))
        r['status'] = j.get('run_status')
        r['answer'] = j.get('answer_verbatim') or ''
        names = [n for n in r['businesses_named'].split(';') if n]
        r['businesses_named'] = ';'.join(n for n in names if be.heading_reason(n) is None)
        out.append(r)
    return out


def load_api_rows(corpus_dir, W0, W1):
    """Raw API rows for the DQ-5 counts: one dict per raw file in the window (any run_status)."""
    out = []
    for path in sorted(glob.glob(os.path.join(corpus_dir, 'NB-CZ-API_*.json'))):
        fn = os.path.basename(path)
        j = json.load(open(path, encoding='utf-8'))
        date = j.get('date_utc') or fn.split('_')[1]
        engine = j.get('engine', '')
        if engine not in ENG or not (W0 <= date <= W1):
            continue
        out.append({'file': fn, 'date': date, 'engine': engine,
                    'market': j.get('market', '') or '', 'niche': j.get('niche', '') or '',
                    'status': j.get('run_status'), 'answer': j.get('answer_verbatim') or '',
                    'recorded': [str(n) for n in (j.get('competitors_mentioned') or []) if str(n).strip()]})
    return out


def build_tops(repo, W0, W1, minans, top):
    """{(market, niche): [top business display names]} with the mention_counts.py rule."""
    be, g, quar = load_gate(repo)
    rows = read_corpus(repo, be, W0, W1)
    tops = {}
    for mk, ni in sorted({(r['market'], r['niche']) for r in rows}):
        cell = [r for r in rows if r['market'] == mk and r['niche'] == ni]
        if not mk.strip() or not ni.strip() or 'LOCATION NOT CAPTURED' in mk:
            continue
        asked, cnt, guard = build_pair(mk, ni, cell, g, quar)
        if sum(len(asked[e]) for e in ENG) < minans:
            continue
        order = sorted((c for c in cnt if any(cnt[c][1].values())),
                       key=lambda c: (-max(cnt[c][1].values()), -len([e for e in ENG if cnt[c][1][e]]), cnt[c][0].casefold()))
        tops[(mk, ni)] = [clean(cnt[c][0]) for c in order[:top]]
    return tops


def name_regex(name):
    """Whole-name, case-insensitive pattern: no letter or digit may touch either end of the name."""
    return re.compile(r'(?<![A-Za-z0-9])' + re.escape(name) + r'(?![A-Za-z0-9])', re.I)


def dq5_counts(api_rows, tops):
    """Per engine: ok, recorded_top10, text_top10, days (see the module docstring)."""
    res = {e: {'ok': 0, 'rec': 0, 'text': 0, 'days': set()} for e in ENG}
    cache = {}
    for r in api_rows:
        if r['status'] != 'OK':
            continue
        c = res[r['engine']]
        c['ok'] += 1
        c['days'].add(r['date'])
        pair = (r['market'], r['niche'])
        top = tops.get(pair, [])
        if not top:
            continue
        if pair not in cache:
            cache[pair] = ({t.casefold() for t in top}, [name_regex(t) for t in top])
        names_cf, rxs = cache[pair]
        if any(n.casefold() in names_cf for n in r['recorded']):
            c['rec'] += 1
        if any(rx.search(r['answer']) for rx in rxs):
            c['text'] += 1
    return res


def salvi_line(api_rows):
    """Chicago IL personal injury attorneys, word Salvi: text days, recorded days, answered days per engine."""
    rx = name_regex('Salvi')
    parts = []
    for e in ENG:
        rs = [r for r in api_rows if r['status'] == 'OK' and r['engine'] == e
              and r['market'] == 'Chicago IL' and r['niche'] == 'personal injury attorneys']
        answered = {r['date'] for r in rs}
        text = {r['date'] for r in rs if rx.search(r['answer'])}
        rec = {r['date'] for r in rs if any(rx.search(n) for n in r['recorded'])}
        parts.append('%s text=%d/%d recorded=%d/%d' % (e, len(text), len(answered), len(rec), len(answered)))
    return 'SALVI Chicago IL personal injury attorneys (days): ' + ' | '.join(parts)


# ------------------------------------------------------------------ selftest
def selftest():
    fails = []

    def check(cond, msg):
        print(('PASS ' if cond else 'FAIL ') + msg)
        if not cond:
            fails.append(msg)
    tmp = tempfile.mkdtemp(prefix='dq5_selftest_')
    try:
        D1, D2 = '2026-09-02', '2026-09-03'

        def put(eng, day, status, answer, recorded, i):
            fn = 'NB-CZ-API_%s_fixture%d_%s.json' % (day, i, eng)
            obj = {'date_utc': day, 'engine': eng, 'market': 'Testville TX', 'niche': 'roofers',
                   'run_status': status, 'answer_verbatim': answer, 'competitors_mentioned': recorded}
            json.dump(obj, open(os.path.join(tmp, fn), 'w', encoding='utf-8'))
        # fixture: three engines, two days; bold name, plain-text name, heading word, a failed run, a name inside a longer word
        put('openai', D1, 'OK', '**Acme Roofing** leads. Blue Fox Plumbing is named in plain text.', ['Acme Roofing', 'Red flags'], 1)
        put('openai', D2, 'OK', 'Try Pace Plumbing or Acme Roofing.', ['Pace Plumbing'], 2)
        put('perplexity', D1, 'OK', 'Blue Fox Plumbing is listed.', ['Blue Fox Plumbing'], 3)
        put('perplexity', D2, 'ERROR', 'Acme Roofing.', ['Acme Roofing'], 4)
        put('gemini', D1, 'OK', '**Ace** is a name. Pace is not Ace.', ['Ace'], 5)
        put('gemini', D2, 'OK', 'no names here', [], 6)
        put('anthropic', D1, 'OK', 'Red flags to check: Acme Roofing.', ['Red flags'], 7)
        tops = {('Testville TX', 'roofers'): ['Acme Roofing', 'Blue Fox Plumbing', 'Ace']}
        rows = load_api_rows(tmp, '2026-09-01', '2026-10-11')
        res = dq5_counts(rows, tops)
        want = {'openai': (2, 1, 2, 2), 'perplexity': (1, 1, 1, 1), 'gemini': (2, 1, 1, 2), 'anthropic': (1, 0, 1, 1)}
        for e in ENG:
            got = (res[e]['ok'], res[e]['rec'], res[e]['text'], len(res[e]['days']))
            check(got == want[e], '%s (ok, recorded_top10, text_top10, days) = %s, want %s' % (e, got, want[e]))
        # (b) recorded list must match a top 10 name, the heading word is not in the top 10
        check(res['openai']['rec'] == 1, '(b) recorded match counts one answer (Acme Roofing), Pace Plumbing does not')
        # (c) bold name counts as a whole name
        check(name_regex('Acme Roofing').search('**Acme Roofing** leads') is not None, '(c) bold whole name counts')
        # (c) plain-text name counts without a recorded entry
        check(name_regex('Blue Fox Plumbing').search('Blue Fox Plumbing is named in plain text') is not None, '(c) plain-text name counts')
        # RED proof: "Ace" inside "Pace" must not count as a whole-name match
        naive = sum(1 for r in rows if r['status'] == 'OK' and 'ace' in r['answer'].casefold())
        whole = sum(1 for r in rows if r['status'] == 'OK' and name_regex('Ace').search(r['answer']))
        print('RED proof: naive substring "ace" matches=%d, whole-name matches=%d (Pace must not count)' % (naive, whole))
        check(naive == 2 and whole == 1, 'RED proof: naive substring would over-count Ace inside Pace (naive 2, whole 1)')
        check(name_regex('Ace').search('Try Pace Plumbing') is None, 'RED: Pace Plumbing does not match the name Ace')
        check(name_regex('Ace').search('Ace.') is not None, 'whole name Ace followed by a period still counts')
        # failed run excluded from every count
        check(res['perplexity']['ok'] == 1, 'ERROR run is excluded from OK answers')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # --business (2026-10-14): rules on a fixture, then the Miami reconcile numbers when the corpus is on disk
    chk = lambda name, ok: check(ok, name)
    fx = [{'engine': 'perplexity', 'date': '2026-09-01', 'status': 'OK', 'market': 'M', 'niche': 'n', 'recorded': ['Extreme Roofing Inc'], 'answer': 'Extreme Roofing Inc is good'},
          {'engine': 'perplexity', 'date': '2026-09-02', 'status': 'FAILED', 'market': 'M', 'niche': 'n', 'recorded': [], 'answer': ''},
          {'engine': 'perplexity', 'date': '2026-09-03', 'status': 'OK', 'market': 'M', 'niche': 'n', 'recorded': ['Extreme Roofing'], 'answer': 'Call Extreme Roofing today'},
          {'engine': 'perplexity', 'date': '2026-09-03', 'status': 'OK', 'market': 'M', 'niche': 'n', 'recorded': ['Other Co'], 'answer': 'Extreme Roofings is not it'},
          {'engine': 'openai', 'date': '2026-09-01', 'status': 'OK', 'market': 'M', 'niche': 'n', 'recorded': [], 'answer': 'no names'}]
    c = business_counts(fx, 'Extreme Roofing Inc', 'contains')['perplexity']
    chk('business contains (Inc form): 1 answer recorded, text 1', c['rec'] == 1 and c['text'] == 1)
    c = business_counts(fx, 'Extreme Roofing', 'contains')['perplexity']
    chk('business contains (short form): 2 answers recorded, text 2 (plural form does not match), days asked 3 answered 2',
        c['rec'] == 2 and c['text'] == 2 and len(c['days_asked']) == 3 and len(c['days_ok']) == 2 and len(c['days_rec']) == 2)
    c = business_counts(fx, 'Extreme Roofing', 'exact')['perplexity']
    chk('business exact: 1 answer, 1 day', c['rec'] == 1 and len(c['days_rec']) == 1)
    c = business_counts(fx, 'Extreme Roofing', 'wholename')['perplexity']
    chk('business wholename on recorded names: 2 (Inc form matches, plural does not)', c['rec'] == 2)
    try:
        business_match('fuzzy', 'x')
        chk('RED: unknown rule stops', False)
    except SystemExit:
        chk('RED: unknown rule stops', True)
    miami = os.path.join(REPO, 'corpus', 'e1')
    if glob.glob(os.path.join(miami, 'NB-CZ-API_2026-08-09_segment_roofing_miami_perplexity.json')):
        rows = filter_cell(load_api_rows(miami, '2026-08-09', '2026-10-07'), 'Miami FL', 'residential roofing')
        a = business_counts(rows, 'Extreme Roofing', 'contains')['perplexity']
        b = business_counts(rows, 'Extreme Roofing Inc', 'exact')['perplexity']
        chk('MIAMI 2026-08-09..2026-10-07: contains 49 of 57 answers, exact 47 of 57 (1013E-MIAMI-RECONCILE.md)',
            a['ok'] == 57 and a['rec'] == 49 and b['rec'] == 47)
        rows = filter_cell(load_api_rows(miami, '2026-09-01', '2026-10-07'), 'Miami FL', 'residential roofing')
        a = business_counts(rows, 'Extreme Roofing', 'contains')['perplexity']
        b = business_counts(rows, 'Extreme Roofing Inc', 'exact')['perplexity']
        chk('MIAMI 2026-09-01..2026-10-07: contains 25 days, exact 23 days, 36 asked, 33 answered',
            len(a['days_rec']) == 25 and len(b['days_rec']) == 23 and len(a['days_asked']) == 36 and len(a['days_ok']) == 33)
    else:
        print('  skip MIAMI live check: corpus/e1 Miami files not on disk')
    if fails:
        print('SELFTEST FAIL %d' % len(fails))
        return 1
    print('SELFTEST PASS')
    return 0

# ------------------------------------------------------------------ one business (2026-10-14, COS wave 3 item 2)
RULES = ('contains', 'exact', 'wholename')


def business_match(rule, name):
    """Returns (recorded_fn, text_fn): recorded_fn(list of recorded names) and text_fn(answer text) -> bool.
    contains: a recorded name contains the business name (case folded); text: whole-name regex.
    exact:    a recorded name equals the business name (case folded, outer spaces); text: whole-name regex.
    wholename: both recorded names and the text use the whole-name regex (no letter or digit touches either end)."""
    k = ' '.join(name.casefold().split())
    rx = name_regex(name)
    if rule == 'contains':
        return (lambda rec: any(k in ' '.join(n.casefold().split()) for n in rec)), (lambda t: bool(rx.search(t)))
    if rule == 'exact':
        return (lambda rec: any(' '.join(n.casefold().split()) == k for n in rec)), (lambda t: bool(rx.search(t)))
    if rule == 'wholename':
        return (lambda rec: any(rx.search(n) for n in rec)), (lambda t: bool(rx.search(t)))
    raise SystemExit('unknown rule ' + rule)


def business_counts(api_rows, name, rule):
    """Per engine: ok answers, answers naming the business by the recorded rule, answers whose text names it,
    days asked (any row), days answered (OK), days named by the recorded rule, days named in the text."""
    recf, textf = business_match(rule, name)
    res = {}
    for r in api_rows:
        e = r['engine']
        c = res.setdefault(e, {'rows': 0, 'ok': 0, 'rec': 0, 'text': 0, 'days_asked': set(), 'days_ok': set(),
                               'days_rec': set(), 'days_text': set()})
        c['rows'] += 1
        c['days_asked'].add(r['date'])
        if r['status'] != 'OK':
            continue
        c['ok'] += 1
        c['days_ok'].add(r['date'])
        if recf(r['recorded']):
            c['rec'] += 1
            c['days_rec'].add(r['date'])
        if textf(r['answer']):
            c['text'] += 1
            c['days_text'].add(r['date'])
    return res


def business_lines(res, name, rule, W0, W1, market=None, niche=None):
    out = []
    for e in sorted(res):
        c = res[e]
        out.append('%s rule=%s ANSWERS ok=%d recorded=%d text=%d | DAYS asked=%d answered=%d recorded=%d text=%d'
                   % (e, rule, c['ok'], c['rec'], c['text'], len(c['days_asked']), len(c['days_ok']),
                      len(c['days_rec']), len(c['days_text'])))
    out.append('BUSINESS %r window %s..%s%s' % (name, W0, W1, (' cell %s / %s' % (market, niche)) if market or niche else ''))
    return out


def filter_cell(api_rows, market=None, niche=None):
    return [r for r in api_rows if (not market or r['market'] == market) and (not niche or r['niche'] == niche)]



def main():
    global CORPUS_DIR
    ap = argparse.ArgumentParser(description='DQ-5 counts (see module docstring).')
    ap.add_argument('--corpus', default=os.path.join(REPO, 'corpus', 'e1'))
    ap.add_argument('--start', default='2026-09-01')
    ap.add_argument('--end', default='2026-10-11')
    ap.add_argument('--top', type=int, default=10)
    ap.add_argument('--min-answers', type=int, default=20)
    ap.add_argument('--business', help='print answers and days for one business per engine (no top-10 build)')
    ap.add_argument('--rule', choices=RULES, default='contains')
    ap.add_argument('--market', help='with --business: restrict to one market label as written in the rows')
    ap.add_argument('--niche', help='with --business: restrict to one niche label as written in the rows')
    ap.add_argument('--selftest', action='store_true')
    A = ap.parse_args()
    if A.selftest:
        return selftest()
    CORPUS_DIR = os.path.abspath(A.corpus)
    if A.business:
        rows = filter_cell(load_api_rows(CORPUS_DIR, A.start, A.end), A.market, A.niche)
        for line in business_lines(business_counts(rows, A.business, A.rule), A.business, A.rule, A.start, A.end, A.market, A.niche):
            print(line)
        return 0
    tops = build_tops(REPO, A.start, A.end, A.min_answers, A.top)
    rows = load_api_rows(CORPUS_DIR, A.start, A.end)
    res = dq5_counts(rows, tops)
    for e in ENG:
        c = res[e]
        print('%s ok=%d recorded_top10=%d text_top10=%d days=%d' % (e, c['ok'], c['rec'], c['text'], len(c['days'])))
    print(salvi_line(rows))
    print('DQ5 window %s..%s top=%d min_answers=%d' % (A.start, A.end, A.top, A.min_answers))
    return 0


if __name__ == '__main__':
    sys.exit(main())
