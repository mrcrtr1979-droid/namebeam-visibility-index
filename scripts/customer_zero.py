#!/usr/bin/env python3
"""Customer-zero citation counter for the Namebeam AI Visibility record (Seat S1-2).

Counts the answers in which an engine cited a URL on one of our own properties.
Hits are read from the published datasets/e1/sources/sources_<date>.csv files, so
every hit is a row anyone can open. Denominators come from the raw answer files in
corpus/e1/, because a count without its denominator is not a result.

Writes datasets/e1/customer_zero/:
    customer_zero_daily.csv    one row per date, engine and field (zeros included)
    customer_zero_hits.csv     every hit: the cited URL, the question, the rank
    customer_zero_rolling.csv  rolling 7 and 30 calendar day windows per as_of date

Columns of customer_zero_daily.csv:
    date, engine, field, engine_answers, field_rows, rows_with_urls,
    answers_citing_ours, our_url_rows, our_distinct_urls, groups_hit
  engine_answers       all answers by that engine that day
  field_rows           answers that carry the source field (the denominator)
  rows_with_urls       of those, answers with at least one URL
  answers_citing_ours  distinct answers with at least one URL on our properties
  Do not add rows of different fields for one engine: a Google results page can cite
  us in both organic results and the AI Overview.

What counts as ours (host match includes subdomains, never a look-alike):
    namebeam.ai, thereceiptsindex.com, receiptsindex.com, trunkline.money,
    carterenterprise.llc, huggingface.co/Namebeam/..., datarade.ai/...namebeam...
    plus any --extra-domain given at run time.

Guards:
    STALE-SOURCES  a day's sources CSV row count differs from what the raw files
                   hold, so the CSV is stale or missing; exit 2.

Usage:
    python3 -I scripts/customer_zero.py                 # rebuild all three files
    python3 -I scripts/customer_zero.py --check-baseline
    python3 -I scripts/customer_zero.py --selftest

Standard library only.
"""
import argparse
import collections
import csv
import datetime
import glob
import io
import os
import re
import sys
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import build_sources as bs  # noqa: E402

ROOT = os.path.dirname(HERE)
DEFAULT_CORPUS = os.path.join(ROOT, 'corpus', 'e1')
DEFAULT_SOURCES = os.path.join(ROOT, 'datasets', 'e1', 'sources')
DEFAULT_OUT = os.path.join(ROOT, 'datasets', 'e1', 'customer_zero')
FIXTURES = os.path.join(ROOT, 'tests', 'fixtures', 'customer_zero')

DAILY_COLS = ['date', 'engine', 'field', 'engine_answers', 'field_rows', 'rows_with_urls',
              'answers_citing_ours', 'our_url_rows', 'our_distinct_urls', 'groups_hit']
HIT_COLS = ['date', 'engine', 'field', 'run_channel', 'market', 'niche', 'prompt_text',
            'url', 'domain', 'our_group', 'rank', 'check_id']
ROLL_COLS = ['as_of', 'window_days', 'days_with_data', 'engine', 'field', 'field_rows',
             'answers_citing_ours', 'our_url_rows']
WINDOWS = (7, 30)


def host_is(host, base):
    return host == base or host.endswith('.' + base)


def make_groups(extra=()):
    groups = [
        ('namebeam.ai', lambda h, p: host_is(h, 'namebeam.ai')),
        ('thereceiptsindex.com', lambda h, p: host_is(h, 'thereceiptsindex.com')),
        ('receiptsindex.com', lambda h, p: host_is(h, 'receiptsindex.com')),
        ('trunkline.money', lambda h, p: host_is(h, 'trunkline.money')),
        ('carterenterprise.llc', lambda h, p: host_is(h, 'carterenterprise.llc')),
        ('huggingface.co/Namebeam',
         lambda h, p: h in ('huggingface.co', 'hf.co')
         and re.match(r'^/(datasets/|spaces/|models/)?namebeam(/|$)', p, re.I) is not None),
        ('datarade.ai/namebeam', lambda h, p: host_is(h, 'datarade.ai') and 'namebeam' in p.lower()),
    ]
    for x in extra:
        x = x.strip().lower()
        if x:
            groups.append((x, lambda h, p, x=x: host_is(h, x)))
    return groups


