#!/usr/bin/env python3
"""Siri panel table for the Namebeam AI Visibility record (Seat S1-4).

Folds the filed Siri panel notes (typed runs 2026-10-03 to 2026-10-06, spoken run 2026-10-07) into one
dated table, one row per Siri answer turn:

    date, mode, day_in_mode, q_id, turn, city, scope, question_text, question_text_source, tag,
    siri_said, siri_said_source, businesses_named, n_businesses_named, sources_shown, source_names,
    sources_captured, format, note, source_file, source_sha256

How the rows are made (nothing is invented):
  * KEYED below holds the businesses, tag, format and sources for each answer, copied by hand from the
    human-read tables in the filed notes.
  * siri_said is text taken from the notes themselves: for the spoken run the speech transcript
    (a local speech-to-text pass, audio errors left as produced), for typed runs the on-screen answer
    paragraph read by OCR (OCR errors left as produced), and empty where the notes hold no text.
  * Provenance checks run on every build and stop it (exit 2) when they fail: every named business and
    every source label must occur in the filed note it came from; the S, C, W counts per day must equal the
    "Tag totals" line of that note; the question count per day must equal the note's table.

The filed notes stay in the Brain (they hold mailbox identifiers and internal remarks); this table carries
the SHA-256 of each note so a row can be tied to the exact file it was read from.

Tags (SIRI_PANEL_PROTOCOL v3): S = Siri answered itself, C = handed to ChatGPT, W = web links alone or no help.

Usage:
    python3 -I scripts/build_siri.py --src BRAIN/corpus --out datasets/e1/siri
    python3 -I scripts/build_siri.py --selftest

Standard library.
"""
import argparse
import csv
import hashlib
import io
import os
import re
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))

COLUMNS = ['date', 'mode', 'day_in_mode', 'q_id', 'turn', 'city', 'scope', 'question_text', 'question_text_source',
           'tag', 'siri_said', 'siri_said_source', 'businesses_named', 'n_businesses_named', 'sources_shown',
           'source_names', 'sources_captured', 'format', 'note', 'source_file', 'source_sha256']

PROTOCOL_Q = {
    'Q1': ('St. Louis, MO', 'city', 'Who is the best interior painting company in St. Louis MO?'),
    'Q2': ('St. Peters, MO', 'city', 'What is a good martial arts school for kids in St. Peters MO?'),
    'Q3': ('St. Louis, MO', 'city', 'Who is the best digital marketing agency in St. Louis?'),
    'Q4': ('', 'near me', 'Who is the best interior painter near me?'),
    'Q5': ('', 'nationwide', 'What is the best service to check if AI engines like ChatGPT recommend my small business?'),
    'Q6': ('Dallas, TX', 'city', 'Who is the best dentist in Dallas TX?'),
    'Q7': ('Houston, TX', 'city', 'Who is the best personal injury lawyer in Houston for a car wreck?'),
}

def K(date, q, turn, tag, biz, fmt, chips, names, note='', captured=True, said_override=None, q_override=None, mode=None):
    mode = mode or ('spoken' if fmt.startswith('spoken') else 'typed')
    return dict(date=date, q=q, turn=turn, tag=tag, biz=biz, fmt=fmt, chips=chips, names=names, note=note,
                captured=captured, said_override=said_override, q_override=q_override, mode=mode)


def note_path(date, mode):
    return 'siri/SIRI_%s.md' % date if mode == 'typed' else 'voice/%s/SPOKEN_%s.md' % (date, date)


def load_jsonl(path):
    """Extra keyed answers filed by the nightly intake: one JSON object per line with the keys of K()."""
    import json
    out = []
    if not os.path.exists(path):
        return out
    with io.open(path, encoding='utf-8') as fh:
        for n, ln in enumerate(fh, 1):
            ln = ln.strip()
            if not ln:
                continue
            d = json.loads(ln)
            out.append(K(d['date'], d['q'], int(d.get('turn', 1)), d['tag'], d.get('biz', []), d.get('fmt', ''), d.get('chips', ''),
                         d.get('names', []), d.get('note', ''), bool(d.get('captured', True)), None, d.get('q_override'), d.get('mode')))
    return out


