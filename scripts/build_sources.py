#!/usr/bin/env python3
"""Sources dataset builder for the Namebeam AI Visibility record (Seat S1-1).

Reads the dated raw answer files in corpus/e1/*.json (or the corpus/e1_raw
*.tar.gz archives) and writes one CSV per UTC date:

    datasets/e1/sources/sources_<YYYY-MM-DD>.csv

One row per URL an engine returned as a source, in the order the engine
returned it. Columns (the first nine are the published contract):

    date, engine, run_channel, market, niche, prompt_text, url, domain, field,
    check_id, rank

Where the URLs live (field map, verified against 16,267 raw rows):
    perplexity   sources_cited           list of URL strings
    google_serp  organic_top_results     list of URL strings
    google_serp  ai_overview_references  list of {href, title, source}
    anthropic, openai, gemini carry an empty sources_cited list: no rows.

Inline URLs inside answer_verbatim prose are NOT sources rows. They are
counted by --census so the gap is visible.

Guards (a check must detect omission, not only deletion):
    UNMAPPED  a structured field outside the map holds http strings. Printed,
              and the run exits 2 under --strict. Schema drift cannot hide.
    EMPTY-DAY an answer day with source-bearing rows but zero URL rows exits 2.
    MISSING   --missing builds every date present in the corpus that has no
              output file yet, so a skipped day heals on the next run.

Usage:
    python3 scripts/build_sources.py --missing            # daily step (default)
    python3 scripts/build_sources.py --date 2026-10-07
    python3 scripts/build_sources.py --all                # full backfill
    python3 scripts/build_sources.py --archives DIR --out DIR --all
    python3 scripts/build_sources.py --census --from 2026-09-27 --to 2026-10-07
    python3 scripts/build_sources.py --selftest

Standard library only. Run with: python3 -I scripts/build_sources.py ...
"""
import argparse
import collections
import csv
import glob
import io
import json
import os
import re
import sys
import tarfile
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_CORPUS = os.path.join(ROOT, 'corpus', 'e1')
DEFAULT_OUT = os.path.join(ROOT, 'datasets', 'e1', 'sources')
FIXTURES = os.path.join(ROOT, 'tests', 'fixtures', 'sources')

COLUMNS = ['date', 'engine', 'run_channel', 'market', 'niche', 'prompt_text',
           'url', 'domain', 'field', 'check_id', 'rank']

# field name -> keys tried, in order, when the list element is a dict
FIELD_MAP = collections.OrderedDict([
    ('sources_cited', ('href', 'url', 'link')),
    ('organic_top_results', ('href', 'url', 'link')),
    ('ai_overview_references', ('href', 'url', 'link')),
])

# Top-level keys that legitimately hold prose or analysis, never a source list.
# serp_diagnostics, pairs and the prose fields are checked by --census only.
PROSE_KEYS = {
    'answer_verbatim', 'ai_overview_text', 'session_note', 'method_note',
    'prompt_text', 'serp_diagnostics', 'pairs', 'competitors_mentioned',
    'engine_stated_criteria', 'check_id', 'business',
}

DATE_IN_NAME = re.compile(r'_(\d{4}-\d{2}-\d{2})_')


# ---------------------------------------------------------------- helpers
def norm_host(host):
    h = (host or '').lower().split('@')[-1].split(':')[0].rstrip('.')
    return h[4:] if h.startswith('www.') else h


def url_domain(u):
    try:
        return norm_host(urlsplit(u).netloc)
    except ValueError:
        return ''


def is_http(s):
    return isinstance(s, str) and s.strip().lower().startswith(('http://', 'https://'))


def element_url(e, keys):
    if isinstance(e, str):
        return e.strip() if is_http(e) else None
    if isinstance(e, dict):
        for k in keys:
            v = e.get(k)
            if is_http(v):
                return v.strip()
    return None


def find_http(obj, out):
    if isinstance(obj, str):
        if is_http(obj):
            out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            find_http(v, out)
    elif isinstance(obj, list):
        for v in obj:
            find_http(v, out)


