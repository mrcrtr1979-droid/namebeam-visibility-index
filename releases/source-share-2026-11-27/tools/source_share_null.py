#!/usr/bin/env python3
"""source_share_null.py: how often does source_share_watch.py fire when nothing changed, and how much of what it fired is real?

The tripwire tests every site every day. One test at |z| >= 3 is wrong about 1 time in 370 when answers are independent;
a record of 100 days over 1,000 sites holds a hundred thousand tests, and the answers are not independent (the same prompt is
asked every day). The honest rate of false fires has to be measured, not assumed. This tool measures it.

METHOD (all of it on the file, no model calls; needs numpy, which the tripwire itself does not):
 1. A vectorised replica of check() is built and PROVEN equal to it: same first-fire events, same z, on both engines
    (--equal), and on seeded synthetic data (--selftest).
 2. PLACEBO RECORDS. The unit of exchange is the day: all answers of one day move together, so the dependence inside a day
    survives. Days are shuffled only among days of the same collector mode and the same design (same count of answers per question
    set), so the mode timeline and the question mix of every date are kept. A shuffled record has NO real change in time by
    construction. The replica is run on B shuffled records exactly as on the real one.
 3. FALSE FIRES. For every cut c, N_real(c) = (site, direction) pairs whose peak |z| reached c on the real record, N_null(c) = the
    mean over placebo records, and the false-discovery rate of the list at that cut is estimated as N_null(c) / N_real(c)
    (the empirical FDR with a permutation null). The family-wise p of one fire is the share of placebo records whose single
    largest |z| (any site, any day) reached it (Westfall and Young max-T). Fires are also split by how often the site is cited,
    because rare sites have heavier null tails than the normal approximation says.
 4. PERSISTENCE. A fire that is real should still be there afterwards. For every first fire with 14 more days on file, the
    share in the next 14 days is compared with the window share and the baseline share: rho = sum(follow - baseline) /
    sum(window - baseline), down moves sign-flipped. Pure noise regresses to the baseline (rho near 0); a lasting shift gives 1.
 5. POWER UNDER THE REAL NOISE. Using the real window and baseline counts of every site on every testable day, a step of d points
    is added to the window and the detector is asked whether it fires: the detection probability with the clustering that is
    really there, set beside the formula that assumes independent answers.

Usage:
  python3 -I tools/source_share_null.py --selftest
  python3 -I tools/source_share_null.py --engine perplexity --equal
  python3 -I tools/source_share_null.py --engine perplexity --perms 2000 --jobs 12
  python3 -I tools/source_share_null.py --reddit
Born 2026-11-27 (CLOUD-DIVE-1127B).
"""
import argparse, collections, concurrent.futures, datetime, math, os, random, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I does not add the script folder
import numpy as np
import source_share_watch as S

CUTS = (3.0, 3.5, 4.0, 4.5, 5.0, 6.0, 7.0)
BANDS = ((3.0, 4.0), (4.0, 5.0), (5.0, 99.0))
FOLLOW = 14
VARIANTS = {'built': (0, False), 'full': (24, False), 'clean': (24, True)}
VARIANT_TITLE = {'built': 'AS BUILT', 'full': 'FULL BASELINE (24+ of 28 days)', 'clean': 'CLEAN: LAST COLLECTOR MODE ONLY, FULL BASELINE'}


def build(rows):
    """Arrays: C[t, m, q, s] answers on calendar day t, mode m, question set q citing site s; N[t, m, q] answers."""
    days = sorted({r[0] for r in rows})
    t0 = days[0]
    T = (days[-1] - t0).days + 1
    modes = sorted({r[1] for r in rows})
    niches = sorted({r[3] for r in rows})
    sites = sorted({s for r in rows for s in r[2]})
    mi = {m: i for i, m in enumerate(modes)}
    qi = {q: i for i, q in enumerate(niches)}
    si = {s: i for i, s in enumerate(sites)}
    C = np.zeros((T, len(modes), len(niches), len(sites)), dtype=np.int16)
    N = np.zeros((T, len(modes), len(niches)), dtype=np.int16)
    for day, mode, ss, niche, _n in rows:
        t, m, q = (day - t0).days, mi[mode], qi[niche]
        N[t, m, q] += 1
        for s in ss:
            C[t, m, q, si[s]] += 1
    has = np.array([N[t].sum() > 0 for t in range(T)])
    return dict(C=C, N=N, t0=t0, T=T, modes=modes, niches=niches, sites=sites, has=has)


