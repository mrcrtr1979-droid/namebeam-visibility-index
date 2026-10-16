#!/usr/bin/env python3
"""7-day variability sheet: what one AI engine answered to one identical local question on seven consecutive days.

Built for the criterion in the IAB "Measuring Visibility in the AI Era" framework (2026-08-03): document variability
across repeated identical queries in a 7-day window. One sheet = one market, one niche, one engine, seven calendar
days, from the published Edition 1 pack. Every day on the sheet carries the raw file name, its SHA-256 and the
run_status, so a reader can pull the answer text from corpus/e1 and recompute every line.

Rule used on the sheet (the pack's raw rule, stated on every sheet): names = businesses_named_no_headings as recorded;
top name = the entry at position 1 of that list; a day is "answered with a name" when run_status is OK and the list is not empty.
The gated and text-mention rules live in reproduce/stability/ and reproduce/recount/; this sheet shows the raw record.

Run from the repo root:
  python3 -I -X utf8 reproduce/variability/variability_sheet.py --market "Atlanta GA" --niche "HVAC repair and replacement" --engine perplexity --start 2026-09-15 --out reproduce/variability/out
  python3 -I -X utf8 reproduce/variability/variability_sheet.py --selftest
--engine all writes one sheet per engine. Standard library, no dependencies. Reads the pack; writes only inside --out.
"""
import argparse, csv, datetime as dt, hashlib, json, os, sys, unittest

PACK = 'releases/edition-1/data/answers_2026-09-01_to_2026-10-13_run_status.csv'
ENGINES = ['openai', 'anthropic', 'gemini', 'perplexity']
LABEL = {'openai': 'OpenAI', 'anthropic': 'Anthropic Claude', 'gemini': 'Google Gemini', 'perplexity': 'Perplexity'}
EDITION_URL = 'https://markets.namebeam.ai/edition-1/'


def norm(n):
    return ' '.join(n.replace('&', 'and').casefold().split())


def names_of(row):
    return [x.strip() for x in (row.get('businesses_named_no_headings') or '').split(';') if x.strip()]


def window_days(start, n=7):
    d0 = dt.date.fromisoformat(start)
    return [(d0 + dt.timedelta(days=i)).isoformat() for i in range(n)]


def jaccard(a, b):
    a, b = {norm(x) for x in a}, {norm(x) for x in b}
    if not a and not b:
        return None
    return len(a & b) / len(a | b)


def build_sheet(rows, market, niche, engine, start):
    """rows: list of dicts from the run_status answers table. Returns the sheet dict."""
    days = window_days(start)
    mk, ni = market.casefold().strip(), niche.casefold().strip()
    cell = [r for r in rows if r['market'].casefold().strip() == mk and r['niche'].casefold().strip() == ni and r['engine'] == engine and r['date'] in days]
    if cell:
        market, niche = cell[0]['market'], cell[0]['niche']
    # earliest saved row per day (rerun 0 before others, then file name)
    by_day = {}
    # earliest saved row per day; an OK row wins over a failed one on the same day (a rerun that succeeded)
    for r in sorted(cell, key=lambda r: ((r.get('run_status') or 'OK') != 'OK', int(r.get('rerun') or 0), r['source_file'])):
        by_day.setdefault(r['date'], r)
    day_rows = []
    for d in days:
        r = by_day.get(d)
        if r is None:
            day_rows.append({'date': d, 'status': 'NO_ROW', 'reason': 'no row in the pack for this day', 'names': [], 'top': '', 'source_file': '', 'sha256': ''})
            continue
        ns = names_of(r)
        st = r.get('run_status') or ('OK' if ns else 'NONE_NAMED')
        if st == 'OK' and not ns:
            st = 'OK_NO_NAME'
        day_rows.append({'date': d, 'status': st, 'reason': r.get('run_status_reason') or '', 'names': ns, 'top': ns[0] if ns else '',
                         'source_file': r['source_file'], 'sha256': r['sha256']})
    answered = [x for x in day_rows if x['status'] == 'OK' and x['names']]
    pairs, top_changes, jac = 0, 0, []
    for a, b in zip(answered, answered[1:]):
        if (dt.date.fromisoformat(b['date']) - dt.date.fromisoformat(a['date'])).days != 1:
            continue  # a pair is d and d+1, both answered with a name
        pairs += 1
        if norm(a['top']) != norm(b['top']):
            top_changes += 1
        j = jaccard(a['names'], b['names'])
        if j is not None:
            jac.append(j)
    distinct = {}
    for x in answered:
        for n in x['names']:
            distinct.setdefault(norm(n), n)
    every_day = [distinct[k] for k in distinct if all(k in {norm(n) for n in x['names']} for x in answered)] if answered else []
    tops = {}
    for x in answered:
        tops.setdefault(norm(x['top']), [x['top'], 0])[1] += 1
    sheet = {
        'market': market, 'niche': niche, 'engine': engine, 'engine_label': LABEL.get(engine, engine),
        'window': [days[0], days[-1]], 'rule': 'raw lists as recorded (businesses_named_no_headings); top = first listed name',
        'days_in_window': 7, 'rows_found': len(cell), 'days_with_row': len([x for x in day_rows if x['status'] != 'NO_ROW']),
        'days_answered_with_name': len(answered),
        'days_by_status': {s: len([x for x in day_rows if x['status'] == s]) for s in sorted({x['status'] for x in day_rows})},
        'consecutive_answered_pairs': pairs, 'top_name_changed': top_changes,
        'top_name_changed_share': (round(top_changes / pairs, 3) if pairs else None),
        'mean_list_overlap_jaccard': (round(sum(jac) / len(jac), 3) if jac else None),
        'distinct_names': len(distinct), 'names_on_every_answered_day': sorted(every_day),
        'top_names': sorted(tops.values(), key=lambda v: (-v[1], v[0])),
        'days': day_rows,
    }
    return sheet