def date_of(name, d):
    v = d.get('date_utc') if isinstance(d, dict) else None
    if isinstance(v, str) and re.match(r'^\d{4}-\d{2}-\d{2}$', v):
        return v
    m = DATE_IN_NAME.search(name)
    return m.group(1) if m else ''


# ---------------------------------------------------------------- readers
def iter_dir(corpus):
    for p in sorted(glob.glob(os.path.join(corpus, '*.json'))):
        try:
            with io.open(p, encoding='utf-8') as fh:
                yield os.path.basename(p), json.load(fh)
        except (ValueError, OSError):
            yield os.path.basename(p), None


def iter_archives(archives):
    items = []
    for p in sorted(glob.glob(os.path.join(archives, '*.tar.gz'))):
        with tarfile.open(p, 'r:gz') as tf:
            for m in tf.getmembers():
                if not (m.isfile() and m.name.endswith('.json')):
                    continue
                try:
                    items.append((os.path.basename(m.name),
                                  json.loads(tf.extractfile(m).read().decode('utf-8', 'replace'))))
                except (ValueError, AttributeError):
                    items.append((os.path.basename(m.name), None))
    items.sort(key=lambda t: t[0])
    return iter(items)


def records(corpus, archives):
    return iter_archives(archives) if archives else iter_dir(corpus)


# ---------------------------------------------------------------- extract
def extract(name, d):
    """Return (rows, unmapped) for one raw record.

    rows: list of dicts keyed by COLUMNS. unmapped: list of (key, n_urls)."""
    rows, unmapped = [], []
    if not isinstance(d, dict):
        return rows, unmapped
    date = date_of(name, d)
    base = {
        'date': date,
        'engine': d.get('engine') or '',
        'run_channel': d.get('run_channel') or '',
        'market': d.get('market') or '',
        'niche': d.get('niche') or '',
        'prompt_text': d.get('prompt_text') or '',
        'check_id': d.get('check_id') or os.path.splitext(name)[0],
    }
    for field, keys in FIELD_MAP.items():
        v = d.get(field)
        if not isinstance(v, list):
            continue
        rank = 0
        for e in v:
            rank += 1
            u = element_url(e, keys)
            if u is None:
                continue
            r = dict(base)
            r.update({'url': u, 'domain': url_domain(u), 'field': field, 'rank': rank})
            rows.append(r)
    for k, v in d.items():
        if k in FIELD_MAP or k in PROSE_KEYS or isinstance(v, str):
            continue
        found = []
        find_http(v, found)
        if found:
            unmapped.append((k, len(found)))
    return rows, unmapped