def windows(C, N, has, window=7, baseline=28, min_answers=60):
    """Every testable day, in the order and with the refusals of check(): yields (a, m, common, nW, nB, cw, cb) where cw, cb
    are the per-site counts in the window and in the baseline of the window's own mode, on the question sets present on both sides,
    and nbd is the number of days in that baseline that hold answers (28 when the baseline is complete)."""
    T, M, Q, Sn = C.shape
    PC = np.zeros((T + 1, M, Q, Sn), dtype=np.int32)
    np.cumsum(C, axis=0, out=PC[1:])
    PN = np.zeros((T + 1, M, Q), dtype=np.int32)
    np.cumsum(N, axis=0, out=PN[1:])
    for a in np.nonzero(has)[0]:
        w0 = max(a - window + 1, 0)
        b0 = max(w0 - baseline, 0)
        Nw = PN[a + 1] - PN[w0]
        Nb = PN[w0] - PN[b0]
        common = (Nw.sum(0) > 0) & (Nb.sum(0) > 0)
        nW_m = Nw[:, common].sum(1)
        nB_m = Nb[:, common].sum(1)
        if nW_m.sum() < min_answers or nB_m.sum() < min_answers:
            continue
        wm = np.nonzero(nW_m > 0)[0]
        if len(wm) != 1 or nB_m[wm[0]] <= 0:
            continue
        m = int(wm[0])
        nW, nB = float(nW_m[m]), float(nB_m[m])
        if nB < min_answers:
            continue
        cw = (PC[a + 1, m][common] - PC[w0, m][common]).sum(0).astype(np.float64)
        cb = (PC[w0, m][common] - PC[b0, m][common]).sum(0).astype(np.float64)
        nbd = int((N[b0:w0, m].sum(axis=1) > 0).sum())
        yield int(a), m, common, nW, nB, cw, cb, nbd


def ztwo(cw, nW, cb, nB, min_hits=15):
    tot = cw + cb
    p = tot / (nW + nB)
    se = np.sqrt(p * (1 - p) * (1 / nW + 1 / nB))
    with np.errstate(divide='ignore', invalid='ignore'):
        z = np.where(se > 0, (cw / nW - cb / nB) / se, 0.0)
    return np.where(tot >= min_hits, z, 0.0)


def scan(C, N, has, zmin=3.0, min_bdays=0, only_last_mode=False, want_events=False, want_z=False):
    """The replica of check() over the record. Returns peaks per site and direction, first-fire day per site and direction,
    the number of fires over all days, the largest |z| anywhere, and the z vector of the last testable day."""
    Sn = C.shape[3]
    peak_up = np.zeros(Sn)
    peak_dn = np.zeros(Sn)
    first_up = np.full(Sn, -1)
    first_dn = np.full(Sn, -1)
    events = {}
    zlog = []
    fire_days = test_days = 0
    maxabs = 0.0
    last_z = np.zeros(Sn)
    last_a = -1
    last_c = None
    last_mode = int(np.nonzero(N[np.nonzero(has)[0][-1]].sum(1))[0][0]) if only_last_mode else None
    for a, m, common, nW, nB, cw, cb, nbd in windows(C, N, has):
        if nbd < min_bdays or (last_mode is not None and m != last_mode):
            continue
        z = ztwo(cw, nW, cb, nB)
        test_days += 1
        if want_z:
            zlog.append((a, z))
        maxabs = max(maxabs, float(np.abs(z).max()))
        up, dn = z >= zmin, z <= -zmin
        fire_days += int(up.sum() + dn.sum())
        np.maximum(peak_up, z, out=peak_up)
        np.minimum(peak_dn, z, out=peak_dn)
        for mask, first, way in ((up, first_up, 'up'), (dn, first_dn, 'down')):
            new = mask & (first < 0)
            if new.any():
                first[new] = a
                if want_events:
                    for s in np.nonzero(new)[0]:
                        events[(int(s), way)] = (int(a), float(z[s]), int(cw[s]), int(nW), int(cb[s]), int(nB), m, common, nbd)
        last_z, last_a, last_c = z, a, (cw, cb, nW, nB)
    return dict(zlog=zlog, last_c=last_c, peak_up=peak_up, peak_dn=peak_dn, first_up=first_up, first_dn=first_dn, fire_days=fire_days,
                test_days=test_days, maxabs=maxabs, events=events, last_z=last_z, last_a=last_a)


def follow_up(C, N, events, T, follow=FOLLOW, min_n=100):
    """Per first fire with `follow` more days on file: (way, first z, window - baseline, follow - baseline), sign-flipped for down."""
    out = []
    for (s, way), (a, z, wh, wn, bh, bn, m, common, _nbd) in events.items():
        if a + follow > T - 1:
            continue
        sl = slice(a + 1, a + follow + 1)
        n = float(N[sl, m][:, common].sum())
        if n < min_n:
            continue
        f = float(C[sl, m][:, common][..., s].sum()) / n
        sign = 1.0 if way == 'up' else -1.0
        out.append((way, z, sign * (wh / wn - bh / bn), sign * (f - bh / bn)))
    return out


def band_sums(fu):
    """[(sum d, sum f, count)] per z band at first fire, then all."""
    res = []
    for lo, hi in BANDS + ((3.0, 99.0),):
        sel = [x for x in fu if lo <= abs(x[1]) < hi]
        res.append((sum(x[2] for x in sel), sum(x[3] for x in sel), len(sel)))
    return res


