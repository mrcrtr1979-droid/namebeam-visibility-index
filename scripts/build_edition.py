#!/usr/bin/env python3
"""Build the Edition 1 data pack (releases/edition-1) from the raw files in corpus/e1.

    python3 -I scripts/build_edition.py --through 2026-10-13 --method-src DIR [--out releases/edition-1]
    python3 -I scripts/build_edition.py --check-latest PATH     # rebuild the full answers table, compare with a LATEST csv
    python3 -I scripts/build_edition.py --selftest

What goes in the pack (window 2026-09-01 to the last run day, never past 2026-10-13):
  data/answers_<w0>_to_<w1>.csv   one row per raw answer file (API, SERP, AGREE), names as the engine returned them
  data/sources_<w0>_to_<w1>.csv   every URL an engine returned as a source, from datasets/e1/sources
  data/siri_panel.csv             the Siri table (rows inside the window)
  data/customer_zero_*.csv        citations of our own domains, inside the window
  metrics/                        metrics v2 table, method list, public-safe sentences, input hashes, regression
  method/                         the code that computes the metrics, in the layout the code expects
  PACK.json, README.md, MANIFEST.sha256, verify.py

Reproduce: python3 releases/edition-1/verify.py [--rebuild] [--metrics]
Standard library only. Reads files; writes only inside --out.
"""
import argparse
import csv
import glob
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import OrderedDict

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
W0 = '2026-09-01'
E1_END = '2026-10-13'

ANSWER_FIELDS = ['date', 'kind', 'target', 'niche', 'market', 'engine', 'businesses_named_count',
                 'businesses_named', 'first_named', 'source_file', 'sha256']

# files the metrics code needs, relative to the Brain-shaped root it expects
METHOD_FILES = [
    'status/staged/1008E_S1_METRICS/receipts_metrics_v2.py',
    'status/staged/1005B_METRICS/receipts_metrics_v1.py',
    'status/staged/1005B_METRICS/metrics_v1_sept.csv',
    'status/staged/1005A_AI_PICKS_CHART/gen_ai_picks_chart.py',
    'status/staged/0929C_ACTOR/gen_citation_index.py',
    'status/staged/0929B_MARKET_PANELS/gen_market_panels.py',
    'status/returns/COMET_1004/SERP_DRIFT_QUARANTINE_2026-10-04.csv',
]
METHOD_ROOT = 'method/Brain_Kit'
V2_REL = METHOD_ROOT + '/status/staged/1008E_S1_METRICS/receipts_metrics_v2.py'


def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha_file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as fh:
        for ch in iter(lambda: fh.read(1 << 20), b''):
            h.update(ch)
    return h.hexdigest()


def dedupe(seq):
    out = OrderedDict()
    for s in seq:
        if s and s not in out:
            out[s] = 1
    return list(out)


# ------------------------------------------------------------------ answers
def answers_rows(corpus_dir):
    """One row per raw file. Same rules as the daily rebuild of the E1 dataset: names are de-duplicated in order."""
    rows = []
    for path in sorted(glob.glob(os.path.join(corpus_dir, '*.json'))):
        fn = os.path.basename(path)
        raw = open(path, 'rb').read()
        try:
            d = json.loads(raw)
        except Exception:
            continue
        kind = fn.split('_')[0].replace('NB-CZ-', '')
        date = d.get('date_utc') or fn.split('_')[1]
        if kind == 'API':
            engine = d.get('engine', '')
            names = dedupe(d.get('competitors_mentioned') or [])
        elif kind == 'SERP':
            engine = d.get('engine', 'google_serp')
            names = dedupe(d.get('organic_top_results') or [])
        elif kind == 'AGREE':
            engine = 'agree'
            acc = []
            for p in d.get('pairs') or []:
                acc += list(p.get('terser_names') or []) + list(p.get('verbose_names') or [])
            names = dedupe(acc)
        else:
            continue
        rows.append({'date': date, 'kind': kind, 'target': d.get('business', ''),
                     'niche': d.get('niche', '') or '', 'market': d.get('market', '') or '',
                     'engine': engine, 'businesses_named_count': len(names),
                     'businesses_named': ';'.join(names), 'first_named': names[0] if names else '',
                     'source_file': fn, 'sha256': sha_bytes(raw)})
    return rows


