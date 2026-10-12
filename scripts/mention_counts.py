#!/usr/bin/env python3
"""1012B-L9: uniform TEXT-MENTION counts for the namebeam.ai explorer.

RULE. For each market x niche pair, candidate businesses = every name that survives the existing gate (G3 deny set,
quarantine, canon/legal-suffix merge, heading filter, 1005A name gate) in ANY engine's recorded list in the window.
For each engine and candidate: named answer days = distinct OK answer days whose answer_verbatim mentions the candidate
(case-insensitive, whole-word, on the canonical form and its recorded display variants, legal suffixes stripped, the
full multiword name required; a single-word name only counts when it is not a common word and appears capitalised).
Denominator = that engine's distinct OK answer days. All four engines use the same rule. Generic phrases (maps, review
sites, directories, bar associations, national chains) are listed apart and stay out of business rankings.

Stdlib only. Usage:
  python3 -I mention_counts.py selftest [REPO]
  python3 -I mention_counts.py build REPO OUTDIR [--start 2026-09-01] [--end 2026-10-11] [--min-answers 20] [--top 10]
"""
import sys, os, re, json, glob, csv, unicodedata, itertools, argparse
from collections import defaultdict

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
    s = s.replace('–', '-').replace('—', '-').replace('’', "'").replace('‘', "'")
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
    """Normalised name with legal suffix words (and a leading 'the') removed."""
    w = norm_text(name).split()
    while len(w) > 1 and w[-1] in SUFFIX:
        w.pop()
    if len(w) > 1 and w[0] == 'the':
        w = w[1:]
    return ' '.join(w)