def distinct(events):
    """Sites that fire together are cited together in the same answers and are one change, not several: events with the same
    day, direction and counts are counted once."""
    return len({(e[0], way, e[2], e[4]) for (s, way), e in events.items()})


def strata(D, coarse=False):
    """Blocks of exchangeable days: same mode and the same count of answers in every question set (coarse: same mode only, for a
    record whose days differ in which question sets answered, such as Gemini where a dead link drops an answer)."""
    N, has = D['N'], D['has']
    groups = collections.defaultdict(list)
    for t in np.nonzero(has)[0]:
        ms = np.nonzero(N[t].sum(1) > 0)[0]
        assert len(ms) == 1, 'a day with two collector modes cannot be shuffled as one block'
        groups[(int(ms[0]), () if coarse else tuple(int(x) for x in N[t, ms[0]]))].append(int(t))
    return list(groups.values())


def placebo_index(D, groups, rng):
    src = np.arange(D['T'])
    for g in groups:
        if len(g) > 1:
            src[g] = np.array(g)[rng.permutation(len(g))]
    return src


def load_rows(engine):
    stats = {}
    resolution = S.load_resolution(S.RESOLUTION) if engine == 'gemini' else None
    return S.load(S.CORPUS, engine, resolution, stats), stats


def run_perms(args):
    engine, n, seed, loose = args
    rows, _ = load_rows(engine)
    D = build(rows)
    groups = strata(D, coarse=(engine != 'perplexity' or loose))
    rng = np.random.default_rng(seed)
    res = {v: dict(pu=[], pd=[], maxabs=[], n_first=[], fire_days=[], test_days=[], last_z=[], bands=[], n_distinct=[], last_cw=[], last_cb=[], last_n=[]) for v in VARIANTS}
    for _ in range(n):
        src = placebo_index(D, groups, rng)
        Cp, Np = D['C'][src], D['N'][src]
        for v, (mb, lm) in VARIANTS.items():
            r = scan(Cp, Np, D['has'], min_bdays=mb, only_last_mode=lm, want_events=True)
            x = res[v]
            x['pu'].append(r['peak_up']); x['pd'].append(r['peak_dn'])
            x['maxabs'].append(r['maxabs']); x['test_days'].append(r['test_days']); x['fire_days'].append(r['fire_days'])
            x['n_first'].append(int((r['first_up'] >= 0).sum() + (r['first_dn'] >= 0).sum()))
            x['last_z'].append(r['last_z'])
            x['bands'].append(band_sums(follow_up(Cp, Np, r['events'], D['T'])))
            x['n_distinct'].append(distinct(r['events']))
            x['last_cw'].append(r['last_c'][0]); x['last_cb'].append(r['last_c'][1]); x['last_n'].append([r['last_c'][2], r['last_c'][3]])
    return res


def equal_check(engine):
    """The replica against the tool: every first-fire event, same site, direction, day, z and counts."""
    rows, _ = load_rows(engine)
    days = sorted({r[0] for r in rows})
    ref = {}
    for day in days:
        status, fires = S.check(rows, day)
        for z, site, wh, wn, bh, bn in fires:
            ref.setdefault((site, 'down' if z < 0 else 'up'), (day, z, wh, wn, bh, bn))
    D = build(rows)
    got = scan(D['C'], D['N'], D['has'], want_events=True)['events']
    mine = {(D['sites'][s], way): (D['t0'] + datetime.timedelta(days=v[0]),) + v[1:6] for (s, way), v in got.items()}
    ok = set(mine) == set(ref)
    worst = 0.0
    for k in ref:
        if k in mine:
            a, b = ref[k], mine[k]
            ok = ok and a[0] == b[0] and a[2:] == b[2:]
            worst = max(worst, abs(a[1] - b[1]))
    print('EQUAL %s: tool %d events, replica %d events, same events and counts %s, largest |dz| %.2e' % (engine, len(ref), len(mine), ok and worst < 1e-9, worst))
    return ok and worst < 1e-9


