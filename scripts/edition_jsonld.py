#!/usr/bin/env python3
"""schema.org Dataset markup (JSON-LD) for the Edition page, built from PACK.json (loop S1b-4).

Google Dataset Search lists a dataset from a crawlable page that carries schema.org/Dataset
JSON-LD (developers.google.com/search/docs/data-types/dataset, read 2026-10-09: name and a
description of 50 to 5000 characters are required; identifier, license, sameAs, creator and
distribution are recommended). Every number in the markup is read from the pack's PACK.json,
never typed by hand, so the page cannot disagree with the files.

    python3 -I scripts/edition_jsonld.py --pack releases/edition-1 --page-url URL \
        [--doi 10.5281/zenodo.NNNN] [--published 2026-10-15] [--modified 2026-10-14] \
        [--datarade-url URL] [--script-tag] [--out FILE] [--check-live]
    python3 -I scripts/edition_jsonld.py --selftest

--check-live sends a HEAD request to every distribution URL (run it after the Hugging Face
publish) and exits 1 if any is not HTTP 200. Without --doi the markup carries no identifier:
a DOI is never invented. Standard library only.
"""
import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HF = 'https://huggingface.co/datasets/Namebeam/ai-visibility-daily-record'
LICENSE_URL = 'https://creativecommons.org/licenses/by/4.0/'


def build(pack, page_url, doi=None, published=None, modified=None, datarade_url=None, hf=HF):
    w0, w1 = pack['window']
    f = pack['files']
    desc = ('Dated record of which businesses AI answer engines name for local questions in US markets, '
            'with the sources the engines returned. Edition 1 covers %s to %s (%d run days). The data pack '
            'holds %d answer rows (one per raw answer file, with the names each engine returned and the '
            'SHA-256 of the raw file), %d source URL rows, %d rows from a Siri panel run by hand, and a '
            'metrics table that gives the denominator of every figure. Perplexity, OpenAI, Anthropic and '
            'Gemini were called through their programming interfaces, and Google results pages were read. '
            'Every file is listed in a SHA-256 manifest and a verify script checks the pack. '
            'Licensed CC BY 4.0.'
            % (w0, w1, pack['run_days'], pack['answers_rows'], pack['sources_rows'], pack['siri_rows']))
    dist = []
    for key, label in (('answers', 'Answers table'), ('sources', 'Sources table'), ('siri', 'Siri panel'),
                       ('metrics', 'Metrics table')):
        rel = f[key]
        dist.append({'@type': 'DataDownload', 'name': label, 'encodingFormat': 'text/csv',
                     'contentUrl': '%s/resolve/main/edition-1/%s' % (hf, rel)})
    for name, rel in (('Method note', 'README.md'), ('Manifest', 'MANIFEST.sha256'), ('Verify script', 'verify.py')):
        dist.append({'@type': 'DataDownload', 'name': name,
                     'encodingFormat': {'README.md': 'text/markdown', 'MANIFEST.sha256': 'text/plain',
                                        'verify.py': 'text/x-python'}[rel],
                     'contentUrl': '%s/resolve/main/edition-1/%s' % (hf, rel)})
    org = {'@type': 'Organization', 'name': 'Carter Enterprise LLC', 'alternateName': 'Namebeam',
           'url': 'https://namebeam.ai'}
    same = [hf]
    if datarade_url:
        same.append(datarade_url)
    d = {'@context': 'https://schema.org/', '@type': 'Dataset',
         'name': 'Namebeam AI Visibility Record, Edition 1',
         'description': desc,
         'url': page_url,
         'version': 'Edition 1 (%s)' % pack['status'].lower(),
         'license': LICENSE_URL,
         'isAccessibleForFree': True,
         'creator': org, 'publisher': org,
         'keywords': ['AI answer engines', 'local search', 'AI search citations', 'dated record', 'open data'],
         'temporalCoverage': '%s/%s' % (w0, w1),
         'spatialCoverage': {'@type': 'Place', 'name': 'United States'},
         'measurementTechnique': ('One fixed question per business or segment is put to each engine every '
                                  'day; each answer is stored as a raw file with its SHA-256. See the method '
                                  'note for the settings of every engine.'),
         'distribution': dist, 'sameAs': same}
    if doi:
        d['identifier'] = 'https://doi.org/' + doi
        d['sameAs'].append('https://doi.org/' + doi)
    if published:
        d['datePublished'] = published
    if modified:
        d['dateModified'] = modified
    return d