def skeleton(k):
    """A stem without middle initials, used only to see that two spellings are one name."""
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
    """Compile one whole-word regex over the stems of the display variants. Single-word stems are kept only when
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


def read_corpus(repo, be, W0, W1):
    """API rows in the window with status, answer text and heading-filtered recorded names."""
    rows = be.answers_rows(os.path.join(repo, 'corpus/e1'))
    out = []
    for r in rows:
        if r['kind'] != 'API' or r['engine'] not in ENG or not (W0 <= r['date'] <= W1):
            continue
        j = json.load(open(os.path.join(repo, 'corpus/e1', r['source_file'])))
        r['status'] = j.get('run_status')
        r['answer'] = j.get('answer_verbatim') or ''
        names = [n for n in r['businesses_named'].split(';') if n]
        r['businesses_named'] = ';'.join(n for n in names if be.heading_reason(n) is None)  # heading filter
        out.append(r)
    return out


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
    # union of both); a single word merges only when its counts equal the longer name's on every engine
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


def main_build(repo, out, W0, W1, minans, top):
    be, g, quar = load_gate(repo)
    rows = read_corpus(repo, be, W0, W1)
    os.makedirs(out, exist_ok=True)
    pairs, t5all, below, guards = [], {}, 0, {}
    for mk, ni in sorted({(r['market'], r['niche']) for r in rows}):
        cell = [r for r in rows if r['market'] == mk and r['niche'] == ni]
        if not mk.strip() or not ni.strip() or 'LOCATION NOT CAPTURED' in mk:
            continue
        asked, cnt, guard = build_pair(mk, ni, cell, g, quar)
        if sum(len(asked[e]) for e in ENG) < minans:
            below += 1
            continue
        t5all[(mk, ni)] = {e: tops(cnt, e) for e in ENG}
        guards[(mk, ni)] = sorted(guard)
        order = sorted((c for c in cnt if any(cnt[c][1].values())),
                       key=lambda c: (-max(cnt[c][1].values()), -len([e for e in ENG if cnt[c][1][e]]), cnt[c][0].casefold()))
        biz = [{'business': clean(cnt[c][0]), 'by_engine': {e: {'named': cnt[c][1][e], 'asked': len(asked[e])} for e in ENG}} for c in order[:top]]
        if biz:
            b = biz[0]
            fnd = '%s was named on %s.' % (b['business'], ', '.join('%d of %d %s answer days' % (b['by_engine'][e]['named'], len(asked[e]), LAB[e]) for e in ENG if asked[e]))
            zero = [LAB[e] for e in ENG if asked[e] and not any(cnt[c][1][e] for c in cnt)]
            fnd += ''.join(' %s named no business in any of its %d answer days.' % (z, len(asked[[k for k in ENG if LAB[k] == z][0]])) for z in zero)
            fnd += ' %d %s named in at least one answer by OpenAI, Perplexity, Gemini or Anthropic in the window.' % (len(order), 'business was' if len(order) == 1 else 'businesses were')
        else:
            fnd = 'No business name survived the name filter for this market and niche in the window.'
        pairs.append({'market': mk, 'niche': ni, 'window_start': W0, 'window_end': W1,
                      'asked_by_engine': {e: len(asked[e]) for e in ENG}, 'businesses_named_total': len(order),
                      'guarded_not_ranked': len(guard), 'top_businesses': biz, 'finding': fnd})
    # overlap
    ov, ps, elig = [], {}, 0
    top1 = {'markets_checked': 0, 'same_top1_2plus_engines': 0, 'strict_same_top1_2plus_engines': 0}
    top1_list = []
    for key, t5 in sorted(t5all.items()):
        have = [e for e in ENG if t5[e]['top']]
        if len(have) < 2:
            continue
        elig += 1
        for a, b in itertools.combinations(ENG, 2):
            if a not in have or b not in have:
                continue
            sh, shw = len(set(t5[a]['top']) & set(t5[b]['top'])), len(set(t5[a]['withties']) & set(t5[b]['withties']))
            ov.append((key[0], key[1], a, b, sh, shw))
            s = ps.setdefault('%s|%s' % (a, b), {'markets': 0, 'none': 0, 'none_ties': 0})
            s['markets'] += 1; s['none'] += (sh == 0); s['none_ties'] += (shw == 0)
        top1['markets_checked'] += 1
        for k, tag in (('top1', 'same_top1_2plus_engines'), ('top1_strict', 'strict_same_top1_2plus_engines')):
            tally = defaultdict(set)
            for e in have:
                for c in t5[e][k]:
                    tally[c].add(e)
            hit = [c for c, es in tally.items() if len(es) >= 2]
            if hit:
                top1[tag] += 1
                if k == 'top1':
                    top1_list.append([key[0], key[1], hit[0], sorted(tally[hit[0]])])
    dist = defaultdict(int)
    for r in ov:
        dist[r[4]] += 1
    overlap = {'window_start': W0, 'window_end': W1, 'engines': ENG, 'markets_with_two_or_more_engines_naming': elig,
               'engine_pair_comparisons': len(ov), 'shared_top5_distribution': {str(k): dist[k] for k in sorted(dist)},
               'by_engine_pair': ps, 'same_business_top1': top1}
    doc = {'source': 'data: Namebeam public record', 'schema': 'v3', 'window_start': W0, 'window_end': W1, 'min_answers': minans, 'top_n': top,
           'engines_counted': ENG,
           'method_note': 'Text-mention rule: a business counts for an engine on every answer day whose saved answer text names it (whole words, full name); each engine is divided by its own answer days.',
           'overlap': overlap, 'pairs': pairs}
    js = json.dumps(doc, ensure_ascii=True, separators=(',', ':')) + '\n'
    open(os.path.join(out, 'explorer_data.json'), 'w', encoding='ascii', newline='\n').write(js)
    with open(os.path.join(out, 'overlap_top5.csv'), 'w', newline='') as fh:
        w = csv.writer(fh); w.writerow(['market', 'niche', 'engine_a', 'engine_b', 'shared_top5', 'shared_with_ties'])
        w.writerows(ov)
    json.dump({'top1': top1_list, 'guards': {'%s|%s' % k: v for k, v in guards.items()}}, open(os.path.join(out, 'extras.json'), 'w'), indent=1)
    print('pairs', len(pairs), 'below', below)
    print(json.dumps(overlap, indent=1))
    return doc


# ------------------------------------------------------------------ selftest
def selftest(repo=None):
    n = [0]
    def ok(cond, msg):
        n[0] += 1
        print(('PASS ' if cond else 'FAIL ') + msg)
        if not cond:
            raise SystemExit(1)
    # synthetic fixtures
    m = make_matcher({'Salvi, Schostok & Pritchard P.C.', 'Salvi Schostok'})
    ok(mentions('Call Salvi, Schostok & Pritchard PC today', m), 'full name with legal suffix matches')
    ok(mentions("salvi schostok and pritchard's office", m), 'case, "and" for "&", possessive match')
    ok(not mentions('Salvi alone is a surname', m), 'single word of a multiword name does not match')
    m2 = make_matcher({'Reliable Heating & Air'})
    ok(not mentions('A reliable heating contractor, plus air filters', m2), '(d) single-word / split-word false match rejected')
    ok(mentions('Reliable Heating and Air serves Atlanta', m2), 'multiword name with and/& match')
    ok(not mentions('PreSalvi Schostok', m), 'whole-word boundary enforced')
    ok(is_guard('Google Maps') and is_guard('Chicago Bar Association') and is_guard('National chains') and is_guard('Yelp'), 'guard list catches generic phrases')
    ok(not is_guard('Jiffy Lube'), 'a real business is not guarded')
    ok(named_days([('d1', 'x Jiffy Lube y'), ('d1', 'none'), ('d2', 'none')], make_matcher({'Jiffy Lube'})) == {'d1'}, 'distinct answer days, two rows on one day count once')
    if repo:
        be, g, quar = load_gate(repo)
        rows = read_corpus(repo, be, '2026-09-01', '2026-10-11')
        def pair(mk, ni):
            return build_pair(mk, ni, [r for r in rows if r['market'] == mk and r['niche'] == ni], g, quar)
        asked, cnt, guard = pair('Chicago IL', 'personal injury attorneys')
        sal = [c for c in cnt if c.startswith('salvi')]
        ok(len(sal) >= 1, 'Salvi is a candidate in Chicago PI: %s' % sal)
        ok(cnt[sal[0]][1]['openai'] == 18 and len(asked['openai']) == 39, '(a) Salvi Chicago OpenAI = 18 of 39 (got %d of %d)' % (cnt[sal[0]][1]['openai'], len(asked['openai'])))
        ok(be.heading_reason('Red flags') is not None and 'red flags' in COMMON_ONE | {'red flags'}, '(c) heading filter drops recorded "Red flags": reason %s' % be.heading_reason('Red flags'))
        ok(all('red flags' != c for c in cnt) and all('red flags' != c for c in guard), '(c) "Red flags" never a candidate')
        a2, c2, g2 = pair('Pleasanton CA', 'oil change shops') if any(r['market'] == 'Pleasanton CA' and r['niche'] == 'oil change shops' for r in rows) else (None, None, None)
        if c2 is None:
            mks = sorted({(r['market'], r['niche']) for r in rows if 'oil' in r['niche']})
            print('oil pairs', mks); raise SystemExit(1)
        jl = [c for c in c2 if c.startswith('jiffy lube')]
        ok(bool(jl) and c2[jl[0]][1]['anthropic'] > 0, '(b) Jiffy Lube counted for Anthropic Pleasanton oil change: %d of %d' % (c2[jl[0]][1]['anthropic'], len(a2['anthropic'])))
    print('SELFTEST OK %d checks' % n[0])


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd'); ap.add_argument('args', nargs='*')
    ap.add_argument('--start', default='2026-09-01'); ap.add_argument('--end', default='2026-10-11')
    ap.add_argument('--min-answers', type=int, default=20); ap.add_argument('--top', type=int, default=10)
    A = ap.parse_args()
    if A.cmd == 'selftest':
        selftest(A.args[0] if A.args else None)
    elif A.cmd == 'build':
        main_build(A.args[0], A.args[1], A.start, A.end, A.min_answers, A.top)