def merge_keyed(base, extra):
    seen = {(k['date'], k['mode'], k['q'], k['turn']) for k in base}
    return base + [k for k in extra if (k['date'], k['mode'], k['q'], k['turn']) not in seen]


def day_in_mode(keyed):
    out = {}
    for mode in ('typed', 'spoken'):
        for n, d in enumerate(sorted({k['date'] for k in keyed if k['mode'] == mode}), 1):
            out[(d, mode)] = n
    return out


KEYED = [
    # ---- 2026-10-03, typed day 1 (screenshots only in the Brain; no OCR text filed)
    K('2026-10-03', 'Q1', 1, 'S', ['Beckner Painting & Contracting', 'Loomis Painting Services', 'Kennedy Painting', 'Improovy Painters St. Louis', 'CertaPro Painters'], 'text + table', 'yelp.com +2 (+4 on the table)', ['yelp.com']),
    K('2026-10-03', 'Q2', 1, 'S', ['World Martial Arts Academy', 'Gracie Barra Jiu-Jitsu St. Peters', 'All Star Karate Academy', 'ATA Martial Arts St. Peters', "Andre's Academy"], 'text + list', 'blackbeltstl.com +3 (+5 on the list)', ['blackbeltstl.com']),
    K('2026-10-03', 'Q3', 1, 'S', ['Timmermann Group', 'Matchbox Design Group', 'Captiva Marketing', 'Funnel Boost Media', 'Gorilla 76'], 'text + list', 'yelp.com +4; themanifest.com +2', ['yelp.com', 'themanifest.com']),
    K('2026-10-03', 'Q4', 1, 'W', [], 'failure', '', [], "Siri: \"I can't find your location. Please make sure Location Services is turned on in Settings.\"", captured=False),
    K('2026-10-03', 'Q4', 2, 'W', [], 'failure', '', [], 'After the phone owner replied "They are on", Siri showed the Location Services toggle ON and did not answer.', captured=False),
    K('2026-10-03', 'Q5', 1, 'S', ['HubSpot AEO', 'Otterly.AI', 'Rankscale', 'Trakkr', 'Peec AI'], 'text + table', 'Elfsight +3; SEO Blog by Ahrefs +1; Meltwater +2', ['Elfsight', 'SEO Blog by Ahrefs', 'Meltwater'], 'Also advised manual testing for free in a logged-out incognito window.'),
    # ---- 2026-10-04, typed day 2
    K('2026-10-04', 'Q1', 1, 'S', ['Beckner Painting & Contracting', 'Loomis Painting Services', 'Kennedy Painting', 'Bright Brush Creations', 'LIME Painting of St. Louis'], 'text + list', 'yelp.com +2', ['yelp.com'], 'The list puts LIME Painting of St. Louis before Bright Brush Creations.'),
    K('2026-10-04', 'Q2', 1, 'S', ['World Martial Arts Academy', 'ATA Martial Arts', 'Gracie Barra Jiu-Jitsu', 'All Star Karate Academy', "Andre's Academy"], 'text + list', 'blackbeltstl.com +3 (+4 on the list)', ['blackbeltstl.com']),
    K('2026-10-04', 'Q3', 1, 'S', ['Timmermann Group', 'Matchbox Design Group', 'Captiva Marketing', 'Abstrakt Marketing Group', 'Funnel Boost Media'], 'text + list', 'themanifest.com +4 (+3 on the list)', ['themanifest.com']),
    K('2026-10-04', 'Q4', 1, 'W', [], 'failure', '', [], 'Question typed as "Whims the best interior painter near me?" (typo). Siri could not find the location.', captured=False),
    K('2026-10-04', 'Q4', 2, 'W', [], 'failure', '', [], 'After the reply "It\'s on", Siri said it was still having trouble finding the location.', captured=False),
    K('2026-10-04', 'Q5', 1, 'S', ['LeadGeneratorX', 'AIRIX', 'HubSpot AEO', 'Beamtrace', 'Otterly.AI', 'Ahrefs Brand Radar'], 'text + table', 'Lead Generator X +3; business.com +1; NEO360 +1', ['Lead Generator X', 'business.com', 'NEO360'], 'Prices as Siri stated them: HubSpot AEO as little as $50 a month, Beamtrace $20 a month, Otterly.AI $29 a month. Also advised manual testing logged out in an incognito window.'),
    # ---- 2026-10-05, typed day 3
    K('2026-10-05', 'Q1', 1, 'S', ['Beckner Painting & Contracting', 'Loomis Painting Services', 'Kennedy Painting', 'Improovy Painters St. Louis', 'Bright Brush Creations'], 'text + list', 'yelp.com +3', ['yelp.com'], 'List order shown; the intro sentence puts Bright Brush Creations before Improovy Painters St. Louis.'),
    K('2026-10-05', 'Q2', 1, 'S', ['World Martial Arts Academy', 'Gracie Barra Jiu-Jitsu St. Peters', 'ATA Martial Arts', "Andre's Academy"], 'text + list', 'blackbeltstl.com +2 (+4 on the list)', ['blackbeltstl.com']),
    K('2026-10-05', 'Q3', 1, 'S', ['Timmermann Group', 'Matchbox Design Group', 'Captiva Marketing', 'Funnel Boost Media', 'Gorilla 76'], 'text + list', 'clutch.co +3; themanifest.com +3', ['clutch.co', 'themanifest.com']),
    K('2026-10-05', 'Q4', 1, 'W', [], 'failure', '', [], 'Siri could not access the current location on the opening ask.', captured=False),
    K('2026-10-05', 'Q4', 2, 'W', ['Five Star Painting of St. Charles', 'Beckner Painting & Contracting', 'Better Painting', 'DiPasquale Painting'], 'text + list', 'yelp.com +1', ['yelp.com'], 'Second reply, after the phone owner typed "It\'s on"; the reply placed the search near Saint Peters, Missouri.'),
    K('2026-10-05', 'Q5', 1, 'S', ['HubSpot AEO', 'Otterly.AI', 'Rankability', 'Rankscale', 'Beamtrace'], 'text + table', 'Elfsight +2; NEO360 +2; Meltwater +2', ['Elfsight', 'NEO360', 'Meltwater'], 'No prices on screen.'),
    # ---- 2026-10-06, typed day 4 (no Q5 that day)
    K('2026-10-06', 'Q1', 1, 'S', ['Beckner Painting & Contracting', 'Loomis Painting Services', 'Kennedy Painting', 'Improovy Painters St. Louis', 'Bright Brush Creations'], 'text + list', 'yelp.com +2', ['yelp.com'], 'List order shown; the intro sentence puts Bright Brush Creations before Improovy Painters St. Louis.'),
    K('2026-10-06', 'Q2', 1, 'S', ['World Martial Arts Academy', 'Gracie Barra Jiu-Jitsu St. Peters', 'ATA Martial Arts St. Peters', "Andre's Academy"], 'text + list', 'blackbeltstl.com +2 (+3 on the list)', ['blackbeltstl.com']),
    K('2026-10-06', 'Q3', 1, 'S', ['Abstrakt Marketing Group', 'Timmermann Group', 'Matchbox Design Group', 'Captiva Marketing', 'Funnel Boost Media'], 'text + list', 'bizjournals.com +4', ['bizjournals.com'], 'Intro order is Abstrakt, Timmermann, Matchbox, Funnel Boost, Captiva; list order is Abstrakt, Timmermann, Matchbox, Captiva, Funnel Boost.'),
    K('2026-10-06', 'Q4', 1, 'W', [], 'text + tips, no names', 'thumbtack.com +5', ['thumbtack.com'], 'No business named. Siri pointed to the platforms Angi, Thumbtack and Yelp and gave hiring tips; the Better Business Bureau appears in the tips.'),
    K('2026-10-06', 'Q4', 2, 'W', [], 'text + tips, no names', 'thumbtack.com +3; homeguide.com +3', ['thumbtack.com', 'homeguide.com'], 'Same question typed again. No business named. Siri said it could not determine the exact location and pointed to the platforms Thumbtack, Angi, HomeGuide and Yelp.'),
    # ---- 2026-10-07, spoken day 1 of protocol v3
    K('2026-10-07', 'Q1', 1, 'S', ['Beckner Painting & Contracting', 'Kennedy Painting', 'Improovy Painters St. Louis', 'CertaPro Painters'], 'spoken paragraph; list below not spoken', 'yelp.com +3', ['yelp.com'], 'Names are those spoken. A "Top Interior Painting" list below the paragraph was on screen and not read aloud.',
      q_override='Who is the best interior painter painting company in St. Louis Missouri'),
    K('2026-10-07', 'Q2', 1, 'S', ['World Martial Arts Academy', 'ATA Martial Arts', 'Gracie Barra Jiu-Jitsu', 'All Star Karate Academy'], 'spoken paragraph', '', [], 'Names are those spoken. No source chip captured.', captured=False,
      q_override='What is a good martial arts school for kids in Saint peters Missouri'),
    K('2026-10-07', 'Q3', 1, 'S', ['Timmermann Group', 'Matchbox Design Group', 'Captiva Marketing', 'Abstrakt Marketing Group'], 'spoken paragraph', '', [], 'Names are those spoken. No source chip captured.', captured=False,
      q_override='Who is the best digital marketing agency in St. Louis'),
    K('2026-10-07', 'Q6', 1, 'S', ['Dental House', 'W Dental', 'The Dentist On Skillman', 'Kessler Park Dental', 'Preston Family Dentistry'], 'spoken paragraph; list below not spoken', '', [], 'Names are those spoken. A list headed "Here are a few highly rated dental practices in Dallas based on patient reviews" was on screen and not read aloud. No source chip captured.', captured=False,
      q_override='Who is the best dentist in Dallas Texas'),
    K('2026-10-07', 'Q7', 1, 'S', ['Scott C. Krist', 'Robert E. Ammons', 'Greg Baumgartner (Baumgartner Law Firm)', 'Sutliff and Stout', 'Joe Isade and Associates'], 'spoken paragraph', 'spoken: Emmonslaw.com, Forbes.com and other sources', ['Emmonslaw.com', 'Forbes.com'], 'Sixth firm heard as "Joe Isade and Associates": audio alone, spelling not verified. Source names are from the audio and their spelling is not verified.',
      q_override='Who is the best personal injury lawyer in Houston for a car wreck'),
]