def sheet_markdown(s):
    L = ['# 7-day variability sheet: %s, %s, %s' % (s['engine_label'], s['niche'], s['market']),
         '',
         'Window %s to %s (7 calendar days). Source: Namebeam AI Visibility Record, Edition 1, %s (pack file %s). Rule: %s. Each day links to its raw file and SHA-256 in corpus/e1.' % (s['window'][0], s['window'][1], EDITION_URL, PACK, s['rule']),
         '',
         '| Measure | Value (window %s to %s) |' % tuple(s['window']), '|---|---|',
         '| Days in window | 7 |',
         '| Days with a saved row | %d of 7 |' % s['days_with_row'],
         '| Days answered with at least one name | %d of 7 |' % s['days_answered_with_name'],
         '| Days by status | %s |' % ', '.join('%s %d' % (k, v) for k, v in s['days_by_status'].items()),
         '| Consecutive-day pairs (d and d+1) both answered with a name | %d |' % s['consecutive_answered_pairs'],
         '| Pairs where the top name changed | %s of %d%s |' % (s['top_name_changed'], s['consecutive_answered_pairs'], '' if s['top_name_changed_share'] is None else ' (%.1f%%)' % (100 * s['top_name_changed_share'])),
         '| Mean list overlap between consecutive answered days (Jaccard) | %s |' % ('n/a' if s['mean_list_overlap_jaccard'] is None else '%.3f' % s['mean_list_overlap_jaccard']),
         '| Distinct names over the window | %d |' % s['distinct_names'],
         '| Names present on every answered day | %s |' % (', '.join(s['names_on_every_answered_day']) or 'none'),
         '| Top names (days at position 1) | %s |' % (', '.join('%s %d' % (n, c) for n, c in s['top_names']) or 'none'),
         '', '## The seven days', '',
         '| Date | Status | Top name | Names as recorded | Raw file | SHA-256 |', '|---|---|---|---|---|---|']
    for d in s['days']:
        L.append('| %s | %s%s | %s | %s | %s | %s |' % (d['date'], d['status'], (' (' + d['reason'] + ')') if d['reason'] and d['status'] not in ('OK',) else '',
                                                     d['top'], '; '.join(d['names']), d['source_file'], d['sha256']))
    L += ['', 'Criterion statement: over these 7 days the same question, in the same words, was asked once a day to %s; %d of 7 days returned a named answer; the business at position 1 changed on %d of %d consecutive-day pairs (d and d+1 both named); the mean day-to-day overlap of the named list was %s. Failed and unnamed days are shown, not dropped.' % (
        s['engine_label'], s['days_answered_with_name'], s['top_name_changed'], s['consecutive_answered_pairs'],
        'n/a' if s['mean_list_overlap_jaccard'] is None else '%.3f' % s['mean_list_overlap_jaccard']), '']
    return '\n'.join(L)


def load_rows(repo):
    csv.field_size_limit(10 ** 9)
    return list(csv.DictReader(open(os.path.join(repo, PACK), encoding='utf-8')))


def slug(s):
    return ''.join(ch if ch.isalnum() else '-' for ch in s.casefold()).strip('-')