def classify(url, groups):
    try:
        path = urlsplit(url).path or ''
    except ValueError:
        path = ''
    host = bs.url_domain(url)
    for name, fn in groups:
        if fn(host, path):
            return name
    return ''


def read_sources(sources_dir):
    out = collections.OrderedDict()
    for p in sorted(glob.glob(os.path.join(sources_dir, 'sources_*.csv'))):
        dt = os.path.basename(p)[len('sources_'):-len('.csv')]
        with io.open(p, encoding='utf-8', newline='') as fh:
            out[dt] = list(csv.DictReader(fh))
    return out


def raw_denominators(corpus):
    """date -> {'engine_answers': Counter(engine), 'field_rows': Counter((engine, field)),
    'url_rows': int}. url_rows counts what the builder extracts, to cross-check the CSVs."""
    res = collections.defaultdict(lambda: {'engine_answers': collections.Counter(),
                                           'field_rows': collections.Counter(), 'url_rows': 0})
    for name, d in bs.records(corpus, None):
        if not isinstance(d, dict) or not d.get('engine'):
            continue
        dt = bs.date_of(name, d)
        eng = d['engine']
        res[dt]['engine_answers'][eng] += 1
        for f in bs.FIELD_MAP:
            if isinstance(d.get(f), list):
                res[dt]['field_rows'][(eng, f)] += 1
        rows, _ = bs.extract(name, d)
        res[dt]['url_rows'] += len(rows)
    return res


def build(corpus, sources_dir, out_dir, extra=()):
    groups = make_groups(extra)
    src = read_sources(sources_dir)
    den = raw_denominators(corpus)
    status = 0
    for dt in sorted(den):
        have = len(src.get(dt, []))
        if have != den[dt]['url_rows']:
            print('STALE-SOURCES %s: sources CSV has %d rows, raw files hold %d' % (dt, have, den[dt]['url_rows']))
            status = 2

    hits, daily = [], []
    stats = {}   # (dt, eng, field) -> dict
    for dt in sorted(den):
        rows = src.get(dt, [])
        with_urls = collections.defaultdict(set)
        citing = collections.defaultdict(set)
        urlrows = collections.Counter()
        distinct = collections.defaultdict(set)
        grp = collections.defaultdict(collections.Counter)
        for r in rows:
            key = (r['engine'], r['field'])
            with_urls[key].add(r['check_id'])
            g = classify(r['url'], groups)
            if g:
                citing[key].add(r['check_id'])
                urlrows[key] += 1
                distinct[key].add(r['url'])
                grp[key][g] += 1
                hits.append({'date': dt, 'engine': r['engine'], 'field': r['field'],
                             'run_channel': r['run_channel'], 'market': r['market'], 'niche': r['niche'],
                             'prompt_text': r['prompt_text'], 'url': r['url'], 'domain': r['domain'],
                             'our_group': g, 'rank': r['rank'], 'check_id': r['check_id']})
        for (eng, f), n in sorted(den[dt]['field_rows'].items()):
            key = (eng, f)
            row = {'date': dt, 'engine': eng, 'field': f,
                   'engine_answers': den[dt]['engine_answers'][eng],
                   'field_rows': n, 'rows_with_urls': len(with_urls[key]),
                   'answers_citing_ours': len(citing[key]), 'our_url_rows': urlrows[key],
                   'our_distinct_urls': len(distinct[key]),
                   'groups_hit': ';'.join('%s:%d' % (g, c) for g, c in sorted(grp[key].items()))}
            daily.append(row)
            stats[(dt, eng, f)] = row

    # rolling windows over calendar days; days_with_data makes a missing run day visible
    dates = sorted(den)
    rolling = []
    for as_of in dates:
        d_as = datetime.date.fromisoformat(as_of)
        for w in WINDOWS:
            start = d_as - datetime.timedelta(days=w - 1)
            in_win = [x for x in dates if start <= datetime.date.fromisoformat(x) <= d_as]
            keys = sorted({(e, f) for x in in_win for (dd, e, f) in stats if dd == x})
            for (e, f) in keys:
                fr = ac = ur = 0
                for x in in_win:
                    s = stats.get((x, e, f))
                    if s:
                        fr += s['field_rows']
                        ac += s['answers_citing_ours']
                        ur += s['our_url_rows']
                rolling.append({'as_of': as_of, 'window_days': w, 'days_with_data': len(in_win),
                                'engine': e, 'field': f, 'field_rows': fr,
                                'answers_citing_ours': ac, 'our_url_rows': ur})

    os.makedirs(out_dir, exist_ok=True)
    for fname, cols, rows in (('customer_zero_daily.csv', DAILY_COLS, daily),
                              ('customer_zero_hits.csv', HIT_COLS, hits),
                              ('customer_zero_rolling.csv', ROLL_COLS, rolling)):
        with io.open(os.path.join(out_dir, fname), 'w', encoding='utf-8', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=cols, lineterminator='\n')
            w.writeheader()
            w.writerows(rows)
    print('customer_zero: %d daily rows, %d hits, %d rolling rows -> %s' % (len(daily), len(hits), len(rolling), out_dir))
    return status, daily, hits, rolling