def selftest():
    rng = random.Random(7)
    d0 = datetime.date(2026, 1, 1)
    rows = []
    for i in range(70):  # two modes (the switch on day 45), 6 question sets, a planted step on site s3 from day 55
        mode = 'default' if i < 45 else 'pplx:agent-fast'
        for q in range(6):
            for _ in range(3):
                sites = {'s%d' % k for k in range(8) if rng.random() < 0.15 + 0.03 * k}
                if i >= 55 and rng.random() < 0.55:
                    sites.add('s3')
                if rng.random() < 0.9:
                    rows.append((d0 + datetime.timedelta(days=i), mode, sites, 'q%d' % q, len(sites)))
    ref = {}
    for day in sorted({r[0] for r in rows}):
        for z, site, wh, wn, bh, bn in S.check(rows, day)[1]:
            ref.setdefault((site, 'down' if z < 0 else 'up'), (day, z, wh, wn, bh, bn))
    D = build(rows)
    r = scan(D['C'], D['N'], D['has'], want_events=True)
    mine = {(D['sites'][s], way): (D['t0'] + datetime.timedelta(days=v[0]),) + v[1:6] for (s, way), v in r['events'].items()}
    assert set(mine) == set(ref) and ref, (sorted(ref), sorted(mine))
    for k in ref:
        assert ref[k][0] == mine[k][0] and ref[k][2:] == mine[k][2:] and abs(ref[k][1] - mine[k][1]) < 1e-9, (k, ref[k], mine[k])
    assert ('s3', 'up') in ref, 'the planted step must fire'
    groups = strata(D)  # a shuffled record keeps its mode timeline and the per-day design
    src = placebo_index(D, groups, np.random.default_rng(1))
    assert np.array_equal(D['N'][src].sum(axis=(1, 2)) > 0, D['has'])
    assert np.array_equal(D['N'][src].sum(axis=2), D['N'].sum(axis=2))
    fu = follow_up(D['C'], D['N'], r['events'], D['T'], follow=10, min_n=30)
    assert all(x[0] in ('up', 'down') for x in fu)
    z1 = ztwo(np.array([30.0]), 100.0, np.array([10.0]), 100.0)
    assert abs(z1[0] - S.z_two(30, 100, 10, 100)) < 1e-12
    print('selftest OK (%d events, replica equals tool, planted step found, placebo keeps modes and design)' % len(ref))


def pct(x, q):
    x = np.asarray(x)
    return float(np.percentile(x, q)) if x.size else float('nan')


def meanf(x):
    x = np.asarray(x)
    return float(x.mean()) if x.size else float('nan')