# spoken transcript ranges (seconds) per question, from the filed transcript
SPOKEN_RANGE = {'Q1': (19.8, 36.8), 'Q2': (36.8, 112.8), 'Q3': (112.8, 170.1), 'Q6': (170.1, 219.8), 'Q7': (219.8, 257.8)}


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for ch in iter(lambda: fh.read(1 << 20), b''):
            h.update(ch)
    return h.hexdigest()


def norm(s):
    return re.sub(r'[^a-z0-9]+', ' ', s.lower()).strip()


def clean_ocr(s):
    s = re.sub(r'\s+', ' ', s).strip()
    s = re.sub(r'(^|[.?!] )\| ', r'\1I ', s)         # OCR reads a leading capital I as a bar
    s = re.sub(r'\bAl\b', 'AI', s)
    return s


STOP = re.compile(r"^(Here (are|is)\b|When |Tips |Top |Best |Key |Comparison|Service |What AI|Manual|e |[-*] |\d+\. |Compare |Check |Verify |Understand )")


def ocr_blocks(text):
    i = text.find('## RAW OCR')
    return text[i:] if i >= 0 else ''


NOISE = re.compile(r"^(\(\+\)|oo cc|eo cc|Dw e$|Doe e$|It's on$)|\+\d|\bJ Co\b|_ _|\bCCC\b")
Q_MARKERS = {'Q1': 'painting company', 'Q2': 'martial arts', 'Q3': 'digital marketing', 'Q4': 'painter near me', 'Q5': 'service to'}


