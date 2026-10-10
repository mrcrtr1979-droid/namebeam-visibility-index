#!/usr/bin/env python3
"""AI-ingestion lag probe: how many days pass between publishing one true, dated fact on a Namebeam page
and the first day each engine states it. Cohort experiment, pre-registered in prereg/.

    python3 -I scripts/ingestion_probe.py --run [--cohort prereg/cohort-01.json] [--force]
    python3 -I scripts/ingestion_probe.py --score [--cohort ...]     # rebuild the derived score and lag tables
    python3 -I scripts/ingestion_probe.py --check-reveal FILE        # check a reveal file against the commitments
    python3 -I scripts/ingestion_probe.py --selftest                 # no network, no keys

How it works
- The cohort file (prereg/cohort-NN.json) is committed before the first probe. It fixes the probe questions (text and
  SHA-256), the engines and their settings, the decision rule, the controls and the publication plan. The fact text
  and its detection pattern are NOT in it: it holds salted SHA-256 commitments to them, so a published fact can be
  checked against what was fixed in advance, and the number cannot be read off the repository before publication.
- Each run asks every engine every probe question once, with the same call settings as the daily E1 run
  (scripts/engines.py), saves the answer word for word under data/ingestion/raw/<date>/, and appends one row per call
  to data/ingestion/ingestion_probe.csv. Failed calls are rows too. A second run on the same date writes nothing.
- On the publication day the fact text, the pattern and the salt are committed in prereg/cohort-NN.reveal.json.
  --score then scores every saved answer, including the ones saved before publication, from the committed files.
Standard library plus requests (already used by the E1 run). Never prints or stores a key.
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
DEFAULT_COHORT = os.path.join(REPO, 'prereg', 'cohort-01.json')
OUT = os.path.join(REPO, 'data', 'ingestion')

PROBE_FIELDS = ['observed_utc', 'date', 'cohort_id', 'phase', 'probe_id', 'role', 'engine', 'model_id', 'search_mode',
                'prompt_sha256', 'ok', 'http_status', 'error_class', 'answer_chars', 'answer_sha256', 'citations_n',
                'url_cited', 'source_named', 'raw_file', 'run_id']
PRESENCE_FIELDS = ['observed_utc', 'date', 'cohort_id', 'source', 'present', 'detail', 'sha256', 'run_id']
SCORE_FIELDS = ['date', 'cohort_id', 'phase', 'probe_id', 'role', 'engine', 'ok', 'fact_named', 'source_named',
                'url_cited', 'hit', 'answer_sha256', 'raw_file']
LAG_FIELDS = ['cohort_id', 'probe_id', 'role', 'engine', 'published_date', 'post_days_probed', 'baseline_days_probed',
              'baseline_hits', 'first_hit_date', 'days_to_first_named', 'status']


def sha_text(s):
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def commitment(salt, text):
    """Salted commitment: SHA-256 of salt, a newline, then the text (UTF-8)."""
    return sha_text(salt + '\n' + text)


def utc_now():
    return datetime.now(timezone.utc)


def load_cohort(path):
    with io.open(path, encoding='utf-8') as fh:
        return json.load(fh)


def check_cohort(c):
    """Problems with a cohort file (empty list = fine). Blocks silent prompt edits mid-cohort."""
    bad = []
    for k in ('cohort_id', 'page_url', 'planned_publish_date', 'probes', 'engines', 'decision_rule', 'commitments'):
        if k not in c:
            bad.append('missing key ' + k)
    for p in c.get('probes', []):
        if sha_text(p.get('prompt_text', '')) != p.get('prompt_sha256'):
            bad.append('probe %s: prompt_sha256 does not match prompt_text' % p.get('probe_id'))
        if p.get('role') not in ('treatment_anchored', 'treatment_unanchored', 'negative_control', 'positive_control'):
            bad.append('probe %s: unknown role %r' % (p.get('probe_id'), p.get('role')))
    for name, com in (c.get('commitments') or {}).items():
        for k in ('fact_text_commitment', 'pattern_commitment'):
            if not re.fullmatch(r'[0-9a-f]{64}', str(com.get(k, ''))):
                bad.append('commitment %s.%s is not a SHA-256 hex digest' % (name, k))
    return bad


def check_reveal(cohort, reveal):
    """Problems with a reveal file (empty list = every revealed text matches its commitment)."""
    bad = []
    for name, r in reveal.items():
        com = (cohort.get('commitments') or {}).get(name)
        if com is None:
            bad.append('%s: no commitment in the cohort file' % name)
            continue
        if commitment(r.get('salt', ''), r.get('fact_text', '')) != com['fact_text_commitment']:
            bad.append('%s: fact_text does not match its commitment' % name)
        if commitment(r.get('salt', ''), r.get('pattern', '')) != com['pattern_commitment']:
            bad.append('%s: pattern does not match its commitment' % name)
        try:
            re.compile(r.get('pattern', ''))
        except re.error as e:
            bad.append('%s: pattern does not compile (%s)' % (name, e))
    return bad


def patterns_for(cohort, reveal):
    """Pattern per probe role that can be scored now: public ones from the cohort file, hidden ones once revealed."""
    pats = {}
    for p in cohort.get('probes', []):
        if p.get('pattern'):
            pats[p['probe_id']] = p['pattern']
        elif p.get('commitment') and p['commitment'] in reveal:
            pats[p['probe_id']] = reveal[p['commitment']]['pattern']
    return pats


def mentions(text, needles):
    low = (text or '').casefold()
    return 1 if any(n.casefold() in low for n in needles) else 0


def cites_host(urls, text, hosts):
    for u in list(urls or []) + [text or '']:
        low = str(u).casefold()
        if any(h.casefold() in low for h in hosts):
            return 1
    return 0


def phase_of(day, cohort):
    pub = cohort.get('published_date') or cohort['planned_publish_date']
    return 'post' if day >= pub else 'baseline'


def error_class(err, status):
    if not err:
        return ''
    if err.startswith('QUOTA'):
        return 'QUOTA'
    if 'not set' in err:
        return 'NO_KEY'
    if status:
        return 'HTTP_%s' % status
    return 'ERROR'


# ------------------------------------------------------------------ engines


class _Recorder(object):
    """Wraps requests.post so the probe can read the HTTP status and the model the provider says answered."""

    def __init__(self, real):
        self.real = real
        self.last = None

    def __call__(self, *a, **k):
        self.last = None
        r = self.real(*a, **k)
        self.last = r
        return r


def live_engines():
    """Engine callables with the E1 call settings, each returning a dict. Imported lazily (needs requests)."""
    sys.path.insert(0, HERE)
    import requests
    import engines as E
    rec = _Recorder(requests.post)
    requests.post = rec

    def model_from_last(engine):
        if engine == 'perplexity':
            return (E.LAST_CALL_META.get('perplexity') or {}).get('served_model') or ''
        try:
            return rec.last.json().get('model') or ''
        except Exception:
            return ''

    def make(engine, mode):
        fn = E.ENGINES[engine]

        def call(prompt):
            ok, text, cites, err = fn(prompt)
            status = getattr(rec.last, 'status_code', '') if rec.last is not None else ''
            return {'ok': ok, 'text': text or '', 'citations': list(cites or []), 'error': err or '',
                    'http_status': status, 'model_id': model_from_last(engine) if ok else '', 'search_mode': mode}
        return call

    def serp(prompt):
        from e1_runner import extract_ai_overview
        ok, payload, organic, err = E.fetch_serp(prompt)
        present, aio_text, refs = (None, '', [])
        if ok:
            present, aio_text, refs = extract_ai_overview(payload)
        status = getattr(rec.last, 'status_code', '') if rec.last is not None else ''
        return {'ok': ok, 'text': aio_text or '', 'citations': list(organic or []) + [r.get('href', '') for r in refs],
                'error': err or '', 'http_status': status, 'model_id': '', 'search_mode': 'google results page',
                'extra': {'organic_top_results': list(organic or []), 'ai_overview_present': present,
                          'ai_overview_references': refs}}

    return {'perplexity': make('perplexity', 'web search forced (E1 setting)'),
            'openai': make('openai', 'no search tool (E1 setting)'),
            'anthropic': make('anthropic', 'no search tool (E1 setting)'),
            'google_serp': serp}


# ------------------------------------------------------------------ files


def read_rows(path):
    if not os.path.exists(path):
        return []
    with io.open(path, encoding='utf-8', newline='') as fh:
        return list(csv.DictReader(fh))


def append_rows(path, rows, fields):
    new = not os.path.exists(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, 'a', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator='\n')
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, '') for k in fields})


def write_rows(path, rows, fields):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, 'w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator='\n')
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, '') for k in fields})


def load_reveal(cohort_path):
    p = cohort_path[:-len('.json')] + '.reveal.json'
    if not os.path.exists(p):
        return {}
    with io.open(p, encoding='utf-8') as fh:
        return json.load(fh)


# ------------------------------------------------------------------ run


def run(cohort_path, out=OUT, engines=None, now=None, run_id='', force=False, fetch=None):
    """One probe run. Returns the number of probe rows written (0 when today's rows already exist)."""
    cohort = load_cohort(cohort_path)
    bad = check_cohort(cohort)
    if bad:
        raise SystemExit('COHORT FILE REJECTED: ' + '; '.join(bad))
    now = now or utc_now()
    day = now.strftime('%Y-%m-%d')
    stamp = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    cid = cohort['cohort_id']
    probe_csv = os.path.join(out, 'ingestion_probe.csv')
    done = [r for r in read_rows(probe_csv) if r['date'] == day and r['cohort_id'] == cid]
    if done and not force:
        print('PERIOD GUARD: %d rows for %s on %s already exist; nothing written.' % (len(done), cid, day))
        return 0
    if day > cohort.get('stop_after_date', '9999-12-31'):
        print('STOP RULE: %s is past stop_after_date %s; nothing written.' % (day, cohort['stop_after_date']))
        return 0
    engines = engines if engines is not None else live_engines()
    hosts = cohort.get('watch_hosts', [])
    names = cohort.get('watch_names', [])
    rows = []
    for p in cohort['probes']:
        for eng in cohort['engines']:
            if eng not in p.get('engines', cohort['engines']):
                continue
            res = engines[eng](p['prompt_text'])
            rel = 'raw/%s/%s_%s_%s.json' % (day, cid, p['probe_id'], eng)
            raw = {'observed_utc': stamp, 'cohort_id': cid, 'probe_id': p['probe_id'], 'role': p['role'], 'engine': eng,
                   'model_id': res.get('model_id', ''), 'search_mode': res.get('search_mode', ''),
                   'prompt_text': p['prompt_text'], 'prompt_sha256': p['prompt_sha256'], 'ok': bool(res['ok']),
                   'http_status': res.get('http_status', ''), 'error': res.get('error', ''),
                   'answer_text': res.get('text', ''), 'citations': res.get('citations', []), 'run_id': run_id}
            if res.get('extra'):
                raw.update(res['extra'])
            path = os.path.join(out, *rel.split('/'))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with io.open(path, 'w', encoding='utf-8', newline='\n') as fh:
                json.dump(raw, fh, indent=1, ensure_ascii=False, sort_keys=True)
                fh.write('\n')
            text = res.get('text', '')
            rows.append({'observed_utc': stamp, 'date': day, 'cohort_id': cid, 'phase': phase_of(day, cohort),
                         'probe_id': p['probe_id'], 'role': p['role'], 'engine': eng, 'model_id': res.get('model_id', ''),
                         'search_mode': res.get('search_mode', ''), 'prompt_sha256': p['prompt_sha256'],
                         'ok': 1 if res['ok'] else 0, 'http_status': res.get('http_status', ''),
                         'error_class': error_class(res.get('error', ''), res.get('http_status', '')),
                         'answer_chars': len(text), 'answer_sha256': sha_text(text) if res['ok'] else '',
                         'citations_n': len(res.get('citations', [])),
                         'url_cited': cites_host(res.get('citations', []), text, hosts) if res['ok'] else '',
                         'source_named': mentions(text, names) if res['ok'] else '', 'raw_file': rel, 'run_id': run_id})
    append_rows(probe_csv, rows, PROBE_FIELDS)
    pres = presence(cohort, day, stamp, run_id, fetch=fetch, pattern=treatment_pattern(cohort_path, cohort))
    append_rows(os.path.join(out, 'ingestion_presence.csv'), pres, PRESENCE_FIELDS)
    score(cohort_path, out)
    ok = sum(1 for r in rows if r['ok'] == 1)
    print('PROBE RUN %s %s: %d rows (%d ok, %d failed), %d presence rows' % (cid, day, len(rows), ok, len(rows) - ok, len(pres)))
    return len(rows)


def _fetch(url, timeout=30):
    import requests
    try:
        r = requests.get(url, timeout=timeout, headers={'User-Agent': 'NamebeamRecordProbe/1.0 (+https://namebeam.ai/)'})
        return r.status_code, r.content
    except Exception as e:
        return 0, str(e).encode('utf-8')[:200]


def treatment_pattern(cohort_path, cohort):
    """The treatment pattern once it is revealed, else None."""
    rv = load_reveal(cohort_path)
    for p in cohort.get('probes', []):
        if p.get('role') == 'treatment_anchored' and p.get('commitment') in rv:
            return rv[p['commitment']]['pattern']
    return None


def presence(cohort, day, stamp, run_id, fetch=None, pattern=None):
    """Is the page live, does it carry the fact (after the reveal), is it in the sitemap and the Wayback Machine?
    One row per source, failures included."""
    fetch = fetch or _fetch
    url = cohort['page_url']
    out = []

    def row(source, present, detail, digest=''):
        out.append({'observed_utc': stamp, 'date': day, 'cohort_id': cohort['cohort_id'], 'source': source,
                    'present': present, 'detail': detail, 'sha256': digest, 'run_id': run_id})
    st, body = fetch(url)
    row('live_page', 1 if st == 200 else 0, 'HTTP %s, %d bytes' % (st, len(body)), hashlib.sha256(body).hexdigest() if st == 200 else '')
    if pattern and st == 200:
        text = re.sub(r'<[^>]+>', ' ', body.decode('utf-8', 'replace'))
        row('page_has_fact', 1 if re.search(pattern, text) else 0, 'treatment pattern searched in the live page text')
    sm = cohort.get('sitemap_url')
    if sm:
        st2, body2 = fetch(sm)
        listed = 1 if (st2 == 200 and url.rstrip('/').encode('utf-8') in body2) else 0
        row('sitemap', listed if st2 == 200 else '', 'HTTP %s' % st2)
    st3, body3 = fetch('https://archive.org/wayback/available?url=' + url)
    snap = ''
    if st3 == 200:
        try:
            closest = (json.loads(body3.decode('utf-8')).get('archived_snapshots') or {}).get('closest') or {}
            snap = closest.get('timestamp', '')
        except ValueError:
            pass
    row('wayback', (1 if snap else 0) if st3 == 200 else '', 'HTTP %s, closest snapshot %s' % (st3, snap or 'none'))
    return out


# ------------------------------------------------------------------ score


def score(cohort_path, out=OUT):
    """Rebuild ingestion_scores.csv and ingestion_lag.csv from the saved raw answers (deterministic, read-only on raw)."""
    cohort = load_cohort(cohort_path)
    reveal = load_reveal(cohort_path)
    bad = check_reveal(cohort, reveal) if reveal else []
    if bad:
        raise SystemExit('REVEAL REJECTED: ' + '; '.join(bad))
    pats = patterns_for(cohort, reveal)
    roles = {p['probe_id']: p for p in cohort['probes']}
    srows = []
    for r in read_rows(os.path.join(out, 'ingestion_probe.csv')):
        if r['cohort_id'] != cohort['cohort_id']:
            continue
        p = roles.get(r['probe_id'], {})
        fact = ''
        if r['ok'] == '1' and r['probe_id'] in pats:
            with io.open(os.path.join(out, *r['raw_file'].split('/')), encoding='utf-8') as fh:
                text = json.load(fh).get('answer_text', '')
            fact = 1 if re.search(pats[r['probe_id']], text) else 0
        hit = ''
        if fact != '':
            need_source = p.get('hit_needs_source', False)
            hit = 1 if fact == 1 and (not need_source or r['source_named'] == '1' or r['url_cited'] == '1') else 0
        srows.append({'date': r['date'], 'cohort_id': r['cohort_id'], 'phase': phase_of(r['date'], cohort),
                      'probe_id': r['probe_id'], 'role': r['role'], 'engine': r['engine'], 'ok': r['ok'],
                      'fact_named': fact, 'source_named': r['source_named'], 'url_cited': r['url_cited'], 'hit': hit,
                      'answer_sha256': r['answer_sha256'], 'raw_file': r['raw_file']})
    srows.sort(key=lambda x: (x['date'], x['probe_id'], x['engine']))
    write_rows(os.path.join(out, 'ingestion_scores.csv'), srows, SCORE_FIELDS)
    write_rows(os.path.join(out, 'ingestion_lag.csv'), lag_table(cohort, srows), LAG_FIELDS)
    return srows


def lag_table(cohort, srows):
    """Decision rule: the first post-publication day with a hit that is followed by a hit on the next probed day."""
    pub = cohort.get('published_date') or ''
    by = {}
    for r in srows:
        by.setdefault((r['probe_id'], r['role'], r['engine']), []).append(r)
    out = []
    for (pid, role, eng), rs in sorted(by.items()):
        scored = [r for r in rs if r['hit'] != '']
        base = [r for r in scored if r['phase'] == 'baseline']
        post = sorted([r for r in scored if r['phase'] == 'post' and pub and r['date'] >= pub], key=lambda x: x['date'])
        first = ''
        for a, b in zip(post, post[1:]):
            if a['hit'] == 1 and b['hit'] == 1:
                first = a['date']
                break
        days = ''
        if first and pub:
            days = (date.fromisoformat(first) - date.fromisoformat(pub)).days
        if not scored:
            status = 'NOT_SCORED_YET (pattern not revealed)'
        elif not pub:
            status = 'BASELINE (not published yet)'
        elif first:
            status = 'NAMED'
        else:
            status = 'NOT_NAMED_YET'
        out.append({'cohort_id': cohort['cohort_id'], 'probe_id': pid, 'role': role, 'engine': eng, 'published_date': pub,
                    'post_days_probed': len(post), 'baseline_days_probed': len(base),
                    'baseline_hits': sum(1 for r in base if r['hit'] == 1), 'first_hit_date': first,
                    'days_to_first_named': days, 'status': status})
    return out


# ------------------------------------------------------------------ selftest


def selftest():
    import shutil
    import tempfile
    fails = []

    def chk(name, ok):
        print(('  ok   ' if ok else '  FAIL ') + name)
        if not ok:
            fails.append(name)
    # a dummy fact, never the real one
    pat = r'(?<![\d.,])4[,.\s]?817(?![\d])'
    for txt, want in (('Namebeam counted 4,817 domains.', 1), ('It was 4817 domains', 1), ('about 4.817 sites', 1),
                      ('14,817 domains', 0), ('4,8170 rows', 0), ('4,816 domains', 0), ('I could not find that figure.', 0)):
        chk('detector %r -> %d' % (txt, want), (1 if re.search(pat, txt) else 0) == want)
    salt = 'f00dfeed'
    fact = 'Example fact: 4,817 domains.'
    c = commitment(salt, fact)
    chk('commitment matches with the right salt', c == commitment(salt, fact))
    chk('commitment differs with a wrong salt', c != commitment('x' + salt, fact))
    tmp = tempfile.mkdtemp()
    try:
        cohort = {'cohort_id': 'cohort-test', 'page_url': 'https://example.org/page', 'planned_publish_date': '2026-10-15',
                  'stop_after_date': '2026-12-14', 'engines': ['e1', 'e2'], 'watch_hosts': ['example.org'],
                  'watch_names': ['Namebeam'], 'decision_rule': 'x',
                  'commitments': {'treatment': {'fact_text_commitment': c, 'pattern_commitment': commitment(salt, pat)}},
                  'probes': [{'probe_id': 'A', 'role': 'treatment_anchored', 'prompt_text': 'Q A?', 'prompt_sha256': sha_text('Q A?'),
                              'commitment': 'treatment'},
                             {'probe_id': 'B', 'role': 'treatment_unanchored', 'prompt_text': 'Q B?', 'prompt_sha256': sha_text('Q B?'),
                              'commitment': 'treatment', 'hit_needs_source': True},
                             {'probe_id': 'PC', 'role': 'positive_control', 'prompt_text': 'Q PC?', 'prompt_sha256': sha_text('Q PC?'),
                              'pattern': r'July\s+31'}]}
        cp = os.path.join(tmp, 'cohort-test.json')
        json.dump(cohort, open(cp, 'w'))
        chk('a valid cohort file passes', check_cohort(cohort) == [])
        edited = json.loads(json.dumps(cohort))
        edited['probes'][0]['prompt_text'] = 'Q A, edited?'
        chk('RED: an edited prompt is caught by prompt_sha256', any('prompt_sha256' in b for b in check_cohort(edited)))
        answers = {'A': 'I could not find it.', 'B': 'Namebeam says 4,817.', 'PC': 'It was first logged on July 31, 2026.'}

        def fake(eng):
            return lambda prompt: {'ok': not (eng == 'e2' and prompt == 'Q A?'), 'text': answers[{'Q A?': 'A', 'Q B?': 'B', 'Q PC?': 'PC'}[prompt]],
                                   'citations': ['https://example.org/page'] if eng == 'e1' else [], 'error': '' if not (eng == 'e2' and prompt == 'Q A?') else 'QUOTA: x',
                                   'http_status': 200, 'model_id': 'm-' + eng, 'search_mode': 'test'}

        def fetch(url):
            if 'wayback' in url:
                return 200, b'{"archived_snapshots": {}}'
            return 200, b'<html>https://example.org/page</html>'
        eng = {'e1': fake('e1'), 'e2': fake('e2')}
        out = os.path.join(tmp, 'out')
        t0 = datetime(2026, 10, 12, 12, 37, tzinfo=timezone.utc)
        n1 = run(cp, out=out, engines=eng, now=t0, run_id='t1', fetch=fetch)
        chk('first run writes 6 rows (3 probes x 2 engines)', n1 == 6)
        rows = read_rows(os.path.join(out, 'ingestion_probe.csv'))
        chk('a failed call is a row (ok 0, error_class QUOTA)', any(r['ok'] == '0' and r['error_class'] == 'QUOTA' for r in rows))
        chk('baseline phase before the publish date', all(r['phase'] == 'baseline' for r in rows))
        chk('url_cited from citations (e1 cites the page)', all(r['url_cited'] == '1' for r in rows if r['engine'] == 'e1'))
        chk('source_named only where the answer names Namebeam', [r['source_named'] for r in rows if r['probe_id'] == 'B'] == ['1', '1'])
        chk('raw file saved word for word', json.load(open(os.path.join(out, *rows[-1]['raw_file'].split('/'))))['answer_text'] == answers['PC'])
        n2 = run(cp, out=out, engines=eng, now=t0 + timedelta(hours=1), run_id='t2', fetch=fetch)
        chk('PERIOD GUARD: a second run on the same date writes 0 rows', n2 == 0 and len(read_rows(os.path.join(out, 'ingestion_probe.csv'))) == 6)
        sc = read_rows(os.path.join(out, 'ingestion_scores.csv'))
        chk('before the reveal the hidden probes are not scored', all(r['fact_named'] == '' for r in sc if r['probe_id'] in ('A', 'B')))
        chk('the public positive control is scored at once', all(r['fact_named'] == '1' for r in sc if r['probe_id'] == 'PC'))
        rv = os.path.join(tmp, 'cohort-test.reveal.json')
        json.dump({'treatment': {'salt': salt, 'fact_text': fact, 'pattern': pat}}, open(rv, 'w'))
        chk('a true reveal matches its commitments', check_reveal(cohort, json.load(open(rv))) == [])
        chk('RED: a tampered reveal is rejected', check_reveal(cohort, {'treatment': {'salt': salt, 'fact_text': fact + ' ', 'pattern': pat}}) != [])
        sc = score(cp, out)
        chk('after the reveal B is a hit (fact plus source), A is not', [r['hit'] for r in sc if r['probe_id'] == 'B'] == [1, 1]
            and all(r['hit'] in (0, '') for r in sc if r['probe_id'] == 'A'))
        # decision rule
        lag = lag_table(dict(cohort, published_date='2026-10-15'), [
            {'probe_id': 'A', 'role': 'r', 'engine': 'e1', 'phase': 'post', 'date': d, 'hit': h}
            for d, h in (('2026-10-15', 0), ('2026-10-16', 1), ('2026-10-17', 0), ('2026-10-18', 1), ('2026-10-19', 1))])
        chk('decision rule: first of two consecutive hits, 3 days after publication', lag[0]['first_hit_date'] == '2026-10-18' and lag[0]['days_to_first_named'] == 3)
        lag2 = lag_table(dict(cohort, published_date='2026-10-15'), [
            {'probe_id': 'A', 'role': 'r', 'engine': 'e1', 'phase': 'post', 'date': '2026-10-16', 'hit': 1}])
        chk('decision rule: one isolated hit is not named', lag2[0]['first_hit_date'] == '' and lag2[0]['status'] == 'NOT_NAMED_YET')
        before = len(read_rows(os.path.join(out, 'ingestion_probe.csv')))
        fetch_fact = lambda url: (200, b'{"archived_snapshots": {}}') if 'wayback' in url else (200, b'<p>Example fact: 4,817 domains.</p>')
        run(cp, out=out, engines=eng, now=t0 + timedelta(days=1), run_id='t3', fetch=fetch_fact)
        pr = [r for r in read_rows(os.path.join(out, 'ingestion_presence.csv')) if r['source'] == 'page_has_fact']
        chk('after the reveal the live page is checked for the fact (1 row, present 1)', len(pr) == 1 and pr[0]['present'] == '1')
        chk('append only: the probe log only grows', len(read_rows(os.path.join(out, 'ingestion_probe.csv'))) == before + 6)
        late = run(cp, out=out, engines=eng, now=datetime(2026, 12, 15, 12, 37, tzinfo=timezone.utc), run_id='t4', fetch=fetch)
        chk('STOP RULE: no rows after stop_after_date', late == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    real = os.path.join(REPO, 'prereg')
    if os.path.isdir(real):
        for f in sorted(os.listdir(real)):
            if f.endswith('.json') and not f.endswith('.reveal.json'):
                rc = load_cohort(os.path.join(real, f))
                chk('repo cohort %s passes its own checks (%s)' % (f, '; '.join(check_cohort(rc)) or 'clean'), check_cohort(rc) == [])
                chk('repo cohort %s holds no plain fact text or hidden pattern' % f,
                    all(not p.get('pattern') for p in rc['probes'] if p.get('commitment')))
    print('SELFTEST ' + ('FAIL: ' + ', '.join(fails) if fails else 'PASS'))
    return not fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--score', action='store_true')
    ap.add_argument('--check-reveal')
    ap.add_argument('--cohort', default=DEFAULT_COHORT)
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if a.check_reveal:
        bad = check_reveal(load_cohort(a.cohort), json.load(io.open(a.check_reveal, encoding='utf-8')))
        print('REVEAL ' + ('MATCH' if not bad else 'MISMATCH: ' + '; '.join(bad)))
        return 0 if not bad else 1
    if a.run:
        run(a.cohort, run_id=os.environ.get('GITHUB_RUN_ID', 'local'), force=a.force)
        return 0
    if a.score:
        score(a.cohort)
        return 0
    ap.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