def write_sheet(s, out):
    os.makedirs(out, exist_ok=True)
    base = os.path.join(out, 'variability_%s_%s_%s_%s' % (slug(s['market']), slug(s['niche']), s['engine'], s['window'][0]))
    md = sheet_markdown(s)
    open(base + '.md', 'w', encoding='utf-8', newline='\n').write(md)
    json.dump(s, open(base + '.json', 'w', encoding='utf-8'), indent=1)
    with open(base + '.csv', 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh); w.writerow(['date', 'status', 'reason', 'top', 'names', 'source_file', 'sha256'])
        for d in s['days']:
            w.writerow([d['date'], d['status'], d['reason'], d['top'], ';'.join(d['names']), d['source_file'], d['sha256']])
    return base + '.md', hashlib.sha256(md.encode('utf-8')).hexdigest()


# ------------------------------------------------------------------ atlas: every cell, every engine, fixed 7-day windows
WEEKS = ['2026-09-01', '2026-09-08', '2026-09-15', '2026-09-22', '2026-09-29', '2026-10-06']
PANEL_CUTOFF = '2026-09-27'


def atlas(rows, out, weeks=WEEKS, panel=False, write_sheets=False):
    """One index row per cell x engine x 7-day window; a per-engine-week summary; optional sheets. panel=True keeps the
    Edition 1 metrics panel: cells with an API row on or before PANEL_CUTOFF, market not US nationwide."""
    cells = sorted({(r['market'], r['niche']) for r in rows if r['engine'] in ENGINES and r['market'].strip() and r['niche'].strip()})
    if panel:
        first = {}
        for r in rows:
            if r['engine'] in ENGINES:
                k = (r['market'], r['niche']); first[k] = min(first.get(k, r['date']), r['date'])
        cells = [c for c in cells if first.get(c, '9999') <= PANEL_CUTOFF and c[0].casefold() != 'us nationwide']
    os.makedirs(out, exist_ok=True)
    idx_path = os.path.join(out, 'variability_index%s.csv' % ('_panel' if panel else ''))
    summ = {}
    with open(idx_path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['market', 'niche', 'engine', 'window_start', 'window_end', 'days_with_row', 'days_answered_with_name', 'consecutive_answered_pairs',
                    'top_name_changed', 'top_name_changed_share', 'mean_list_overlap_jaccard', 'distinct_names', 'names_on_every_answered_day', 'top_names'])
        for mk, ni in cells:
            for e in ENGINES:
                for wk in weeks:
                    s = build_sheet(rows, mk, ni, e, wk)
                    w.writerow([mk, ni, e, s['window'][0], s['window'][1], s['days_with_row'], s['days_answered_with_name'], s['consecutive_answered_pairs'],
                                s['top_name_changed'], '' if s['top_name_changed_share'] is None else s['top_name_changed_share'],
                                '' if s['mean_list_overlap_jaccard'] is None else s['mean_list_overlap_jaccard'], s['distinct_names'],
                                ';'.join(s['names_on_every_answered_day']), ';'.join('%s:%d' % (n, c) for n, c in s['top_names'])])
                    k = (e, wk); t = summ.setdefault(k, [0, 0, 0])
                    t[0] += s['consecutive_answered_pairs']; t[1] += s['top_name_changed']; t[2] += s['days_answered_with_name']
                    if write_sheets and s['days_with_row']:
                        write_sheet(s, os.path.join(out, 'sheets'))
    sum_path = os.path.join(out, 'variability_summary%s.csv' % ('_panel' if panel else ''))
    with open(sum_path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh); w.writerow(['engine', 'window_start', 'cells', 'answered_days', 'pairs', 'top_changed', 'share'])
        for (e, wk) in sorted(summ, key=lambda k: (ENGINES.index(k[0]), k[1])):
            p_, c_, d_ = summ[(e, wk)]
            w.writerow([e, wk, len(cells), d_, p_, c_, '' if not p_ else round(c_ / p_, 3)])
    return idx_path, sum_path, len(cells)


# ------------------------------------------------------------------ tests
def _row(date, engine, names, status='OK', reason='', market='M', niche='N', sf=None, rerun=0):
    return {'date': date, 'engine': engine, 'market': market, 'niche': niche, 'businesses_named_no_headings': ';'.join(names),
            'run_status': status, 'run_status_reason': reason, 'source_file': sf or ('f_%s_%s.json' % (date, engine)),
            'sha256': hashlib.sha256((date + engine + ';'.join(names)).encode()).hexdigest(), 'rerun': str(rerun)}