def _collect(lines, j):
    """Text lines from j on, skipping leading blanks, until a blank, a list marker or screen chrome."""
    out = []
    while j < len(lines):
        t = lines[j].strip()
        if not t:
            if out:
                break
            j += 1
            continue
        if STOP.match(t) or t.startswith('```') or t.startswith('###') or NOISE.search(t):
            break
        out.append(t)
        j += 1
    txt = clean_ocr(' '.join(out))
    if txt and not re.search(r'[.?!"\')]$', txt):
        txt += ' [cut at the screen edge]'
    return txt


def _question_ends(lines, marker):
    """Indexes just after each question bubble whose joined text contains marker (bubbles start 'Who is' / 'What is' / 'Whims')."""
    ends = []
    for i, ln in enumerate(lines):
        if re.match(r'^(Who is|What is|Whims)\b', ln.strip()):
            j = i
            while j < len(lines) and not lines[j].rstrip().endswith('?') and j - i < 5:
                j += 1
            if marker in ' '.join(x.strip() for x in lines[i:j + 1]):
                ends.append(j + 1)
    return ends


def ocr_answer(ocr, q, occurrence=1, after_marker=False):
    """Answer paragraph after the occurrence-th bubble for question q. With after_marker, the paragraph after the typed
    follow-up line "It's on" instead."""
    lines = ocr.split('\n')
    ends = _question_ends(lines, Q_MARKERS[q])
    if len(ends) < occurrence:
        return ''
    j = ends[occurrence - 1]
    if after_marker:
        while j < len(lines) and lines[j].strip() != "It's on":
            if lines[j].startswith('###'):
                return ''
            j += 1
        j += 1
    return _collect(lines, j)


