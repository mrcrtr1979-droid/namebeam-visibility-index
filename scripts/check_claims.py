#!/usr/bin/env python3
"""check_claims.py: flag every number on a surface that the claims registry does not hold (2026-10-14, [FABLE-PARALLEL-1013E]).

    python3 -I scripts/check_claims.py --registry releases/edition-1/CLAIMS.json FILE [FILE ...] [--strict]
    python3 -I scripts/check_claims.py --selftest

A surface is any text file: HTML, Markdown, a press note, a pitch, a curl of a live page. The checker reads three
shapes of number and looks each up in the registry:
  PAIR     "N of M" (commas allowed) must match a claim's numerator and denominator exactly.
  PERCENT  "X percent" or "X%" must round-match a claim's value at the surface's own precision (X.Y or X).
  COUNT    "N answer rows", "N run days", "N source rows", "N questions", "N files in the manifest", "N US local markets",
           "N niche" must match the pack claim of that unit.
Dates (2026-10-13), years, times (14:00), SHA fragments, version strings and numbers inside URLs or attributes are skipped.
Every hit is printed as REGISTERED or UNREGISTERED with file, line and the sentence; the exit code is 1 when any
UNREGISTERED hit exists (with --strict, also when a PAIR is registered under a different engine than the sentence names:
the R72 check, a number quoted beside the wrong engine). Nothing is written.
"""
import argparse
import io
import json
import os
import re
import sys
import tempfile

PAIR = re.compile(r'(?<![\d.,])(\d{1,3}(?:,\d{3})*|\d+)\s+of\s+(?:its\s+|the\s+|their\s+)?(\d{1,3}(?:,\d{3})*|\d+)(?![\d,])')
PERCENT = re.compile(r'(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*(?:percent|%)')
COUNT_UNITS = {'answer rows': 'pack.answers_rows', 'run days': 'pack.run_days', 'source rows': 'pack.sources_rows',
               'sources rows': 'pack.sources_rows', 'questions': 'pack.prompts_rows', 'files in the manifest': 'pack.files',
               'us local markets': 'pack.counts.us_local_markets', 'niches': 'pack.counts.niche_labels', 'niche labels': 'pack.counts.niche_labels',
               'api rows': 'pack.rows_by_kind.API', 'serp rows': 'pack.rows_by_kind.SERP', 'agree rows': 'pack.rows_by_kind.AGREE',
               'ai engine answers': 'pack.rows_by_kind.API', 'google result pages': 'pack.rows_by_kind.SERP', 'engine agreement rows': 'pack.rows_by_kind.AGREE'}
COUNT = re.compile(r'(?<![\d.,])(\d{1,3}(?:,\d{3})*|\d+)\s+(' + '|'.join(sorted((re.escape(k) for k in COUNT_UNITS), key=len, reverse=True)) + r')\b', re.I)
SKIP = re.compile(r'\d{4}-\d{2}-\d{2}|\b20\d\d\b|\b\d{1,2}:\d{2}\b|\b[0-9a-f]{12,}\b|\bv\d+(?:\.\d+)*\b|https?://\S+|\b\d+\.\d+\.\d+(?:\.\d+)*\b')
ENGINES = {'openai': ['openai', 'chatgpt'], 'anthropic': ['anthropic', 'claude'], 'gemini': ['gemini', 'google ai'], 'perplexity': ['perplexity'],
           'google_serp': ['google organic', 'google results', 'google result'], 'agree': ['agreement']}


BLOCKS = re.compile(r'<(style|script)\b[^>]*>.*?</\1>', re.S | re.I)
RANGE = re.compile(r'\b\d{1,3}\s+to\s+\d{1,3}\s*(?:percent|%)')


def strip_markup(line):
    line = re.sub(r'<[^>]+>', ' ', line)
    line = re.sub(r'\s(?:href|src|content|id|class|name)="[^"]*"', ' ', line)
    line = RANGE.sub(' ', line)   # "20 to 80 percent" is a definition, not a claim
    return line


