#!/usr/bin/env python3
"""1012D-W2 findings for Namebeam Edition 1. Text-mention rule from mention_counts.py (branch mention-rule-src).
Usage: python3 -I findings.py REPO OUTJSON [--start 2026-09-01] [--end 2026-10-12] [--min-answers 20]
Needs mention_counts.py in the same folder. Stdlib only. Engines: openai, anthropic, gemini, perplexity (google_serp excluded: read_corpus keeps kind==API only)."""
import sys, os, json, re, statistics, argparse, datetime as dt
from collections import defaultdict, Counter
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mention_counts as mc

ENG = mc.ENG
EXTRA_DROP = {'yelp', 'ad club', 'labor day', 'google business', 'google business profile'}  # named in the task; also in the L9 guard/deny sets


def first_pos(a, rx, srx):
    """Earliest match offset of a candidate in the normalised answer (single-word names: offset of the word in normalised text)."""
    best = None
    if rx:
        m = rx.search(a.nt)
        if m: best = m.start()
    if srx:
        m = srx.search(a.ct)
        if m:
            w = mc.norm_text(m.group(0))
            m2 = re.search(r'(?<![a-z0-9])' + re.escape(w) + r'(?![a-z0-9])', a.nt)
            p = m2.start() if m2 else 10**9
            best = p if best is None else min(best, p)
    return best


def pair_data(mk, ni, cell, g, quar):
    ok = [r for r in cell if r['status'] == 'OK']
    asked, cnt, guard = mc.build_pair(mk, ni, cell, g, quar)
    ans = defaultdict(list)  # (engine,date) -> [Ans] ; sorted by source file so the first run of a day is first
    for r in sorted(ok, key=lambda r: r['source_file']):
        ans[(r['engine'], r['date'])].append(mc.Ans(r['date'], r['answer']))
    named = {}   # (engine,date) -> {biz: first position in first answer that names it}
    for c, (disp, counts, variants) in cnt.items():
        if mc.stem(disp) in EXTRA_DROP or c in EXTRA_DROP:
            continue
        rx, srx = mc.make_matcher(set(variants) | {c})
        for (e, d), al in ans.items():
            pos = [p for p in (first_pos(a, rx, srx) for a in al if mc.mentions(a, (rx, srx))) if p is not None]
            if pos or any(mc.mentions(a, (rx, srx)) for a in al):
                named.setdefault((e, d), {})[c] = min(pos) if pos else 10**9
    first_run_top = {}
    for (e, d), al in ans.items():  # top-named = business with earliest position in the day's FIRST answer
        a = al[0]; best = None
        for c in named.get((e, d), {}):
            disp, counts, variants = cnt[c]
            rx, srx = mc.make_matcher(set(variants) | {c})
            if mc.mentions(a, (rx, srx)):
                p = first_pos(a, rx, srx); p = 10**9 if p is None else p
                if best is None or (p, c) < best: best = (p, c)
        if best: first_run_top[(e, d)] = best[1]
    return asked, cnt, ans, named, first_run_top


