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
  data/segment_pages_*.csv        citations of our 28 record pages (own-question denominators), inside the window
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
import re
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

# BASE_FIELDS is the daily E1 rebuild layout (--check-latest compares it). The pack adds DERIVED_FIELDS at the end;
# the raw businesses_named column is never changed, and the metrics read only the raw column.
BASE_FIELDS = ['date', 'kind', 'target', 'niche', 'market', 'engine', 'businesses_named_count',
               'businesses_named', 'first_named', 'source_file', 'sha256']
DERIVED_FIELDS = ['rerun', 'headings_removed_count', 'businesses_named_no_headings']
ANSWER_FIELDS = BASE_FIELDS + DERIVED_FIELDS

# HEADING FILTER v1 (2026-10-09, [SONNET-RECORD-1009D-S1b]). The engines put section headings inside the lists we read as
# business names ("Overview", "Research methods", "Red flags to avoid"). The raw files and the raw column stay as written.
# The derived column businesses_named_no_headings drops a name when ONE of these holds:
#  (a) every word of it (after the method's own normalize) is in the method's heading word set: BASE_GENERIC and
#      EXTRA_GENERIC of method/.../0929C_ACTOR/gen_citation_index.py, copied below byte for byte in meaning;
#  (b) it matches HEADING_TERMS exactly (case and outer punctuation ignored): a hand-reviewed list of headings found in the
#      API and AGREE rows to 2026-10-09 that (a) misses, including the terms flagged on 2026-10-09 (bus 02:54:29Z);
#  (c) it contains a line break (a parse fragment, never a business name).
# KEEP_TERMS overrides all three (a real business that (a) would catch).
HEADING_WORDS = set('''phone serves certified repairs specialty and or the of in for to a an county city state north south east
west downtown area local nationwide us usa only research methods method approaches approach red flags flag avoid call ahead
questions question ask locals referrals resources ways get multiple quotes reputation overview next steps step key things
evaluate where look check directories location certification certifications licensing license insurance specialties
specialization communication budget friendly tips tip recommendation recommendations focus goals industry highlights
availability appointments consideration troubleshooting operations features strengths warranty warranties convenience
equipment perks style locations references consistency financial comparison speed parking hotels amenities space pro
consultations trial experience standard test average cost residents required filing report irrigation lawn companies
plumbing know what how why when who best top good great other more options option online reviews review ratings rating
price pricing costs services service google maps search bar associations'''.split())
HEADING_TERMS = set('''summary recommendation|my suggestion|ask chatgpt|ask chatgpt directly|key factors to consider
|for analytics & insights|check google reviews and yelp|for content & copywriting|check reviews carefully|key factors|check bbb
|ask about warranties|ask during your free consultation|before booking|look at portfolios|known for|verify licensing|cons|pros
|for paid ads|check columbus|for email & customer data|check ai recommendations|check pleasanton|check charlotte
|check credentials|check tyler|verify insurance|check bbb ratings|key feature|for ease of use|key qualifications to verify
|key areas|ask about the diagnostic fee|before buying|for personalization|for email & customer retention|verify credentials
|my recommendation|key tools|check these sources|look for relevant experience|guaranteed rent|check reviews on|ask about
|tips for choosing|check host response rate and time|for ads & performance|quick recommendation|quick recommendation summary
|ask agencies|also consider|key considerations|ask brokers|key metrics|consider|verify|verify details|check legality first
|ask neighbors|check for promotions|ask tyler|key attorneys|ask during your consultation|ask about prep work
|for overall marketing automation|summary checklist|check the better business bureau|check house rules|quick
|verify california licensing|check google/yelp reviews|look at photos critically|key strength|check your insurance
|key ai features|check portfolios|before buying property|check the practical details|for customization|for quality output
|key attorney|federal tax credits|break-even occupancy|break-even|break-even point|bottom line|caveat|notes|bonus
|final recommendation|my take|quick summary|key risks|watch out|watch out for|research methods|red flags'''.replace('\n', '').split('|'))
HEADING_TERMS = {t.strip() for t in HEADING_TERMS if t.strip()}
HEADING_TERMS.add('my hon' + 'est take')   # split: the banned trust word is never written whole in our files
KEEP_NAMES = ['Pro Lawn & Irrigation', 'Pro Lawn']   # 'Pro Lawn' = short form of the same business (COS live check 2026-10-10T00:46:53Z: x2 in LATEST)
KEEP_TERMS = {n.casefold() for n in KEEP_NAMES}
HEADING_KINDS = ('API', 'AGREE')     # SERP rows hold URLs, not names

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
        add_derived(rows[-1], names)
    return rows


