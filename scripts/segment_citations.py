#!/usr/bin/env python3
"""Segment-page citation watch for the Namebeam AI Visibility record (loop S1b-1).

Question it answers, once per run day: did Perplexity cite any of the pages we publish
for the record (27 city and segment pages plus the hub), and how many answers did it
read to find out? A zero is printed as a zero, with its denominator.

Inputs (all published, so every count can be re-opened by anyone):
    datasets/e1/sources/sources_<date>.csv       Perplexity source lists, one URL per row
    roster/segment_pages.json                    the watched slugs and the watch start date
    datasets/e1/customer_zero/customer_zero_daily.csv   cross-check of the denominator

Writes datasets/e1/customer_zero/ (beside the customer-zero counter):
    segment_pages_daily.csv        one row per date and watched page, zeros kept
    segment_pages_rolling.csv      per as_of date: last 7 calendar days and since the watch start
    segment_pages_unlisted_hits.csv  cited namebeam.ai URLs that are not on the roster
    segment_pages_probe.csv        one row per day and page: HTTP status, bytes, body hash
                                   (append by date; the only file that cannot be rebuilt)

What counts as a citation of a page: the cited URL host is namebeam.ai or a subdomain of
it (never a look-alike), and the path, lower-cased with a trailing slash and a .html
suffix removed, is exactly /<slug>. A citation of /data/e1/<slug>.json is counted in its
own column and never added to the page count. One answer that cites a page twice counts
once (answers_citing_page); url_rows counts the URL rows.

Guards:
    DENOM-MISMATCH  the answers-with-sources count here differs from customer_zero_daily.csv
                    for the same date (exit 2). Two counters must agree on the denominator.
    NO-PERPLEXITY-SOURCES  printed in day_status when a run day has no Perplexity source rows
                    (the engine failed that day): the zero for that day is "no data".

Usage:
    python3 -I scripts/segment_citations.py                  # rebuild the three derived files
    python3 -I scripts/segment_citations.py --probe          # also GET each page once (appends)
    python3 -I scripts/segment_citations.py --readout        # print the table for the latest date
    python3 -I scripts/segment_citations.py --readout 2026-10-15
    python3 -I scripts/segment_citations.py --selftest

Standard library only.
"""
import argparse
import collections
import csv
import datetime
import glob
import hashlib
import io
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_SOURCES = os.path.join(ROOT, 'datasets', 'e1', 'sources')
DEFAULT_OUT = os.path.join(ROOT, 'datasets', 'e1', 'customer_zero')
DEFAULT_ROSTER = os.path.join(ROOT, 'roster', 'segment_pages.json')
SITE = 'https://namebeam.ai'
UA = 'NamebeamRecordWatch/1.0 (+https://namebeam.ai)'

DAILY_COLS = ['date', 'phase', 'slug', 'kind', 'day_status', 'perplexity_answers_with_sources',
              'own_question_answers', 'own_question_status',
              'answers_citing_page', 'url_rows', 'best_rank', 'answers_citing_data_json']
ROLL_COLS = ['as_of', 'window', 'window_calendar_days', 'days_with_data', 'slug', 'kind',
             'perplexity_answers_with_sources', 'own_question_answers', 'answers_citing_page',
             'url_rows', 'answers_citing_data_json']
UNLISTED_COLS = ['date', 'url', 'path', 'rank', 'check_id', 'prompt_text']
PROBE_COLS = ['date', 'slug', 'url', 'http_status', 'bytes', 'sha256_16', 'last_modified', 'error']


# ---------------------------------------------------------------- matching

def host_is(host, base):
    return host == base or host.endswith('.' + base)


def norm_path(path):
    p = (path or '').strip().lower()
    while p.endswith('/') and len(p) > 1:
        p = p[:-1]
    if p.endswith('.html'):
        p = p[:-5]
    return p


