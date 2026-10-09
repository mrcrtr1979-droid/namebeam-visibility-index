#!/usr/bin/env python3
"""Independent recompute of the Edition 1 headline numbers, and the input files for the second recompute (M365 Analyst).

    python3 -I scripts/headline_recompute.py --pack releases/edition-1 [--out DIR]
    python3 -I scripts/headline_recompute.py --selftest

Written from the definitions in the method note, NOT by importing the metrics code, so a shared bug cannot hide:
  raw next-day persistence, Perplexity, per window =
    numerator   = business-day namings on day d whose name is also named on d+1, where d and d+1 both answered
    denominator = business-day namings on day d where d+1 is answered
  over cells (market + niche) asked on or before 2026-09-27, market not US nationwide, API rows, engine perplexity.
  A name is the raw string, stripped and case folded. A day is answered when at least one name came back.
  KC page: Perplexity source rows whose URL is https://namebeam.ai/kansas-city-health-insurance (answers, run days).
With --out it writes, for the second recompute: names_long.csv (one row per cell, engine, date, name),
answered_days.csv (one row per cell, engine, answered date), kc_sources.csv, FORMULAS.md, EXPECTED.csv.
Standard library only. Reads the pack; writes only inside --out.
"""
import argparse
import csv
import datetime
import glob
import io
import json
import os
import sys
import tempfile
from collections import defaultdict

sys.dont_write_bytecode = True
PANEL_CUTOFF = '2026-09-27'
KC_URL = 'https://namebeam.ai/kansas-city-health-insurance'
WINDOWS = {'WA': ('2026-09-01', '2026-09-27'), 'WB': ('2026-09-28', None)}


def nxt(d):
    return (datetime.date.fromisoformat(d) + datetime.timedelta(days=1)).isoformat()


def load(pack):
    meta = json.load(open(os.path.join(pack, 'PACK.json'), encoding='utf-8'))
    with io.open(os.path.join(pack, *meta['files']['answers'].split('/')), encoding='utf-8', newline='') as fh:
        rows = list(csv.DictReader(fh))
    return meta, rows


def series(rows, engine='perplexity'):
    panel = {(r['market'].strip(), r['niche'].strip()) for r in rows if r['kind'] == 'API' and r['date'] <= PANEL_CUTOFF}
    ser = defaultdict(lambda: {'answered': set(), 'named': defaultdict(set)})
    for r in rows:
        mk, ni = r['market'].strip(), r['niche'].strip()
        if r['kind'] != 'API' or r['engine'] != engine or not mk or mk.casefold() == 'us nationwide' or (mk, ni) not in panel:
            continue
        names = {n.strip().casefold() for n in r['businesses_named'].split(';') if n.strip()}
        s = ser[(mk, ni)]
        if names:
            s['answered'].add(r['date'])
        s['named'][r['date']] |= names
    return ser


def persistence(ser, w0, w1):
    num = den = 0
    for s in ser.values():
        for d in s['answered']:
            if not (w0 <= d <= w1) or nxt(d) not in s['answered'] or nxt(d) > w1:
                continue
            for b in s['named'][d]:
                den += 1
                num += b in s['named'][nxt(d)]
    return num, den


def kc(pack, meta, w0, w1):
    rows = []
    with io.open(os.path.join(pack, *meta['files']['sources'].split('/')), encoding='utf-8', newline='') as fh:
        for r in csv.DictReader(fh):
            if r.get('engine') == 'perplexity' and r.get('url', '').rstrip('/') == KC_URL and w0 <= r.get('date', '') <= w1:
                rows.append(r)
    answers = {(r['date'], r.get('check_id', '')) for r in rows}
    return rows, len(answers), len({r['date'] for r in rows})


def compute(pack):
    meta, rows = load(pack)
    w1 = meta['window'][1]
    ser = series(rows)
    out = []
    for w, (a, b) in WINDOWS.items():
        b = b or w1
        n, d = persistence(ser, a, b)
        out.append(('P.raw.perplexity.%s' % w, '%s..%s' % (a, b), n, d, round(n / d, 6) if d else None))
    kr, ka, kd = kc(pack, meta, '2026-09-28', '2026-10-07')
    out.append(('KC.page.answers', '2026-09-28..2026-10-07', ka, kd, None))
    kr2, ka2, kd2 = kc(pack, meta, '2026-09-28', w1)
    out.append(('KC.page.answers', '2026-09-28..%s' % w1, ka2, kd2, None))
    return meta, rows, ser, out


