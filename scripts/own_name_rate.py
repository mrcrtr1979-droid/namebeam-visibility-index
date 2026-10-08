#!/usr/bin/env python3
"""Own-name rate for the Namebeam AI Visibility record (Seat S1-3).

For the one fixed question the record asks about Namebeam itself ("What is the best
service to check if AI engines like ChatGPT recommend my small business?", niche
"ai visibility checks", market "US nationwide"), this counts per date and engine:

    rows            raw answer files for that question on that date
    rows_ok         of those, rows whose run_status is OK
    rows_names      of those, rows that name at least one business (competitors_mentioned)
    rows_named_us   rows where the engine named Namebeam in its list of businesses
                    (business_mentioned true, or a listed name that is Namebeam)
    rows_text_us    rows whose answer text contains the word Namebeam

A day is counted for an engine when it has at least one such row. Reruns of one day
are folded into the day, so every "days" figure is a count of dates, not of files.

Why this exists: two earlier reports gave different denominators for the same
question (57 days and 29 days for OpenAI). They are different things. 57 counts
days whose run succeeded; 29 counts days on which the answer named any business at
all. This file keeps both, and the days Namebeam was named, side by side.

Usage:
    python3 -I scripts/own_name_rate.py                       # write datasets/e1/own_name/own_name_daily.csv
    python3 -I scripts/own_name_rate.py --summary 2026-08-02 2026-10-04
    python3 -I scripts/own_name_rate.py --archives DIR        # read tar.gz archives instead of corpus/e1
    python3 -I scripts/own_name_rate.py --selftest

Standard library.
"""
import argparse
import collections
import csv
import io
import os
import re
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_sources as bs  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, '..'))
Q_BUSINESS = 'Namebeam'
Q_NICHE = 'ai visibility checks'
Q_MARKET = 'US nationwide'
ENGINES = ['openai', 'anthropic', 'gemini', 'perplexity']
COLUMNS = ['date', 'engine', 'rows', 'rows_ok', 'rows_names', 'rows_named_us', 'rows_text_us']
WORD_US = re.compile(r'(?<![A-Za-z0-9])namebeam(?![A-Za-z0-9])', re.I)


def is_question(d):
    return (isinstance(d, dict) and d.get('business') == Q_BUSINESS
            and d.get('niche') == Q_NICHE and d.get('market') == Q_MARKET
            and d.get('engine') in ENGINES)


def names_of(d):
    v = d.get('competitors_mentioned')
    return [x for x in v if isinstance(x, str) and x.strip()] if isinstance(v, list) else []


def named_us(d, names):
    if d.get('business_mentioned') is True:
        return True
    return any(WORD_US.search(n) for n in names)


def text_us(d):
    t = d.get('answer_verbatim')
    return bool(isinstance(t, str) and WORD_US.search(t))


def tally(recs):
    """recs: iterable of (filename, dict). Returns {(date, engine): [rows, ok, names, named_us, text_us]}."""
    out = collections.defaultdict(lambda: [0, 0, 0, 0, 0])
    for name, d in recs:
        if not is_question(d):
            continue
        dt = bs.date_of(name, d)
        if not dt:
            continue
        names = names_of(d)
        c = out[(dt, d['engine'])]
        c[0] += 1
        c[1] += 1 if d.get('run_status') == 'OK' else 0
        c[2] += 1 if names else 0
        c[3] += 1 if named_us(d, names) else 0
        c[4] += 1 if text_us(d) else 0
    return out


def write_daily(path, tal):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, 'w', encoding='utf-8', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(COLUMNS)
        for (dt, eng) in sorted(tal):
            w.writerow([dt, eng] + tal[(dt, eng)])


def read_daily(path):
    with io.open(path, encoding='utf-8', newline='') as fh:
        return [{k: (int(v) if k not in ('date', 'engine') else v) for k, v in r.items()}
                for r in csv.DictReader(fh)]


def summarize(daily, d_from, d_to):
    """Days (dates) per engine within the window. Returns {engine: dict}."""
    out = {}
    for e in ENGINES:
        rs = [r for r in daily if r['engine'] == e and d_from <= r['date'] <= d_to]
        out[e] = {
            'days_asked': len({r['date'] for r in rs if r['rows'] > 0}),
            'days_run_ok': len({r['date'] for r in rs if r['rows_ok'] > 0}),
            'days_with_names': len({r['date'] for r in rs if r['rows_names'] > 0}),
            'days_named_us': len({r['date'] for r in rs if r['rows_named_us'] > 0}),
            'days_text_us': len({r['date'] for r in rs if r['rows_text_us'] > 0}),
        }
    return out