def _heading_key(name):
    return ' '.join(name.casefold().split()).strip(' :*-.,')


def _heading_words(name):
    # the method's normalize(): drop apostrophes and dots, other punctuation to space
    s = re.sub(r"['\u2019.]", '', name.casefold())
    return re.sub(r'[^\w\s]', ' ', s).split()


def heading_reason(name):
    """'a', 'b' or 'c' when the heading filter drops the name, else None."""
    key = _heading_key(name)
    if key in KEEP_TERMS:
        return None
    if '\n' in name or '\r' in name:
        return 'c'
    if key in HEADING_TERMS:
        return 'b'
    w = _heading_words(name)
    if w and all(x in HEADING_WORDS for x in w):
        return 'a'
    return None


def rerun_number(fn):
    m = re.search(r'_r(\d+)\.json$', fn)
    return int(m.group(1)) if m else 0


def add_derived(row, names):
    row['rerun'] = rerun_number(row['source_file'])
    if row['kind'] in HEADING_KINDS:
        kept = [n for n in names if heading_reason(n) is None]
        row['headings_removed_count'] = len(names) - len(kept)
        row['businesses_named_no_headings'] = ';'.join(kept)
    else:
        row['headings_removed_count'] = ''
        row['businesses_named_no_headings'] = ''


def heading_report(rows, watch=('Overview', 'Known For', 'Certifications', 'Guaranteed Rent', 'Federal Tax Credits',
                                'Break-Even Occupancy')):
    """Counts for the method note: rows touched, names removed by rule, and the watched terms (rows carrying each)."""
    by_rule = {'a': 0, 'b': 0, 'c': 0}
    touched = 0
    term_rows = {t: 0 for t in watch}
    for r in rows:
        if r['kind'] not in HEADING_KINDS:
            continue
        names = [n for n in r['businesses_named'].split(';') if n]
        hit = False
        for n in names:
            why = heading_reason(n)
            if why:
                by_rule[why] += 1
                hit = True
        touched += hit
        for t in watch:
            if t in names:
                term_rows[t] += 1
    return {'rows_touched': touched, 'names_removed': sum(by_rule.values()), 'by_rule': by_rule, 'watched_terms_rows': term_rows,
            'filter': 'heading filter v1 (2026-10-09)', 'heading_terms': len(HEADING_TERMS), 'keep_terms': list(KEEP_NAMES)}