def summarize(daily, hits):
    tot = collections.defaultdict(lambda: [0, 0])
    for r in daily:
        t = tot[(r['engine'], r['field'])]
        t[0] += r['field_rows']
        t[1] += r['answers_citing_ours']
    print('RECORD TOTALS (answers citing ours of answers carrying the field):')
    for (e, f), (n, c) in sorted(tot.items()):
        print('  %-12s %-24s %4d of %5d' % (e, f, c, n))
    by = collections.Counter((h['our_group'], h['engine'], h['field']) for h in hits)
    print('HITS BY GROUP (url rows):')
    for k, v in sorted(by.items()):
        print('  %-26s %-12s %-24s %d' % (k[0], k[1], k[2], v))


def check_baseline(hits, d_from='2026-09-28', d_to='2026-10-07'):
    kc = {h['check_id'] for h in hits
          if h['our_group'] == 'namebeam.ai' and h['engine'] == 'perplexity'
          and h['url'].rstrip('/').endswith('/kansas-city-health-insurance') and d_from <= h['date'] <= d_to}
    tl = {h['check_id'] for h in hits
          if h['our_group'] == 'trunkline.money' and h['engine'] == 'perplexity' and h['date'] == '2026-09-29'}
    ok1, ok2 = len(kc) == 9, len(tl) == 1
    print('BASELINE kansas-city-health-insurance, perplexity answers %s..%s: %d (expect 9) %s'
          % (d_from, d_to, len(kc), 'MATCH' if ok1 else 'MISMATCH'))
    print('BASELINE trunkline.money, perplexity answers on 2026-09-29: %d (expect 1) %s'
          % (len(tl), 'MATCH' if ok2 else 'MISMATCH'))
    return 0 if (ok1 and ok2) else 1


