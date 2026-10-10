#!/usr/bin/env python3
"""Assemble the --method-src tree for scripts/build_edition.py, from the method copy already in the repo.

    python3 -I scripts/method_src.py --out DIR          # writes DIR/status/..., checks all 7 hashes, prints OK or stops
    python3 -I scripts/method_src.py --selftest

Why: build_edition.py patches one default path in gen_citation_index.py while vendoring. The copy under
releases/edition-1/method is already patched (SHA-256 b8afb215...), so giving it back as --method-src makes the build
stop with "patch target not found". This script takes the vendored files, undoes that one documented patch, and checks
every file against the hash of the original in the Brain (read 2026-10-09 from Brain_Kit by [SONNET-RECORD-1009D-S1b]).
Any other difference stops it. Standard library only. Writes only inside --out.
"""
import argparse
import hashlib
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
VENDORED = os.path.join(REPO, 'releases', 'edition-1', 'method', 'Brain_Kit')

# original files in the Brain, SHA-256 read 2026-10-09 (device sha256sum)
PINNED = {
    'status/staged/1008E_S1_METRICS/receipts_metrics_v2.py': '867737213d1f515f5c478b16975a75c9da7944ba8f4b7d9160f19dc4418174fb',
    'status/staged/1005B_METRICS/receipts_metrics_v1.py': '07c26c278e1a5169087d2c822fc20638f1e2b8e035f76706ed12e68d05482d86',
    'status/staged/1005B_METRICS/metrics_v1_sept.csv': '58abfebd7ab33e15c35bbd91401ee22f69043672795138c386197045ff69e23a',
    'status/staged/1005A_AI_PICKS_CHART/gen_ai_picks_chart.py': '611894c48c21a512bc856463c2e4b6b7a33155375aebcce71a036e820a4edde6',
    'status/staged/0929C_ACTOR/gen_citation_index.py': 'bb23288c8080ea35a29bf5ae08ac0eff2350eaa831223eb5f7c650a14e772231',
    'status/staged/0929B_MARKET_PANELS/gen_market_panels.py': 'f8765bd6c808c6761a4f3892c3de5d957a77d1cdffdaf9c466cbeaf78e8581fd',
    'status/returns/COMET_1004/SERP_DRIFT_QUARANTINE_2026-10-04.csv': '0f5946f01e69911851c91e45f7b4dcc2681e56e957c19b8bb3104090de3d4011',
}
UNPATCH = {'status/staged/0929C_ACTOR/gen_citation_index.py': ('"Brain_Kit/"', '"/mnt/user-data/uploads/' + 'Alpha' + ' Vault/Carter Enterprise LLC/Brain_Kit/"')}


def sha(b):
    return hashlib.sha256(b).hexdigest()


def assemble(out, src=VENDORED, pinned=PINNED):
    bad = []
    for rel, want in sorted(pinned.items()):
        raw = open(os.path.join(src, *rel.split('/')), 'rb').read()
        if rel in UNPATCH:
            new, old = UNPATCH[rel]
            t = raw.decode('utf-8')
            if t.count(new) != 1:
                bad.append('%s: patched string found %d times, want 1' % (rel, t.count(new)))
                continue
            raw = t.replace(new, old).encode('utf-8')
        got = sha(raw)
        if got != want:
            bad.append('%s: sha256 %s, want %s' % (rel, got[:16], want[:16]))
            continue
        d = os.path.join(out, *rel.split('/'))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        open(d, 'wb').write(raw)
    return bad


def selftest():
    fails = []

    def chk(name, ok):
        print(('  ok   ' if ok else '  FAIL ') + name)
        if not ok:
            fails.append(name)

    out = tempfile.mkdtemp()
    bad = assemble(out)
    chk('the repo copy rebuilds all 7 originals with matching hashes (%s)' % '; '.join(bad), not bad)
    g = os.path.join(out, 'status', 'staged', '0929C_ACTOR', 'gen_citation_index.py')
    chk('gen_citation_index.py is the original bb23288c', os.path.exists(g) and sha(open(g, 'rb').read()).startswith('bb23288c'))
    # RED: a planted changed byte in one vendored file is caught
    fake = tempfile.mkdtemp()
    shutil.copytree(VENDORED, os.path.join(fake, 'B'))
    p = os.path.join(fake, 'B', 'status', 'staged', '1005B_METRICS', 'metrics_v1_sept.csv')
    open(p, 'ab').write(b'\n')
    bad = assemble(tempfile.mkdtemp(), os.path.join(fake, 'B'))
    chk('RED: one changed byte stops it (%d bad)' % len(bad), len(bad) == 1 and 'metrics_v1_sept.csv' in bad[0])
    # RED: giving the already unpatched original (or a twice patched file) is caught by the patch count
    g2 = os.path.join(fake, 'B', 'status', 'staged', '0929C_ACTOR', 'gen_citation_index.py')
    open(g2, 'wb').write(open(g, 'rb').read())
    bad = assemble(tempfile.mkdtemp(), os.path.join(fake, 'B'))
    chk('RED: an unpatched source file is reported, not silently passed', any('gen_citation_index.py' in b for b in bad))
    print('SELFTEST ' + ('FAIL: ' + ', '.join(fails) if fails else 'PASS'))
    return not fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if not a.out:
        ap.print_help()
        return 1
    if os.path.exists(a.out) and os.listdir(a.out):
        print('STOP: --out must be empty or absent')
        return 1
    bad = assemble(a.out)
    for b in bad:
        print('BAD  ' + b)
    print('METHOD SRC ' + ('OK (7 files, hashes pinned)' if not bad else 'STOP') + ' ' + os.path.abspath(a.out))
    return 0 if not bad else 1


if __name__ == '__main__':
    sys.exit(main())
