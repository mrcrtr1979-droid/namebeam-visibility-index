#!/usr/bin/env python3
"""1013A-L6 per-niche findings, Edition 1 panel (38 cells). Usage: python3 -I niche_findings.py REPO OUT.json [--end 2026-10-12]
Names counted from saved answer TEXT via mention_counts text-mention rule (M602). Each engine keeps its own denominator. Stdlib only."""
import sys, os, json, argparse
from collections import defaultdict, Counter
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SRC)
import mention_counts as mc
import findings as fd
ENG = ['perplexity', 'openai', 'anthropic', 'gemini']
LAB = {'openai': 'OpenAI', 'anthropic': 'Anthropic', 'perplexity': 'Perplexity', 'gemini': 'Gemini'}

def pct(a, b): return round(100.0 * a / b, 1) if b else None

def main(repo, out, W0, W1):
    be, g, quar = mc.load_gate(repo)
    rows = mc.read_corpus(repo, be, W0, W1)
    panel = {(r['niche'], r['market']) for r in rows if r['date'] <= be.PANEL_CUTOFF and r['market'].casefold() != 'us nationwide'
             and r['market'].strip() and r['niche'].strip()}
    byn = defaultdict(list)
    for ni, mk in sorted(panel): byn[ni].append(mk)
    res = {'window_start': None, 'window_end': W1, 'latest_landed_day': max(r['date'] for r in rows), 'panel_cells': len(panel), 'niches': {}}
    first = min(r['date'] for r in rows if (r['niche'], r['market']) in panel)
    res['window_start'] = first
    for ni in sorted(byn):
        E = {e: dict(answer_days=0, named=0, bizset=set(), cnt=Counter(), changes=0, consec=0) for e in ENG}
        for mk in byn[ni]:
            cell = [r for r in rows if r['market'] == mk and r['niche'] == ni and r['date'] >= first]
            asked, cnt, ans, named, top = fd.pair_data(mk, ni, cell, g, quar)
            for (e, d) in ans:
                x = E[e]; x['answer_days'] += 1
                bs = named.get((e, d), {})
                if bs: x['named'] += 1
                for b in bs: x['bizset'].add((mk, b)); x['cnt'][(mk, b)] += 1
            for e in ENG:
                days = sorted(d for (ee, d) in top if ee == e)
                for a, b in zip(days, days[1:]):
                    import datetime as dt
                    if (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days == 1:
                        E[e]['consec'] += 1; E[e]['changes'] += top[(e, a)] != top[(e, b)]
        pe = {}
        for e in ENG:
            x = E[e]; tb = x['cnt'].most_common(1)
            pe[e] = {'answer_days': x['answer_days'], 'answers_naming_a_business': x['named'], 'pct_naming': pct(x['named'], x['answer_days']),
                     'distinct_businesses': len(x['bizset']),
                     'top_business': (tb[0][0][1], tb[0][0][0]) if tb else None, 'top_business_answers': tb[0][1] if tb else 0,
                     'top_business_share_pct_of_answers': pct(tb[0][1], x['answer_days']) if tb else 0.0,
                     'top_changed_day_to_day': x['changes'], 'top_consecutive_day_pairs': x['consec'], 'top_change_pct': pct(x['changes'], x['consec'])}
        res['niches'][ni] = {'cities': sorted(byn[ni]), 'per_engine': pe}
    for ni, n in res['niches'].items():
        pe = n['per_engine']; cities = ', '.join(c.replace(', ', ' ') if False else c for c in n['cities'])
        P, O, A, G = pe['perplexity'], pe['openai'], pe['anthropic'], pe['gemini']
        mx = max((e for e in ENG if e != 'gemini'), key=lambda e: pe[e]['distinct_businesses'])
        n['headline'] = ('In %s, %s to %s, Perplexity named at least one business in %d of %d saved answers (%s%%) and OpenAI in %d of %d (%s%%); '
            'the most-named business appeared in %s%% of Perplexity answers (%d of %d) and %s%% of OpenAI answers (%d of %d).' % (
            cities, first, W1, P['answers_naming_a_business'], P['answer_days'], P['pct_naming'], O['answers_naming_a_business'], O['answer_days'], O['pct_naming'],
            P['top_business_share_pct_of_answers'], P['top_business_answers'], P['answer_days'], O['top_business_share_pct_of_answers'], O['top_business_answers'], O['answer_days']))
        n['headline_distinct'] = ('Across the same window, Perplexity named %d distinct businesses in %d answers, OpenAI %d in %d, Anthropic %d in %d; Gemini %d in %d (Gemini answered on few days, own denominator).' % (
            P['distinct_businesses'], P['answer_days'], O['distinct_businesses'], O['answer_days'], A['distinct_businesses'], A['answer_days'], G['distinct_businesses'], G['answer_days']))
        n['disagreement'] = ('Perplexity named a business in %d of %d answers (%s%%) while Anthropic did in %d of %d (%s%%) and OpenAI in %d of %d (%s%%); '
            'the top business changed from one day to the next in %s of %s consecutive-day pairs for Perplexity and %s of %s for OpenAI.' % (
            P['answers_naming_a_business'], P['answer_days'], P['pct_naming'], A['answers_naming_a_business'], A['answer_days'], A['pct_naming'],
            O['answers_naming_a_business'], O['answer_days'], O['pct_naming'], P['top_changed_day_to_day'], P['top_consecutive_day_pairs'], O['top_changed_day_to_day'], O['top_consecutive_day_pairs']))
    json.dump(res, open(out, 'w'), indent=1, sort_keys=True, ensure_ascii=True)
    print('niches', len(res['niches']), 'cells', len(panel), 'window', first, W1, 'latest', res['latest_landed_day'])

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('repo'); ap.add_argument('out'); ap.add_argument('--end', default='2026-10-12')
    A = ap.parse_args(); main(A.repo, A.out, '2026-09-01', A.end)