def classify(url, slugs):
    """('page', slug) | ('data', slug) | ('other', path) | None (not ours)."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or '').lower()
    except ValueError:
        return None
    if not host_is(host, 'namebeam.ai'):
        return None
    p = norm_path(parts.path)
    body = p[1:] if p.startswith('/') else p
    if body in slugs:
        return ('page', body)
    if body.startswith('data/e1/') and body.endswith('.json') and body[len('data/e1/'):-5] in slugs:
        return ('data', body[len('data/e1/'):-5])
    return ('other', p or '/')


# ---------------------------------------------------------------- inputs

def load_roster(path):
    with io.open(path, encoding='utf-8') as fh:
        d = json.load(fh)
    pages = [(x['slug'], x['kind']) for x in d['pages']]
    if len({s for s, _ in pages}) != len(pages):
        raise ValueError('duplicate slug in roster')
    questions = {x['slug']: {(q['market'], q['niche']) for q in x.get('questions', [])} for x in d['pages']}
    return d['watch_start'], d['published'], pages, questions


def read_sources(sources_dir, d_from):
    """date -> list of Perplexity sources_cited rows, for dates >= d_from. A date whose file
    exists but holds no Perplexity row maps to []."""
    out = collections.OrderedDict()
    for p in sorted(glob.glob(os.path.join(sources_dir, 'sources_*.csv'))):
        dt = os.path.basename(p)[len('sources_'):-len('.csv')]
        if dt < d_from:
            continue
        with io.open(p, encoding='utf-8', newline='') as fh:
            rows = [r for r in csv.DictReader(fh)
                    if r.get('engine') == 'perplexity' and r.get('field') == 'sources_cited']
        out[dt] = rows
    return out


def read_cz_denominators(out_dir):
    """date -> rows_with_urls for perplexity sources_cited, from customer_zero_daily.csv."""
    p = os.path.join(out_dir, 'customer_zero_daily.csv')
    if not os.path.exists(p):
        return None
    res = {}
    with io.open(p, encoding='utf-8', newline='') as fh:
        for r in csv.DictReader(fh):
            if r['engine'] == 'perplexity' and r['field'] == 'sources_cited':
                res[r['date']] = int(r['rows_with_urls'])
    return res


# ---------------------------------------------------------------- compute

def compute(src, pages, questions, watch_start, published):
    slugs = {s for s, _ in pages}
    kinds = dict(pages)
    daily, unlisted, denom = [], [], {}
    for dt, rows in src.items():
        answers = {r['check_id'] for r in rows}
        denom[dt] = len(answers)
        per_page = {s: {'ans': set(), 'url_rows': 0, 'ranks': [], 'data': set()} for s in slugs}
        for r in rows:
            c = classify(r['url'], slugs)
            if c is None:
                continue
            if c[0] == 'page':
                b = per_page[c[1]]
                b['ans'].add(r['check_id'])
                b['url_rows'] += 1
                try:
                    b['ranks'].append(int(r['rank']))
                except (ValueError, KeyError):
                    pass
            elif c[0] == 'data':
                per_page[c[1]]['data'].add(r['check_id'])
            else:
                unlisted.append({'date': dt, 'url': r['url'], 'path': c[1], 'rank': r.get('rank', ''),
                                 'check_id': r['check_id'], 'prompt_text': r.get('prompt_text', '')})
        asked = collections.defaultdict(set)     # (market, niche) -> check_ids with a source list
        for r in rows:
            asked[(r.get('market', ''), r.get('niche', ''))].add(r['check_id'])
        status = 'OK' if answers else 'NO-PERPLEXITY-SOURCES'
        phase = 'WATCH' if dt >= watch_start else ('PUBLICATION-DAY' if dt >= published else 'BEFORE')
        for s, k in pages:
            b = per_page[s]
            if questions.get(s):
                own = len(set().union(*[asked.get(q, set()) for q in questions[s]]))
                own_status = 'ASKED' if own else 'QUESTION-NOT-ANSWERED'
            else:
                own, own_status = '', 'NO-QUESTION'
            daily.append({'date': dt, 'phase': phase, 'slug': s, 'kind': k, 'day_status': status,
                          'perplexity_answers_with_sources': len(answers),
                          'own_question_answers': own, 'own_question_status': own_status,
                          'answers_citing_page': len(b['ans']), 'url_rows': b['url_rows'],
                          'best_rank': min(b['ranks']) if b['ranks'] else '',
                          'answers_citing_data_json': len(b['data'])})
    return daily, unlisted, denom


def rolling(daily, pages, watch_start):
    by = collections.defaultdict(dict)       # date -> slug -> row
    for r in daily:
        by[r['date']][r['slug']] = r
    dates = sorted(d for d in by if d >= watch_start)
    out = []
    ws = datetime.date.fromisoformat(watch_start)
    for as_of in dates:
        d_as = datetime.date.fromisoformat(as_of)
        for label, start in (('7d', d_as - datetime.timedelta(days=6)), ('since_watch', ws)):
            win = [x for x in dates if start <= datetime.date.fromisoformat(x) <= d_as]
            ok_days = [x for x in win if next(iter(by[x].values()))['day_status'] == 'OK']
            for s, k in pages:
                rr = [by[x][s] for x in ok_days]
                out.append({'as_of': as_of, 'window': label,
                            'window_calendar_days': (d_as - start).days + 1,
                            'days_with_data': len(ok_days), 'slug': s, 'kind': k,
                            'perplexity_answers_with_sources': sum(r['perplexity_answers_with_sources'] for r in rr),
                            'own_question_answers': (sum(r['own_question_answers'] for r in rr)
                                                     if rr and rr[0]['own_question_answers'] != '' else ''),
                            'answers_citing_page': sum(r['answers_citing_page'] for r in rr),
                            'url_rows': sum(r['url_rows'] for r in rr),
                            'answers_citing_data_json': sum(r['answers_citing_data_json'] for r in rr)})
    return out


def write_csv(path, cols, rows):
    with io.open(path, 'w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator='\n')
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------- probe

def default_fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read(4000000)
            return {'status': resp.status, 'bytes': len(body),
                    'sha': hashlib.sha256(body).hexdigest()[:16],
                    'last_modified': resp.headers.get('Last-Modified', ''), 'error': ''}
    except urllib.error.HTTPError as e:
        return {'status': e.code, 'bytes': 0, 'sha': '', 'last_modified': '', 'error': 'HTTP %d' % e.code}
    except Exception as e:  # network down, DNS, timeout: record it, never hide it
        return {'status': 0, 'bytes': 0, 'sha': '', 'last_modified': '',
                'error': (type(e).__name__ + ': ' + str(e))[:120]}


def probe(pages, out_dir, today, fetch=default_fetch):
    path = os.path.join(out_dir, 'segment_pages_probe.csv')
    old = []
    if os.path.exists(path):
        with io.open(path, encoding='utf-8', newline='') as fh:
            old = [r for r in csv.DictReader(fh) if r['date'] != today]   # a rerun replaces its own day
    new = []
    for s, _ in pages:
        url = '%s/%s' % (SITE, s)
        f = fetch(url)
        new.append({'date': today, 'slug': s, 'url': url, 'http_status': f['status'], 'bytes': f['bytes'],
                    'sha256_16': f['sha'], 'last_modified': f['last_modified'], 'error': f['error']})
    rows = sorted(old + new, key=lambda r: (r['date'], r['slug']))
    write_csv(path, PROBE_COLS, rows)
    bad = [r['slug'] for r in new if str(r['http_status']) != '200']
    print('segment_pages_probe: %d pages read for %s, %d not HTTP 200%s'
          % (len(new), today, len(bad), (' (' + ', '.join(bad[:6]) + ')') if bad else ''))
    return new


# ---------------------------------------------------------------- run, readout

def run(sources_dir, out_dir, roster_path, d_from=None, do_probe=False, today=None, fetch=default_fetch):
    watch_start, published, pages, questions = load_roster(roster_path)
    d_from = d_from or published
    src = read_sources(sources_dir, d_from)
    daily, unlisted, denom = compute(src, pages, questions, watch_start, published)
    roll = rolling(daily, pages, watch_start)
    os.makedirs(out_dir, exist_ok=True)
    write_csv(os.path.join(out_dir, 'segment_pages_daily.csv'), DAILY_COLS, daily)
    write_csv(os.path.join(out_dir, 'segment_pages_rolling.csv'), ROLL_COLS, roll)
    write_csv(os.path.join(out_dir, 'segment_pages_unlisted_hits.csv'), UNLISTED_COLS, unlisted)
    status = 0
    cz = read_cz_denominators(out_dir)
    if cz is None:
        print('CROSSCHECK-SKIPPED: customer_zero_daily.csv not found')
    else:
        for dt, n in denom.items():
            if dt in cz and cz[dt] != n:
                print('DENOM-MISMATCH %s: %d answers with sources here, %d in customer_zero_daily.csv' % (dt, n, cz[dt]))
                status = 2
    watch_days = [d for d in denom if d >= watch_start]
    print('segment_pages: %d daily rows (%d run days from %s, %d in the watch window from %s), '
          '%d unlisted namebeam.ai hits -> %s'
          % (len(daily), len(denom), d_from, len(watch_days), watch_start, len(unlisted), out_dir))
    if do_probe:
        probe(pages, out_dir,
              today or datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d'), fetch)
    return status


def readout(out_dir, as_of=None):
    p = os.path.join(out_dir, 'segment_pages_rolling.csv')
    with io.open(p, encoding='utf-8', newline='') as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        print('No watch-window rows yet (first row appears with the 2026-10-09 run).')
        return 1
    dates = sorted({r['as_of'] for r in rows})
    as_of = as_of or dates[-1]
    if as_of not in dates:
        print('No rolling row for %s; available: %s .. %s' % (as_of, dates[0], dates[-1]))
        return 1
    sel = {(r['window'], r['slug']): r for r in rows if r['as_of'] == as_of}
    print('SEGMENT-PAGE CITATION READOUT as of %s (Perplexity answers that returned a source list)' % as_of)
    for label in ('since_watch', '7d'):
        sample = next(v for (w, _), v in sel.items() if w == label)
        print('\n%s: %s calendar days, %s run days with data, %s answers with sources'
              % (label, sample['window_calendar_days'], sample['days_with_data'],
                 sample['perplexity_answers_with_sources']))
        print('| page | kind | answers to its own question | answers citing the page | url rows | '
              'answers citing the page data file |')
        print('|---|---|---:|---:|---:|---:|')
        for (w, s), v in sel.items():
            if w == label:
                print('| %s | %s | %s | %s | %s | %s |' % (s, v['kind'], v['own_question_answers'] or 'n/a',
                                                        v['answers_citing_page'], v['url_rows'],
                                                        v['answers_citing_data_json']))
        hit = [s for (w, s), v in sel.items() if w == label and int(v['answers_citing_page']) > 0]
        zero = [s for (w, s), v in sel.items() if w == label and int(v['answers_citing_page']) == 0]
        print('cited at least once: %d of %d pages (%s)' % (len(hit), len(hit) + len(zero), ', '.join(hit) or 'none'))
        print('zero citations: %d pages' % len(zero))
    return 0


# ---------------------------------------------------------------- selftest

def selftest():
    ok = True

    def check(label, cond):
        nonlocal ok
        print('%s  %s' % ('GREEN' if cond else 'RED  ', label))
        ok = ok and bool(cond)

    pages = [('atlanta-hvac', 'city'), ('ai-name-record', 'hub'), ('kansas-city-health-insurance', 'kc')]
    slugs = {s for s, _ in pages}
    cases = [
        ('https://namebeam.ai/atlanta-hvac', ('page', 'atlanta-hvac')),
        ('https://www.namebeam.ai/atlanta-hvac/', ('page', 'atlanta-hvac')),
        ('https://namebeam.ai/atlanta-hvac?utm_source=x#top', ('page', 'atlanta-hvac')),
        ('https://namebeam.ai/Atlanta-HVAC.html', ('page', 'atlanta-hvac')),
        ('https://namebeam.ai/data/e1/atlanta-hvac.json', ('data', 'atlanta-hvac')),
        ('https://namebeam.ai/brand-new-page', ('other', '/brand-new-page')),
        ('https://namebeam.ai.evil.com/atlanta-hvac', None),
        ('https://notnamebeam.ai/atlanta-hvac', None),
        ('https://example.com/atlanta-hvac', None),
    ]
    check('classifier: subdomain, slash, query, case and .html match; data file separate; look-alikes and '
          'other hosts do not', all(classify(u, slugs) == want for u, want in cases))

    mk = {'A': ('Atlanta GA', 'hvac'), 'B': ('Atlanta GA', 'hvac'), 'C': ('Elsewhere', 'other'),
          'D': ('Kansas City MO', 'brokers')}
    questions = {'atlanta-hvac': {('Atlanta GA', 'hvac')}, 'ai-name-record': set(),
                 'kansas-city-health-insurance': {('Kansas City MO', 'brokers')}}

    def row(dt, cid, url, rank):
        return {'date': dt, 'engine': 'perplexity', 'field': 'sources_cited', 'check_id': cid, 'url': url,
                'rank': str(rank), 'prompt_text': 'q', 'market': mk[cid][0], 'niche': mk[cid][1]}

    src = collections.OrderedDict()
    src['2026-10-08'] = [row('2026-10-08', 'A', 'https://example.com/x', 1)]
    src['2026-10-09'] = [row('2026-10-09', 'A', 'https://namebeam.ai/atlanta-hvac', 3),
                         row('2026-10-09', 'A', 'https://www.namebeam.ai/atlanta-hvac/', 5),
                         row('2026-10-09', 'B', 'https://namebeam.ai/data/e1/atlanta-hvac.json', 2),
                         row('2026-10-09', 'B', 'https://example.com/atlanta-hvac', 1),
                         row('2026-10-09', 'C', 'https://namebeam.ai/brand-new-page', 4),
                         row('2026-10-09', 'C', 'https://notnamebeam.ai/atlanta-hvac', 6)]
    src['2026-10-10'] = []
    src['2026-10-11'] = [row('2026-10-11', 'D', 'https://namebeam.ai/kansas-city-health-insurance', 2)]
    daily, unlisted, denom = compute(src, pages, questions, '2026-10-09', '2026-10-08')
    d = {(r['date'], r['slug']): r for r in daily}
    a = d[('2026-10-09', 'atlanta-hvac')]
    check('one answer citing a page twice counts once, url rows 2, best rank 3',
          a['answers_citing_page'] == 1 and a['url_rows'] == 2 and a['best_rank'] == 3)
    check('data-file citation is its own column and not in the page count',
          a['answers_citing_data_json'] == 1 and d[('2026-10-09', 'ai-name-record')]['answers_citing_page'] == 0)
    check('denominator is distinct answers with a source list (3 on 10-09)',
          denom['2026-10-09'] == 3 and a['perplexity_answers_with_sources'] == 3)
    check('own-question denominator: 2 answers to the Atlanta question on 10-09, hub has none',
          a['own_question_answers'] == 2 and a['own_question_status'] == 'ASKED'
          and d[('2026-10-09', 'ai-name-record')]['own_question_status'] == 'NO-QUESTION')
    check('a page whose question got no answer that day says QUESTION-NOT-ANSWERED, not a bare zero',
          d[('2026-10-09', 'kansas-city-health-insurance')]['own_question_answers'] == 0
          and d[('2026-10-09', 'kansas-city-health-insurance')]['own_question_status'] == 'QUESTION-NOT-ANSWERED'
          and d[('2026-10-11', 'kansas-city-health-insurance')]['own_question_answers'] == 1)
    check('every page prints a row for every date, zeros kept (3 pages x 4 dates)', len(daily) == 12
          and d[('2026-10-09', 'kansas-city-health-insurance')]['answers_citing_page'] == 0)
    check('a day with no Perplexity sources is NO-PERPLEXITY-SOURCES, not a bare zero',
          d[('2026-10-10', 'atlanta-hvac')]['day_status'] == 'NO-PERPLEXITY-SOURCES'
          and d[('2026-10-10', 'atlanta-hvac')]['perplexity_answers_with_sources'] == 0)
    check('phases: publication day, watch days',
          d[('2026-10-08', 'atlanta-hvac')]['phase'] == 'PUBLICATION-DAY'
          and d[('2026-10-09', 'atlanta-hvac')]['phase'] == 'WATCH')
    check('an unlisted namebeam.ai URL is reported, a third-party host is not',
          [u['path'] for u in unlisted] == ['/brand-new-page'])
    roll = rolling(daily, pages, '2026-10-09')
    r = {(x['as_of'], x['window'], x['slug']): x for x in roll}
    k = r[('2026-10-11', 'since_watch', 'atlanta-hvac')]
    check('since_watch on 10-11: 3 calendar days, 2 days with data (10-10 excluded), 1 citing answer of 4',
          k['window_calendar_days'] == 3 and k['days_with_data'] == 2
          and k['perplexity_answers_with_sources'] == 4 and k['answers_citing_page'] == 1
          and k['own_question_answers'] == 2)
    check('7d window on 10-11 matches since_watch while the watch is under 7 days',
          r[('2026-10-11', '7d', 'atlanta-hvac')]['answers_citing_page'] == 1
          and r[('2026-10-11', '7d', 'atlanta-hvac')]['days_with_data'] == 2)

    # RED proof: a literal substring match counts third-party hosts and look-alikes.
    naive = sum(1 for x in src['2026-10-09'] if 'atlanta-hvac' in x['url'])
    precise = sum(1 for x in src['2026-10-09'] if classify(x['url'], slugs) == ('page', 'atlanta-hvac'))
    check('RED PROOF: substring matching counts %d url rows, the precise matcher %d' % (naive, precise),
          naive != precise)

    # files, guard and probe, in a scratch folder
    with tempfile.TemporaryDirectory() as tmp:
        sdir, odir = os.path.join(tmp, 's'), os.path.join(tmp, 'o')
        os.makedirs(sdir)
        os.makedirs(odir)
        for dt, rows in src.items():
            with io.open(os.path.join(sdir, 'sources_%s.csv' % dt), 'w', encoding='utf-8', newline='') as fh:
                w = csv.DictWriter(fh, fieldnames=['date', 'engine', 'run_channel', 'market', 'niche',
                                                   'prompt_text', 'url', 'domain', 'field', 'check_id', 'rank'],
                                   lineterminator='\n')
                w.writeheader()
                for x in rows:
                    w.writerow({'date': dt, 'engine': 'perplexity', 'run_channel': 'api', 'market': x['market'],
                                'niche': x['niche'], 'prompt_text': 'q', 'url': x['url'], 'domain': 'd',
                                'field': 'sources_cited', 'check_id': x['check_id'], 'rank': x['rank']})
        rp = os.path.join(tmp, 'roster.json')
        with io.open(rp, 'w', encoding='utf-8') as fh:
            json.dump({'watch_start': '2026-10-09', 'published': '2026-10-08',
                       'pages': [{'slug': s, 'kind': k_,
                                  'questions': [{'market': m_, 'niche': n_} for m_, n_ in sorted(questions[s])]}
                                 for s, k_ in pages]}, fh)
        st = run(sdir, odir, rp)
        check('no customer_zero_daily.csv: cross-check skipped, run exits 0', st == 0)
        with io.open(os.path.join(odir, 'customer_zero_daily.csv'), 'w', encoding='utf-8', newline='') as fh:
            fh.write('date,engine,field,engine_answers,field_rows,rows_with_urls,answers_citing_ours,'
                     'our_url_rows,our_distinct_urls,groups_hit\n'
                     '2026-10-09,perplexity,sources_cited,3,3,3,1,3,2,namebeam.ai:3\n')
        check('denominators agree with customer_zero_daily.csv: exit 0', run(sdir, odir, rp) == 0)
        with io.open(os.path.join(odir, 'customer_zero_daily.csv'), 'w', encoding='utf-8', newline='') as fh:
            fh.write('date,engine,field,engine_answers,field_rows,rows_with_urls,answers_citing_ours,'
                     'our_url_rows,our_distinct_urls,groups_hit\n'
                     '2026-10-09,perplexity,sources_cited,9,9,9,1,3,2,namebeam.ai:3\n')
        check('DENOM-MISMATCH guard: a different denominator exits 2', run(sdir, odir, rp) == 2)

        calls = []

        def fake(url):
            calls.append(url)
            return {'status': 404 if url.endswith('ai-name-record') else 200, 'bytes': 10,
                    'sha': 'abc', 'last_modified': '', 'error': ''}
        probe(pages, odir, '2026-10-09', fake)
        probe(pages, odir, '2026-10-09', fake)
        probe(pages, odir, '2026-10-10', fake)
        with io.open(os.path.join(odir, 'segment_pages_probe.csv'), encoding='utf-8', newline='') as fh:
            pr = list(csv.DictReader(fh))
        check('probe: a rerun replaces its own day, days accumulate (3 pages x 2 days = 6 rows)', len(pr) == 6)
        check('probe: a 404 is recorded as 404, not dropped',
              any(x['slug'] == 'ai-name-record' and x['http_status'] == '404' for x in pr))
        check('probe: requests the live URL of each page, no cache-buster',
              calls[:3] == [SITE + '/atlanta-hvac', SITE + '/ai-name-record', SITE + '/kansas-city-health-insurance'])
        run(sdir, odir, rp)
        buf = io.StringIO()
        old, sys.stdout = sys.stdout, buf
        try:
            readout(odir, '2026-10-11')
        finally:
            sys.stdout = old
        txt = buf.getvalue()
        check('readout prints every page including zeros and the denominator',
              '| ai-name-record | hub | n/a | 0 | 0 | 0 |' in txt and 'answers with sources' in txt
              and 'cited at least once: 2 of 3 pages' in txt)
    print('SELFTEST %s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--sources', default=DEFAULT_SOURCES)
    ap.add_argument('--out', default=DEFAULT_OUT)
    ap.add_argument('--roster', default=DEFAULT_ROSTER)
    ap.add_argument('--from', dest='d_from', default=None, help='first date to write (default: published date)')
    ap.add_argument('--probe', action='store_true', help='GET each page once and append the result')
    ap.add_argument('--readout', nargs='?', const='', default=None, metavar='AS_OF')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.readout is not None:
        return readout(a.out, a.readout or None)
    return run(a.sources, a.out, a.roster, a.d_from, a.probe)


if __name__ == '__main__':
    sys.exit(main())
