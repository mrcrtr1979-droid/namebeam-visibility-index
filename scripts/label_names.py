#!/usr/bin/env python3
"""1012D-X4 name labeler. Stdlib + urllib only.

Reads candidate names from each day's saved answers (corpus/e1/*.json), drops obvious non-businesses with a
deterministic list, sends the rest to Claude Haiku 4.5 through the Message Batches API (50 percent discount) and
appends one row per (name, market, niche) to corpus/labels/name_labels.csv:
  name, market, niche, label, reason, model, labeled_at
Labels are BUSINESS / NOT_BUSINESS / UNSURE. Existing corpus rows are never edited; existing label rows are never
rewritten (append only; a name already labeled for a market+niche is skipped).

Usage:
  python3 -I label_names.py --selftest
  python3 -I label_names.py --count   [--repo .]                    # candidate counts, no API
  python3 -I label_names.py --dry-run [--repo .] [--out DIR]        # build batch JSONL, no API call
  python3 -I label_names.py --run     [--repo .] [--max-names N]    # needs env ANTHROPIC_API_KEY
"""
import sys, os, re, json, csv, glob, time, argparse, unicodedata, tempfile, datetime
import urllib.request, urllib.error

MODEL = 'claude-haiku-4-5-20251001'
API = 'https://api.anthropic.com/v1/messages/batches'
VERSION = '2023-06-01'
CHUNK = 40            # names per batch request
MAX_TOKENS = 40 * 45  # output budget per request (about 45 tokens per name)
LABELS_PATH = 'corpus/labels/name_labels.csv'
FIELDS = ['name', 'market', 'niche', 'label', 'reason', 'model', 'labeled_at']

SYSTEM = (
    'You label names that appear in AI assistant answers to local-business questions. For each item decide whether the '
    'name is a specific operating business, firm or practitioner a customer could hire or visit (BUSINESS); or not a '
    'business, such as a holiday, a place, a platform, directory, review site, association, government body, '
    'certification, legal term, product category or sentence fragment (NOT_BUSINESS); or you cannot tell (UNSURE). '
    'Use the market and niche only as context. National chains and franchises count as BUSINESS. '
    'Reply with JSON only: a list of objects {"i": <item number>, "label": "BUSINESS"|"NOT_BUSINESS"|"UNSURE", '
    '"reason": "<at most 12 words>"}, one per item, in order.')

# ---- deterministic pre-filter (labels NOT_BUSINESS without an API call) -----------------------------------------
PRE_EXACT = {
    'labor day', 'memorial day', 'fourth of july', 'july 4th', 'google business', 'google business profile', 'ad club',
    'google maps', 'google', 'yelp', 'bing', 'bing maps', 'apple maps', 'facebook', 'nextdoor', 'reddit', 'angi',
    'angies list', 'homeadvisor', 'thumbtack', 'bbb', 'better business bureau', 'avvo', 'findlaw', 'justia', 'nolo',
    'super lawyers', 'martindale hubbell', 'martindale', 'best lawyers', 'national chains', 'national chain', 'chains',
    'zillow', 'trulia', 'redfin', 'tripadvisor', 'healthgrades', 'zocdoc', 'vitals', 'groupon', 'consumer reports',
    'houzz', 'porch', 'airbnb', 'vrbo', 'mapquest', 'chamber of commerce', 'local chamber of commerce', 'state bar',
    'lawyer referral service', 'yellow pages', 'yp', 'google reviews', 'linkedin', 'instagram', 'tiktok', 'youtube',
    'red flags', 'spoliation letter', 'board certification', 'av preeminent', 'epa 608', 'manual d'}
PRE_RE = re.compile(
    r'\b(bar association|bar foundation|trial lawyers|association of|lawyer referral|referral service|chamber of commerce|'
    r'review sites?|rating sites?|directory|directories|near me|top rated|best of|search results|statute of limitations|'
    r'attorney general|department of|board of|location and hours|office hours)\b')
SUFFIX = {'law', 'firm', 'offices', 'office', 'llp', 'llc', 'pllc', 'pc', 'p', 'c', 'pa', 'inc', 'co', 'company'}
FUNC = {'for', 'by', 'your', 'our', 'in', 'on', 'with', 'to', 'of', 'at', 'from', 'or', 'a', 'an', 'is', 'near', 'how', 'what'}
# 1013A fix: business names often start with A, An, At, From, On (A-1 Roofing, At The Beach Tanning, From the Heart
# Senior Services). Those words no longer reject a name by themselves; such names go to the model instead.
LEAD = FUNC - {'a', 'an', 'at', 'from', 'on'}