def spoken_said(text, q):
    lo, hi = SPOKEN_RANGE[q]
    out = []
    for m in re.finditer(r'^\[\s*([\d.]+)-\s*([\d.]+)\]\s*(.*)$', text, re.M):
        a = float(m.group(1))
        if lo <= a < hi:
            out.append(m.group(3).strip())
    return ' '.join(out)


def typed_said(date, text, k, turn_counts):
    said, src = _typed_said(date, text, k)
    if not said and src == 'ocr':
        src = 'none: no matching text in the OCR'
    return said, src


def _typed_said(date, text, k):
    ocr = ocr_blocks(text)
    if not ocr:
        return '', 'none: the screenshots are not transcribed in the filed note'
    q = k['q']
    if q == 'Q4':
        occ = k['turn'] if date == '2026-10-06' else 1
        return ocr_answer(ocr, q, occurrence=occ, after_marker=(k['turn'] == 2 and date != '2026-10-06')), 'ocr'
    if q in Q_MARKERS:
        return ocr_answer(ocr, q), 'ocr'
    return '', 'none'


def tag_totals(text):
    m = re.search(r'Tag totals:\s*S\s*(\d+),\s*C\s*(\d+),\s*W\s*(\d+)', text)
    return tuple(int(x) for x in m.groups()) if m else None