def read_surface(path):
    text = io.open(path, encoding='utf-8', errors='replace').read()
    return BLOCKS.sub(' ', text)   # CSS and script never carry a claim


def load(registry):
    reg = json.load(io.open(registry, encoding='utf-8'))
    pairs = {}
    pcts = {}
    counts = {}
    for c in reg['claims']:
        if c.get('numerator') is not None and c.get('denominator') is not None:
            pairs.setdefault((int(c['numerator']), int(c['denominator'])), []).append(c)
        if c.get('value') is not None and c.get('denominator') is not None:
            try:
                v = 100.0 * float(c['value'])
                pcts.setdefault(round(v, 1), []).append(c)
                pcts.setdefault(float(round(v)), []).append(c)
            except (TypeError, ValueError):
                pass
        if c['kind'] == 'pack':
            counts[c['id']] = c
    return reg, pairs, pcts, counts


def engine_in(text):
    t = text.casefold()
    return {e for e, words in ENGINES.items() if any(w in t for w in words)}


def check_file(path, reg, pairs, pcts, counts, strict=False):
    hits = []
    text = read_surface(path)
    for ln, raw in enumerate(text.splitlines(), start=1):
        line = strip_markup(raw)
        clean = SKIP.sub(' ', line)
        line_engines = engine_in(clean)
        for m in PAIR.finditer(clean):
            n, d = int(m.group(1).replace(',', '')), int(m.group(2).replace(',', ''))
            cs = pairs.get((n, d), [])
            status = 'REGISTERED' if cs else 'UNREGISTERED'
            if cs and strict:
                near = engine_in(clean[max(0, m.start() - 300):m.end() + 80]) or (line_engines if len(line_engines) == 1 else set())
                if near and not any(c['engine'] in near or not c['engine'] for c in cs):
                    status = 'WRONG-ENGINE'
            hits.append((status, path, ln, 'PAIR %s of %s' % (m.group(1), m.group(2)), clean.strip()[:160], cs[0]['id'] if cs else ''))
        for m in PERCENT.finditer(clean):
            v = float(m.group(1))
            key = round(v, 1) if '.' in m.group(1) else float(round(v))
            cs = pcts.get(key, [])
            hits.append(('REGISTERED' if cs else 'UNREGISTERED', path, ln, 'PERCENT %s' % m.group(1), clean.strip()[:160], cs[0]['id'] if cs else ''))
        for m in COUNT.finditer(clean):
            n = int(m.group(1).replace(',', ''))
            cid = COUNT_UNITS[m.group(2).casefold()]
            c = counts.get(cid)
            ok = c is not None and int(c['value']) == n
            hit_id = cid if ok else ''
            if not ok:
                # any claim with the same unit and value (for example run days of one metrics window)
                unit = c['unit'] if c else m.group(2).casefold()
                alt = [x for x in reg['claims'] if x.get('unit') == unit and x.get('denominator') is None and x.get('value') is not None and int(x['value']) == n]
                if alt:
                    ok, hit_id = True, alt[0]['id']
            hits.append(('REGISTERED' if ok else 'UNREGISTERED', path, ln, 'COUNT %s %s' % (m.group(1), m.group(2)), clean.strip()[:160], hit_id))
    return hits