def clean(s):
    s = (s or '').replace('\u2013', '-').replace('\u2014', '-').replace('\u2019', "'").replace('\u2018', "'")
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'\s+', ' ', s).strip()


def norm(s):
    t = clean(s).casefold()
    t = re.sub(r"'s\b", '', t).replace('&', ' and ').replace('.', '').replace("'", '')
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', t)).strip()


def stem(name):
    w = norm(name).split()
    while len(w) > 1 and w[-1] in SUFFIX:
        w.pop()
    if len(w) > 1 and w[0] == 'the':
        w = w[1:]
    return ' '.join(w)


def prefilter(name):
    """Return a reason string when the name is a known non-business, else None."""
    s = stem(name)
    if not s:
        return 'empty name'
    if s in PRE_EXACT or norm(name) in PRE_EXACT:
        return 'known non-business (deny list)'
    if PRE_RE.search(s):
        return 'generic phrase, association or directory'
    w = s.split()
    # trailing word read from the cleaned original, so 'A/C' or 'A-1' is not split into 'a c' / 'a 1'
    raw = [t.strip('.,;:()').casefold() for t in clean(name).split()]
    raw = [t for t in raw if t]
    if len(w) > 1 and len(raw) > 1 and (w[0] in LEAD or raw[-1] in FUNC):
        return 'sentence fragment'
    if len(w) > 7:
        return 'too long to be a name'
    return None


# ---- candidate extraction ------------------------------------------------------------------------------------
BOLD = re.compile(r'\*\*([^*\n]{3,80}?)\*\*')


def text_names(answer):
    """M602: bolded spans in the answer text, kept when they look like a proper name."""
    out = []
    for m in BOLD.finditer(answer or ''):
        s = clean(m.group(1)).strip(' :-,;')
        s = s[:-1] if s.endswith('.') and not re.search(r'\b[A-Za-z]\.[A-Za-z]\.$', s) else s
        w = s.split()
        if not (1 <= len(w) <= 6) or not s[:1].isupper() or re.search(r'[:?!]', s):
            continue
        if len(w) > 1 and sum(1 for t in w if t[:1].isupper() or t.lower() in {'and', '&', 'of', 'the'}) < len(w) - 1:
            continue
        out.append(s)
    return out


def iter_candidates(repo):
    """Yield (name, market, niche, source) for every recorded or text-extracted name, one per answer file."""
    for p in sorted(glob.glob(os.path.join(repo, 'corpus', 'e1', '*.json'))):
        try:
            j = json.load(open(p, encoding='utf-8'))
        except Exception:
            continue
        if not isinstance(j, dict) or j.get('run_channel') not in (None, 'api'):
            continue
        mk, ni = clean(j.get('market')), clean(j.get('niche'))
        if not mk or not ni:
            continue
        seen = set()
        for n in (j.get('competitors_mentioned') or []):
            if isinstance(n, str):
                seen.add((clean(n), 'recorded'))
        for n in text_names(j.get('answer_verbatim')):
            seen.add((n, 'text'))
        for n, src in seen:
            if n and len(n) <= 90:
                yield n, mk, ni, src


def collect(repo):
    """{(stem, market, niche): (display, source set)} unique across the corpus."""
    out = {}
    for n, mk, ni, src in iter_candidates(repo):
        k = (stem(n), mk, ni)
        if not k[0]:
            continue
        d = out.get(k)
        if d is None:
            out[k] = [n, {src}]
        else:
            d[1].add(src)
            if len(n) < len(d[0]) and n != n.lower():
                d[0] = n
    return out


def load_labeled(repo):
    p = os.path.join(repo, LABELS_PATH)
    done = set()
    if os.path.exists(p):
        with open(p, newline='', encoding='utf-8') as fh:
            for r in csv.DictReader(fh):
                done.add((stem(r['name']), r['market'], r['niche']))
    return done


def split_work(repo):
    cands = collect(repo)
    done = load_labeled(repo)
    pre, todo = [], []
    for k in sorted(cands):
        if k in done:
            continue
        disp = cands[k][0]
        why = prefilter(disp)
        (pre if why else todo).append((disp, k[1], k[2], why))
    return cands, done, pre, todo