def report(a):
    rows, stats = load_rows(a.engine)
    D = build(rows)
    sites = D['sites']
    hits = D['C'].sum(axis=(0, 1, 2))
    day = lambda t: (D['t0'] + datetime.timedelta(days=int(t))).isoformat()
    B = a.perms
    per = max(1, math.ceil(B / a.jobs))
    jobs = [(a.engine, per, a.seed + 1000 * k, a.strata == 'mode') for k in range(math.ceil(B / per))]
    with concurrent.futures.ProcessPoolExecutor(max_workers=a.jobs) as ex:
        parts = list(ex.map(run_perms, jobs))
    first_t, last_t = np.nonzero(D['has'])[0][0], np.nonzero(D['has'])[0][-1]
    out = {}
    for v, (mb, lm) in VARIANTS.items():
        real = scan(D['C'], D['N'], D['has'], min_bdays=mb, only_last_mode=lm, want_events=True)
        P = {k: np.concatenate([p[v][k] for p in parts]) for k in parts[0][v]}
        out[v] = (real, P)
    B = len(out['built'][1]['maxabs'])
    print('CALIBRATION | engine %s | %d placebo records (days shuffled within %s) | record %s to %s | %d sites' % (a.engine, B, 'collector mode only (loose)' if (a.strata == 'mode' or a.engine != 'perplexity') else 'collector mode and daily design', day(first_t), day(last_t), len(sites)))
    print('PLACEBO KEEPS: the mode timeline, the number of answers each day, the question-set mix of each day, the dependence inside a day. PLACEBO REMOVES: every real change over time.')
    print('THREE READINGS OF THE DETECTOR: "as built" = the tripwire as filed (tests from the first day a 60-answer baseline exists, however short); "full baseline" = only days whose baseline holds 24 or more of its 28 days; "clean" = full baseline and only the collector mode the detector reads today. Shuffle: %s.' % ('days of the same mode only (loose)' if (a.strata == 'mode' or a.engine != 'perplexity') else 'days of the same mode and the same daily design'))
    cls = np.digitize(np.concatenate([hits, hits]), (40, 150))
    for v in VARIANTS:
        real, P = out[v]
        title = VARIANT_TITLE[v]
        print('\n' + '=' * 100 + '\n' + title + ': %d testable days on the real record' % real['test_days'])
        n_real = int((real['first_up'] >= 0).sum() + (real['first_dn'] >= 0).sum())
        nf = P['n_first']
        print('1. FIRST-FIRE EVENTS (site and direction, first day |z| >= 3): real %d | placebo mean %.1f, 2.5%% to 97.5%% range %.0f to %.0f, largest %d | real exceeds %.1f%% of placebo records' % (
            n_real, nf.mean(), pct(nf, 2.5), pct(nf, 97.5), nf.max(), 100.0 * (nf < n_real).mean()))
        print('   fires per testable day: real %.2f, placebo %.2f' % (real['fire_days'] / max(1, real['test_days']), (P['fire_days'] / np.maximum(1, P['test_days'])).mean()))
        nd = P['n_distinct']
        print('   DISTINCT CHANGES (sites that fire together on the same counts are one change): real %d | placebo mean %.1f, 2.5%% to 97.5%% range %.0f to %.0f' % (distinct(real['events']), nd.mean(), pct(nd, 2.5), pct(nd, 97.5)))
        # events by baseline length (as built only)
        if v == 'built':
            short = sum(1 for e in real['events'].values() if e[8] < 24)
            print('   of the %d real events, %d fired on a baseline of fewer than 24 data days; %d on a fuller baseline' % (len(real['events']), short, len(real['events']) - short))
        pk_real = np.concatenate([real['peak_up'], -real['peak_dn']])
        pk_null = np.concatenate([P['pu'], -P['pd']], axis=1)
        print('2. FALSE-DISCOVERY RATE OF THE LIST AT EACH CUT on the peak |z| (FDR = placebo mean count / real count)')
        print('   cut  N_real  N_null  FDR    placebo N_null 95%% range   FDR by how often the site is cited: rare (<40) / middle / common (150+)  (N_real each)')
        for c in CUTS:
            nr = int((pk_real >= c).sum())
            nn_b = (pk_null >= c).sum(axis=1)
            nn = nn_b.mean()
            by = []
            for k in range(3):
                nrk = int(((pk_real >= c) & (cls == k)).sum())
                nnk = ((pk_null >= c) & (cls == k)).sum(axis=1).mean()
                by.append('%s (%d)' % ('%.0f%%' % min(100.0, 100.0 * nnk / nrk) if nrk else 'n/a', nrk))
            print('   %.1f  %6d  %6.1f  %4s   %4.0f to %-4.0f             %s' % (c, nr, nn, '%.0f%%' % min(100.0, 100.0 * nn / nr) if nr else 'n/a', pct(nn_b, 2.5), pct(nn_b, 97.5), ' / '.join(by)))
        thr = sorted(set(pk_real[pk_real >= 3.0]))
        fdr_at = {t: min(1.0, (pk_null >= t).sum(axis=1).mean() / max(1, (pk_real >= t).sum())) for t in thr}
        qval = {t: min(fdr_at[u] for u in thr if u <= t) for t in thr}
        ev_rows = []
        for (s, way), e in real['events'].items():
            pk = real['peak_up'][s] if way == 'up' else -real['peak_dn'][s]
            col = P['pu'][:, s] if way == 'up' else -P['pd'][:, s]
            ev_rows.append((e[0], sites[s], way, e[1], pk, int(hits[s]), e[8], (1 + (col >= pk).sum()) / (B + 1), (1 + (P['maxabs'] >= pk).sum()) / (B + 1), qval[pk], e[2], e[3], e[4], e[5]))
        real_events = ev_rows
        if v == 'built':
            print('3. EVERY FIRST FIRE (as built), with its calibrated odds of being noise')
            print('   day         site                         way   z first  z peak  cites  base-days  P(noise) this site  any site (max-T)  q: FDR if listed at its peak')
            for r in sorted(ev_rows):
                print('   %s  %-28s %-5s %+6.1f  %+6.1f  %5d  %5d      %.4f              %.4f          %4.0f%%' % (day(r[0]), r[1], r[2], r[3], r[4] if r[2] == 'up' else -r[4], r[5], r[6], r[7], r[8], 100 * r[9]))
        for lvl in (0.05, 0.10, 0.25):
            print('   events with q <= %.2f: %d of %d' % (lvl, sum(1 for r in ev_rows if r[9] <= lvl), len(ev_rows)))
        print('   events with max-T p <= 0.05 (would not appear in 95%% of placebo records at all): %d of %d' % (sum(1 for r in ev_rows if r[8] <= 0.05), len(ev_rows)))
        # newest day list
        lz = real['last_z']
        fired = np.nonzero(np.abs(lz) >= 3.0)[0]
        nl = (np.abs(P['last_z']) >= 3.0).sum(axis=1)
        mx = np.abs(P['last_z']).max(axis=1)
        print('4. THE NEWEST-DAY LIST (as of %s): %d fires on the real record; on the same day placebo records fire on %.2f sites on average (95th percentile %.0f), so about %.0f%% of a list this size is expected to be noise' % (
            day(real['last_a']), len(fired), nl.mean(), pct(nl, 95), 100.0 * min(1.0, nl.mean() / max(1, len(fired)))))
        for s in sorted(fired, key=lambda i: -abs(lz[i])):
            p_site = (1 + (np.abs(P['last_z'][:, s]) >= abs(lz[s])).sum()) / (B + 1)
            p_fw = (1 + (mx >= abs(lz[s])).sum()) / (B + 1)
            print('   %-28s z %+5.1f  cites on file %5d  P(noise) this site %.4f   any of the %d sites %.4f' % (sites[s], lz[s], hits[s], p_site, len(sites), p_fw))
        common = hits >= 150
        var_z = P['last_z'][:, common].var(axis=0)
        print('5. DESIGN EFFECT (variance of z on placebo records, 1.0 = the normal approximation is exact), %d sites cited 150+ times: median %.2f | 10th to 90th percentile %.2f to %.2f | above 1.2: %.0f%% of sites | below 0.8: %.0f%%' % (
            common.sum(), np.median(var_z), pct(var_z, 10), pct(var_z, 90), 100.0 * (var_z > 1.2).mean(), 100.0 * (var_z < 0.8).mean()))
        # persistence
        print('6. PERSISTENCE over the next %d days: rho = sum(follow - baseline) / sum(window - baseline), down moves sign-flipped. Reading: a long baseline and pure noise give about 0.2 to 0.3 (checked on independent answers); a shift that stays gives 1 or more, because the window holds only part of it on the first day it fires' % FOLLOW)
        fu_all = follow_up(D['C'], D['N'], real['events'], D['T'])
        rng = np.random.default_rng(a.seed)
        for k, (lo, hi) in enumerate(BANDS + ((3.0, 99.0),)):
            sel = [x for x in fu_all if lo <= abs(x[1]) < hi]
            if not sel:
                continue
            d = np.array([x[2] for x in sel]); f = np.array([x[3] for x in sel])
            rho = f.sum() / d.sum() if d.sum() else float('nan')
            boots = []
            for _ in range(4000):
                ix = rng.integers(0, len(sel), len(sel))
                boots.append(f[ix].sum() / d[ix].sum() if d[ix].sum() else np.nan)
            pl = np.array([(q[k][1] / q[k][0]) if q[k][0] and q[k][2] else np.nan for q in P['bands']])
            pl = pl[~np.isnan(pl)]
            label = 'z first fire %.0f to %.0f' % (lo, hi) if hi < 90 else 'all first fires'
            if lo == 5.0:
                label = 'z first fire 5 or more'
            sgn = np.mean(f > 0)
            pm = np.array([(q[k][1] / q[k][2]) if q[k][2] else np.nan for q in P['bands']])
            pm = pm[~np.isnan(pm)]
            print('   %-24s %3d events | rho real %.2f (95%% interval %.2f to %.2f) | placebo %.2f (95%% range %.2f to %.2f) | real minus placebo %+.2f | next-14-day shift in points: real %+.1f, placebo %+.1f (95%% range %+.1f to %+.1f) | same side of the baseline: %.0f%%' % (
                label, len(sel), rho, np.nanpercentile(boots, 2.5), np.nanpercentile(boots, 97.5), meanf(pl), pct(pl, 2.5), pct(pl, 97.5), rho - meanf(pl), 100 * f.mean(), 100 * meanf(pm), 100 * pct(pm, 2.5), 100 * pct(pm, 97.5), 100 * sgn))
        # power: placebo records are stationary by construction, so the only noise left is sampling and clustering
        cw_b, cb_b, n_b = P['last_cw'], P['last_cb'], P['last_n']
        nW0, nB0 = float(n_b[0][0]), float(n_b[0][1])
        lw, lb = real['last_c'][0], real['last_c'][1]
        pbar = (lw + lb) / (nW0 + nB0)
        steps = (0.02, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20)
        edges = (0.01, 0.03, 0.06, 0.12, 0.25, 1.01)
        print('7. DETECTION PROBABILITY on the last testable day, window %.0f answers, baseline %.0f: a site whose true share steps up by d points, placebo noise (sampling and clustering only), z >= 3. Sites grouped by their pooled share on the real record.' % (nW0, nB0))
        print('   pooled share     ' + ' '.join('%6s' % ('+%d pts' % round(100 * x)) for x in steps) + '    step for 80%% detection: real noise | if answers were independent | false fire rate at +0')
        for lo, hi in zip(edges[:-1], edges[1:]):
            ix = np.nonzero((pbar >= lo) & (pbar < hi))[0]
            if len(ix) == 0:
                continue
            cwk, cbk = cw_b[:, ix], cb_b[:, ix]

            def det(g):
                z = ztwo(np.minimum(nW0, cwk + g * nW0), nW0, cbk, nB0)
                return float((z >= 3.0).mean())
            probs = [det(g) for g in steps]
            real_mde = next((g for g in np.arange(0.01, 0.60, 0.01) if det(g) >= 0.8), None)
            mid = float(np.median(pbar[ix]))
            se0 = math.sqrt(mid * (1 - mid) * (1 / nW0 + 1 / nB0))
            ind = None
            for g in np.arange(0.005, 0.60, 0.005):
                se1 = math.sqrt((mid + g) * (1 - mid - g) / nW0 + mid * (1 - mid) / nB0) if mid + g < 1 else 1e-9
                if 0.5 * (1 + math.erf(((g - 3 * se0) / se1) / math.sqrt(2))) >= 0.8:
                    ind = g
                    break
            print('   %4.0f%% to %3.0f%% (%3d sites)' % (100 * lo, 100 * min(hi, 1.0), len(ix)) + ' '.join('%6s' % ('%.0f%%' % (100 * p_)) for p_ in probs)
                  + '    %s | %s | %.2f%% (independent answers: 0.13%%)' % ('%.0f pts' % (100 * real_mde) if real_mde else 'over 60 pts', '%.1f pts' % (100 * ind) if ind else 'n/a', 100 * det(0.0)))
    return 0