def build_rows(src):
    rows = []; problems = []
    texts = {}
    shas = {}
    dim = day_in_mode(KEYED)
    for date, mode in sorted({(k['date'], k['mode']) for k in KEYED}):
        rel = note_path(date, mode)
        p = os.path.join(src, rel)
        if not os.path.exists(p):
            problems.append('missing note %s' % rel)
            continue
        texts[(date, mode)] = io.open(p, encoding='utf-8').read()
        shas[(date, mode)] = sha256(p)
    for k in KEYED:
        date, mode = k['date'], k['mode']
        if (date, mode) not in texts:
            continue
        rel = note_path(date, mode)
        text = texts[(date, mode)]
        city, scope, qtext = PROTOCOL_Q[k['q']]
        qsrc = 'protocol wording; screenshots not transcribed' if date == '2026-10-03' else 'on screen, read from the filed note'
        if k['q_override']:
            qtext = k['q_override']
        elif date == '2026-10-04' and k['q'] == 'Q4':
            qtext = 'Whims the best interior painter near me?'
        if mode == 'spoken':
            said, ssrc = spoken_said(text, k['q']), 'speech transcript (audio errors left as produced)'
        else:
            said, ssrc = typed_said(date, text, k, None)
            ssrc = ssrc.replace('ocr', 'ocr of the on-screen text (OCR errors left as produced)', 1) if ssrc == 'ocr' else ssrc
        ntext = norm(text)
        for b in k['biz']:
            if norm(b) not in ntext:
                problems.append('%s %s: business "%s" does not occur in %s' % (date, k['q'], b, rel))
        for sname in k['names']:
            if norm(sname) not in ntext:
                problems.append('%s %s: source "%s" does not occur in %s' % (date, k['q'], sname, rel))
        rows.append([date, mode, dim[(date, mode)], k['q'], k['turn'], city, scope, qtext, qsrc, k['tag'], said, ssrc,
                     '; '.join(k['biz']), len(k['biz']), k['chips'], '; '.join(k['names']), 'yes' if k['captured'] else 'no',
                     k['fmt'], k['note'], os.path.basename(rel), shas[(date, mode)]])
    # per-day checks: tag totals and question counts
    for (date, mode), text in texts.items():
        ks = [k for k in KEYED if k['date'] == date and k['mode'] == mode]
        per_q = {}
        for k in ks:
            per_q.setdefault(k['q'], k['tag'])
        got = (sum(1 for t in per_q.values() if t == 'S'), sum(1 for t in per_q.values() if t == 'C'), sum(1 for t in per_q.values() if t == 'W'))
        want = tag_totals(text)
        # some notes print the day's counts, one prints the running total of the typed run
        cum = [0, 0, 0]
        seen = {}
        for k in KEYED:
            if k['date'] <= date and k['mode'] == mode:
                seen.setdefault((k['date'], k['q']), k['tag'])
        for t in seen.values():
            cum['SCW'.index(t)] += 1
        if want is not None and got != want and tuple(cum) != want:
            problems.append('%s: keyed tags S/C/W %s (running %s) != filed Tag totals %s' % (date, got, tuple(cum), want))
    return rows, problems


def print_summary(rows):
    """Counts with their denominators. A question is one (date, mode, q_id); its tag is the same on every turn."""
    qs = {}
    for r in rows:
        qs.setdefault((r[0], r[1], r[3]), r[9])
    tags = {t: sum(1 for v in qs.values() if v == t) for t in 'SCW'}
    named = {}
    for r in rows:
        key = (r[0], r[1], r[3])
        named[key] = named.get(key, 0) + (1 if r[13] else 0)
    us = sum(1 for r in rows if 'namebeam' in r[12].lower() or 'namebeam' in r[10].lower())
    print('questions %d (S %d, C %d, W %d); typed %d, spoken %d; questions with at least one business named %d of %d; rows mentioning Namebeam %d of %d'
          % (len(qs), tags['S'], tags['C'], tags['W'], sum(1 for k in qs if k[1] == 'typed'), sum(1 for k in qs if k[1] == 'spoken'),
             sum(1 for v in named.values() if v), len(named), us, len(rows)))