def print_summary(daily, d_from, d_to):
    s = summarize(daily, d_from, d_to)
    print('own-name rate, window %s..%s (days are dates; every figure is days_x / days_asked unless stated)' % (d_from, d_to))
    print('engine | days_asked | days_run_ok | days_with_names | days_named_us | days_text_us')
    for e in ENGINES:
        v = s[e]
        print('%s | %d | %d | %d | %d | %d' % (e, v['days_asked'], v['days_run_ok'], v['days_with_names'],
                                                 v['days_named_us'], v['days_text_us']))
    return s


# ------------------------------------------------------------------ selftest
def selftest():
    fx = os.path.join(REPO, 'tests', 'fixtures', 'own_name')
    fails = []

    def chk(name, ok):
        print(('  ok   ' if ok else '  FAIL ') + name)
        if not ok:
            fails.append(name)

    tal = tally(bs.iter_dir(os.path.join(fx, 'corpus')))
    tmp = os.path.join(fx, '_out_daily.csv')
    write_daily(tmp, tal)
    got = io.open(tmp, encoding='utf-8', newline='').read()
    os.remove(tmp)
    want = io.open(os.path.join(fx, 'expected_daily.csv'), encoding='utf-8', newline='').read()
    chk('daily table equals the hand-written expected file byte for byte', got == want)
    daily = [dict(zip(COLUMNS, [k[0], k[1]] + v)) for k, v in sorted(tal.items())]
    s = summarize(daily, '2026-01-01', '2026-01-03')
    chk('openai days_asked=3 days_run_ok=2 days_with_names=2 days_named_us=1 days_text_us=1',
        (s['openai']['days_asked'], s['openai']['days_run_ok'], s['openai']['days_with_names'],
         s['openai']['days_named_us'], s['openai']['days_text_us']) == (3, 2, 2, 1, 1))
    chk('a rerun on one date counts as one day (perplexity days_asked=1, rows=2)',
        s['perplexity']['days_asked'] == 1 and tal[('2026-01-01', 'perplexity')][0] == 2)
    chk('look-alike Namebeamish is not Namebeam', tal[('2026-01-03', 'openai')][3] == 0 and tal[('2026-01-03', 'openai')][4] == 0)
    chk('other niche and other business files are ignored', ('2026-01-02', 'gemini') not in tal)
    s2 = summarize(daily, '2026-01-02', '2026-01-02')
    chk('window filter: the single date 2026-01-02', s2['openai']['days_asked'] == 1)
    # RED proofs: a naive check must give a different, wrong answer on the same fixture
    naive_ok = 0
    for name, d in bs.iter_dir(os.path.join(fx, 'corpus')):
        if is_question(d) and d['engine'] == 'openai' and 'namebeam' in (d.get('answer_verbatim') or '').lower():
            naive_ok += 1
    chk('RED: substring matching counts the look-alike (naive 2 vs word match 1)', naive_ok == 2)
    chk('RED: counting every file as a run-ok day overstates it (3 asked vs 2 ok)',
        summarize(daily, '2026-01-01', '2026-01-03')['openai']['days_asked'] != summarize(daily, '2026-01-01', '2026-01-03')['openai']['days_run_ok'])
    print('SELFTEST ' + ('FAIL: ' + ', '.join(fails) if fails else 'PASS'))
    return not fails


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--corpus', default=os.path.join(REPO, 'corpus', 'e1'))
    ap.add_argument('--archives')
    ap.add_argument('--out', default=os.path.join(REPO, 'datasets', 'e1', 'own_name', 'own_name_daily.csv'))
    ap.add_argument('--summary', nargs=2, metavar=('FROM', 'TO'))
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    tal = tally(bs.records(a.corpus, a.archives))
    write_daily(a.out, tal)
    print('wrote %s (%d date-engine rows)' % (a.out, len(tal)))
    if a.summary:
        print_summary(read_daily(a.out), a.summary[0], a.summary[1])
    return 0


if __name__ == '__main__':
    sys.exit(main())