def inject(C, N, has, s, t0, way, delta, m, rng):
    """Add a sustained change to site s from day t0 on, in collector mode m: up turns non-citing answers into citing ones, down the reverse,
    so that the site's share moves by about `delta` (a fraction) from its share before t0."""
    days = [t for t in np.nonzero(has)[0] if N[t, m].sum() > 0]
    pre = [t for t in days if t < t0]
    p0 = C[pre, m, :, s].sum() / max(1.0, N[pre, m].sum())
    for t in days:
        if t < t0:
            continue
        col = C[t, m, :, s]
        if way == 'up':
            flip = (col == 0) & (N[t, m] > 0) & (rng.random(col.shape) < delta / max(1e-9, 1 - p0))
            col[flip] = 1
        else:
            flip = (col > 0) & (rng.random(col.shape) < delta / max(1e-9, p0))
            col[flip] = 0
    return p0


def validate(a):
    """Known-truth test of the whole pipeline: plant sustained changes in placebo records, run the detector, and compare (1) how many it
    finds and how fast, (2) the false-discovery rate the placebo estimate predicts with the false-discovery rate that really happened."""
    rows, _ = load_rows(a.engine)
    D = build(rows)
    groups = strata(D, coarse=(a.engine != 'perplexity' or a.strata == 'mode'))
    rng = np.random.default_rng(a.seed + 77)
    mb, lm = VARIANTS['clean']
    has = D['has']
    last_t = np.nonzero(has)[0][-1]
    m = int(np.nonzero(D['N'][last_t].sum(1))[0][0])
    days_m = [t for t in np.nonzero(has)[0] if D['N'][t, m].sum() > 0]
    # first day with a full baseline in this mode
    first_full = None
    for a_, m_, c_, nW, nB, cw, cb, nbd in windows(D['C'], D['N'], has):
        if m_ == m and nbd >= mb:
            first_full = a_
            break
    t0_lo, t0_hi = first_full - 3, last_t - 14
    cand_t0 = [t for t in days_m if t0_lo <= t <= t0_hi]
    # null counts of the clean detector from plain placebo records
    B0 = a.perms or 400
    null_peaks = []
    for _ in range(B0):
        src = placebo_index(D, groups, rng)
        r = scan(D['C'][src], D['N'][src], has, min_bdays=mb, only_last_mode=lm)
        null_peaks.append(np.concatenate([r['peak_up'], -r['peak_dn']]))
    null_peaks = np.array(null_peaks)
    cuts = (3.0, 4.0, 5.0)
    n_null = {c: (null_peaks >= c).sum(axis=1).mean() for c in cuts}
    # planted shifts
    share_last = D['C'][days_m][:, m].sum(axis=(0, 1)) / max(1.0, D['N'][days_m][:, m].sum())
    cand_sites = np.nonzero((share_last >= 0.02) & (share_last <= 0.35))[0]
    sizes = (0.06, 0.10, 0.15, 0.25)
    NS, K = a.sims, 16
    tot = {d: [0, 0] for d in sizes}       # detected within 14 days, planted
    delays = {d: [] for d in sizes}
    R = {c: 0 for c in cuts}
    V = {c: 0 for c in cuts}
    est = {c: 0.0 for c in cuts}
    for _ in range(NS):
        src = placebo_index(D, groups, rng)
        Cp, Np = D['C'][src].copy(), D['N'][src]
        planted = {}
        for s_ in rng.choice(cand_sites, K, replace=False):
            way = 'up' if rng.random() < 0.7 else 'down'
            d_ = float(rng.choice(sizes))
            t0 = int(rng.choice(cand_t0))
            p0 = inject(Cp, Np, has, int(s_), t0, way, d_, m, rng)
            if way == 'down' and d_ > 0.8 * p0:
                continue
            planted[(int(s_), way)] = (t0, d_)
        r = scan(Cp, Np, has, min_bdays=mb, only_last_mode=lm, want_events=True, want_z=True)
        for (s_, way), (t0, d_) in planted.items():
            sgn = 1.0 if way == 'up' else -1.0
            hit = [x for (aa, z) in r['zlog'] for x in [aa] if t0 <= aa <= t0 + 14 and sgn * z[s_] >= 3.0]
            tot[d_][1] += 1
            if hit:
                tot[d_][0] += 1
                delays[d_].append(min(hit) - t0)
        pk = {}
        for (s_, way), e in r['events'].items():
            pk[(s_, way)] = r['peak_up'][s_] if way == 'up' else -r['peak_dn'][s_]
        for c in cuts:
            ev = [k for k, v in pk.items() if v >= c]
            R[c] += len(ev)
            V[c] += sum(1 for k in ev if not (k in planted and r['events'][k][0] >= planted[k][0]))
            est[c] += n_null[c]
    print('KNOWN-TRUTH TEST | engine %s | %d records, each a day-shuffled placebo with %d planted sustained changes (70%% up, 30%% down; sizes %s points) | detector: CLEAN reading | null counts from %d plain placebo records' % (
        a.engine, NS, K, ', '.join('%d' % round(100 * x) for x in sizes), B0))
    print('1. RECALL: a planted change counts as found if the detector reaches |z| >= 3 in the right direction within 14 days of its start')
    for d_ in sizes:
        dd = delays[d_]
        print('   +/-%2d points: found %4d of %4d (%.0f%%) | median days to first fire %s' % (round(100 * d_), tot[d_][0], tot[d_][1], 100.0 * tot[d_][0] / max(1, tot[d_][1]), ('%.0f' % np.median(dd)) if dd else 'n/a'))
    print('2. THE FALSE-DISCOVERY ESTIMATE AGAINST THE TRUTH (sums over all %d records; a false event is any event that is not a planted change at or after its start)' % NS)
    print('   cut  events  false  false-discovery rate that happened | rate the placebo estimate predicts (N_null / events)')
    for c in cuts:
        print('   %.1f  %6d  %5d  %5.1f%%                              | %5.1f%%' % (c, R[c], V[c], 100.0 * V[c] / max(1, R[c]), 100.0 * est[c] / max(1, R[c])))
    return 0


