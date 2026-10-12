#!/usr/bin/env python3
"""Parity check between published text and the text-mention name table (data/mentions_by_engine.csv), Edition 1.1.

    python3 -I scripts/check_mention_parity.py --pack DIR             # the pack's table against the pack's text files
    python3 -I scripts/check_mention_parity.py --table CSV FILE [FILE ...]
    python3 -I scripts/check_mention_parity.py --selftest

RED (exit 1) when published text:
  (1) gives a per-engine count for a business in the table ("OpenAI 18 of 39" or "18 of 39 OpenAI") that differs from the
      table row for that business and engine (in the market and niche the paragraph names, when it names one);
  (2) says an engine "0 of N" with no table row of that engine at 0 named days of N answer days;
  (3) says an engine "named no business" when the table has a business that engine named in that market and niche, when
      the paragraph names no market and niche of the table, or when the stated answer days differ;
  (4) says "almost no overlap": the table holds counts, and a count is what the text should give.
GREEN (exit 0) otherwise. The unit of text is a paragraph (a blank line, or a markdown table row, ends one). A count is
tied to the nearest business name of the table before it in the same paragraph.
Text files read with --pack: every .md, .txt and .html file in the pack except under method/ and reproduce/.
Standard library only. Reads files; writes only in a temporary folder during --selftest.
"""
import argparse
import csv
import os
import re
import sys
import tempfile

sys.dont_write_bytecode = True
LAB = {'openai': 'OpenAI', 'perplexity': 'Perplexity', 'gemini': 'Gemini', 'anthropic': 'Anthropic'}
ENG_BY_LAB = {v.casefold(): k for k, v in LAB.items()}
ENG_ALT = '|'.join(LAB.values())
NUM = r'(\d{1,3}(?:,\d{3})*|\d+)'
COUNT_A = re.compile(r'\b(' + ENG_ALT + r')\s+' + NUM + r'\s+of\s+' + NUM + r'\b')
COUNT_B = re.compile(r'\b' + NUM + r'\s+of\s+' + NUM + r'\s+(' + ENG_ALT + r')\b')
NONE_RE = re.compile(r'\b(' + ENG_ALT + r')\s+named\s+no\s+business(?:es)?(?:\s+in\s+any\s+of\s+its\s+' + NUM + r'\s+answer\s+days)?', re.I)
ALMOST_RE = re.compile(r'almost\s+no\s+overlap', re.I)
TEXT_EXT = ('.md', '.txt', '.html')


def n(s):
    return int(s.replace(',', ''))


def read_table(path):
    with open(path, encoding='utf-8', newline='') as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r['named_days'], r['answer_days'] = int(r['named_days']), int(r['answer_days'])
    return rows


def paragraphs(text):
    """(start line, paragraph text). A blank line or a markdown table row ends a paragraph."""
    out, buf, start = [], [], 1
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('|'):
            if buf:
                out.append((start, ' '.join(buf)))
                buf = []
            if line.strip():
                out.append((i, line))
            start = i + 1
            continue
        if not buf:
            start = i
        buf.append(line.strip())
    if buf:
        out.append((start, ' '.join(buf)))
    return out


class Table(object):
    def __init__(self, rows):
        self.rows = rows
        self.pairs = sorted({(r['market'], r['niche']) for r in rows})
        names = sorted({r['business'] for r in rows if len(r['business']) >= 4}, key=lambda s: (-len(s), s))
        self.biz_rx = re.compile('|'.join(r'(?<![A-Za-z0-9])' + re.escape(b).replace(r'\&', r'(?:&|and)').replace('&', '(?:&|and)')
                                          + r'(?![A-Za-z0-9])' for b in names), re.I) if names else None
        self.by_name = {}
        for b in names:
            self.by_name[b.casefold()] = b
        pr = sorted(self.pairs, key=lambda p: -len(p[0] + p[1]))
        self.pair_rx = re.compile('|'.join(re.escape('%s, %s' % p) for p in pr)) if pr else None

    def business_at(self, text):
        """[(start, business)] in text."""
        out = []
        if self.biz_rx:
            for m in self.biz_rx.finditer(text):
                key = re.sub(r'\band\b', '&', m.group(0).casefold())
                b = self.by_name.get(m.group(0).casefold()) or next((v for k, v in self.by_name.items()
                                                                     if re.sub(r'\band\b', '&', k) == key), None)
                if b:
                    out.append((m.start(), b))
        return out

    def pair_at(self, text):
        out = []
        if self.pair_rx:
            for m in self.pair_rx.finditer(text):
                mk, ni = m.group(0).split(', ', 1)
                for p in self.pairs:
                    if '%s, %s' % p == m.group(0):
                        out.append((m.start(), p))
                        break
        return out

    def select(self, engine, business=None, pair=None):
        return [r for r in self.rows if r['engine'] == engine and (business is None or r['business'] == business)
                and (pair is None or (r['market'], r['niche']) == pair)]


