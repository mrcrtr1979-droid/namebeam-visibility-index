#!/usr/bin/env python3
"""Recount the headline figures from runs.csv (same folder). Exit 0 if all match, 1 otherwise."""
import csv, os, sys
here = os.path.dirname(os.path.abspath(__file__))
rows = list(csv.DictReader(open(os.path.join(here, 'runs.csv'), encoding='utf-8', newline='')))
def named(s):
    return sum(1 for r in rows if r['set'] == s and r['named_business'] == '1')
got = {'runs': len(rows), 'claude_search_off_named': named('claude_search_off'),
       'gemini_search_off_named': named('gemini_search_off'), 'gemini_search_on_named': named('gemini_search_on')}
want = {'runs': 420, 'claude_search_off_named': 8, 'gemini_search_off_named': 134, 'gemini_search_on_named': 140}
bad = 0
for k in want:
    ok = got[k] == want[k]
    bad += not ok
    print('%-26s counted %3d expected %3d %s' % (k, got[k], want[k], 'OK' if ok else 'MISMATCH'))
print('VERIFY PASS' if not bad else 'VERIFY FAIL')
sys.exit(1 if bad else 0)