def main(repo, out, W0, W1, minans):
    be, g, quar = mc.load_gate(repo)
    rows = mc.read_corpus(repo, be, W0, W1)
    # all-status rows per (pair, engine, date) for coverage
    attempted = defaultdict(set); okd = defaultdict(set)
    for r in rows:
        attempted[(r['market'], r['niche'], r['engine'])].add(r['date'])
        if r['status'] == 'OK': okd[(r['market'], r['niche'], r['engine'])].add(r['date'])
    R = {'window': [W0, W1], 'engines': ENG, 'dropped_names': sorted(EXTRA_DROP), 'pairs': {}}
    P = {}
    for mk, ni in sorted({(r['market'], r['niche']) for r in rows}):
        if not mk.strip() or not ni.strip() or 'LOCATION NOT CAPTURED' in mk: continue
        cell = [r for r in rows if r['market'] == mk and r['niche'] == ni]
        if sum(1 for r in cell if r['status'] == 'OK') < minans: continue
        P[(mk, ni)] = pair_data(mk, ni, cell, g, quar)
    R['n_pairs'] = len(P)
    # 1 name rate
    nr = {e: [0, 0] for e in ENG}
    for (mk, ni), (asked, cnt, ans, named, top) in P.items():
        for (e, d) in ans:
            nr[e][1] += 1; nr[e][0] += bool(named.get((e, d)))
    nl = {e: [0, 0] for e in ENG}
    for (mk, ni), (asked, cnt, ans, named, top) in P.items():
        if mk.startswith('US nationwide'): continue
        for (e, d) in ans:
            nl[e][1] += 1; nl[e][0] += bool(named.get((e, d)))
    R['name_rate_excl_nationwide'] = {e: {'named': nl[e][0], 'answer_days': nl[e][1], 'pct': round(100 * nl[e][0] / nl[e][1], 1)} for e in ENG}
    R['name_rate_by_pair_zero_for_all_engines'] = sorted('%s | %s' % k for k, v in P.items() if not v[3])
    R['name_rate'] = {e: {'named': nr[e][0], 'answer_days': nr[e][1], 'pct': round(100 * nr[e][0] / nr[e][1], 1) if nr[e][1] else None} for e in ENG}
    # 2 agreement (top-named business same across engines on the same day)
    agree_pairs = {}; tot_a = tot_n = 0; by_ep = defaultdict(lambda: [0, 0])
    import itertools
    for key, (asked, cnt, ans, named, top) in P.items():
        a = n = 0
        dates = sorted({d for (e, d) in top})
        for d in dates:
            es = [e for e in ENG if (e, d) in top]
            for x, y in itertools.combinations(es, 2):
                n += 1; hit = top[(x, d)] == top[(y, d)]; a += hit
                k = '%s+%s' % (x, y); by_ep[k][1] += 1; by_ep[k][0] += hit
        if n: agree_pairs[key] = (a, n)
        tot_a += a; tot_n += n
    rates = [a / n for a, n in agree_pairs.values()]
    R['agreement'] = {'pairs_with_comparisons': len(agree_pairs), 'pooled_agree': tot_a, 'pooled_comparisons': tot_n,
                      'pooled_pct': round(100 * tot_a / tot_n, 1) if tot_n else None,
                      'median_pair_rate_pct': round(100 * statistics.median(rates), 1) if rates else None,
                      'pairs_zero_agreement': sum(1 for a, n in agree_pairs.values() if a == 0),
                      'pairs_over_25pct': sum(1 for a, n in agree_pairs.values() if a / n > .25),
                      'pairs_over_50pct': sum(1 for a, n in agree_pairs.values() if a / n > .5),
                      'deciles_pct': [round(100 * statistics.quantiles(rates, n=10)[i], 1) for i in range(9)] if len(rates) > 9 else None,
                      'by_engine_pair': {k: {'agree': v[0], 'comparisons': v[1], 'pct': round(100 * v[0] / v[1], 1)} for k, v in sorted(by_ep.items())},
                      'per_pair': {'%s | %s' % k: [a, n] for k, (a, n) in sorted(agree_pairs.items())}}
    # 3 persistence
    ps = {e: [0, 0] for e in ENG}
    for key, (asked, cnt, ans, named, top) in P.items():
        for e in ENG:
            days = sorted(d for (ee, d) in ans if ee == e)
            for d in days:
                d1 = (dt.date.fromisoformat(d) + dt.timedelta(days=1)).isoformat()
                if d1 not in days: continue
                s0, s1 = set(named.get((e, d), {})), set(named.get((e, d1), {}))
                ps[e][1] += len(s0); ps[e][0] += len(s0 & s1)
    R['persistence'] = {e: {'named_again_next_day': ps[e][0], 'business_days': ps[e][1], 'pct': round(100 * ps[e][0] / ps[e][1], 1) if ps[e][1] else None} for e in ENG}
    # 4 concentration
    conc = {}; conc_e = {e: [] for e in ENG}
    for key, (asked, cnt, ans, named, top) in P.items():
        m = Counter()
        me = {e: Counter() for e in ENG}
        for (e, d), bs in named.items():
            for b in bs: m[b] += 1; me[e][b] += 1
        t = sum(m.values())
        if t: conc[key] = (sum(v for _, v in m.most_common(3)), t, len(m))
        for e in ENG:
            te = sum(me[e].values())
            if te: conc_e[e].append(sum(v for _, v in me[e].most_common(3)) / te)
    sh = [a / b for a, b, _ in conc.values()]
    R['concentration'] = {'pairs': len(conc), 'median_top3_share_pct': round(100 * statistics.median(sh), 1), 'min_pct': round(100 * min(sh), 1), 'max_pct': round(100 * max(sh), 1),
                          'pooled_top3': sum(a for a, b, c in conc.values()), 'pooled_mentions': sum(b for a, b, c in conc.values()),
                          'median_by_engine_pct': {e: round(100 * statistics.median(v), 1) for e, v in conc_e.items() if v},
                          'median_businesses_per_pair': statistics.median(c for a, b, c in conc.values())}
    # 5 gemini coverage
    gk = [k for k in P]
    gat = sum(len(attempted[(mk, ni, 'gemini')]) for mk, ni in gk); gok = sum(len(okd[(mk, ni, 'gemini')]) for mk, ni in gk)
    st = Counter(r['status'] for r in rows if r['engine'] == 'gemini' and (r['market'], r['niche']) in P)
    alld = sum(len(set().union(*[attempted[(mk, ni, e)] for e in ENG])) for mk, ni in gk)
    pk = {e: sum(len(okd[(mk, ni, e)]) for mk, ni in gk) for e in ENG}
    gdates = sorted({r['date'] for r in rows if r['engine'] == 'gemini' and r['status'] == 'OK'})
    R['gemini_coverage'] = {'ok_answer_days': gok, 'pair_days_attempted_gemini': gat, 'pair_days_any_engine': alld,
                            'pct_of_attempted': round(100 * gok / gat, 1), 'pct_of_any_engine_pair_days': round(100 * gok / alld, 1),
                            'status_rows': dict(st), 'ok_days_by_engine': pk, 'first_ok_date': gdates[0] if gdates else None, 'last_ok_date': gdates[-1] if gdates else None,
                            'distinct_dates_with_gemini_ok': len(gdates)}
    # 6 stories: spread between engines on one business, engines with >=10 answer days counted
    cand = []
    for key, (asked, cnt, ans, named, top) in P.items():
        for c, (disp, counts, variants) in cnt.items():
            if mc.stem(disp) in EXTRA_DROP or c in EXTRA_DROP: continue
            rates = {e: (counts[e], len(asked[e])) for e in ENG if e != 'gemini' and len(asked[e]) >= 30}  # Gemini answered 2026-09-01..09-26 only: shown, not ranked
            if len(rates) < 3: continue
            hi = max(rates, key=lambda e: rates[e][0] / rates[e][1]); lo = min(rates, key=lambda e: rates[e][0] / rates[e][1])
            gap = rates[hi][0] / rates[hi][1] - rates[lo][0] / rates[lo][1]
            ds = sorted(d for (e, d), bs in named.items() if c in bs)
            cand.append((gap, key, mc.clean(disp), {e: [counts[e], len(asked[e])] for e in ENG}, hi, lo, ds[0], ds[-1]))
    cand.sort(key=lambda x: (-x[0], -max(v[0] for v in x[3].values())))
    seen, st6 = set(), []
    for x in cand:
        if x[1] in seen: continue
        seen.add(x[1]); st6.append({'market': x[1][0], 'niche': x[1][1], 'business': x[2], 'by_engine': x[3], 'gap_pct_points': round(100 * x[0], 1), 'highest': x[4], 'lowest': x[5], 'first_named': x[6], 'last_named': x[7]})
        if len(st6) == 5: break
    R['stories'] = st6
    R['pairs_list'] = ['%s | %s' % k for k in sorted(P)]
    json.dump(R, open(out, 'w'), indent=1)
    print(json.dumps({k: v for k, v in R.items() if k not in ('pairs_list',)}, indent=1)[:9000])

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('repo'); ap.add_argument('out')
    ap.add_argument('--start', default='2026-09-01'); ap.add_argument('--end', default='2026-10-12'); ap.add_argument('--min-answers', type=int, default=20)
    A = ap.parse_args(); main(A.repo, A.out, A.start, A.end, A.min_answers)
