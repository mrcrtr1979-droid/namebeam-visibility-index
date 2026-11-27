#!/usr/bin/env python3
"""source_share_watch.py: a tripwire on which sites an AI engine cites. Zero model calls, standard library only.

It reads corpus/namebeam/citation_corpus.csv and, for one engine, compares each site's share of answers in the
newest WINDOW days with its share in the BASELINE days before that. A site whose share moved by |z| >= Z fires.
It never compares across a change in our own collector: rows are grouped by collector mode (the flags column),
and a window that mixes modes, or a window and baseline in different modes, prints MODE CHANGE and fires nothing.

Gemini cites through redirect links that expire after about 30 days. For --engine gemini the links are read through
corpus/namebeam/gemini_source_resolution.csv, and an answer is kept only when EVERY one of its links resolved. An answer
with a dead link is left out and counted, never read as "cited nothing". The run prints the coverage and any FAILED rows.

Usage:
  python3 -I tools/source_share_watch.py                      newest day in the file, engine perplexity
  python3 -I tools/source_share_watch.py --engine gemini      the same series on Gemini (resolved links only)
  python3 -I tools/source_share_watch.py --asof 2026-09-19    as the record stood on a past day
  python3 -I tools/source_share_watch.py --backtest           first day each site would have fired
  python3 -I tools/source_share_watch.py --series OUT.csv     the weekly table on the fixed 15-set panel
  python3 -I tools/source_share_watch.py --selftest
Born 2026-11-27 (CLOUD-DIVE-1127). Share = percent of answers that cite the site at least once.
Only question sets present in both the window and the baseline are compared, so a change in the mix of questions cannot fire.
"""
import argparse, collections, csv, datetime, io, math, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, 'corpus', 'namebeam', 'citation_corpus.csv')
RESOLUTION = os.path.join(ROOT, 'corpus', 'namebeam', 'gemini_source_resolution.csv')

# The fixed panel of the weekly series: the 15 question sets Perplexity answered every day from 2026-08-24
# (health insurance 2, heating and cooling 4, injury law 5, roofing 4). Every engine is read on the same 15.
PANEL = (
    'segment_health_insurance_denver', 'segment_health_insurance_kansas_city',
    'segment_hvac_atlanta', 'segment_hvac_houston', 'segment_hvac_phoenix', 'segment_hvac_tampa',
    'segment_personal_injury_law_atlanta', 'segment_personal_injury_law_chicago', 'segment_personal_injury_law_dallas',
    'segment_personal_injury_law_houston', 'segment_personal_injury_law_phoenix',
    'segment_roofing_charlotte', 'segment_roofing_dallas', 'segment_roofing_denver', 'segment_roofing_miami',
)
SERIES_SITES = (
    'reddit.com', 'expertise.com', 'forbes.com', 'angi.com', 'bbb.org', 'thumbtack.com', 'yelp.com', 'superlawyers.com',
    'findlaw.com', 'avvo.com', 'justia.com', 'consumeraffairs.com', 'bestinhood.com', 'theroofingbrief.com', 'roofs.wiki',
    '5starhomeservicepros.com',
)


def domains(text):
    out = set()
    for u in re.split(r'[;\s]+', text or ''):
        m = re.match(r'https?://([^/\s;,]+)', u.strip())
        if m:
            out.add('.'.join(m.group(1).lower().split(':')[0].split('.')[-2:]))
    return out


def links(text):
    return [u.strip() for u in re.split(r'[;\s]+', text or '') if u.strip().startswith('http')]


def mode_of(flags):
    m = re.search(r'pplx:[a-z0-9-]+', flags or '')
    return m.group(0) if m else 'default'


def load_resolution(path):
    """{redirect_url: resolved_url} for the Gemini redirect links that resolved (a dead link has no resolved_url)."""
    csv.field_size_limit(10 ** 9)
    out = {}
    with io.open(path, encoding='utf-8', errors='ignore', newline='') as fh:
        for r in csv.DictReader(fh):
            if (r.get('resolved_url') or '').startswith('http'):
                out[r['redirect_url']] = r['resolved_url']
    return out


