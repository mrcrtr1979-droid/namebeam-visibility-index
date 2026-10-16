#!/usr/bin/env python3
"""name_filter_report.py: per-engine before/after of NAME FILTER v2 on a built answers table (2026-10-14, COS wave 3 item 1).

    python3 -I scripts/name_filter_report.py --answers releases/edition-1/data/answers_2026-09-01_to_2026-10-13.csv \
        [--corpus corpus/e1] [--top 40]

Reads each API and AGREE row's businesses_named_no_headings (heading filter v1, the published derived column), applies
scripts/name_filter.py v2 with the raw answer text (read from corpus/<source_file> for rule P), and prints per engine:
rows, rows where the filtered list differs, names before, names after, names removed, and the removed names by rule with
counts (top N). Nothing is written. Standard library only.
"""
import argparse
import collections
import csv
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import name_filter as nf  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--answers', required=True)
    ap.add_argument('--corpus', default=os.path.join(HERE, '..', 'corpus', 'e1'))
    ap.add_argument('--top', type=int, default=40)
    a = ap.parse_args()
    per = collections.OrderedDict()
    removed = collections.defaultdict(collections.Counter)
    byrule = collections.defaultdict(collections.Counter)
    with io.open(a.answers, encoding='utf-8', newline='') as fh:
        for r in csv.DictReader(fh):
            if r['kind'] not in ('API', 'AGREE'):
                continue
            e = r['engine']
            c = per.setdefault(e, {'rows': 0, 'differ': 0, 'before': 0, 'after': 0})
            names = [n for n in r['businesses_named_no_headings'].split(';') if n]
            text = ''
            if r['kind'] == 'API':
                p = os.path.join(a.corpus, r['source_file'])
                if os.path.exists(p):
                    try:
                        text = json.load(io.open(p, encoding='utf-8')).get('answer_verbatim') or ''
                    except Exception:
                        text = ''
            kept = []
            for n in names:
                why = nf.reason(n, text)
                if why is None:
                    kept.append(n)
                else:
                    removed[e][n] += 1
                    byrule[e][why] += 1
            c['rows'] += 1
            c['before'] += len(names)
            c['after'] += len(kept)
            if len(kept) != len(names):
                c['differ'] += 1
    print('engine | rows | rows_differ | names_before | names_after | names_removed | by_rule')
    for e, c in per.items():
        print('%s | %d | %d | %d | %d | %d | %s' % (e, c['rows'], c['differ'], c['before'], c['after'], c['before'] - c['after'],
                                                  ' '.join('%s=%d' % kv for kv in sorted(byrule[e].items()))))
    for e in per:
        print('\nREMOVED %s (top %d):' % (e, a.top))
        for n, k in removed[e].most_common(a.top):
            print('  %4d  %s  [%s]' % (k, n, nf.reason(n, '') or 'P'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