def before(items, pos):
    prior = [x for x in items if x[0] < pos]
    return prior[-1][1] if prior else None


def check_text(table, text, label='text'):
    """Returns (findings, claims checked). Each finding is a string starting with the label and line."""
    bad, checked = [], 0
    for line_no, para in paragraphs(text):
        biz = table.business_at(para)
        prs = table.pair_at(para)
        claims = [(m.start(), ENG_BY_LAB[m.group(1).casefold()], n(m.group(2)), n(m.group(3)), m.group(0)) for m in COUNT_A.finditer(para)]
        claims += [(m.start(), ENG_BY_LAB[m.group(3).casefold()], n(m.group(1)), n(m.group(2)), m.group(0)) for m in COUNT_B.finditer(para)]
        for pos, e, k, d, s in sorted(claims):
            b, p = before(biz, pos), before(prs, pos)
            if b is None and k != 0:
                continue   # a total or a denominator, not a per-business count
            checked += 1
            rows = table.select(e, b, p)
            if not any(r['named_days'] == k and r['answer_days'] == d for r in rows):
                have = ', '.join(sorted({'%d of %d' % (r['named_days'], r['answer_days']) for r in rows})[:4]) or 'no row'
                what = ('%s for %s' % (s, b)) if b else ('%s (zero count)' % s)
                bad.append('%s:%d %s%s differs from mentions_by_engine.csv (%s)' % (label, line_no, what,
                                                                                  ' in %s, %s' % p if p else '', have))
        for m in NONE_RE.finditer(para):
            checked += 1
            e, p = ENG_BY_LAB[m.group(1).casefold()], before(prs, m.start())
            rows = table.select(e, None, p) if p else []
            why = None
            if not p:
                why = 'no market and niche of the table named before it'
            elif not rows:
                why = 'no table row for %s in %s, %s' % (LAB[e], p[0], p[1])
            elif any(r['named_days'] for r in rows):
                why = '%s named %d businesses there in the table' % (LAB[e], sum(1 for r in rows if r['named_days']))
            elif m.group(2) and n(m.group(2)) != rows[0]['answer_days']:
                why = 'answer days %s, table %d' % (m.group(2), rows[0]['answer_days'])
            if why:
                bad.append('%s:%d "%s" without backing: %s' % (label, line_no, m.group(0), why))
        for m in ALMOST_RE.finditer(para):
            checked += 1
            bad.append('%s:%d "%s": not a count; give the overlap count from mentions_by_engine.csv' % (label, line_no, m.group(0)))
    return bad, checked


def pack_texts(pack):
    for root, dirs, files in os.walk(pack):
        rel = os.path.relpath(root, pack).replace(os.sep, '/')
        dirs[:] = sorted(d for d in dirs if not (rel == '.' and d in ('method', 'reproduce')) and d != '__pycache__')
        for f in sorted(files):
            if f.endswith(TEXT_EXT):
                yield os.path.join(root, f)


def run(table_path, files, base=None):
    table = Table(read_table(table_path))
    bad, checked = [], 0
    for f in files:
        b, c = check_text(table, open(f, encoding='utf-8').read(), os.path.relpath(f, base) if base else f)
        bad += b
        checked += c
    for x in bad:
        print('RED   ' + x)
    print('PARITY %s: %d files, %d claims checked, %d table rows, %d failing' % ('RED' if bad else 'GREEN', len(files), checked,
                                                                               len(table.rows), len(bad)))
    return 1 if bad else 0