def parse_rows(dict_rows, engine, resolution=None, stats=None):
    """Rows (day, mode, sites, niche, n_links). With a resolution map, an answer is kept only if every link resolved.
    stats (a dict) collects: sourced (answers with links), unresolved {(day, niche): n}, failed {(day, flag): n}."""
    rows = []
    for r in dict_rows:
        if (r.get('engine') or '').lower() != engine:
            continue
        try:
            day = datetime.date.fromisoformat((r.get('date_utc') or '')[:10])
        except ValueError:
            continue
        flag = (r.get('flags') or '').strip()
        if stats is not None and flag.startswith('FAILED'):
            stats.setdefault('failed', collections.Counter())[(day, flag[:60])] += 1
        if 'http' not in (r.get('sources_cited') or ''):
            continue
        niche = (r.get('niche') or '').strip()
        urls = links(r['sources_cited'])
        if stats is not None:
            stats['sourced'] = stats.get('sourced', 0) + 1
        if resolution is not None:
            real = [resolution.get(u) for u in urls]
            if not all(real):
                if stats is not None:
                    stats.setdefault('unresolved', collections.Counter())[(day, niche)] += 1
                continue
            sites = set()
            for u in real:
                sites |= domains(u)
        else:
            sites = domains(r['sources_cited'])
        rows.append((day, mode_of(r.get('flags')), sites, niche, len(urls)))
    return rows


def load(path, engine, resolution=None, stats=None):
    csv.field_size_limit(10 ** 9)
    with io.open(path, encoding='utf-8', errors='ignore', newline='') as fh:
        return parse_rows(csv.DictReader(fh), engine, resolution, stats)


def z_two(a, n, b, m):
    """Two-proportion z: share a/n (window) against b/m (baseline)."""
    if not n or not m:
        return 0.0
    p = (a + b) / (n + m)
    se = math.sqrt(p * (1 - p) * (1 / n + 1 / m))
    return (a / n - b / m) / se if se else 0.0


def check(rows, asof, window=7, baseline=28, zmin=3.0, min_answers=60, min_hits=15):
    """Returns (status, fires). fires: [(z, site, window_hits, window_n, base_hits, base_n)]."""
    w0 = asof - datetime.timedelta(days=window - 1)
    b0 = w0 - datetime.timedelta(days=baseline)
    win = [r for r in rows if w0 <= r[0] <= asof]
    base = [r for r in rows if b0 <= r[0] < w0]
    common = {r[3] for r in win} & {r[3] for r in base}  # same question sets on both sides, so a change of mix cannot fire
    win = [r for r in win if r[3] in common]
    base = [r for r in base if r[3] in common]
    if len(win) < min_answers or len(base) < min_answers:
        return 'TOO FEW ANSWERS (window %d, baseline %d, need %d)' % (len(win), len(base), min_answers), []
    wm, bm = {r[1] for r in win}, {r[1] for r in base}
    if len(wm) > 1 or wm != bm and not wm <= bm:
        return 'MODE CHANGE (window %s, baseline %s): no comparison' % (sorted(wm), sorted(bm)), []
    base = [r for r in base if r[1] in wm]  # baseline restricted to the window's own mode
    if len(base) < min_answers:
        return 'MODE CHANGE: baseline in the window mode has %d answers, need %d' % (len(base), min_answers), []
    wc, bc = collections.Counter(), collections.Counter()
    for r in win:
        wc.update(r[2])
    for r in base:
        bc.update(r[2])
    fires = []
    for site in set(wc) | set(bc):
        if wc[site] + bc[site] < min_hits:
            continue
        z = z_two(wc[site], len(win), bc[site], len(base))
        if abs(z) >= zmin:
            fires.append((z, site, wc[site], len(win), bc[site], len(base)))
    return 'OK (window %d answers, baseline %d, mode %s)' % (len(win), len(base), sorted(wm)[0]), sorted(fires)


def week_of(day):
    return day - datetime.timedelta(days=day.weekday())


def series(rows, engine, unresolved=None, panel=PANEL, sites=SERIES_SITES, since=None):
    """The weekly table on the fixed panel, Monday-start weeks (from the week of `since` on, when given). Returns (header, lines).
    A week with no answers is written with blank cells, so a gap in the record shows as a gap and never as zero."""
    wk = collections.defaultdict(list)
    for r in rows:
        if r[3] in panel and (since is None or r[0] >= since):
            wk[week_of(r[0])].append(r)
    left_out = collections.Counter()
    for (day, niche), n in (unresolved or {}).items():
        if niche in panel and (since is None or day >= since):
            left_out[week_of(day)] += n
    head = (['week_start_monday', 'engine', 'panel_question_sets', 'answers', 'answers_default_mode',
             'answers_agent_fast_mode', 'citations_per_answer'] + ['pct_answers_citing_' + s for s in sites]
            + ['days_with_answers', 'answers_left_out_dead_links'])
    lines = []
    if not wk and not left_out:
        return head, lines
    w, last = min(set(wk) | set(left_out)), max(set(wk) | set(left_out))
    while w <= last:
        rs = wk.get(w, [])
        n = len(rs)
        line = [w.isoformat(), engine, len(panel), n, sum(1 for r in rs if r[1] == 'default'),
                sum(1 for r in rs if r[1] == 'pplx:agent-fast')]
        if n:
            line.append('%.1f' % (sum(r[4] for r in rs) / n))
            line += ['%.1f' % (100.0 * sum(1 for r in rs if s in r[2]) / n) for s in sites]
        else:
            line += [''] * (1 + len(sites))
        line += [len({r[0] for r in rs}), left_out.get(w, 0)]
        lines.append(line)
        w += datetime.timedelta(days=7)
    return head, lines


