#!/usr/bin/env python3
"""Byte scan of the text we wrote, before anything is published (Edition 1 runbook step 5).

    python3 -I scripts/byte_scan.py PATH [PATH ...] [--strict] [--include-data]
    python3 -I scripts/byte_scan.py --selftest

HARD (exit 1): em dash U+2014, en dash U+2013, emoji, the retired brand names, the banned trust word (TRUST_WORD below, any form).
REVIEW (printed with file:line; exit 1 only with --strict): the words "first" and "only". A word scan cannot tell a
claim from plain use, so a person reads each REVIEW line before release.
Scope: every text file under PATH (.md .txt .py .json .csv .sha256 .jsonld .html .yml). Files under a data/ folder are
engine output kept as the engines wrote it (the record is never edited), so they are skipped unless --include-data;
the skip count is printed. Standard library only. Reads files; writes nothing.
"""
import os
import re
import sys
import tempfile

sys.dont_write_bytecode = True
TEXT_EXT = ('.md', '.txt', '.py', '.json', '.csv', '.sha256', '.jsonld', '.html', '.yml', '.yaml')
RETIRED = ('alpha' + ' vault', 'carter' + ' studio')     # split so this file passes its own scan
EMOJI = re.compile('[' + chr(0x1F000) + '-' + chr(0x1FAFF) + chr(0x2600) + '-' + chr(0x27BF) + chr(0xFE0F) + chr(0x200D) + ']')
TRUST_WORD = 'hon' + 'est'
TRUST_RE = re.compile(r'\b' + TRUST_WORD, re.I)
EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)
REVIEW = re.compile(r'\b(first|only)\b', re.I)


def scan_text(text):
    """Returns (hard, review): lists of (line_no, rule, snippet)."""
    hard, review = [], []
    for i, line in enumerate(text.splitlines(), 1):
        low = line.casefold()
        snip = line.strip()[:120]
        if EM_DASH in line:
            hard.append((i, 'em dash', snip))
        if EN_DASH in line:
            hard.append((i, 'en dash', snip))
        if EMOJI.search(line):
            hard.append((i, 'emoji', snip))
        for r in RETIRED:
            if r in low:
                hard.append((i, 'retired name', snip))
        if TRUST_RE.search(line):
            hard.append((i, 'banned word', snip))
        if REVIEW.search(line):
            review.append((i, REVIEW.search(line).group(1).lower(), snip))
    return hard, review


def walk(paths, include_data, include_vendored=False):
    skipped = 0
    walk.vendored = 0
    for p in paths:
        if os.path.isfile(p):
            yield p
            continue
        for root, dirs, files in os.walk(p):
            dirs[:] = sorted(d for d in dirs if d not in ('.git', '__pycache__'))
            parts = os.path.relpath(root, p).replace(os.sep, '/').split('/')
            for f in sorted(files):
                if not f.endswith(TEXT_EXT):
                    continue
                if ('data' in parts or (f.startswith('panel_') and f.endswith('.csv'))) and not include_data:
                    skipped += 1
                    continue
                if 'method' in parts and not include_vendored:
                    walk.vendored = getattr(walk, 'vendored', 0) + 1
                    continue
                yield os.path.join(root, f)
    walk.skipped = skipped


def run(paths, strict=False, include_data=False, out=print, include_vendored=False):
    nh = nr = nf = 0
    for f in walk(paths, include_data, include_vendored):
        nf += 1
        try:
            text = open(f, encoding='utf-8').read()
        except UnicodeDecodeError:
            out('HARD   %s: not UTF-8' % f)
            nh += 1
            continue
        hard, review = scan_text(text)
        for i, rule, snip in hard:
            out('HARD   %s:%d %s | %s' % (f, i, rule, snip))
        for i, rule, snip in review:
            out('REVIEW %s:%d %s | %s' % (f, i, rule, snip))
        nh += len(hard)
        nr += len(review)
    skipped = getattr(walk, 'skipped', 0)
    ok = nh == 0 and (nr == 0 or not strict)
    out('BYTE SCAN %s: %d files, %d hard, %d review, %d engine-output files skipped (kept as written), '
        '%d vendored method files skipped (hash-pinned copies)' % ('CLEAN' if ok else 'RED', nf, nh, nr, skipped, walk.vendored))
    return ok


