#!/usr/bin/env python3
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
        mp = pack.get('metrics_panel') or {}
        csv_in = os.path.join(HERE, *pack['files']['answers'].split('/'))
        if mp.get('file'):
            repo = os.path.abspath(os.path.join(HERE, '..', '..'))
            spec = importlib.util.spec_from_file_location('build_edition', os.path.join(repo, 'scripts', 'build_edition.py'))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            import csv as _csv
            with open(csv_in, encoding='utf-8', newline='') as fh:
                arows = list(_csv.DictReader(fh))
            kept, left = mod.panel_split(arows)
            pf = os.path.join(tmp, 'panel.csv')
            mod.write_rows(pf, kept, mod.ANSWER_FIELDS)
            good = sha(pf) == want.get(mp['file']) and len(left) == mp['rows_left_out']
            ok = ok and good
            print(('MATCH    ' if good else 'MISMATCH ') + 'derivation of ' + mp['file'] + ' from the answers table (%d rows left out)' % len(left))
            csv_in = os.path.join(HERE, *mp['file'].split('/'))
        p = subprocess.run([sys.executable, '-I', '-B', v2, '--run', '--csv', csv_in,
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