def export(pack, outdir, meta, rows, ser, out):
    os.makedirs(outdir, exist_ok=True)
    with io.open(os.path.join(outdir, 'names_long.csv'), 'w', encoding='utf-8', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['market', 'niche', 'engine', 'date', 'name'])
        for (mk, ni), s in sorted(ser.items()):
            for d in sorted(s['named']):
                for n in sorted(s['named'][d]):
                    w.writerow([mk, ni, 'perplexity', d, n])
    with io.open(os.path.join(outdir, 'answered_days.csv'), 'w', encoding='utf-8', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['market', 'niche', 'engine', 'date'])
        for (mk, ni), s in sorted(ser.items()):
            for d in sorted(s['answered']):
                w.writerow([mk, ni, 'perplexity', d])
    kr, _, _ = kc(pack, meta, '2026-09-01', meta['window'][1])
    with io.open(os.path.join(outdir, 'kc_sources.csv'), 'w', encoding='utf-8', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['date', 'check_id', 'url'])
        for r in kr:
            w.writerow([r['date'], r.get('check_id', ''), r['url']])
    with io.open(os.path.join(outdir, 'EXPECTED.csv'), 'w', encoding='utf-8', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['metric', 'window', 'numerator', 'denominator', 'value'])
        w.writerows(out)
    io.open(os.path.join(outdir, 'FORMULAS.md'), 'w', encoding='utf-8').write(FORMULAS % meta['window'][1])


FORMULAS = """# Formulas for the second recompute (do these from the CSV files, not from any number given to you)

Files: names_long.csv (market, niche, engine, date, name), answered_days.csv (market, niche, engine, date), kc_sources.csv.
A cell is market + niche. Every row is Perplexity. A day is answered for a cell when it appears in answered_days.csv.

1. Raw next-day persistence, window WA = 2026-09-01 to 2026-09-27, and window WB = 2026-09-28 to %s.
   For every row of names_long.csv with date d inside the window:
     keep it only if the same cell has answered days d and d+1 in answered_days.csv, and d+1 is inside the window.
   Denominator = number of kept rows.
   Numerator   = number of kept rows whose (market, niche, name) also appears in names_long.csv on date d+1.
   Report numerator, denominator and numerator / denominator to 6 decimals, for WA and for WB.
2. KC page citations: in kc_sources.csv, for 2026-09-28 to 2026-10-07: number of distinct (date, check_id) pairs
   (answers) and number of distinct dates (run days). Then the same for 2026-09-28 to the last date in the file.
Return one table: metric, window, numerator, denominator, value.
"""


def selftest():
    fails = []

    def chk(name, ok):
        print(('  ok   ' if ok else '  FAIL ') + name)
        if not ok:
            fails.append(name)

    def R(d, mk, ni, names, eng='perplexity'):
        return {'kind': 'API', 'engine': eng, 'market': mk, 'niche': ni, 'date': d, 'businesses_named': ';'.join(names)}
    rows = [R('2026-09-01', 'A', 'n', ['X', 'Y']), R('2026-09-02', 'A', 'n', ['x ', 'Z']),   # x recurs (case, space)
            R('2026-09-03', 'A', 'n', []),                                               # not answered: 09-02 pairs drop
            R('2026-09-04', 'A', 'n', ['Q']), R('2026-09-05', 'A', 'n', ['Q']),
            R('2026-09-01', 'US nationwide', 'n', ['X']), R('2026-09-02', 'US nationwide', 'n', ['X']),
            R('2026-09-01', 'A', 'n', ['X'], eng='openai'), R('2026-10-01', 'B', 'late', ['L']), R('2026-10-02', 'B', 'late', ['L'])]
    ser = series(rows)
    n, d = persistence(ser, '2026-09-01', '2026-09-27')
    chk('toy WA: 2 of 3 (X;Y then x;Z, Q then Q; nationwide, openai and late cell out)', (n, d) == (2, 3))
    chk('late cell (asked after the cutoff) is out of the panel', ('B', 'late') not in ser)
    n, d = persistence(ser, '2026-09-01', '2026-09-04')
    chk('window end: a pair whose next day is past the window end is not counted (1 of 2)', (n, d) == (1, 2))
    print('SELFTEST ' + ('FAIL: ' + ', '.join(fails) if fails else 'PASS'))
    return not fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pack', default='releases/edition-1')
    ap.add_argument('--out')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    meta, rows, ser, out = compute(a.pack)
    for m, w, n, d, v in out:
        print('%s %s %s %s %s' % (m, w, n, d, v if v is not None else ''))
    if a.out:
        export(a.pack, a.out, meta, rows, ser, out)
        print('wrote', a.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