def coverage_lines(engine, rows, stats):
    """What the instrument could and could not read, printed on every Gemini run and whenever a row failed."""
    out = []
    if stats.get('failed'):
        by = collections.defaultdict(list)
        for (day, flag), n in stats['failed'].items():
            by[flag].append((day, n))
        for flag, ds in sorted(by.items(), key=lambda kv: min(kv[1])):
            out.append('FAILED ROWS %s: %d rows %s to %s, flag "%s"' % (
                engine, sum(n for d, n in ds), min(d for d, n in ds), max(d for d, n in ds), flag))
    if stats.get('unresolved') is not None or engine == 'gemini':
        left = sum((stats.get('unresolved') or {}).values())
        ud = sorted(d for d, _ in (stats.get('unresolved') or {}))
        out.append('COVERAGE %s: %d of %d answers with links resolve every link (%s to %s); %d left out for dead links (%s to %s)' % (
            engine, len(rows), stats.get('sourced', 0), min((r[0] for r in rows), default='none'), max((r[0] for r in rows), default='none'),
            left, ud[0] if ud else 'none', ud[-1] if ud else 'none'))
    return out


def selftest():
    d0 = datetime.date(2026, 1, 1)
    rows = []
    for i in range(60):  # 15 answers a day; site a.com cited in 8 of 15, then in 2 of 15 from day 40
        hit = 8 if i < 40 else 2
        for k in range(15):
            rows.append((d0 + datetime.timedelta(days=i), 'default', {'a.com'} if k < hit else {'b.com'}, 'q%d' % (k % 3)))
    status, fires = check(rows, d0 + datetime.timedelta(days=39))
    assert status.startswith('OK') and not fires, (status, fires)
    status, fires = check(rows, d0 + datetime.timedelta(days=46))
    assert any(s == 'a.com' and z < -3 for z, s, *_ in fires), fires
    mixed = rows + [(d0 + datetime.timedelta(days=60 + i), 'pplx:agent-fast', {'b.com'}, 'q%d' % (k % 3)) for i in range(3) for k in range(15)]
    newmix = rows + [(d0 + datetime.timedelta(days=60 + i), 'default', {'c.com'}, 'brand_new_question') for i in range(7) for k in range(15)]
    status, fires = check(newmix, d0 + datetime.timedelta(days=66))
    assert not any(s == 'c.com' for z, s, *_ in fires), fires  # a question set with no baseline cannot fire
    status, fires = check(mixed, d0 + datetime.timedelta(days=62))
    assert status.startswith('MODE CHANGE') and not fires, (status, fires)
    assert check(rows[:30], d0 + datetime.timedelta(days=1))[0].startswith('TOO FEW')
    # Gemini redirect links: an answer is kept only when every link resolved; a dead link never reads as "cited nothing"
    rmap = {'https://r.example/1': 'https://www.alpha.com/x', 'https://r.example/2': 'https://news.beta.org/y'}
    fake = [
        {'engine': 'gemini', 'date_utc': '2026-10-20', 'niche': 'q1', 'flags': '', 'sources_cited': 'https://r.example/1; https://r.example/2'},
        {'engine': 'gemini', 'date_utc': '2026-10-20', 'niche': 'q1', 'flags': '', 'sources_cited': 'https://r.example/1; https://r.example/dead'},
        {'engine': 'gemini', 'date_utc': '2026-10-21', 'niche': 'q1', 'flags': 'FAILED: HTTP Error 402: Payment Required', 'sources_cited': ''},
        {'engine': 'perplexity', 'date_utc': '2026-10-20', 'niche': 'q1', 'flags': '', 'sources_cited': 'https://r.example/1'},
    ]
    st = {}
    got = parse_rows(fake, 'gemini', rmap, st)
    assert len(got) == 1 and got[0][2] == {'alpha.com', 'beta.org'} and got[0][4] == 2, got
    assert st['unresolved'] == {(datetime.date(2026, 10, 20), 'q1'): 1}, st
    assert st['sourced'] == 2 and sum(st['failed'].values()) == 1, st
    assert any('FAILED ROWS gemini: 1 rows' in s for s in coverage_lines('gemini', got, st))
    # weekly series: a week with no answers is blank, never zero; a partial week shows its days
    sr = [(datetime.date(2026, 10, 19), 'default', {'a.com'}, 'segment_hvac_atlanta', 4), (datetime.date(2026, 11, 2), 'default', {'b.com'}, 'segment_hvac_atlanta', 6)]
    head, lines = series(sr, 'gemini', {(datetime.date(2026, 10, 27), 'segment_hvac_atlanta'): 3}, sites=('a.com',))
    assert [l[0] for l in lines] == ['2026-10-19', '2026-10-26', '2026-11-02'], lines
    assert lines[0][6:8] == ['4.0', '100.0'] and lines[1][3] == 0 and lines[1][6] == '' and lines[1][-1] == 3, lines
    assert [l[0] for l in series(sr, 'gemini', None, sites=('a.com',), since=datetime.date(2026, 11, 1))[1]] == ['2026-11-02']
    print('selftest OK')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--engine', default='perplexity')
    ap.add_argument('--asof')
    ap.add_argument('--window', type=int, default=7)
    ap.add_argument('--baseline', type=int, default=28)
    ap.add_argument('--z', type=float, default=3.0)
    ap.add_argument('--backtest', action='store_true')
    ap.add_argument('--series', metavar='OUT.csv', help='write the weekly table on the fixed panel to this file')
    ap.add_argument('--since', metavar='YYYY-MM-DD', help='with --series: start at the week of this day (Perplexity: 2026-08-24, the first full week of the fixed panel)')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    resolution = load_resolution(RESOLUTION) if a.engine == 'gemini' else None
    stats = {}
    rows = load(CORPUS, a.engine, resolution, stats)
    if not rows:
        print('no rows for engine %s in %s' % (a.engine, os.path.relpath(CORPUS, ROOT)))
        return 1
    days = sorted({r[0] for r in rows})
    if a.series:
        head, lines = series(rows, a.engine, stats.get('unresolved'), since=datetime.date.fromisoformat(a.since) if a.since else None)
        with io.open(a.series, 'w', encoding='utf-8', newline='') as fh:
            w = csv.writer(fh)
            w.writerow(head)
            w.writerows(lines)
        print('SERIES %s: %d weeks written to %s' % (a.engine, len(lines), a.series))
        return 0
    if a.backtest:
        first = {}
        for day in days:
            status, fires = check(rows, day, a.window, a.baseline, a.z)
            for z, site, wh, wn, bh, bn in fires:
                first.setdefault((site, 'down' if z < 0 else 'up'), (day, z, wh, wn, bh, bn))
        print('BACKTEST %s, %s to %s: first day each site fires (window %d days against the %d before, |z| >= %.1f)' % (a.engine, days[0], days[-1], a.window, a.baseline, a.z))
        for line in coverage_lines(a.engine, rows, stats):
            print(line)
        for (site, way), (day, z, wh, wn, bh, bn) in sorted(first.items(), key=lambda kv: kv[1][0]):
            print('  %s  %-28s %-4s z %+.1f  window %d of %d (%.0f%%)  baseline %d of %d (%.0f%%)' % (day, site, way, z, wh, wn, 100.0 * wh / wn, bh, bn, 100.0 * bh / bn))
        return 0
    asof = datetime.date.fromisoformat(a.asof) if a.asof else days[-1]
    status, fires = check(rows, asof, a.window, a.baseline, a.z)
    print('SOURCE SHARE WATCH | engine %s | as of %s | %s' % (a.engine, asof, status))
    for line in coverage_lines(a.engine, rows, stats):
        print(line)
    for z, site, wh, wn, bh, bn in fires:
        print('  FIRE %-28s z %+.1f  window %d of %d (%.0f%%)  baseline %d of %d (%.0f%%)' % (site, z, wh, wn, 100.0 * wh / wn, bh, bn, 100.0 * bh / bn))
    if not fires and status.startswith('OK'):
        print('  no site moved by |z| >= %.1f' % a.z)
    return 0


if __name__ == '__main__':
    sys.exit(main())