# ---- batch building ------------------------------------------------------------------------------------------
def user_prompt(items):
    lines = ['%d. name: %s | market: %s | niche: %s' % (i + 1, n, m, ni) for i, (n, m, ni) in enumerate(items)]
    return 'Label these %d items.\n' % len(items) + '\n'.join(lines)


def build_requests(todo):
    reqs = []
    for c in range(0, len(todo), CHUNK):
        chunk = [(n, m, ni) for n, m, ni, _ in todo[c:c + CHUNK]]
        reqs.append({'custom_id': 'chunk-%06d' % (c // CHUNK), 'params': {
            'model': MODEL, 'max_tokens': MAX_TOKENS, 'system': SYSTEM,
            'messages': [{'role': 'user', 'content': user_prompt(chunk)}]}})
    return reqs


def write_jsonl(reqs, path):
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        for r in reqs:
            fh.write(json.dumps(r, ensure_ascii=True) + '\n')


# ---- API -------------------------------------------------------------------------------------------------------
def api(method, url, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    rq = urllib.request.Request(url, data=data, method=method, headers={
        'x-api-key': key, 'anthropic-version': VERSION, 'content-type': 'application/json'})
    try:
        with urllib.request.urlopen(rq, timeout=120) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise SystemExit('API error %s %s: %s' % (e.code, url, e.read()[:300].decode('utf-8', 'replace')))


def parse_labels(text, n):
    """Parse the model's JSON list; returns {index(1-based): (label, reason)}."""
    m = re.search(r'\[.*\]', text or '', re.S)
    out = {}
    if not m:
        return out
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return out
    for o in arr:
        if isinstance(o, dict) and isinstance(o.get('i'), int) and 1 <= o['i'] <= n:
            lab = str(o.get('label', '')).upper()
            if lab in ('BUSINESS', 'NOT_BUSINESS', 'UNSURE'):
                out[o['i']] = (lab, clean(str(o.get('reason', '')))[:120])
    return out


def append_rows(repo, rows):
    p = os.path.join(repo, LABELS_PATH)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    new = not os.path.exists(p)
    with open(p, 'a', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh, lineterminator='\n')
        if new:
            w.writerow(FIELDS)
        w.writerows(rows)


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def run(repo, max_names):
    key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not key:
        raise SystemExit('ANTHROPIC_API_KEY not set')
    cands, done, pre, todo = split_work(repo)
    if max_names:
        todo = todo[:max_names]
    t = now()
    rows = [[n, m, ni, 'NOT_BUSINESS', why, 'prefilter', t] for n, m, ni, why in pre]
    reqs = build_requests(todo)
    print('prefilter rows %d, api names %d, requests %d' % (len(pre), len(todo), len(reqs)))
    if reqs:
        b = json.loads(api('POST', API, key, {'requests': reqs}))
        bid = b['id']
        print('batch', bid)
        while b.get('processing_status') != 'ended':
            time.sleep(60)
            b = json.loads(api('GET', API + '/' + bid, key))
        raw = api('GET', b['results_url'], key).decode('utf-8')
        by = {}
        for line in raw.splitlines():
            r = json.loads(line)
            if r.get('result', {}).get('type') == 'succeeded':
                by[r['custom_id']] = ''.join(c.get('text', '') for c in r['result']['message']['content'])
        t = now()
        for ci, req in enumerate(reqs):
            chunk = todo[ci * CHUNK:(ci + 1) * CHUNK]
            got = parse_labels(by.get(req['custom_id'], ''), len(chunk))
            for i, (n, m, ni, _) in enumerate(chunk, 1):
                if i in got:
                    rows.append([n, m, ni, got[i][0], got[i][1], MODEL, t])  # unanswered names stay unlabeled and retry next run
    append_rows(repo, rows)
    print('appended', len(rows))


# ---- selftest ------------------------------------------------------------------------------------------------------
def selftest():
    n = [0]

    def ok(c, msg):
        n[0] += 1
        print(('PASS ' if c else 'FAIL ') + msg)
        if not c:
            raise SystemExit(1)
    ok(prefilter('Labor Day') is not None, '"Labor Day" -> NOT_BUSINESS by deterministic pre-filter')
    ok(prefilter('Google Business') and prefilter('Ad Club'), '"Google Business" and "Ad Club" caught by pre-filter')
    ok(prefilter('Salvi, Schostok & Pritchard') is None, '"Salvi, Schostok & Pritchard" kept as candidate')
    ok(prefilter('Jiffy Lube') is None and prefilter('Reliable Heating & Air') is None, 'real businesses kept')
    ok(prefilter('Chicago Bar Association') is not None, 'association caught')
    ok(all(prefilter(n) is None for n in ['A-1 Property Services', 'A-1 Roofing & Construction', 'A/C & Plumbing Doctors',
        'At The Beach Tanning', 'From the Heart Senior Services', 'Bob Hamilton Plumbing, Heating & A/C']),
       'business names starting with A/At/From or ending in A/C are kept (1013A regression)')
    ok(prefilter('For UV Tanning') and prefilter('How it Works') and prefilter('Best For'),
       'real sentence fragments still caught')
    ok(text_names('Try **Salvi, Schostok & Pritchard P.C.** or **Why it matters:** and **best price**') ==
       ['Salvi, Schostok & Pritchard P.C.'], 'M602 bolded name captured, headings and lowercase phrases dropped')
    ok(parse_labels('x [{"i":1,"label":"business","reason":"law firm"},{"i":9,"label":"BUSINESS"}]', 2) ==
       {1: ('BUSINESS', 'law firm')}, 'response parser accepts valid rows, drops out-of-range')
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, 'corpus', 'e1'))
    fx = [('A1', ['Labor Day', 'Jiffy Lube'], 'Great: **Salvi, Schostok & Pritchard P.C.** and **Jiffy Lube**.'),
          ('A2', ['Salvi, Schostok & Pritchard', 'Google Business'], 'Nothing bold.')]
    for nm, comp, ans in fx:
        json.dump({'market': 'Chicago IL', 'niche': 'personal injury attorneys', 'run_channel': 'api',
                   'competitors_mentioned': comp, 'answer_verbatim': ans}, open(os.path.join(d, 'corpus', 'e1', nm + '.json'), 'w'))
    cands, done, pre, todo = split_work(d)
    ok(sorted(x[0] for x in pre) == ['Google Business', 'Labor Day'], 'fixture pre-filter rows: Labor Day, Google Business')
    ok(len(todo) == 2 and any('Salvi' in x[0] for x in todo), 'fixture api candidates deduped across files: %s' % [x[0] for x in todo])
    reqs = build_requests(todo)
    ok(len(reqs) == 1 and reqs[0]['params']['model'] == MODEL, 'one request, model %s' % MODEL)
    append_rows(d, [['Jiffy Lube', 'Chicago IL', 'personal injury attorneys', 'BUSINESS', 'chain', MODEL, now()]])
    before = open(os.path.join(d, LABELS_PATH)).read()
    _, _, pre2, todo2 = split_work(d)
    ok(all('Jiffy' not in x[0] for x in todo2), 'already-labeled name is skipped (append-only)')
    append_rows(d, [['X', 'm', 'n', 'UNSURE', 'r', MODEL, now()]])
    ok(open(os.path.join(d, LABELS_PATH)).read().startswith(before), 'append leaves earlier bytes untouched')
    ok(len(json.load(open(os.path.join(d, 'corpus', 'e1', 'A1.json')))['competitors_mentioned']) == 2, 'corpus rows not edited')
    print('SELFTEST OK %d checks' % n[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--count', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--repo', default='.')
    ap.add_argument('--out', default='.')
    ap.add_argument('--max-names', type=int, default=0)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.count or a.dry_run:
        cands, done, pre, todo = split_work(a.repo)
        src = {}
        for v in cands.values():
            for s in v[1]:
                src[s] = src.get(s, 0) + 1
        reqs = build_requests(todo)
        sysc = len(SYSTEM)
        inch = sum(len(json.dumps(r['params']['messages'])) for r in reqs) + sysc * len(reqs)
        print('unique (name,market,niche):', len(cands), 'already labeled:', len(done), 'by source (overlap possible):', src)
        print('prefilter NOT_BUSINESS:', len(pre), ' api names:', len(todo), ' requests:', len(reqs), ' input chars:', inch)
        if a.dry_run:
            p = os.path.join(a.out, 'batch_dryrun.jsonl')
            write_jsonl(reqs, p)
            print('wrote', p, os.path.getsize(p), 'bytes; NO API CALL MADE')
        return
    if a.run:
        return run(a.repo, a.max_names)
    ap.print_help()


if __name__ == '__main__':
    main()