def problems(d):
    """Offline checks against the Google Dataset requirements and the Namebeam copy rules."""
    out = []
    for k in ('name', 'description', 'url', 'license', 'creator', 'distribution', 'temporalCoverage'):
        if not d.get(k):
            out.append('missing ' + k)
    n = len(d.get('description', ''))
    if not 50 <= n <= 5000:
        out.append('description length %d outside 50..5000' % n)
    for x in d.get('distribution', []):
        if x.get('@type') != 'DataDownload' or not x.get('encodingFormat') or not x.get('contentUrl', '').startswith('https://'):
            out.append('bad distribution entry %s' % x.get('name'))
    tc = d.get('temporalCoverage', '')
    try:
        a, b = tc.split('/')
        datetime.date.fromisoformat(a)
        datetime.date.fromisoformat(b)
    except ValueError:
        out.append('temporalCoverage is not YYYY-MM-DD/YYYY-MM-DD')
    text = json.dumps(d, ensure_ascii=False)
    if any(ord(c) > 127 for c in text):
        out.append('non-ASCII character')
    if '—' in text or '–' in text:
        out.append('em or en dash')
    low = text.lower()
    for w in ('first ', ' only ', 'honest', 'alpha vault', 'carter studio', 'unmatched', 'best in class'):
        if w in low and w != ' only ':
            out.append('banned word: ' + w.strip())
    if ' only ' in low:
        out.append('the word "only" appears (no first/only claims)')
    if 'identifier' in d and not d['identifier'].startswith('https://doi.org/10.'):
        out.append('identifier is not a DOI URL')
    return out


def head_status(url):
    req = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'NamebeamRecordWatch/1.0 (+https://namebeam.ai)'})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def check_live(d, head=head_status):
    bad = 0
    for x in d['distribution']:
        st = head(x['contentUrl'])
        print('%s  %s  %s' % (st, 'OK ' if st == 200 else 'BAD', x['contentUrl']))
        bad += st != 200
    return 1 if bad else 0


def selftest():
    ok = True

    def check(label, cond):
        nonlocal ok
        print('%s  %s' % ('GREEN' if cond else 'RED  ', label))
        ok = ok and bool(cond)

    pack = {'window': ['2026-09-01', '2026-10-13'], 'run_days': 41, 'answers_rows': 12345, 'sources_rows': 40000,
            'siri_rows': 28, 'status': 'FINAL',
            'files': {'answers': 'data/answers_2026-09-01_to_2026-10-13.csv', 'sources': 'data/sources_2026-09-01_to_2026-10-13.csv',
                      'siri': 'data/siri_panel.csv', 'metrics': 'metrics/metrics_v2.csv'}}
    d = build(pack, 'https://example.test/edition-1')
    check('no problems on a normal build', problems(d) == [])
    check('numbers come from PACK: 12345 answer rows, 41 run days, window in temporalCoverage',
          '12345 answer rows' in d['description'] and '(41 run days)' in d['description']
          and d['temporalCoverage'] == '2026-09-01/2026-10-13')
    check('no DOI given: no identifier (a DOI is never invented)', 'identifier' not in d)
    d2 = build(pack, 'https://example.test/e', doi='10.5281/zenodo.1234567', published='2026-10-15')
    check('DOI given: identifier is the DOI URL and it is in sameAs',
          d2['identifier'] == 'https://doi.org/10.5281/zenodo.1234567' and 'https://doi.org/10.5281/zenodo.1234567' in d2['sameAs']
          and problems(d2) == [])
    check('distribution: 4 tables as text/csv plus method note, manifest and verify script',
          len(d['distribution']) == 7 and sum(x['encodingFormat'] == 'text/csv' for x in d['distribution']) == 4)
    bad = dict(d)
    bad['description'] = 'too short'
    check('RED PROOF: a 9 character description is flagged', any('description length' in p for p in problems(bad)))
    bad = dict(d)
    bad['creator'] = ''
    check('RED PROOF: a missing creator is flagged', 'missing creator' in problems(bad))
    bad = dict(d)
    bad['description'] = d['description'] + ' The first record of its kind — only here.'
    ps = problems(bad)
    check('RED PROOF: em dash, "first" and "only" are flagged',
          any('dash' in p for p in ps) and any('banned word: first' in p for p in ps) and any('"only"' in p for p in ps))
    bad = dict(d2)
    bad['identifier'] = 'zenodo.1234567'
    check('RED PROOF: a bare identifier that is not a DOI URL is flagged', any('identifier' in p for p in problems(bad)))
    seen = []
    rc = check_live(d, head=lambda u: seen.append(u) or (404 if u.endswith('verify.py') else 200))
    check('check_live: asks every distribution URL and fails (exit 1) on a 404', len(seen) == 7 and rc == 1)
    print('SELFTEST %s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--pack', default=os.path.join(ROOT, 'releases', 'edition-1'))
    ap.add_argument('--page-url')
    ap.add_argument('--doi')
    ap.add_argument('--published')
    ap.add_argument('--modified')
    ap.add_argument('--datarade-url')
    ap.add_argument('--script-tag', action='store_true')
    ap.add_argument('--out')
    ap.add_argument('--check-live', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.page_url:
        ap.error('--page-url is required')
    with open(os.path.join(a.pack, 'PACK.json'), encoding='utf-8') as fh:
        pack = json.load(fh)
    d = build(pack, a.page_url, a.doi, a.published, a.modified, a.datarade_url)
    ps = problems(d)
    if ps:
        print('PROBLEMS: ' + '; '.join(ps))
        return 2
    body = json.dumps(d, indent=2, ensure_ascii=True)
    if a.script_tag:
        body = '<script type="application/ld+json">\n%s\n</script>' % body
    if a.out:
        with open(a.out, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(body + '\n')
        print('wrote %s (%d bytes), description %d characters' % (a.out, len(body), len(d['description'])))
    else:
        print(body)
    return check_live(d) if a.check_live else 0


if __name__ == '__main__':
    sys.exit(main())
