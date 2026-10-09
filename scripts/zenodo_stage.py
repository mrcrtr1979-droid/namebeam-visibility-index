#!/usr/bin/env python3
"""Stage the Edition pack for a Zenodo deposit (loop S1b-4). Nothing is sent to Zenodo.

Writes, into --out, three files that a signed-in person uploads or pastes:
  edition-1.zip              the pack folder as one zip, with a single top-level folder edition-1/
  zenodo_metadata.json       the deposit metadata in the Zenodo REST format (upload_type dataset)
  zenodo_STAGE_REPORT.txt    sha256 and size of the zip, the file count, the checks that passed

Numbers in the description are read from PACK.json. A DOI is permanent once Zenodo publishes, so this
script stops at a staged draft: the publish click stays with the account owner. The pack is checked
with its own verify.py first; a pack that does not verify is not zipped.

    python3 -I scripts/zenodo_stage.py --pack releases/edition-1 --out DIR [--version 1.0.0] [--publication-date 2026-10-15]
    python3 -I scripts/zenodo_stage.py --selftest
Standard library only.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
HF = 'https://huggingface.co/datasets/Namebeam/ai-visibility-daily-record'
REPO = 'https://github.com/mrcrtr1979-droid/namebeam-visibility-index'
SKIP_DIRS = {'.git', '__pycache__'}


def metadata(pack, version, pub_date):
    w0, w1 = pack['window']
    desc = ('<p>Dated record of which businesses AI answer engines name for local questions in US markets, with the '
            'sources the engines returned. Edition 1 covers %s to %s (%d run days).</p>'
            '<p>The pack holds %d answer rows (one per raw answer file, with the names each engine returned and the '
            'SHA-256 of the raw file), %d source URL rows, %d rows from a Siri panel run by hand, and a metrics table '
            'that gives the denominator of every figure. Perplexity, OpenAI, Anthropic and Gemini were called through '
            'their programming interfaces, and Google results pages were read.</p>'
            '<p>Every file is listed in MANIFEST.sha256 and verify.py checks the pack. The method note in README.md '
            'states the settings of every engine, the windows and the denominators. The same files are on Hugging Face '
            'and the daily raw files are in the GitHub repository.</p>'
            % (w0, w1, pack['run_days'], pack['answers_rows'], pack['sources_rows'], pack['siri_rows']))
    return {'metadata': {
        'upload_type': 'dataset',
        'title': 'Namebeam AI Visibility Record, Edition 1 (%s to %s)' % (w0, w1),
        'publication_date': pub_date,
        'description': desc,
        'creators': [{'name': 'Carter Enterprise LLC (Namebeam)'}],
        'access_right': 'open',
        'license': 'cc-by-4.0',
        'version': version,
        'language': 'eng',
        'keywords': ['AI answer engines', 'local search', 'AI search citations', 'dated record', 'open data'],
        'related_identifiers': [
            {'relation': 'isIdenticalTo', 'identifier': HF, 'resource_type': 'dataset'},
            {'relation': 'isSupplementedBy', 'identifier': REPO, 'resource_type': 'software'},
        ],
    }}


def problems(meta):
    out = []
    m = meta['metadata']
    for k in ('upload_type', 'title', 'publication_date', 'description', 'creators', 'access_right', 'license'):
        if not m.get(k):
            out.append('missing ' + k)
    text = json.dumps(meta, ensure_ascii=False)
    if any(ord(c) > 127 for c in text):
        out.append('non-ASCII character')
    low = text.lower()
    for w in ('honest', 'alpha vault', 'carter studio', 'unmatched', 'best in class', ' first ', ' only '):
        if w in low:
            out.append('banned word: ' + w.strip())
    return out


def pack_files(pack_dir):
    files = []
    for root, dirs, names in os.walk(pack_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for n in sorted(names):
            files.append(os.path.join(root, n))
    return files


def make_zip(pack_dir, zip_path, top='edition-1'):
    files = pack_files(pack_dir)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in files:
            rel = os.path.relpath(f, pack_dir).replace(os.sep, '/')
            zi = zipfile.ZipInfo('%s/%s' % (top, rel), date_time=(2026, 10, 14, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            with open(f, 'rb') as fh:
                z.writestr(zi, fh.read())
    return len(files)


def check_zip(zip_path, top='edition-1'):
    """R28 zip law: one top-level folder, no absolute or parent paths."""
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        bad = z.testzip()
    roots = {n.split('/')[0] for n in names}
    errs = []
    if roots != {top}:
        errs.append('top-level entries are %s, expected only %s' % (sorted(roots), top))
    if any(n.startswith('/') or '..' in n.split('/') for n in names):
        errs.append('absolute or parent path in zip')
    if bad:
        errs.append('corrupt member ' + bad)
    return errs, len(names)


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def stage(pack_dir, out_dir, version, pub_date, run_verify=True):
    with open(os.path.join(pack_dir, 'PACK.json'), encoding='utf-8') as fh:
        pack = json.load(fh)
    if run_verify:
        r = subprocess.run([sys.executable, '-I', os.path.join(pack_dir, 'verify.py')], capture_output=True, text=True)
        if r.returncode != 0:
            print((r.stdout + r.stderr).strip()[-600:])
            print('NOT STAGED: the pack does not verify')
            return 2
    meta = metadata(pack, version, pub_date)
    ps = problems(meta)
    if ps:
        print('NOT STAGED: metadata problems: ' + '; '.join(ps))
        return 2
    os.makedirs(out_dir, exist_ok=True)
    zp = os.path.join(out_dir, 'edition-1.zip')
    n = make_zip(pack_dir, zp)
    errs, members = check_zip(zp)
    if errs:
        print('NOT STAGED: ' + '; '.join(errs))
        return 2
    with open(os.path.join(out_dir, 'zenodo_metadata.json'), 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=True)
        fh.write('\n')
    rep = ['Zenodo stage report (nothing sent to Zenodo)',
           'pack status: %s, window %s to %s, %d run days' % (pack['status'], pack['window'][0], pack['window'][1], pack['run_days']),
           'zip: edition-1.zip, %d bytes, sha256 %s' % (os.path.getsize(zp), sha256_file(zp)),
           'zip members: %d (pack files: %d), single top-level folder edition-1/ (R28 zip law)' % (members, n),
           'pack verify.py: %s' % ('PASS' if run_verify else 'SKIPPED'),
           'metadata problems: none',
           'publish: the account owner clicks Publish; the DOI is permanent after that']
    with open(os.path.join(out_dir, 'zenodo_STAGE_REPORT.txt'), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(rep) + '\n')
    print('\n'.join(rep))
    return 0


def selftest():
    ok = True

    def check(label, cond):
        nonlocal ok
        print('%s  %s' % ('GREEN' if cond else 'RED  ', label))
        ok = ok and bool(cond)

    pack = {'window': ['2026-09-01', '2026-10-13'], 'run_days': 41, 'answers_rows': 12345, 'sources_rows': 40000,
            'siri_rows': 28, 'status': 'FINAL'}
    m = metadata(pack, '1.0.0', '2026-10-15')
    check('no problems on a normal build', problems(m) == [])
    check('numbers from PACK in title and description',
          '2026-09-01 to 2026-10-13' in m['metadata']['title'] and '12345 answer rows' in m['metadata']['description'])
    check('license cc-by-4.0, open access, dataset', m['metadata']['license'] == 'cc-by-4.0'
          and m['metadata']['access_right'] == 'open' and m['metadata']['upload_type'] == 'dataset')
    bad = json.loads(json.dumps(m))
    bad['metadata']['description'] += ' The only record of its kind — honest.'
    ps = problems(bad)
    check('RED PROOF: non-ASCII dash, "only" and "honest" are flagged',
          any('non-ASCII' in p for p in ps) and any('only' in p for p in ps) and any('honest' in p for p in ps))
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, 'pk')
        os.makedirs(os.path.join(src, 'data'))
        os.makedirs(os.path.join(src, 'hf'))
        for rel in ('README.md', 'data/a.csv', 'hf/README.md'):
            with open(os.path.join(src, rel), 'w') as fh:
                fh.write('x\n')
        zp = os.path.join(d, 'e.zip')
        n = make_zip(src, zp)
        errs, members = check_zip(zp)
        check('zip has one top-level folder and carries every pack file (3 files, hf/ included because the manifest lists it)', errs == [] and n == 3 and members == 3)
        with zipfile.ZipFile(os.path.join(d, 'bad.zip'), 'w') as z:
            z.writestr('README.md', 'x')
            z.writestr('edition-1/a', 'x')
        errs, _ = check_zip(os.path.join(d, 'bad.zip'))
        check('RED PROOF: a zip with a loose root file is flagged', any('top-level' in e for e in errs))
    print('SELFTEST %s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--pack', default=os.path.join(os.path.dirname(HERE), 'releases', 'edition-1'))
    ap.add_argument('--out')
    ap.add_argument('--version', default='1.0.0')
    ap.add_argument('--publication-date', default='2026-10-15')
    ap.add_argument('--skip-verify', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.out:
        ap.error('--out is required')
    return stage(a.pack, a.out, a.version, a.publication_date, run_verify=not a.skip_verify)


if __name__ == '__main__':
    sys.exit(main())