class Tests(unittest.TestCase):
    def test_counts_and_changes(self):
        rows = [_row('2026-09-01', 'openai', ['A Co', 'B Co']), _row('2026-09-02', 'openai', ['B Co', 'A Co']),
                _row('2026-09-03', 'openai', [], 'FAILED', 'HTTP 429'), _row('2026-09-04', 'openai', ['B Co']),
                _row('2026-09-05', 'openai', ['C Co']), _row('2026-09-06', 'openai', [], 'OK'), _row('2026-09-07', 'openai', ['C Co', 'A Co'])]
        s = build_sheet(rows, 'M', 'N', 'openai', '2026-09-01')
        self.assertEqual(s['days_with_row'], 7); self.assertEqual(s['days_answered_with_name'], 5)
        self.assertEqual(s['days_by_status'], {'FAILED': 1, 'OK': 5, 'OK_NO_NAME': 1})
        self.assertEqual(s['consecutive_answered_pairs'], 2)       # 01-02 and 04-05 only (d and d+1 both named)
        self.assertEqual(s['top_name_changed'], 2)                   # A->B, B->C
        self.assertAlmostEqual(s['mean_list_overlap_jaccard'], round((1.0 + 0.0) / 2, 3))
        self.assertEqual(s['distinct_names'], 3); self.assertEqual(s['names_on_every_answered_day'], [])

    def test_missing_days_and_reruns(self):
        rows = [_row('2026-09-01', 'gemini', ['X']), _row('2026-09-01', 'gemini', ['Y'], rerun=1), _row('2026-09-03', 'gemini', ['X'])]
        s = build_sheet(rows, 'M', 'N', 'gemini', '2026-09-01')
        self.assertEqual(s['days_by_status']['NO_ROW'], 5); self.assertEqual(s['days'][0]['top'], 'X')  # rerun 0 wins
        self.assertEqual(s['consecutive_answered_pairs'], 0)       # 09-01 and 09-03 are not d and d+1
        self.assertEqual(s['names_on_every_answered_day'], ['X'])

    def test_ok_rerun_beats_failed_first_row(self):
        rows = [_row('2026-09-01', 'gemini', [], 'FAILED', 'HTTP 429'), _row('2026-09-01', 'gemini', ['Z'], rerun=1)]
        s = build_sheet(rows, 'M', 'N', 'gemini', '2026-09-01')
        self.assertEqual(s['days'][0]['status'], 'OK'); self.assertEqual(s['days'][0]['top'], 'Z')

    def test_empty_cell(self):
        s = build_sheet([], 'M', 'N', 'anthropic', '2026-09-01')
        self.assertEqual(s['days_answered_with_name'], 0); self.assertIsNone(s['top_name_changed_share'])
        self.assertIn('none', sheet_markdown(s))

    def test_name_normalisation(self):
        self.assertEqual(norm('Reliable Heating & Air'), 'reliable heating and air')
        self.assertEqual(jaccard(['A & B'], ['a and b']), 1.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    ap.add_argument('--market'); ap.add_argument('--niche'); ap.add_argument('--engine', default='all'); ap.add_argument('--start')
    ap.add_argument('--out', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out'))
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--atlas', action='store_true', help='index every cell x engine x fixed 7-day window')
    ap.add_argument('--panel', action='store_true', help='with --atlas: Edition 1 metrics panel cells only')
    ap.add_argument('--write-sheets', action='store_true', help='with --atlas: also write every sheet')
    a = ap.parse_args()
    if a.selftest:
        return unittest.main(argv=[sys.argv[0]], verbosity=1)
    if a.atlas:
        rows = load_rows(a.repo)
        i, sm, n = atlas(rows, a.out, panel=a.panel, write_sheets=a.write_sheets)
        print('atlas: %d cells -> %s and %s' % (n, i, sm))
        for line in open(sm, encoding='utf-8'):
            print('  ' + line.rstrip())
        return 0
    if not (a.market and a.niche and a.start):
        ap.error('--market, --niche and --start are required')
    rows = load_rows(a.repo)
    for e in (ENGINES if a.engine == 'all' else [a.engine]):
        s = build_sheet(rows, a.market, a.niche, e, a.start)
        path, h = write_sheet(s, a.out)
        print('%s | answered %d of 7 | top changed %s of %d | sha256 %s' % (path, s['days_answered_with_name'], s['top_name_changed'], s['consecutive_answered_pairs'], h[:16]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