def write_rows(path, rows, fields, lineterminator='\n'):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with io.open(path, 'w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator=lineterminator)
        w.writeheader()
        w.writerows(rows)


def in_window(d, w0, w1):
    return w0 <= d <= w1


# ------------------------------------------------------------------ other data
def merge_sources(src_dir, w0, w1, out):
    header = None
    n = 0
    days = []
    with io.open(out, 'w', encoding='utf-8', newline='') as fo:
        w = csv.writer(fo, lineterminator='\n')
        for p in sorted(glob.glob(os.path.join(src_dir, 'sources_*.csv'))):
            d = os.path.basename(p)[len('sources_'):-len('.csv')]
            if not in_window(d, w0, w1):
                continue
            with io.open(p, encoding='utf-8', newline='') as fi:
                r = csv.reader(fi)
                h = next(r)
                if header is None:
                    header = h
                    w.writerow(h)
                elif h != header:
                    raise SystemExit('sources header differs in ' + p)
                for row in r:
                    w.writerow(row)
                    n += 1
            days.append(d)
    return n, days


def filter_by_date(src, out, col, w0, w1):
    n = 0
    with io.open(src, encoding='utf-8', newline='') as fi, io.open(out, 'w', encoding='utf-8', newline='') as fo:
        r = csv.DictReader(fi)
        w = csv.DictWriter(fo, fieldnames=r.fieldnames, lineterminator='\n')
        w.writeheader()
        for row in r:
            if in_window(row[col], w0, w1):
                w.writerow(row)
                n += 1
    return n


def vendor_method(method_src, dst_root):
    """Copy the metrics code into the layout it expects. Byte for byte, no edits."""
    for rel in METHOD_FILES:
        s = os.path.join(method_src, *rel.split('/'))
        d = os.path.join(dst_root, METHOD_ROOT, *rel.split('/'))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copyfile(s, d)


def run_metrics(pack, answers_csv, through, out_dir):
    v2 = os.path.join(pack, *V2_REL.split('/'))
    os.makedirs(out_dir, exist_ok=True)
    p = subprocess.run([sys.executable, '-I', '-B', v2, '--run', '--csv', answers_csv, '--out', out_dir,
                        '--through', through], capture_output=True, text=True, timeout=1500)
    return p.returncode, p.stdout, p.stderr


# ------------------------------------------------------------------ manifest
def manifest_lines(pack):
    out = []
    for root, dirs, files in os.walk(pack):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__')
        for f in sorted(files):
            rel = os.path.relpath(os.path.join(root, f), pack).replace(os.sep, '/')
            if rel == 'MANIFEST.sha256' or f.endswith('.pyc'):
                continue
            out.append('%s  %s' % (sha_file(os.path.join(root, f)), rel))
    return sorted(out, key=lambda l: l.split('  ', 1)[1])


VERIFY_PY = r'''#!/usr/bin/env python3
"""Verify the Edition 1 pack. Prints MATCH or MISMATCH for every file and a final line.

    python3 verify.py              # every file against MANIFEST.sha256
    python3 verify.py --rebuild    # also rebuild the answers table from corpus/e1 of this repo and compare
    python3 verify.py --metrics    # also recompute the metrics table with the included code and compare
Standard library only. Exit code 0 only when everything matches.
"""
import hashlib, importlib.util, os, subprocess, sys, tempfile, json

HERE = os.path.dirname(os.path.abspath(__file__))


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as fh:
        for ch in iter(lambda: fh.read(1 << 20), b''):
            h.update(ch)
    return h.hexdigest()


def main():
    a = sys.argv[1:]
    ok = True
    want = {}
    for line in open(os.path.join(HERE, 'MANIFEST.sha256'), encoding='utf-8'):
        line = line.rstrip('\n')
        if line:
            h, rel = line.split('  ', 1)
            want[rel] = h
    n = 0
    for rel in sorted(want):
        p = os.path.join(HERE, *rel.split('/'))
        got = sha(p) if os.path.exists(p) else None
        good = got == want[rel]
        ok = ok and good
        n += 1
        print(('MATCH    ' if good else 'MISMATCH ') + rel)
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), HERE).replace(os.sep, '/')
            if rel != 'MANIFEST.sha256' and not f.endswith('.pyc') and rel not in want:
                ok = False
                print('EXTRA    ' + rel + ' (not in the manifest)')
    pack = json.load(open(os.path.join(HERE, 'PACK.json'), encoding='utf-8'))
    if '--rebuild' in a:
        repo = os.path.abspath(os.path.join(HERE, '..', '..'))
        spec = importlib.util.spec_from_file_location('build_edition', os.path.join(repo, 'scripts', 'build_edition.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        rows = [r for r in mod.answers_rows(os.path.join(repo, 'corpus', 'e1')) if pack['window'][0] <= r['date'] <= pack['window'][1]]
        tmp = tempfile.mkdtemp()
        out = os.path.join(tmp, 'answers.csv')
        mod.write_rows(out, rows, mod.ANSWER_FIELDS)
        rel = pack['files']['answers']
        good = sha(out) == want.get(rel)
        ok = ok and good
        print(('MATCH    ' if good else 'MISMATCH ') + 'rebuild of ' + rel + ' from corpus/e1 (%d rows)' % len(rows))
    if '--metrics' in a:
        tmp = tempfile.mkdtemp()
        v2 = os.path.join(HERE, 'method', 'Brain_Kit', 'status', 'staged', '1008E_S1_METRICS', 'receipts_metrics_v2.py')
        p = subprocess.run([sys.executable, '-I', '-B', v2, '--run', '--csv', os.path.join(HERE, *pack['files']['answers'].split('/')),
                            '--out', tmp, '--through', pack['window'][1]], capture_output=True, text=True)
        good = p.returncode == 0 and sha(os.path.join(tmp, 'metrics_v2.csv')) == want.get('metrics/metrics_v2.csv')
        ok = ok and good
        print(('MATCH    ' if good else 'MISMATCH ') + 'recompute of metrics/metrics_v2.csv')
        if p.returncode != 0:
            print(p.stdout[-600:], p.stderr[-600:])
    print('VERIFY ' + ('MATCH' if ok else 'MISMATCH') + ' (%d files)' % n)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
'''


def write_manifest(pack):
    lines = manifest_lines(pack)
    with io.open(os.path.join(pack, 'MANIFEST.sha256'), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(lines) + '\n')
    return len(lines)


# ------------------------------------------------------------------ build
def gaps_text(days, w0, w1):
    import datetime
    have = set(days)
    d = datetime.date.fromisoformat(w0)
    end = datetime.date.fromisoformat(w1)
    miss = []
    while d <= end:
        if d.isoformat() not in have:
            miss.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return ', '.join(miss) if miss else 'none'



def build(through, out, method_src, status):
    w1 = min(through, E1_END)
    pack = os.path.abspath(out)
    if os.path.isdir(pack):
        shutil.rmtree(pack)
    os.makedirs(os.path.join(pack, 'data'))
    corpus = os.path.join(REPO, 'corpus', 'e1')
    rows = [r for r in answers_rows(corpus) if in_window(r['date'], W0, w1)]
    last = max(r['date'] for r in rows)
    tag = '%s_to_%s' % (W0, last)
    ans_rel = 'data/answers_%s.csv' % tag
    write_rows(os.path.join(pack, *ans_rel.split('/')), rows, ANSWER_FIELDS)
    src_rel = 'data/sources_%s.csv' % tag
    n_src, src_days = merge_sources(os.path.join(REPO, 'datasets', 'e1', 'sources'), W0, last, os.path.join(pack, *src_rel.split('/')))
    n_siri = filter_by_date(os.path.join(REPO, 'datasets', 'e1', 'siri', 'siri_panel.csv'), os.path.join(pack, 'data', 'siri_panel.csv'), 'date', W0, last)
    cz = os.path.join(REPO, 'datasets', 'e1', 'customer_zero')
    n_cz = {}
    for name, col in (('customer_zero_daily', 'date'), ('customer_zero_hits', 'date'), ('customer_zero_rolling', 'as_of')):
        n_cz[name] = filter_by_date(os.path.join(cz, name + '.csv'), os.path.join(pack, 'data', name + '.csv'), col, W0, last)
    vendor_method(method_src, pack)
    rc, so, se = run_metrics(pack, os.path.join(pack, *ans_rel.split('/')), last, os.path.join(pack, 'metrics'))
    sys.stdout.write(so[-1500:])
    if rc != 0:
        sys.stderr.write(se[-1500:])
        raise SystemExit('metrics run failed (exit %d)' % rc)
    by_kind = {}
    for r in rows:
        by_kind[r['kind']] = by_kind.get(r['kind'], 0) + 1
    days = sorted({r['date'] for r in rows})
    meta = {'edition': 1, 'status': status, 'window': [W0, last], 'requested_through': through,
            'run_days': len(days), 'answers_rows': len(rows), 'rows_by_kind': by_kind,
            'sources_rows': n_src, 'sources_days': len(src_days), 'siri_rows': n_siri, 'customer_zero_rows': n_cz,
            'files': {'answers': ans_rel, 'sources': src_rel, 'siri': 'data/siri_panel.csv', 'metrics': 'metrics/metrics_v2.csv'}}
    with io.open(os.path.join(pack, 'PACK.json'), 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(meta, fh, indent=1, sort_keys=True)
        fh.write('\n')
    with io.open(os.path.join(pack, 'verify.py'), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(VERIFY_PY)
    note = os.path.join(HERE, 'edition1_method_note.md')
    if os.path.exists(note):
        text = io.open(note, encoding='utf-8').read()
        text = (text.replace('{{STATUS}}', status).replace('{{W0}}', W0).replace('{{W1}}', last)
                .replace('{{RUN_DAYS}}', str(len(days))).replace('{{ANSWERS_ROWS}}', '{:,}'.format(len(rows)))
                .replace('{{GAPS}}', gaps_text(days, W0, last)).replace('{{ANSWERS_FILE}}', ans_rel).replace('{{SOURCES_FILE}}', src_rel))
        with io.open(os.path.join(pack, 'README.md'), 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(text)
    n = write_manifest(pack)
    print('pack %s: window %s..%s, %d run days, %d answer rows, %d source rows, %d siri rows, %d files in the manifest'
          % (pack, W0, last, len(days), len(rows), n_src, n_siri, n))
    return pack


# ------------------------------------------------------------------ tests
def selftest():
    fails = []

    def chk(name, ok):
        print(('  ok   ' if ok else '  FAIL ') + name)
        if not ok:
            fails.append(name)

    tmp = tempfile.mkdtemp()
    cdir = os.path.join(tmp, 'corpus')
    os.makedirs(cdir)

    def put(fn, d):
        json.dump(d, open(os.path.join(cdir, fn), 'w'))

    put('NB-CZ-API_2026-09-02_a.json', {'date_utc': '2026-09-02', 'business': 'A Co', 'niche': 'n', 'market': 'M1', 'engine': 'openai',
                                         'competitors_mentioned': ['X', 'Y', 'X', '']})
    put('NB-CZ-SERP_2026-09-03_a.json', {'date_utc': '2026-09-03', 'business': 'A Co', 'niche': 'n', 'market': 'M1', 'engine': 'google_serp',
                                          'organic_top_results': ['https://u1/', 'https://u2/']})
    put('NB-CZ-AGREE_2026-09-04_a.json', {'date_utc': '2026-09-04', 'business': 'A Co', 'pairs': [{'terser_names': ['P'], 'verbose_names': ['P', 'Q']}]})
    put('NB-CZ-OTHER_2026-09-04_a.json', {'date_utc': '2026-09-04'})
    open(os.path.join(cdir, 'NB-CZ-API_2026-09-05_bad.json'), 'w').write('{not json')
    rows = answers_rows(cdir)
    chk('three kinds read, unknown kind and broken json skipped (3 rows)', len(rows) == 3)
    by = {r['kind']: r for r in rows}   # files sort by name: AGREE, API, SERP
    r0 = by['API']
    chk('API names de-duplicated in order, empty string dropped (X;Y, count 2, first X)',
        (r0['businesses_named'], r0['businesses_named_count'], r0['first_named']) == ('X;Y', 2, 'X'))
    chk('SERP rows carry the organic results', by['SERP']['businesses_named'] == 'https://u1/;https://u2/' and by['SERP']['engine'] == 'google_serp')
    chk('AGREE rows merge both name lists without repeats (P;Q)', by['AGREE']['businesses_named'] == 'P;Q' and by['AGREE']['engine'] == 'agree')
    chk('row sha256 is the hash of the raw file bytes', r0['sha256'] == sha_file(os.path.join(cdir, 'NB-CZ-API_2026-09-02_a.json')))
    chk('window filter keeps 2026-09-03 only', [r['date'] for r in rows if in_window(r['date'], '2026-09-03', '2026-09-03')] == ['2026-09-03'])
    # manifest and verify round trip
    pack = os.path.join(tmp, 'pack')
    os.makedirs(os.path.join(pack, 'data'))
    open(os.path.join(pack, 'data', 'a.csv'), 'w').write('x,y\n1,2\n')
    json.dump({'window': ['2026-09-01', '2026-09-30'], 'files': {'answers': 'data/a.csv'}}, open(os.path.join(pack, 'PACK.json'), 'w'))
    open(os.path.join(pack, 'verify.py'), 'w').write(VERIFY_PY)
    write_manifest(pack)
    p = subprocess.run([sys.executable, '-I', '-B', os.path.join(pack, 'verify.py')], capture_output=True, text=True)
    chk('verify prints MATCH on an untouched pack', p.returncode == 0 and 'VERIFY MATCH (3 files)' in p.stdout)
    open(os.path.join(pack, 'data', 'a.csv'), 'w').write('x,y\n1,3\n')
    p = subprocess.run([sys.executable, '-I', '-B', os.path.join(pack, 'verify.py')], capture_output=True, text=True)
    chk('RED: one changed byte gives MISMATCH and exit 1', p.returncode == 1 and 'MISMATCH data/a.csv' in p.stdout and 'VERIFY MISMATCH' in p.stdout)
    open(os.path.join(pack, 'data', 'a.csv'), 'w').write('x,y\n1,2\n')
    open(os.path.join(pack, 'data', 'extra.csv'), 'w').write('z\n')
    p = subprocess.run([sys.executable, '-I', '-B', os.path.join(pack, 'verify.py')], capture_output=True, text=True)
    chk('RED: a file missing from the manifest is reported as EXTRA', p.returncode == 1 and 'EXTRA    data/extra.csv' in p.stdout)
    os.remove(os.path.join(pack, 'data', 'a.csv'))
    p = subprocess.run([sys.executable, '-I', '-B', os.path.join(pack, 'verify.py')], capture_output=True, text=True)
    chk('RED: a deleted file is reported as MISMATCH', 'MISMATCH data/a.csv' in p.stdout and p.returncode == 1)
    print('SELFTEST ' + ('FAIL: ' + ', '.join(fails) if fails else 'PASS'))
    return not fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--through')
    ap.add_argument('--out', default=os.path.join(REPO, 'releases', 'edition-1'))
    ap.add_argument('--method-src', help='directory shaped like the Brain kit that holds the metrics code')
    ap.add_argument('--status', default='STAGED')
    ap.add_argument('--check-latest')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if a.check_latest:
        rows = answers_rows(os.path.join(REPO, 'corpus', 'e1'))
        tmp = os.path.join(tempfile.mkdtemp(), 'x.csv')
        with io.open(tmp, 'w', encoding='utf-8', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=ANSWER_FIELDS)   # default CRLF, as the daily rebuild writes it
            w.writeheader()
            w.writerows(rows)
        good = sha_file(tmp) == sha_file(a.check_latest)
        print('%s rebuild of %d rows vs %s' % ('MATCH' if good else 'DIFF', len(rows), a.check_latest))
        return 0 if good else 1
    if not (a.through and a.method_src):
        ap.print_help()
        return 1
    build(a.through, a.out, a.method_src, a.status)
    return 0


if __name__ == '__main__':
    sys.exit(main())