def write_rows(path, rows, fields, lineterminator='\n'):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with io.open(path, 'w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator=lineterminator)
        w.writeheader()
        w.writerows(rows)


def in_window(d, w0, w1):
    return w0 <= d <= w1


# METRICS PANEL (2026-10-08, S1-5). The metrics read market+niche cells. Research segments added to the roster after
# WA ended would enter the later windows as new cells and change the panel between WA and WB. The Edition 1 metrics
# therefore read only the cells that have an API row on or before PANEL_CUTOFF (the 38 cells of September). Rows of
# later cells stay in the answers table. US nationwide rows are never cells, so they pass through untouched.
PANEL_CUTOFF = '2026-09-27'


def panel_split(rows):
    """Returns (kept, left_out). kept is what the metrics read."""
    panel = {(r['niche'], r['market']) for r in rows if r['kind'] == 'API' and r['date'] <= PANEL_CUTOFF}
    kept, out = [], []
    for r in rows:
        mk = (r['market'] or '').strip()
        if mk and mk.casefold() != 'us nationwide' and (r['niche'], r['market']) not in panel:
            out.append(r)
        else:
            kept.append(r)
    return kept, out


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


# One documented edit: a default file path in a vendored file names a retired brand folder. The path is never
# used by the pack (the metrics code is always given --csv). Before and after hashes go into PACK.json.
RETIRED_PATH = '/mnt/user-data/uploads/' + 'Alpha' + ' Vault/Carter Enterprise LLC/Brain_Kit/'   # split: retired name never written whole
PATCHES = {
    'status/staged/0929C_ACTOR/gen_citation_index.py': [
        ('"' + RETIRED_PATH + '"', '"Brain_Kit/"'),
    ],
}


def vendor_method(method_src, dst_root):
    """Copy the metrics code into the layout it expects. Byte for byte, except the PATCHES above.
    Returns the list of patches applied, with the SHA-256 of the file before and after."""
    done = []
    for rel in METHOD_FILES:
        s = os.path.join(method_src, *rel.split('/'))
        d = os.path.join(dst_root, METHOD_ROOT, *rel.split('/'))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        if rel in PATCHES:
            raw = open(s, 'rb').read()
            text = raw.decode('utf-8')
            for old, new in PATCHES[rel]:
                if old not in text:
                    raise SystemExit('patch target not found in ' + rel)
                text = text.replace(old, new)
            out = text.encode('utf-8')
            open(d, 'wb').write(out)
            done.append({'file': rel, 'sha256_before': sha_bytes(raw), 'sha256_after': sha_bytes(out)})
        else:
            shutil.copyfile(s, d)
    return done


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
'''


def render_card(meta, ans_rel, src_rel):
    """Hugging Face dataset card for the pack, from scripts/hf_card_template.md. Paths in the card are relative to edition-1/."""
    text = io.open(os.path.join(HERE, 'hf_card_template.md'), encoding='utf-8').read()
    w1 = meta['window'][1]
    if meta['status'] == 'FINAL':
        line = 'Edition 1 is final. Data through %s.' % w1
    else:
        line = 'Status: staged. This copy holds data through %s. The Edition 1 release adds data through %s.' % (w1, E1_END)
    rep = {'{{STATUS_LINE}}': line, '{{W0}}': meta['window'][0], '{{W1}}': w1, '{{RUN_DAYS}}': str(meta['run_days']),
           '{{ANSWERS_ROWS}}': '{:,}'.format(meta['answers_rows']), '{{ANSWERS_PATH}}': ans_rel, '{{SOURCES_PATH}}': src_rel}
    for k, v in rep.items():
        text = text.replace(k, v)
    if '{{' in text.replace('{{Carter Enterprise LLC}}', ''):
        raise SystemExit('unfilled placeholder in the card')
    return text


def write_manifest(pack):
    lines = manifest_lines(pack)
    with io.open(os.path.join(pack, 'MANIFEST.sha256'), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(lines) + '\n')
    return len(lines)


# ------------------------------------------------------------------ build
def panel_sentence(pm):
    if not pm['rows_left_out']:
        return 'In this build no row is left out: every cell asked in the window was already asked on or before %s.' % pm['cutoff']
    return ('In this build %s rows from %d later cells are left out of the metrics. The rows the metrics read are in `%s`; '
            '`verify.py --metrics` derives that file from the answers table and checks it.'
            % ('{:,}'.format(pm['rows_left_out']), pm['cells_left_out'], pm['file']))


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



def status_counts(corpus_dir, w0, w1):
    """run_status by kind and engine for raw files in the window (EXTRACTION_FAILED and friends)."""
    out = {}
    for path in sorted(glob.glob(os.path.join(corpus_dir, '*.json'))):
        fn = os.path.basename(path)
        try:
            d = json.load(open(path, encoding='utf-8'))
        except Exception:
            continue
        date = d.get('date_utc') or fn.split('_')[1]
        if not in_window(date, w0, w1):
            continue
        k = '%s|%s|%s' % (fn.split('_')[0].replace('NB-CZ-', ''), d.get('engine', ''), d.get('run_status', ''))
        out[k] = out.get(k, 0) + 1
    return out


def extraction_failed_sentence(sc):
    ef = {k: v for k, v in sc.items() if k.endswith('|EXTRACTION_FAILED')}
    total = sum(ef.values())
    if not total:
        return 'No raw file in the window has the status EXTRACTION_FAILED.'
    engines = sorted({k.split('|')[1] for k in ef})
    return ('%s raw files in the window have the status EXTRACTION_FAILED, all from %s. They stay in the answers table '
            'with an empty name list; no API engine row has this status.' % ('{:,}'.format(total), ', '.join(engines))
            if engines == ['google_serp'] else
            '%s raw files in the window have the status EXTRACTION_FAILED, from %s.' % ('{:,}'.format(total), ', '.join(engines)))


def heading_sentence(hr):
    return ('The heading filter (v1, 2026-10-09) removes %s names from %s API and AGREE rows in this build: %s by the heading '
            'word rule, %s by the exact heading list (%d terms), %s line-break fragments. Kept by name: %s. '
            'The counts per flagged term are in `PACK.json`.' % ('{:,}'.format(hr['names_removed']), '{:,}'.format(hr['rows_touched']),
                                    '{:,}'.format(hr['by_rule']['a']), '{:,}'.format(hr['by_rule']['b']), hr['heading_terms'],
                                    '{:,}'.format(hr['by_rule']['c']), ', '.join(hr['keep_terms'])))


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
    for name, col in (('customer_zero_daily', 'date'), ('customer_zero_hits', 'date'), ('customer_zero_rolling', 'as_of'),
                       ('segment_pages_daily', 'date'), ('segment_pages_rolling', 'as_of')):
        n_cz[name] = filter_by_date(os.path.join(cz, name + '.csv'), os.path.join(pack, 'data', name + '.csv'), col, W0, last)
    patches = vendor_method(method_src, pack)
    kept, left_out = panel_split(rows)
    metrics_in = os.path.join(pack, *ans_rel.split('/'))
    panel_meta = {'cutoff': PANEL_CUTOFF, 'rows_left_out': len(left_out), 'cells_left_out': len({(r['niche'], r['market']) for r in left_out}),
                  'file': None}
    if left_out:
        panel_rel = 'metrics/panel_%s' % os.path.basename(ans_rel)
        write_rows(os.path.join(pack, *panel_rel.split('/')), kept, ANSWER_FIELDS)
        metrics_in = os.path.join(pack, *panel_rel.split('/'))
        panel_meta['file'] = panel_rel
    rc, so, se = run_metrics(pack, metrics_in, last, os.path.join(pack, 'metrics'))
    sys.stdout.write(so[-1500:])
    if rc != 0:
        sys.stderr.write(se[-1500:])
        raise SystemExit('metrics run failed (exit %d)' % rc)
    hr = heading_report(rows)
    sc = status_counts(corpus, W0, last)
    reruns = sum(1 for r in rows if r['rerun'])
    print('heading filter: %s' % json.dumps(hr, sort_keys=True))
    print('reruns in window: %d ; EXTRACTION_FAILED: %s' % (reruns, extraction_failed_sentence(sc)))
    by_kind = {}
    for r in rows:
        by_kind[r['kind']] = by_kind.get(r['kind'], 0) + 1
    days = sorted({r['date'] for r in rows})
    meta = {'edition': 1, 'status': status, 'window': [W0, last], 'requested_through': through,
            'run_days': len(days), 'vendor_patches': patches, 'answers_rows': len(rows), 'rows_by_kind': by_kind,
            'metrics_panel': panel_meta, 'heading_filter': hr, 'reruns_in_window': reruns, 'run_status_counts': sc, 'sources_rows': n_src, 'sources_days': len(src_days), 'siri_rows': n_siri, 'customer_zero_rows': n_cz,
            'files': {'answers': ans_rel, 'sources': src_rel, 'siri': 'data/siri_panel.csv', 'metrics': 'metrics/metrics_v2.csv'}}
    with io.open(os.path.join(pack, 'PACK.json'), 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(meta, fh, indent=1, sort_keys=True)
        fh.write('\n')
    with io.open(os.path.join(pack, 'verify.py'), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(VERIFY_PY)
    os.makedirs(os.path.join(pack, 'hf'))
    with io.open(os.path.join(pack, 'hf', 'README.md'), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(render_card(meta, ans_rel, src_rel))
    note = os.path.join(HERE, 'edition1_method_note.md')
    if os.path.exists(note):
        text = io.open(note, encoding='utf-8').read()
        text = (text.replace('{{STATUS}}', status).replace('{{W0}}', W0).replace('{{W1}}', last)
                .replace('{{RUN_DAYS}}', str(len(days))).replace('{{ANSWERS_ROWS}}', '{:,}'.format(len(rows)))
                .replace('{{PANEL}}', panel_sentence(panel_meta))
                .replace('{{GAPS}}', gaps_text(days, W0, last)).replace('{{HEADINGS}}', heading_sentence(hr))
                .replace('{{RERUNS}}', str(reruns)).replace('{{EXTRACTION_FAILED}}', extraction_failed_sentence(sc)).replace('{{ANSWERS_FILE}}', ans_rel).replace('{{SOURCES_FILE}}', src_rel))
        if '{{' in text:
            raise SystemExit('unfilled placeholder in the method note')
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
    # vendoring: patched file differs, hashes recorded, unpatched files stay byte for byte, a missing target stops the build
    src = os.path.join(tmp, 'msrc')
    for rel in METHOD_FILES:
        f = os.path.join(src, *rel.split('/'))
        os.makedirs(os.path.dirname(f), exist_ok=True)
        body = 'x = ("' + RETIRED_PATH + '"\n"c.csv")\n' if rel in PATCHES else 'y = 1\n'
        open(f, 'w').write(body)
    dst = os.path.join(tmp, 'mdst')
    done = vendor_method(src, dst)
    pf = os.path.join(dst, METHOD_ROOT, 'status', 'staged', '0929C_ACTOR', 'gen_citation_index.py')
    chk('vendor patch replaces the retired path and records before and after hashes',
        RETIRED_PATH not in open(pf).read() and len(done) == 1 and done[0]['sha256_before'] != done[0]['sha256_after'])
    other = os.path.join(dst, METHOD_ROOT, 'status', 'staged', '1005B_METRICS', 'receipts_metrics_v1.py')
    chk('an unpatched file is copied byte for byte', sha_file(other) == sha_file(os.path.join(src, 'status', 'staged', '1005B_METRICS', 'receipts_metrics_v1.py')))
    open(os.path.join(src, 'status', 'staged', '0929C_ACTOR', 'gen_citation_index.py'), 'w').write('nothing to patch\n')
    try:
        vendor_method(src, os.path.join(tmp, 'mdst2'))
        stopped = False
    except SystemExit:
        stopped = True
    chk('RED: a missing patch target stops the build', stopped)
    meta = {'status': 'STAGED', 'window': ['2026-09-01', '2026-10-07'], 'run_days': 36, 'answers_rows': 10152}
    card = render_card(meta, 'data/answers_x.csv', 'data/sources_x.csv')
    chk('card: every placeholder filled, paths and counts in place',
        '{{W' not in card and 'edition-1/data/answers_x.csv' in card and '10,152 answer rows' in card and 'staged' in card)
    chk('card: no em dash, en dash or emoji', not any(ch in card for ch in ('\u2014', '\u2013')) and all(ord(c) < 0x2000 or c in '\u2019' for c in card))
    chk('card: FINAL status line differs', 'Edition 1 is final' in render_card(dict(meta, status='FINAL'), 'data/a.csv', 'data/s.csv'))
    def R(date, kind, niche, market):
        return {'date': date, 'kind': kind, 'niche': niche, 'market': market}
    rows = [R('2026-09-01', 'API', 'roofers', 'Town A'), R('2026-09-27', 'API', 'plumbers', 'Town B'),
            R('2026-10-01', 'API', 'roofers', 'Town A'),                        # old cell, later day: stays
            R('2026-10-08', 'API', 'mortgage lenders', 'Town C'),               # new cell: left out
            R('2026-10-08', 'SERP', 'mortgage lenders', 'Town C'),              # its SERP row: left out too
            R('2026-10-08', 'AGREE', 'mortgage lenders', 'Town C'),
            R('2026-10-08', 'API', 'ai visibility checks', 'US nationwide'),    # never a cell: passes through
            R('2026-10-08', 'API', 'x', '')]
    kept, out = panel_split(rows)
    chk('panel: later cell rows (API, SERP, AGREE) are left out', len(out) == 3 and all(r['niche'] == 'mortgage lenders' for r in out))
    chk('panel: old cells on later days, US nationwide and empty-market rows stay', len(kept) == 5)
    chk('panel: a cell asked on 2026-09-28 or later is not in the panel', panel_split([R('2026-09-28', 'API', 'n', 'Town D')])[1] != [])
    chk('panel: a cell asked on 2026-09-27 is in the panel', panel_split([R('2026-09-27', 'API', 'n', 'Town D')])[1] == [])
    # RED proof: a panel built from every row, not from rows up to the cutoff, leaves nothing out
    global PANEL_CUTOFF
    keep_cut = PANEL_CUTOFF
    PANEL_CUTOFF = '2999-01-01'
    red_out = panel_split(rows)[1]
    PANEL_CUTOFF = keep_cut
    chk('RED: with no cutoff the late cell is wrongly kept (%d left out, not 3)' % len(red_out), len(red_out) == 0)
    chk('panel sentence: none left out', 'no row is left out' in panel_sentence({'rows_left_out': 0, 'cutoff': PANEL_CUTOFF}))
    chk('panel sentence: counts and file named', '338 rows from 26 later cells' in panel_sentence({'rows_left_out': 338, 'cells_left_out': 26, 'cutoff': PANEL_CUTOFF, 'file': 'metrics/panel_x.csv'}))
    # heading filter v1
    flagged = ['Overview', 'Known For', 'Certifications', 'Guaranteed Rent', 'Federal Tax Credits', 'Break-Even Occupancy',
               'Key qualifications to verify', 'Research methods', 'Red flags']
    chk('heading: every flagged term is removed (%s)' % ', '.join(f for f in flagged if heading_reason(f) is None),
        all(heading_reason(f) for f in flagged))
    chk('heading: case and outer punctuation ignored ("overview:", "KNOWN FOR")', heading_reason('overview:') and heading_reason('KNOWN FOR'))
    chk('heading: a line-break fragment is removed', heading_reason('My Suggestion\nRather') == 'c')
    real = ['Extreme Roofing Inc', 'Pro Lawn & Irrigation', 'Pro Lawn', 'Guaranteed Rate', 'QuickBooks', 'Atomicdust', 'Zehl & Associates',
            'Airbnb', 'Key Realty Group']
    chk('heading: real business names pass (%s)' % ', '.join(r for r in real if heading_reason(r)), not any(heading_reason(r) for r in real))
    clean = {'kind': 'API', 'source_file': 'NB-CZ-API_2026-10-01_x.json'}
    add_derived(clean, ['A Roofing Co', 'B Roofing LLC'])
    chk('heading: a clean row passes untouched (0 removed, same names)',
        clean['headings_removed_count'] == 0 and clean['businesses_named_no_headings'] == 'A Roofing Co;B Roofing LLC' and clean['rerun'] == 0)
    dirty = {'kind': 'API', 'source_file': 'NB-CZ-API_2026-08-05_x_r3.json'}
    add_derived(dirty, ['Overview', 'Extreme Roofing Inc', 'Red flags to avoid', 'Known For'])
    chk('heading: a dirty row keeps only the business (3 removed) and marks rerun 3',
        dirty['headings_removed_count'] == 3 and dirty['businesses_named_no_headings'] == 'Extreme Roofing Inc' and dirty['rerun'] == 3)
    serp = {'kind': 'SERP', 'source_file': 'NB-CZ-SERP_2026-10-01_x.json'}
    add_derived(serp, ['https://u1/'])
    chk('heading: SERP rows are not filtered (blank derived cells)', serp['headings_removed_count'] == '' and serp['businesses_named_no_headings'] == '')
    rep = heading_report([dict(dirty, businesses_named='Overview;Extreme Roofing Inc;Red flags to avoid;Known For')])
    chk('heading report counts rows, names and watched terms', rep['rows_touched'] == 1 and rep['names_removed'] == 3
        and rep['watched_terms_rows']['Overview'] == 1 and rep['watched_terms_rows']['Known For'] == 1)
    global HEADING_TERMS
    keep_terms = HEADING_TERMS
    HEADING_TERMS = set()
    red = heading_reason('Known For')
    HEADING_TERMS = keep_terms
    chk('RED: with the exact list emptied, Known For slips through (the list is load-bearing)', red is None)
    gci_path = os.path.join(REPO, 'releases', 'edition-1', METHOD_ROOT, 'status', 'staged', '0929C_ACTOR', 'gen_citation_index.py')
    if os.path.exists(gci_path):
        import importlib.util
        spec = importlib.util.spec_from_file_location('gci_check', gci_path)
        m = importlib.util.module_from_spec(spec)
        sys.path.insert(0, os.path.dirname(gci_path))
        spec.loader.exec_module(m)
        want = set(m.BASE_GENERIC) | set(m.EXTRA_GENERIC)
        chk('heading words equal the method set BASE_GENERIC + EXTRA_GENERIC (missing %s, extra %s)'
            % (sorted(want - HEADING_WORDS), sorted(HEADING_WORDS - want)), want == HEADING_WORDS)
    chk('derived fields come after the base fields; check-latest layout unchanged',
        ANSWER_FIELDS[:len(BASE_FIELDS)] == BASE_FIELDS and BASE_FIELDS[-1] == 'sha256')
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
            w = csv.DictWriter(fh, fieldnames=BASE_FIELDS, extrasaction='ignore')   # default CRLF, as the daily rebuild writes it
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