def write_csv(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, 'w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator='\n')
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------- build
def build(corpus, archives, out, dates=None, only_missing=False, strict=False):
    """Build per-date CSVs. dates: set of dates to build (None = all)."""
    by_date = collections.defaultdict(list)
    answers_by_date = collections.Counter()
    carrying_by_date = collections.Counter()   # records whose engine can carry sources
    unmapped = collections.Counter()
    bad = 0
    for name, d in records(corpus, archives):
        if d is None:
            bad += 1
            continue
        if not isinstance(d, dict):
            continue
        rows, um = extract(name, d)
        dt = date_of(name, d)
        if d.get('engine'):
            answers_by_date[dt] += 1
            if d.get('engine') in ('perplexity', 'google_serp'):
                carrying_by_date[dt] += 1
        by_date[dt].extend(rows)
        for k, n in um:
            unmapped[(d.get('engine') or d.get('row_type') or '?', k)] += n
    status = 0
    built = []
    for dt in sorted(answers_by_date):
        if not dt:
            continue
        if dates is not None and dt not in dates:
            continue
        path = os.path.join(out, 'sources_%s.csv' % dt)
        if only_missing and os.path.exists(path):
            continue
        rows = by_date.get(dt, [])
        if carrying_by_date[dt] and not rows:
            print('EMPTY-DAY %s: %d source-capable answers, 0 URL rows' % (dt, carrying_by_date[dt]))
            status = 2
        write_csv(path, rows)
        built.append((dt, len(rows)))
    for (eng, key), n in sorted(unmapped.items()):
        print('UNMAPPED field %r on %s rows holds %d http strings; add it to FIELD_MAP or PROSE_KEYS' % (key, eng, n))
        if strict:
            status = 2
    if bad:
        print('WARNING: %d unreadable raw files skipped' % bad)
    print('built %d day file(s), %d URL rows total' % (len(built), sum(n for _, n in built)))
    for dt, n in built[-5:]:
        print('  sources_%s.csv rows=%d' % (dt, n))
    return status, built


# ---------------------------------------------------------------- census
def census(corpus, archives, d_from, d_to):
    """Per engine and field: rows that carry the field (the denominator),
    rows with at least one URL, and URL rows. The denominator is rows where
    the key exists, never all engine answers: ai_overview_references exists
    only on rows written from 2026-10-03."""
    ans = collections.Counter()
    has_field = collections.Counter()
    with_urls = collections.Counter()
    url_rows = collections.Counter()
    inline = collections.Counter()
    first = {}
    nrec = 0
    for name, d in records(corpus, archives):
        if not isinstance(d, dict) or not d.get('engine'):
            continue
        dt = date_of(name, d)
        if (d_from and dt < d_from) or (d_to and dt > d_to):
            continue
        nrec += 1
        eng = d['engine']
        ans[eng] += 1
        rows, _ = extract(name, d)
        per_field = collections.Counter(r['field'] for r in rows)
        for f in FIELD_MAP:
            if isinstance(d.get(f), list):
                has_field[(eng, f)] += 1
                with_urls[(eng, f)] += 1 if per_field[f] else 0
                url_rows[(eng, f)] += per_field[f]
                if dt and (eng, f) not in first or dt < first.get((eng, f), '9'):
                    first[(eng, f)] = dt
        txt = d.get('answer_verbatim')
        if isinstance(txt, str) and re.search(r'https?://', txt):
            inline[eng] += 1
    print('CENSUS window %s..%s  engine answers=%d' % (d_from or 'start', d_to or 'end', nrec))
    print('%-12s %-24s %9s %10s %9s %10s  %s' % ('engine', 'field', 'engine_n', 'field_rows', 'with_urls', 'url_rows', 'field_first_date'))
    for eng in sorted(ans):
        shown = False
        for f in FIELD_MAP:
            if has_field[(eng, f)]:
                print('%-12s %-24s %9d %10d %9d %10d  %s' % (eng, f, ans[eng], has_field[(eng, f)],
                                                        with_urls[(eng, f)], url_rows[(eng, f)], first[(eng, f)]))
                shown = True
        if not shown:
            print('%-12s %-24s %9d' % (eng, '(no source field)', ans[eng]))
    for eng in sorted(inline):
        print('inline http in answer_verbatim (prose, not source rows): %s %d answers' % (eng, inline[eng]))


# ---------------------------------------------------------------- selftest
def legacy_collect(obj, active, out):
    """The W3-1 extractor: URLs under any key containing 'cited' or 'source'.
    Kept ONLY so the selftest can prove the check goes RED against it."""
    if isinstance(obj, str):
        if active and obj.startswith('http'):
            out.append(obj.strip())
    elif isinstance(obj, dict):
        for k, v in obj.items():
            legacy_collect(v, active or ('cited' in str(k).lower() or 'source' in str(k).lower()), out)
    elif isinstance(obj, list):
        for v in obj:
            legacy_collect(v, active, out)


def selftest():
    fx_dir = os.path.join(FIXTURES, 'corpus')
    exp_path = os.path.join(FIXTURES, 'expected_sources_2026-01-01.csv')
    ok = True

    def check(label, cond):
        nonlocal ok
        print('%s  %s' % ('GREEN' if cond else 'RED  ', label))
        ok = ok and cond

    tmp = os.path.join(FIXTURES, '_out')
    for f in glob.glob(os.path.join(tmp, '*')):
        os.remove(f)
    status, built = build(fx_dir, None, tmp, strict=False)
    got_path = os.path.join(tmp, 'sources_2026-01-01.csv')
    got = io.open(got_path, encoding='utf-8').read()
    exp = io.open(exp_path, encoding='utf-8').read()
    check('fixture output equals expected CSV byte for byte', got == exp)
    rows = list(csv.DictReader(io.StringIO(got)))
    by_field = collections.Counter(r['field'] for r in rows)
    check('fixture field counts sources_cited=3 organic=2 aio=3 (dup kept)',
          by_field == {'sources_cited': 3, 'organic_top_results': 2, 'ai_overview_references': 3})
    check('www stripped, host lowercased, port dropped',
          any(r['domain'] == 'example.com' for r in rows) and not any(r['domain'].startswith('www.') for r in rows))
    check('engines with empty sources_cited emit no rows',
          not any(r['engine'] in ('openai', 'anthropic', 'gemini') for r in rows))
    check('agreement row (no engine) emits no rows', not any(r['check_id'].startswith('NB-CZ-AGREE') for r in rows))
    check('rank preserves list position incl. a skipped non-http entry',
          [r['rank'] for r in rows if r['field'] == 'sources_cited'] == ['1', '3', '4'])

    # RED proof 1: the legacy W3-1 extractor must disagree with the expected row count.
    legacy_total = 0
    for name, d in iter_dir(fx_dir):
        urls = []
        legacy_collect(d, False, urls)
        legacy_total += len(urls)
    check('RED PROOF: legacy extractor total (%d) differs from expected (%d), so this check can fail'
          % (legacy_total, len(rows)), legacy_total != len(rows))

    # RED proof 2: dropping one field from the map must change the output.
    saved = FIELD_MAP.pop('ai_overview_references')
    try:
        s2, b2 = build(fx_dir, None, tmp)
        mutated = io.open(got_path, encoding='utf-8').read()
    finally:
        FIELD_MAP['ai_overview_references'] = saved
    check('RED PROOF: removing ai_overview_references from FIELD_MAP changes the output', mutated != exp)

    # Guard: unmapped URL field is reported and fails under --strict.
    um_dir = os.path.join(FIXTURES, 'corpus_unmapped')
    s3, _ = build(um_dir, None, tmp, strict=True)
    check('UNMAPPED guard: a new URL-bearing field exits 2 under strict', s3 == 2)

    # Guard: empty day with source-capable answers.
    ed_dir = os.path.join(FIXTURES, 'corpus_emptyday')
    s4, _ = build(ed_dir, None, tmp)
    check('EMPTY-DAY guard: perplexity answers with zero URL rows exit 2', s4 == 2)

    for f in glob.glob(os.path.join(tmp, '*')):
        os.remove(f)
    os.rmdir(tmp)
    print('SELFTEST %s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--corpus', default=DEFAULT_CORPUS)
    ap.add_argument('--archives', default=None, help='dir of *.tar.gz raw archives (e1_raw)')
    ap.add_argument('--out', default=DEFAULT_OUT)
    ap.add_argument('--date', help='build one UTC date')
    ap.add_argument('--all', action='store_true', help='rebuild every date')
    ap.add_argument('--missing', action='store_true', help='build dates with no output file (default)')
    ap.add_argument('--strict', action='store_true')
    ap.add_argument('--census', action='store_true')
    ap.add_argument('--from', dest='d_from')
    ap.add_argument('--to', dest='d_to')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.census:
        census(a.corpus, a.archives, a.d_from, a.d_to)
        return 0
    if a.date:
        status, built = build(a.corpus, a.archives, a.out, dates={a.date}, strict=a.strict)
        if not built:
            print('MISSING: no raw answer files for %s' % a.date)
            return 3
        return status
    status, _ = build(a.corpus, a.archives, a.out,
                      dates=None, only_missing=not a.all, strict=a.strict)
    return status


if __name__ == '__main__':
    sys.exit(main())