def selftest():
    ok = True

    def check(label, cond):
        nonlocal ok
        print('%s  %s' % ('GREEN' if cond else 'RED  ', label))
        ok = ok and cond

    corpus = os.path.join(FIXTURES, 'corpus')
    tmp_src = os.path.join(FIXTURES, '_sources')
    tmp_out = os.path.join(FIXTURES, '_out')
    for d in (tmp_src, tmp_out):
        for f in glob.glob(os.path.join(d, '*')):
            os.remove(f)
    bs.build(corpus, None, tmp_src)
    status, daily, hits, rolling = build(corpus, tmp_src, tmp_out)
    exp_hits = io.open(os.path.join(FIXTURES, 'expected_hits.csv'), encoding='utf-8').read()
    exp_daily = io.open(os.path.join(FIXTURES, 'expected_daily.csv'), encoding='utf-8').read()
    got_hits = io.open(os.path.join(tmp_out, 'customer_zero_hits.csv'), encoding='utf-8').read()
    got_daily = io.open(os.path.join(tmp_out, 'customer_zero_daily.csv'), encoding='utf-8').read()
    check('hits file equals the hand-written expected file byte for byte', got_hits == exp_hits)
    check('daily file equals the hand-written expected file byte for byte', got_daily == exp_daily)
    groups_found = {h['our_group'] for h in hits}
    check('subdomain, HF Namebeam path, Datarade namebeam slug and receipts index all match',
          groups_found == {'namebeam.ai', 'huggingface.co/Namebeam', 'datarade.ai/namebeam',
                           'thereceiptsindex.com', 'trunkline.money'})
    urls = {h['url'] for h in hits}
    check('look-alikes and other owners are not hits',
          not any(x in urls for x in ('https://notnamebeam.ai/y', 'https://namebeam.ai.evil.com/z',
                                      'https://huggingface.co/datasets/other/thing',
                                      'https://datarade.ai/data-products/other')))
    zero = [r for r in daily if r['date'] == '2026-02-02' and r['engine'] == 'perplexity']
    check('a day with no hits still prints its row with the denominator',
          len(zero) == 1 and zero[0]['answers_citing_ours'] == 0 and zero[0]['field_rows'] == 1)
    serp = [r for r in daily if r['date'] == '2026-02-01' and r['engine'] == 'google_serp']
    check('one results page citing us in organic and AIO counts once per field, not summed',
          sorted((r['field'], r['answers_citing_ours']) for r in serp)
          == [('ai_overview_references', 1), ('organic_top_results', 1)])
    r7 = [r for r in rolling if r['as_of'] == '2026-02-02' and r['window_days'] == 7
          and r['engine'] == 'perplexity']
    check('rolling 7 day window sums both fixture days with days_with_data 2',
          len(r7) == 1 and r7[0]['field_rows'] == 2 and r7[0]['answers_citing_ours'] == 1
          and r7[0]['days_with_data'] == 2)

    # RED proof: the W2-1 style literal substring match on datarade.ai over-counts.
    sub = sum(1 for h in bs_rows(tmp_src) if 'datarade.ai' in h['url'] or 'namebeam.ai' in h['url'])
    check('RED PROOF: literal substring matching (%d) differs from the precise matcher (%d)'
          % (sub, len(hits)), sub != len(hits))

    # Guard: a stale sources CSV is reported.
    stale = os.path.join(FIXTURES, '_stale')
    for f in glob.glob(os.path.join(stale, '*')):
        os.remove(f)
    os.makedirs(stale, exist_ok=True)
    s2, _, _, _ = build(corpus, stale, tmp_out)
    check('STALE-SOURCES guard: missing sources CSVs exit 2', s2 == 2)

    for d in (tmp_src, tmp_out, stale):
        for f in glob.glob(os.path.join(d, '*')):
            os.remove(f)
        os.rmdir(d)
    print('SELFTEST %s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


def bs_rows(sources_dir):
    out = []
    for rows in read_sources(sources_dir).values():
        out.extend(rows)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--corpus', default=DEFAULT_CORPUS)
    ap.add_argument('--sources', default=DEFAULT_SOURCES)
    ap.add_argument('--out', default=DEFAULT_OUT)
    ap.add_argument('--extra-domain', action='append', default=[])
    ap.add_argument('--check-baseline', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    status, daily, hits, rolling = build(a.corpus, a.sources, a.out, a.extra_domain)
    summarize(daily, hits)
    if a.check_baseline:
        return max(status, check_baseline(hits))
    return status


if __name__ == '__main__':
    sys.exit(main())
