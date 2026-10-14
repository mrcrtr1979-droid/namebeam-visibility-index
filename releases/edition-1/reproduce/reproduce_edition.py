#!/usr/bin/env python3
"""Recompute the Edition 1 headline numbers from the pack alone, offline, and compare them with expected values.

    python3 -I scripts/reproduce_edition.py --pack releases/edition-1
    python3 -I scripts/reproduce_edition.py --pack releases/edition-1 --write-expected
    python3 -I scripts/reproduce_edition.py --selftest

Three kinds of check, one printed line each:
  headline  the recomputed value against EXPECTED_HEADLINES.csv in the pack
  metrics   a row of metrics/metrics_v2.csv against the recompute, where headline_recompute.py computes the same
            method and engine (P.raw.pername and P.gated.pername, perplexity, windows WA and WB). Every other tracked
            row (P.raw.pername, P.gated.pername, J.raw.pair_bothnamed.median) is checked through the sha256 of
            metrics_v2.csv alone, because no recompute exists for it here.
  input     the sha256 of every file the run read, against EXPECTED_INPUTS.csv

The formulas are not copied here. Persistence, the KC page counts and the gated figures are imported from
scripts/headline_recompute.py, which runs the gate code that ships in the pack. The input list is every file the run
opened for reading (recorded with a PEP 578 audit hook), so the gate modules and the quarantine file are hashed too.
--write-expected writes EXPECTED_HEADLINES.csv and EXPECTED_INPUTS.csv into the pack and nothing else.
Standard library, Python 3.8 or later. Exit 0 when every check matches.
"""
import argparse
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
KIT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(KIT_DIR)
import headline_recompute as hr  # noqa: E402

HEADLINES = 'EXPECTED_HEADLINES.csv'
INPUTS = 'EXPECTED_INPUTS.csv'
FORMULAS_KEY = 'kit:scripts/headline_recompute.py'
TRACKED = ('P.raw.pername', 'P.gated.pername', 'J.raw.pair_bothnamed.median')
RECOMPUTED = {('P.raw.pername', 'perplexity'): 'P.raw.perplexity',
              ('P.gated.pername', 'perplexity'): 'P.gated.perplexity'}
OPENED = []


def _note_open(event, args):
    """Audit hook (PEP 578): keep the path of every file opened for reading, imports included."""
    if event == 'open' and isinstance(args[0], str):
        mode = args[1] if len(args) > 1 and isinstance(args[1], str) else 'r'
        if not set(mode) & set('wax+'):
            OPENED.append(args[0])


sys.addaudithook(_note_open)


def sha256(path):
    h = hashlib.sha256()
    with io.open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def read_rows(path):
    with io.open(path, encoding='utf-8', newline='') as fh:
        return list(csv.DictReader(fh))


def norm_int(x):
    x = (x or '').strip()
    return str(int(x)) if x else ''


def norm_val(x):
    if x is None or str(x).strip() == '':
        return ''
    return '%.6f' % float(x)


def row(ok, name, window, num, den, value, basis):
    text = '%-8s %-40s %-24s %12s %12s %-18s %s' % (
        'MATCH' if ok else 'MISMATCH', name, window, num, den, value, basis)
    return ok, text


def reproduce(pack):
    """Recompute the headlines, the metrics comparisons and the input list from the pack."""
    start = len(OPENED)
    meta, _rows, _ser, out = hr.compute(pack)
    end = meta['window'][1]
    heads = []  # (check, window, numerator, denominator, value, note)
    for metric, _span, n, d, v in out:
        if metric.startswith('P.raw.perplexity.'):
            heads.append(('P.raw.perplexity', metric.rsplit('.', 1)[1], n, d, v, ''))
    gated = hr.gated_series(pack, meta)
    dates = {}
    for label, (a, b) in hr.WINDOWS.items():
        b = b or end
        dates[label] = (a, b)
        n, d = hr.persistence(gated, a, b)
        heads.append(('P.gated.perplexity', label, n, d, round(n / d, 6) if d else None, ''))
    seen = set()
    for metric, span, n, d, _v in out:
        if metric == 'KC.page.answers' and span not in seen:
            seen.add(span)
            heads.append(('KC.page.answers', span, n, d, None, 'numerator = answers, denominator = run days'))
    metrics_rel = meta['files']['metrics']
    root = os.path.realpath(pack)
    metric_rows = read_rows(os.path.join(root, *metrics_rel.split('/')))
    inputs = {}
    for path in OPENED[start:]:
        real = os.path.realpath(path)
        if real.startswith(root + os.sep) and '__pycache__' not in real.split(os.sep) and os.path.isfile(real):
            inputs['pack:' + os.path.relpath(real, root).replace(os.sep, '/')] = sha256(real)
    inputs[FORMULAS_KEY] = sha256(os.path.join(KIT_DIR, 'headline_recompute.py'))
    return {'headlines': heads, 'dates': dates, 'metric_rows': metric_rows,
            'metrics_key': 'pack:' + metrics_rel, 'inputs': inputs}