def reddit(a):
    """The Reddit fall, tested at the unit that is exchangeable (the day): one changepoint, scan-corrected, with an interval."""
    rows, _ = load_rows('perplexity')
    panel = set(S.PANEL)
    by = collections.defaultdict(lambda: [0, 0])
    for day, mode, ss, niche, _n in rows:
        if mode == 'default' and niche in panel and datetime.date(2026, 8, 24) <= day <= datetime.date(2026, 9, 26):
            by[day][1] += 1
            by[day][0] += 1 if 'reddit.com' in ss else 0
    days = sorted(by)
    c = np.array([by[d][0] for d in days], dtype=float)
    n = np.array([by[d][1] for d in days], dtype=float)

    def ll(k, c, n):
        def seg(cc, nn):
            p = cc.sum() / nn.sum()
            return (cc.sum() * math.log(p) if p > 0 else 0.0) + ((nn.sum() - cc.sum()) * math.log(1 - p) if p < 1 else 0.0)
        return seg(c[:k], n[:k]) + seg(c[k:], n[k:])

    def best(c, n, lo=5):
        ks = list(range(lo, len(c) - lo + 1))
        v = [ll(k, c, n) for k in ks]
        i = int(np.argmax(v))
        p0 = c.sum() / n.sum()
        null = c.sum() * math.log(p0) + (n.sum() - c.sum()) * math.log(1 - p0)
        return ks[i], 2 * (v[i] - null), ks, v
    k, lr, ks, v = best(c, n)
    rng = np.random.default_rng(a.seed)
    B = 20000
    ge = 0
    for _ in range(B):
        pi = rng.permutation(len(c))
        if best(c[pi], n[pi])[1] >= lr:
            ge += 1
    p_scan = (1 + ge) / (B + 1)
    keep = [kk for kk, vv in zip(ks, v) if 2 * (max(v) - vv) <= 3.84]
    pre, post = (c[:k].sum(), n[:k].sum()), (c[k:].sum(), n[k:].sum())
    # day-block bootstrap of the two shares
    boots = []
    for _ in range(4000):
        i1 = rng.integers(0, k, k); i2 = rng.integers(k, len(c), len(c) - k)
        boots.append(c[i2].sum() / n[i2].sum() - c[i1].sum() / n[i1].sum())
    print('REDDIT ON PERPLEXITY, default mode, the 15-set panel, %s to %s (%d days, %d answers)' % (days[0], days[-1], len(days), int(n.sum())))
    print('  best single changepoint: after %s (%d days before, %d after)' % (days[k - 1], k, len(c) - k))
    print('  share before %d of %d (%.1f%%), after %d of %d (%.1f%%); difference %.1f points, day-block bootstrap 95%% interval %.1f to %.1f' % (
        pre[0], pre[1], 100 * pre[0] / pre[1], post[0], post[1], 100 * post[0] / post[1], 100 * (post[0] / post[1] - pre[0] / pre[1]), 100 * np.percentile(boots, 2.5), 100 * np.percentile(boots, 97.5)))
    print('  likelihood-ratio statistic %.1f; scan-corrected permutation p over every split point and %d day shuffles: %.5f' % (lr, B, p_scan))
    print('  changepoint location, likelihood interval (2 log-likelihood units, about 95%%): after %s to after %s' % (days[min(keep) - 1], days[max(keep) - 1]))
    print('  the earlier "z = 6.55" used a split chosen after looking, on answers treated as independent; the scan p above does not.')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--engine', default='perplexity')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--equal', action='store_true')
    ap.add_argument('--reddit', action='store_true')
    ap.add_argument('--validate', action='store_true', help='known-truth test: plant changes in placebo records and compare found, missed and false')
    ap.add_argument('--sims', type=int, default=300)
    ap.add_argument('--perms', type=int, default=0)
    ap.add_argument('--strata', default='design', choices=('design', 'mode'), help='shuffle days within mode and daily design (default) or within mode only (loose)')
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--seed', type=int, default=20261127)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.equal:
        return 0 if equal_check(a.engine) else 1
    if a.reddit:
        return reddit(a)
    if a.validate:
        return validate(a)
    if a.perms:
        return report(a)
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