def selftest():
    fails = []

    def chk(name, ok):
        print(('  ok   ' if ok else '  FAIL ') + name)
        if not ok:
            fails.append(name)

    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, 'data'))

    def put(rel, text):
        p = os.path.join(tmp, *rel.split('/'))
        open(p, 'w', encoding='utf-8').write(text)
        return p

    clean = put('clean.md', 'Window 2026-09-01 to 2026-10-13. Answer rows: 18,000.\n')
    chk('clean file passes', run([clean], out=lambda s: None))
    planted = {'em.md': 'a ' + EM_DASH + ' b\n', 'en.md': '1' + EN_DASH + '2\n', 'emoji.md': 'done ' + chr(0x1F680) + '\n',
               'av.md': 'from ' + 'Alpha' + ' Vault\n', 'cs.md': 'CARTER' + ' STUDIO\n', 'h.md': 'an ' + TRUST_WORD.title() + ' ladder\n',
               'h2.md': TRUST_WORD + 'ly\n'}
    for name, text in planted.items():
        p = put(name, text)
        chk('RED: planted %s is caught' % name, not run([p], out=lambda s: None))
    rv = put('rv.md', 'The first record of its kind.\n')
    chk('REVIEW word reported but not failing by default', run([rv], out=lambda s: None))
    chk('RED: REVIEW word fails with --strict', not run([rv], strict=True, out=lambda s: None))
    lines = []
    run([rv], out=lines.append)
    chk('REVIEW line names file, line and word', any(l.startswith('REVIEW') and ':1 first' in l for l in lines))
    d = put('data/answers.csv', 'name\nKey Feature ' + EN_DASH + ' Rentalizer\nMy ' + TRUST_WORD.title() + ' Take\n')
    sub = tempfile.mkdtemp()
    os.makedirs(os.path.join(sub, 'data'))
    open(os.path.join(sub, 'data', 'a.csv'), 'w', encoding='utf-8').write(open(d, encoding='utf-8').read())
    lines = []
    chk('data/ engine output is skipped and counted', run([sub], out=lines.append) and '1 engine-output files skipped' in lines[-1])
    chk('RED: --include-data scans it', not run([sub], include_data=True, out=lambda s: None))
    v = tempfile.mkdtemp()
    os.makedirs(os.path.join(v, 'method'))
    os.makedirs(os.path.join(v, 'metrics'))
    open(os.path.join(v, 'method', 'm.py'), 'w', encoding='utf-8').write('x = "' + EM_DASH + '"\n')
    open(os.path.join(v, 'metrics', 'panel_a.csv'), 'w', encoding='utf-8').write('n\nMy ' + TRUST_WORD.title() + ' Take\n')
    open(os.path.join(v, 'metrics', 'metrics_v2_sentences.md'), 'w', encoding='utf-8').write('A sentence ' + EM_DASH + ' here.\n')
    lines = []
    ok = run([v], out=lines.append)
    chk('RED: our own metrics sentence file is still scanned (panel and method skipped, counted)',
        not ok and '1 engine-output files skipped' in lines[-1] and '1 vendored method files skipped' in lines[-1])
    w = tempfile.mkdtemp()
    os.makedirs(os.path.join(w, 'method'))
    open(os.path.join(w, 'method', 'm.py'), 'w', encoding='utf-8').write('x = "' + EM_DASH + '"\n')
    chk('vendored method code alone is skipped by default', run([w], out=lambda s: None))
    chk('RED: --include-vendored scans the method code', not run([w], include_vendored=True, out=lambda s: None))
    chk('this scanner passes its own scan', run([os.path.abspath(__file__)], out=lambda s: None))
    print('SELFTEST ' + ('FAIL: ' + ', '.join(fails) if fails else 'PASS'))
    return not fails


def main():
    a = sys.argv[1:]
    if '--selftest' in a:
        return 0 if selftest() else 1
    paths = [x for x in a if not x.startswith('--')]
    if not paths:
        print(__doc__)
        return 1
    return 0 if run(paths, '--strict' in a, '--include-data' in a, include_vendored='--include-vendored' in a) else 1


if __name__ == '__main__':
    sys.exit(main())
