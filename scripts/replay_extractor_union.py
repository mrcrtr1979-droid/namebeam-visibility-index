#!/usr/bin/env python3
"""Replay old vs new competitor extraction over stored answers.

    python3 scripts/replay_extractor_union.py [--from 2026-10-01] [--to 2026-10-11]

Reads corpus/e1/NB-CZ-API_*.json (status OK, engine answer text in answer_verbatim) and, per engine, prints:
answers, mean names old/new, answers with a top-10 business old/new, and the 20 most frequent NEW names
that are not in any top-10 list (junk the union lets in). The top-10 lists are the explorer lists in
tests/fixtures/extractor/explorer_top10_2026-10-11.json. Read-only; writes nothing.
"""
import argparse
import collections
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
for k in ("PERPLEXITY", "GEMINI", "OPENAI", "ANTHROPIC", "BRIGHTDATA"):
    os.environ.setdefault(k + "_API_KEY", "replay-dummy")
import e1_runner  # noqa: E402


def norm(s):
    return re.sub(r'[^a-z0-9]+', ' ', str(s).lower()).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--from', dest='d0', default='2026-10-01')
    ap.add_argument('--to', dest='d1', default='2026-10-11')
    ap.add_argument('--top10', default=os.path.join(REPO, 'tests/fixtures/extractor/explorer_top10_2026-10-11.json'))
    ap.add_argument('--corpus', default=os.path.join(REPO, 'corpus/e1'))
    a = ap.parse_args()
    top = {(m, n): l for m, n, l in json.load(open(a.top10))}
    all_top = {norm(x) for l in top.values() for x in l}
    st = collections.defaultdict(lambda: dict(n=0, old=0, new=0, hit_old=0, hit_new=0))
    junk = collections.defaultdict(collections.Counter)
    for f in sorted(glob.glob(os.path.join(a.corpus, 'NB-CZ-API_*.json'))):
        d = json.load(open(f))
        if d.get('run_status') != 'OK' or not (a.d0 <= d.get('date_utc', '') <= a.d1):
            continue
        row = {'business': d.get('business', ''), 'variants_business_name_forms': []}
        text = d.get('answer_verbatim') or ''
        src = d.get('sources_cited')
        src = src if isinstance(src, list) else []
        old = e1_runner.extract_competitors_old(row, text, src)
        new = e1_runner.extract_competitors_union(row, text, src)
        tn = {norm(x) for x in top.get((d.get('market'), d.get('niche')), [])}
        s = st[d['engine']]
        s['n'] += 1
        s['old'] += len(old)
        s['new'] += len(new)
        s['hit_old'] += any(norm(x) in tn for x in old)
        s['hit_new'] += any(norm(x) in tn for x in new)
        oldn = {norm(x) for x in old}
        for x in new:
            if norm(x) not in all_top and norm(x) not in oldn:
                junk[d['engine']][x] += 1
    print('window %s..%s, run_status OK' % (a.d0, a.d1))
    print('engine | answers | mean names old | mean names new | answers with top-10 business old | new')
    for e in sorted(st):
        s = st[e]
        print('%s | %d | %.2f | %.2f | %d | %d' % (e, s['n'], s['old'] / s['n'], s['new'] / s['n'], s['hit_old'], s['hit_new']))
    allj = collections.Counter()
    for c in junk.values():
        allj.update(c)
    print('\n20 most frequent NEW names not in any top-10 list (all engines):')
    for n, c in allj.most_common(int(os.environ.get("TOPN", 20))):
        print('  %4d  %s' % (c, n))
    for e in sorted(junk):
        print('\n%s, top 20:' % e)
        for n, c in junk[e].most_common(20):
            print('  %4d  %s' % (c, n))


if __name__ == '__main__':
    main()