def selftest():
    fails = []

    def chk(name, ok):
        print(('  ok   ' if ok else '  FAIL ') + name)
        if not ok:
            fails.append(name)
    tmp = tempfile.mkdtemp()
    reg = {'claims': [
        {'id': 'metric.WB.perplexity.S', 'kind': 'metric', 'engine': 'perplexity', 'value': 0.279202, 'numerator': 98, 'denominator': 351, 'unit': 'x', 'window': ['a', 'b'], 'sentence': ''},
        {'id': 'pack.answers_rows', 'kind': 'pack', 'engine': '', 'value': 12180, 'numerator': 12180, 'denominator': None, 'unit': 'answer rows', 'window': ['a', 'b'], 'sentence': ''},
        {'id': 'pack.run_days', 'kind': 'pack', 'engine': '', 'value': 42, 'numerator': 42, 'denominator': None, 'unit': 'run days', 'window': ['a', 'b'], 'sentence': ''}]}
    rp = os.path.join(tmp, 'CLAIMS.json')
    json.dump(reg, io.open(rp, 'w', encoding='utf-8'))
    surface = os.path.join(tmp, 'page.html')
    io.open(surface, 'w', encoding='utf-8').write(
        '<p>Perplexity: 98 of 351 (27.9 percent) in WB.</p>\n'
        '<p>rose to 42.2 percent (114 of 270)</p>\n'
        '<p>12,180 answer rows over 42 run days, released 2026-10-15 at 14:00, sha 611894c48c21a512.</p>\n'
        '<p>OpenAI, Anthropic and Gemini named it in 0 of 135.</p>\n'
        '<p>OpenAI named it in 98 of 351 answers.</p>\n'
        '<a href="https://x/98-of-351">link</a> 10,152 answer rows\n')
    r, pairs, pcts, counts = load(rp)
    hits = check_file(surface, r, pairs, pcts, counts, strict=True)
    def find(kind):
        return [h for h in hits if h[3] == kind]
    chk('registered pair and its percent pass', find('PAIR 98 of 351')[0][0] == 'REGISTERED' and find('PERCENT 27.9')[0][0] == 'REGISTERED')
    chk('RED: a pair with no claim is UNREGISTERED (114 of 270)', find('PAIR 114 of 270')[0][0] == 'UNREGISTERED')
    chk('RED: a percent with no claim is UNREGISTERED (42.2)', find('PERCENT 42.2')[0][0] == 'UNREGISTERED')
    chk('counts: 12,180 answer rows and 42 run days pass', find('COUNT 12,180 answer rows')[0][0] == 'REGISTERED' and find('COUNT 42 run days')[0][0] == 'REGISTERED')
    chk('RED: a stale count is UNREGISTERED (10,152 answer rows)', find('COUNT 10,152 answer rows')[0][0] == 'UNREGISTERED')
    chk('RED: a blended denominator is UNREGISTERED (0 of 135)', find('PAIR 0 of 135')[0][0] == 'UNREGISTERED')
    chk('RED (strict): a registered pair beside the wrong engine is WRONG-ENGINE', find('PAIR 98 of 351')[1][0] == 'WRONG-ENGINE')
    chk('dates, times, sha fragments and URLs are skipped', not any('2026' in h[3] or '14:00' in h[3] or '611894' in h[3] for h in hits) and not any(h[2] == 6 and 'PAIR' in h[3] for h in hits))
    print('SELFTEST ' + ('FAIL: ' + ', '.join(fails) if fails else 'PASS'))
    return not fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--registry')
    ap.add_argument('files', nargs='*')
    ap.add_argument('--strict', action='store_true')
    ap.add_argument('--quiet', action='store_true', help='print UNREGISTERED and WRONG-ENGINE hits and the summary only')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if not a.registry or not a.files:
        ap.error('--registry and at least one file are required')
    reg, pairs, pcts, counts = load(a.registry)
    bad = 0
    total = 0
    for f in a.files:
        hits = check_file(f, reg, pairs, pcts, counts, a.strict)
        for status, path, ln, what, sent, cid in hits:
            total += 1
            if status != 'REGISTERED':
                bad += 1
            if not a.quiet or status != 'REGISTERED':
                print('%-12s %s:%d  %s  | %s%s' % (status, os.path.basename(path), ln, what, sent, ('  [' + cid + ']') if cid else ''))
    print('CHECK %s: %d numbers read, %d not in the registry (%s)' % ('RED' if bad else 'GREEN', total, bad, a.registry))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