def write_expected(pack, run):
    hp, ip = os.path.join(pack, HEADLINES), os.path.join(pack, INPUTS)
    with io.open(hp, 'w', encoding='utf-8', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['check', 'window', 'numerator', 'denominator', 'value'])
        for check_name, window, n, d, v, _note in run['headlines']:
            w.writerow([check_name, window, n, d, norm_val(v)])
    with io.open(ip, 'w', encoding='utf-8', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(['input_file', 'sha256'])
        for key in sorted(run['inputs']):
            w.writerow([key, run['inputs'][key]])
    return hp, ip


def check(pack, run):
    """Compare the run with the expected files in the pack. Returns (ok, text) pairs."""
    hp, ip = os.path.join(pack, HEADLINES), os.path.join(pack, INPUTS)
    for p in (hp, ip):
        if not os.path.isfile(p):
            raise IOError('%s is not in the pack; create it with --write-expected' % os.path.basename(p))
    expected = {(r['check'], r['window']): (norm_int(r['numerator']), norm_int(r['denominator']), norm_val(r['value']))
                for r in read_rows(hp)}
    listed = {r['input_file']: r['sha256'].strip() for r in read_rows(ip)}

    inputs_out, input_ok = [], {}
    for key in sorted(set(listed) | set(run['inputs'])):
        have, want = run['inputs'].get(key), listed.get(key)
        if want is None:
            ok, basis = False, 'read by this run, not listed in %s' % INPUTS
        elif have is None:
            ok, basis = False, 'listed in %s, not read by this run' % INPUTS
        elif have == want:
            ok, basis = True, key
        else:
            ok, basis = False, '%s; expected sha256 %s' % (key, want)
        input_ok[key] = ok
        inputs_out.append(row(ok, 'input sha256', '-', '-', '-', have or '-', basis))

    head_out, got = [], {}
    for check_name, window, n, d, v, note in run['headlines']:
        now = (str(n), str(d), norm_val(v))
        got[(check_name, window)] = now
        want = expected.get((check_name, window))
        label = ' (%s)' % note if note else ''
        if want is None:
            head_out.append(row(False, check_name, window, now[0], now[1], now[2], 'no expected row in %s' % HEADLINES))
        elif now == want:
            head_out.append(row(True, check_name, window, now[0], now[1], now[2], 'recomputed' + label))
        else:
            head_out.append(row(False, check_name, window, now[0], now[1], now[2],
                                'recomputed%s; expected %s/%s/%s' % (label, want[0], want[1], want[2])))
    for key in sorted(expected):
        if key not in got:
            head_out.append(row(False, key[0], key[1], '-', '-', '-', 'expected row not produced by this run'))

    metric_out = []
    for r in run['metric_rows']:
        method, engine, w = r['method_id'], r['engine'], r['window_id']
        if method not in TRACKED:
            continue
        name = '%s/%s' % (method, engine)
        num, den, val = r['numerator'] or '-', r['denominator'] or '-', r['value'] or '-'
        rec = RECOMPUTED.get((method, engine))
        if rec and (rec, w) in got:
            n, d, v = got[(rec, w)]
            same = ((norm_int(r['numerator']), norm_int(r['denominator']), norm_val(r['value'])) == (n, d, v)
                    and (r['window_from'], r['window_to']) == run['dates'][w])
            basis = ('recomputed; same as metrics_v2.csv' if same else
                     'metrics_v2.csv differs from the recompute (%s/%s/%s)' % (n, d, v))
            metric_out.append(row(same, name, w, num, den, val, basis))
        else:
            ok = input_ok.get(run['metrics_key'], False)
            metric_out.append(row(ok, name, w, num, den, val,
                                  'sha256 of metrics_v2.csv; no recompute for this row'))
    return head_out + metric_out + inputs_out


FIXTURE_GATE = '''# Stand-in for the pack gate module, for the selftest fixture. It drops any name containing 'pipe'.
from collections import defaultdict
import csv

W0 = W1 = None


def load_series(src):
    ser = defaultdict(lambda: {'answered': set(), 'named': defaultdict(set)})
    with open(src, encoding='utf-8', newline='') as fh:
        for r in csv.DictReader(fh):
            if r['kind'] != 'API':
                continue
            names = {n.strip().casefold() for n in r['businesses_named'].split(';') if n.strip() and 'pipe' not in n.casefold()}
            s = ser[(r['market'].strip(), r['niche'].strip(), r['engine'].strip())]
            if names:
                s['answered'].add(r['date'])
            s['named'][r['date']] |= names
    return ser, ['perplexity'], [], 0
'''

FIXTURE_ANSWERS = '''date,kind,engine,market,niche,businesses_named
2026-09-01,API,perplexity,Testville,plumbers,Alpha Plumbing;Beta Pipe
2026-09-01,SERP,perplexity,Testville,plumbers,Serp Ignored Name
2026-09-01,API,openai,Testville,plumbers,Openai Ignored Name
2026-09-02,API,perplexity,Testville,plumbers,alpha plumbing;Gamma Drain
2026-09-03,API,perplexity,Testville,plumbers,
2026-09-04,API,perplexity,Testville,plumbers,Delta Roofing
2026-09-05,API,perplexity,Testville,plumbers,Delta Roofing
2026-09-28,API,perplexity,Testville,plumbers,Echo Electric
2026-09-29,API,perplexity,Testville,plumbers,Echo Electric
2026-09-30,API,perplexity,Testville,plumbers,Hotel Pest
2026-10-01,API,perplexity,Testville,plumbers,Golf Glazing
'''

FIXTURE_SOURCES = '''date,engine,check_id,url
2026-09-20,perplexity,c4,https://namebeam.ai/kansas-city-health-insurance
2026-09-28,perplexity,c1,https://namebeam.ai/kansas-city-health-insurance
2026-09-28,perplexity,c2,https://namebeam.ai/kansas-city-health-insurance/
2026-09-28,perplexity,c3,https://example.org/elsewhere
2026-09-30,perplexity,c1,https://namebeam.ai/kansas-city-health-insurance
'''

FIXTURE_METRICS = '''version,window_id,window_from,window_to,engine,engine_regime,metric,method_id,name_set,statistic,value,numerator,denominator,denominator_unit,n_cells,note
receipts_metrics_v2,WA,2026-09-01,2026-09-27,perplexity,sonar_chat_completions,P,P.raw.pername,raw,share,0.666667,2,3,business-day namings with the next day answered,1,
receipts_metrics_v2,WB,2026-09-28,2026-10-01,perplexity,agent_api_forced_web_search,P,P.raw.pername,raw,share,0.333333,1,3,business-day namings with the next day answered,1,
receipts_metrics_v2,WA,2026-09-01,2026-09-27,perplexity,sonar_chat_completions,P,P.gated.pername,gated,share,1.000000,2,2,business-day namings with the next day answered,1,
receipts_metrics_v2,WB,2026-09-28,2026-10-01,perplexity,agent_api_forced_web_search,P,P.gated.pername,gated,share,0.333333,1,3,business-day namings with the next day answered,1,
receipts_metrics_v2,WA,2026-09-01,2026-09-27,anthropic,api_no_search_tool,P,P.raw.pername,raw,share,0.500000,5,10,business-day namings with the next day answered,1,
receipts_metrics_v2,WE,2026-09-01,2026-10-01,perplexity,mixed_two_regimes,P,P.raw.pername,raw,share,0.500000,3,6,business-day namings with the next day answered,1,
receipts_metrics_v2,WA,2026-09-01,2026-09-27,gemini|anthropic,pair,J,J.raw.pair_bothnamed.median,raw,median,0.250000,,,pair days both named,1,
'''


def fixture():
    """A tiny pack with hand-checkable numbers: WA raw 2 of 3, WB raw 1 of 3, WA gated 2 of 2, KC 3 answers on 2 days."""
    pack = {'edition': 1, 'window': ['2026-09-01', '2026-10-01'],
            'files': {'answers': 'data/answers.csv', 'sources': 'data/sources.csv', 'metrics': 'metrics/metrics_v2.csv'},
            'metrics_panel': {'file': None}}
    return {'PACK.json': json.dumps(pack, indent=1) + '\n',
            'data/answers.csv': FIXTURE_ANSWERS,
            'data/sources.csv': FIXTURE_SOURCES,
            'metrics/metrics_v2.csv': FIXTURE_METRICS,
            'method/Brain_Kit/status/staged/1005B_METRICS/receipts_metrics_v1.py': FIXTURE_GATE}


def read_bytes(path):
    with io.open(path, 'rb') as fh:
        return fh.read()


def write_bytes(path, data):
    with io.open(path, 'wb') as fh:
        fh.write(data)


def plant(path, check_name, window, column, value):
    rows = read_rows(path)
    for r in rows:
        if r['check'] == check_name and r['window'] == window:
            r[column] = value
    with io.open(path, 'w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=['check', 'window', 'numerator', 'denominator', 'value'], lineterminator='\n')
        w.writeheader()
        w.writerows(rows)


def cli(args):
    cmd = [sys.executable, '-I', os.path.abspath(__file__)] + args
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    return p.returncode, p.stdout


def selftest():
    fails = []

    def step(label, ok):
        print(('  ok   ' if ok else '  FAIL ') + label)
        if not ok:
            fails.append(label)

    with tempfile.TemporaryDirectory() as tmp:
        pack = os.path.join(tmp, 'pack')
        for rel, text in fixture().items():
            path = os.path.join(pack, *rel.split('/'))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with io.open(path, 'w', encoding='utf-8', newline='') as fh:
                fh.write(text)
        heads = os.path.join(pack, HEADLINES)
        listed = os.path.join(pack, INPUTS)
        pj = os.path.join(pack, 'PACK.json')

        code, _out = cli(['--pack', pack, '--write-expected'])
        step('--write-expected writes both expected files', code == 0 and os.path.isfile(heads) and os.path.isfile(listed))
        by_hand = {(r['check'], r['window']): (r['numerator'], r['denominator']) for r in read_rows(heads)}
        step('fixture WA raw persistence is 2 of 3, as worked out by hand', by_hand.get(('P.raw.perplexity', 'WA')) == ('2', '3'))

        code, out = cli(['--pack', pack])
        lines = out.splitlines()
        step('correct expected files: every check MATCH (GREEN)', code == 0 and lines[-1].startswith('REPRODUCE MATCH'))

        original = read_bytes(heads)
        plant(heads, 'P.raw.perplexity', 'WA', 'numerator', '1')
        code, out = cli(['--pack', pack])
        lines = out.splitlines()
        red = [l for l in lines if l.startswith('MISMATCH') and 'P.raw.perplexity' in l and ' WA ' in l]
        step('planted wrong expected value: that check is MISMATCH (RED)',
             code == 1 and len(red) == 1 and lines[-1].startswith('REPRODUCE MISMATCH'))
        write_bytes(heads, original)
        code, out = cli(['--pack', pack])
        step('expected file restored: every check MATCH again (GREEN)', code == 0)

        original = read_bytes(pj)
        write_bytes(pj, original + b'\n')
        code, out = cli(['--pack', pack])
        lines = out.splitlines()
        bad = [l for l in lines if l.startswith('MISMATCH')]
        step('changed input file: its input sha256 is MISMATCH and nothing else (RED)',
             code == 1 and len(bad) == 1 and 'input sha256' in bad[0] and 'pack:PACK.json' in bad[0])
        write_bytes(pj, original)
        code, out = cli(['--pack', pack])
        step('input file restored: every check MATCH again (GREEN)', code == 0)

    if fails:
        print('SELFTEST FAIL: ' + ', '.join(fails))
        return False
    print('SELFTEST PASS')
    return True


def main():
    ap = argparse.ArgumentParser(description='Recompute the Edition 1 headline numbers from the pack and compare them '
                                             'with expected values.')
    ap.add_argument('--pack', default='releases/edition-1', help='the pack directory')
    ap.add_argument('--write-expected', action='store_true',
                    help='write EXPECTED_HEADLINES.csv and EXPECTED_INPUTS.csv into the pack')
    ap.add_argument('--selftest', action='store_true', help='run the fixture tests')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    pack = os.path.abspath(a.pack)
    try:
        run = reproduce(pack)
        if a.write_expected:
            hp, ip = write_expected(pack, run)
            print('wrote %s (%d rows)' % (os.path.relpath(hp), len(run['headlines'])))
            print('wrote %s (%d files)' % (os.path.relpath(ip), len(run['inputs'])))
            return 0
        results = check(pack, run)
    except Exception as exc:  # a run that cannot finish is not a reproduction
        label = 'WRITE STOPPED' if a.write_expected else 'REPRODUCE MISMATCH'
        print('%s (run stopped: %s: %s)' % (label, type(exc).__name__, exc))
        return 1
    for _ok, text in results:
        print(text)
    bad = sum(1 for ok, _text in results if not ok)
    if bad:
        print('REPRODUCE MISMATCH (%d of %d checks)' % (bad, len(results)))
        return 1
    print('REPRODUCE MATCH (%d checks)' % len(results))
    return 0


if __name__ == '__main__':
    sys.exit(main())