# ------------------------------------------------------------------ selftest
def selftest():
    fails = []

    def chk(name, cond):
        print(('  ok   ' if cond else '  FAIL ') + name)
        if not cond:
            fails.append(name)
    tmp = tempfile.mkdtemp()
    tpath = os.path.join(tmp, 'mentions_by_engine.csv')
    rows = []
    for b, cnt in (('Salvi, Schostok & Pritchard', (18, 25, 19, 0)), ('Clifford Law Offices', (19, 9, 19, 0))):
        for e, k, d in zip(('openai', 'perplexity', 'gemini', 'anthropic'), cnt, (39, 37, 19, 40)):
            rows.append({'market': 'Chicago IL', 'niche': 'personal injury attorneys', 'business': b, 'engine': e, 'named_days': k,
                         'answer_days': d, 'window_start': '2026-09-01', 'window_end': '2026-10-11'})
    for e, k, d in (('openai', 0, 40), ('perplexity', 30, 37)):
        rows.append({'market': 'Tyler TX', 'niche': 'tanning salons', 'business': 'Sun Spot Tan', 'engine': e, 'named_days': k,
                     'answer_days': d, 'window_start': '2026-09-01', 'window_end': '2026-10-11'})
    with open(tpath, 'w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator='\n')
        w.writeheader()
        w.writerows(rows)
    table = Table(read_table(tpath))
    good = ('Chicago IL, personal injury attorneys, 2026-09-01 to 2026-10-11. Salvi, Schostok & Pritchard: OpenAI 18 of 39, '
            'Perplexity 25 of 37, Gemini 19 of 19, Anthropic 0 of 40. Clifford Law Offices: 19 of 39 OpenAI answer days.\n\n'
            'Tyler TX, tanning salons: OpenAI named no business in any of its 40 answer days.\n\n'
            '| OpenAI | 1,898 of 2,000 rows |\n\nOpenAI 235 of 235 rows had empty lists.\n')
    b, c = check_text(table, good)
    chk('GREEN: matching counts, backed zero, backed "named no business", totals ignored (%d claims, %s)' % (c, b), not b and c == 6)
    b, _ = check_text(table, 'Chicago IL, personal injury attorneys. Salvi, Schostok and Pritchard: OpenAI 18 of 39.')
    chk('GREEN: "and" for "&" in a business name', not b)
    cases = [
        ('RED: a per-engine count that differs (OpenAI 1 of 39, the recorded count)',
         'Chicago IL, personal injury attorneys: Salvi, Schostok & Pritchard, OpenAI 1 of 39.'),
        ('RED: the other word order differs (17 of 37 Perplexity)',
         'Chicago IL, personal injury attorneys: Salvi, Schostok & Pritchard was named on 17 of 37 Perplexity answer days.'),
        ('RED: a count in the wrong market', 'Tyler TX, tanning salons: Clifford Law Offices, OpenAI 19 of 39.'),
        ('RED: an engine 0 of N with no table row behind it', 'Chicago IL, personal injury attorneys: Gemini 0 of 19.'),
        ('RED: "named no business" while the table has a named business',
         'Chicago IL, personal injury attorneys: OpenAI named no business in any of its 39 answer days.'),
        ('RED: "named no business" with no market and niche', 'OpenAI named no business in any of its 40 answer days.'),
        ('RED: "named no business" with the wrong answer days', 'Tyler TX, tanning salons: OpenAI named no business in any of its 41 answer days.'),
        ('RED: "almost no overlap"', 'The four engines show almost no overlap in the businesses they name.'),
    ]
    for name, text in cases:
        b, _ = check_text(table, text)
        chk(name + ' (%d finding)' % len(b), len(b) == 1)
    # planted fixture on disk, through the command line path
    pack = os.path.join(tmp, 'pack')
    os.makedirs(os.path.join(pack, 'data'))
    os.makedirs(os.path.join(pack, 'method'))
    os.rename(tpath, os.path.join(pack, 'data', 'mentions_by_engine.csv'))
    open(os.path.join(pack, 'README.md'), 'w').write(good)
    open(os.path.join(pack, 'method', 'code.md'), 'w').write('almost no overlap\n')   # method/ is code, not read
    chk('GREEN: fixture pack', main_pack(pack) == 0)
    open(os.path.join(pack, 'README.md'), 'a').write('\nChicago IL, personal injury attorneys: Salvi, Schostok & Pritchard, OpenAI 1 of 39.\n')
    chk('RED: planted bad line in the fixture pack gives exit 1', main_pack(pack) == 1)
    print('SELFTEST ' + ('FAIL: ' + ', '.join(fails) if fails else 'PASS'))
    return not fails


def main_pack(pack):
    return run(os.path.join(pack, 'data', 'mentions_by_engine.csv'), list(pack_texts(pack)), pack)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pack')
    ap.add_argument('--table')
    ap.add_argument('files', nargs='*')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if a.pack:
        return main_pack(a.pack)
    if a.table and a.files:
        return run(a.table, a.files)
    ap.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