def write_csv(path, rows):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with io.open(path, 'w', encoding='utf-8', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(COLUMNS)
        w.writerows(rows)


# ------------------------------------------------------------------ selftest
def selftest():
    fails = []

    def chk(name, ok):
        print(('  ok   ' if ok else '  FAIL ') + name)
        if not ok:
            fails.append(name)

    fx = os.path.join(REPO, 'tests', 'fixtures', 'siri')
    global KEYED
    saved = KEYED
    try:
        KEYED = [K('2026-01-01', 'Q1', 1, 'S', ['Acme Painting', 'Beta Brush'], 'text + list', 'yelp.com +2', ['yelp.com']),
                 K('2026-01-01', 'Q4', 1, 'W', [], 'failure', '', [], 'location failure', captured=False),
                 K('2026-01-02', 'Q1', 1, 'S', ['Acme Painting', 'Gamma Colors'], 'spoken paragraph', '', [], '', captured=False,
                   q_override='Who is the best interior painting company in Testville')]
        rows, problems = build_rows(fx)
        out = os.path.join(fx, '_out.csv')
        write_csv(out, rows)
        got = io.open(out, encoding='utf-8', newline='').read()
        os.remove(out)
        want = io.open(os.path.join(fx, 'expected_siri.csv'), encoding='utf-8', newline='').read()
        # source_sha256 differs per checkout only if the fixture notes change; the expected file carries the fixture hashes
        chk('table equals the hand-written expected file byte for byte', got == want)
        chk('no provenance problems on the fixture', problems == [])
        chk('spoken said comes from the transcript range', rows[2][10].startswith('Some of the top painters include Acme Painting'))
        chk('typed said comes from OCR and fixes the leading bar', rows[0][10] == "I can help: Acme Painting and Beta Brush are well rated.")
        import json, tempfile
        tmp = tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False, encoding='utf-8')
        tmp.write(json.dumps({'date': '2026-01-01', 'q': 'Q1', 'turn': 1, 'tag': 'S', 'biz': ['X'], 'fmt': 'text'}) + '\n')
        tmp.write(json.dumps({'date': '2026-01-03', 'q': 'Q2', 'turn': 1, 'tag': 'S', 'biz': ['Y'], 'fmt': 'text', 'chips': 'a +1', 'names': ['a']}) + '\n')
        tmp.close()
        merged = merge_keyed(KEYED, load_jsonl(tmp.name))
        os.remove(tmp.name)
        chk('jsonl intake adds new answers and does not duplicate an existing one', len(merged) == len(KEYED) + 1 and merged[-1]['date'] == '2026-01-03')
        # RED proofs
        KEYED_BAD = list(KEYED)
        KEYED = KEYED_BAD[:]; KEYED[0] = K('2026-01-01', 'Q1', 1, 'S', ['Acme Painting', 'Invented Co'], 'text + list', 'yelp.com +2', ['yelp.com'])
        _, pb = build_rows(fx)
        chk('RED: a business that is not in the filed note is caught', any('Invented Co' in x for x in pb))
        KEYED = KEYED_BAD[:]; KEYED[1] = K('2026-01-01', 'Q4', 1, 'S', [], 'failure', '', [], '', captured=False)
        _, pb = build_rows(fx)
        chk('RED: a tag that disagrees with the note Tag totals is caught', any('Tag totals' in x for x in pb))
        KEYED = KEYED_BAD[:]; KEYED[0] = K('2026-01-01', 'Q1', 1, 'S', ['Acme Painting'], 'text + list', 'madeup.example +2', ['madeup.example'])
        _, pb = build_rows(fx)
        chk('RED: a source label that is not in the filed note is caught', any('madeup.example' in x for x in pb))
    finally:
        KEYED = saved
    print('SELFTEST ' + ('FAIL: ' + ', '.join(fails) if fails else 'PASS'))
    return not fails


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', help='Brain corpus directory holding siri/ and voice/')
    ap.add_argument('--out', default=os.path.join(REPO, 'datasets', 'e1', 'siri'))
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    if not a.src:
        ap.error('--src is required')
    global KEYED
    KEYED = merge_keyed(KEYED, load_jsonl(os.path.join(a.src, 'siri', 'siri_keyed.jsonl')))
    rows, problems = build_rows(a.src)
    write_csv(os.path.join(a.out, 'siri_panel.csv'), rows)
    print('wrote %s (%d rows, %d dates)' % (os.path.join(a.out, 'siri_panel.csv'), len(rows), len({r[0] for r in rows})))
    print_summary(rows)
    for p in problems:
        print('PROBLEM', p)
    if problems:
        return 2
    print('PROVENANCE OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
