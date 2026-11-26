#!/usr/bin/env python3
"""1012A-CUTS: one-page market cut per market/niche from the raw corpus. Reuses the Edition 1 builder (answers_rows,
heading filter) and the 1005A name gate (aggregate) and the v1 metrics functions. Stdlib + reportlab.
v2 (1012A-FULKIT): white-label. Usage:
python3 -I make_cuts_v2.py REPO OUTDIR --brand "Agency Name" --markets "Dallas TX|personal injury attorneys;Atlanta GA|hvac repair and replacement" [--end 2026-10-11] [--start 2026-09-01] [--story]
Brand appears in header and footer; the only Namebeam text is the source line "data: Namebeam public record"."""
import sys, os, re, csv, json, glob, hashlib, unicodedata, statistics
from collections import defaultdict, Counter
from urllib.parse import urlparse
import argparse
ap = argparse.ArgumentParser()
ap.add_argument('repo'); ap.add_argument('out')
ap.add_argument('--brand', default='Sample Agency')
ap.add_argument('--markets', required=True, help='semicolon list of "Market|niche"')
ap.add_argument('--start', default='2026-09-01'); ap.add_argument('--end', default='2026-10-11')
ap.add_argument('--story', action='store_true')
A = ap.parse_args()
REPO, OUT = A.repo, A.out
BRAND = A.brand.strip()
os.makedirs(os.path.join(OUT, 'csv'), exist_ok=True)
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(REPO, 'scripts'))
import build_edition as be
M = os.path.join(REPO, 'releases/edition-1/method/Brain_Kit/status/staged')
sys.path.insert(0, os.path.join(M, '1005A_AI_PICKS_CHART'))
import gen_ai_picks_chart as g
gci = g.gci
spec_path = os.path.join(M, '1005B_METRICS', 'receipts_metrics_v1.py')
import importlib.util
sp = importlib.util.spec_from_file_location('v1', spec_path); v1 = importlib.util.module_from_spec(sp); sp.loader.exec_module(v1)

W0, W1 = A.start, A.end
PPLX_CHANGE = '2026-09-28'
def _cell(spec):
    mk, ni = [x.strip() for x in spec.split('|')]
    city = re.sub(r'\s+[A-Z]{2}$', '', mk)
    lab = ni.replace('hvac', 'HVAC')
    return (mk, ni, city, lab)
CELLS = [_cell(s) for s in A.markets.split(';') if s.strip()]
ENG = ['openai', 'anthropic', 'perplexity', 'gemini']
LAB = {'openai': 'OpenAI', 'anthropic': 'Anthropic', 'perplexity': 'Perplexity', 'gemini': 'Gemini'}
quar = g.load_quarantine(os.path.join(M, '..', '..', 'returns', 'COMET_1004', 'SERP_DRIFT_QUARANTINE_2026-10-04.csv'))

# status per raw file
status = {}
for p in glob.glob(os.path.join(REPO, 'corpus/e1/NB-CZ-API_*.json')) + glob.glob(os.path.join(REPO, 'corpus/e1/NB-CZ-SERP_*.json')):
    try:
        d = json.load(open(p)); status[os.path.basename(p)] = d.get('run_status'), d.get('organic_top_results')
    except Exception:
        pass
allrows = be.answers_rows(os.path.join(REPO, 'corpus/e1'))
print('answers_rows', len(allrows))

def clean(s):
    s = s.replace('–', '-').replace('—', '-')
    s = unicodedata.normalize('NFKD', s).encode('latin-1', 'ignore').decode('latin-1')
    return re.sub(r'\s+', ' ', s).strip()

def esc(s):
    return s

RUN_DENY = {'spoliation letter', 'board certification', 'manual d', 'epa 608', 'fort worth', 'houston lawyer referral service', 'georgia power', 'av preeminent'}
SUFFIX = {'law', 'firm', 'offices', 'office', 'llp', 'llc', 'pllc', 'pc', 'p', 'c', 'pa', 'inc', 'co', 'company'}
RUNLOG = {'denied': Counter(), 'merged': Counter()}
def canon(disp):
    t = re.sub(r'[^a-z0-9& ]', ' ', clean(disp).casefold().replace('.', ''))
    w = t.split()
    while w and w[-1] in SUFFIX and len(w) > 1:
        w.pop()
    c = ' '.join(w)
    if c in RUN_DENY or ' '.join(t.split()) in RUN_DENY:
        return None
    return c
summary = []
for mk, ni, city, nilabel in CELLS:
    rows = [r for r in allrows if r['market'] == mk and r['niche'] == ni and W0 <= r['date'] <= W1]
    api = [r for r in rows if r['kind'] == 'API' and r['engine'] in ENG]
    ok = [r for r in api if status.get(r['source_file'], (None,))[0] == 'OK']
    notok = Counter((r['engine'], status.get(r['source_file'], ('?',))[0]) for r in api if r not in ok)
    data, st = g.aggregate(ok, quar)
    ser = {}
    asked = {}
    for e in ENG:
        s = {'answered': set(), 'named': defaultdict(set)}
        dn = {}
        for ws in data[mk]:
            c = data[mk][ws][e]
            s['answered'] |= c['answered']
            for key, (disp0, days) in c['named'].items():
                cn = canon(disp0)
                if cn is None:
                    RUNLOG['denied'][disp0] += len(days); continue
                if cn != re.sub(r'[^a-z0-9 ]', '', key.casefold()).strip() and key != cn:
                    pass
                ent = dn.setdefault(cn, [disp0, set()])
                if len(disp0) < len(ent[0]): ent[0] = disp0
                if key != cn and disp0 != ent[0]: RUNLOG['merged'][(disp0, ent[0])] += 1
                ent[1] |= days
                for d in days:
                    s['named'][d].add(cn)
        ser[e] = (s, dn)
        asked[e] = {r['date'] for r in ok if r['engine'] == e}
    dates = sorted({r['date'] for r in ok})
    # table: businesses by sum of answer-days named across the four engines
    disp = {}; cnt = defaultdict(lambda: defaultdict(int))
    for e in ENG:
        for key, (dsp, days) in ser[e][1].items():
            disp.setdefault(key, dsp)
            cnt[key][e] = len(days)
    order = sorted(cnt, key=lambda k: (-sum(cnt[k].values()), -len([e for e in ENG if cnt[k][e]]), disp[k].casefold()))
    tot_asked = sum(len(asked[e]) for e in ENG)
    top = order[:10]
    table = [(clean(disp[k]), {e: cnt[k][e] for e in ENG}, sum(cnt[k].values())) for k in top]
    # metrics per engine
    met = {}
    for e in ENG:
        s = ser[e][0]
        nr = v1.name_rate(s)
        # persistence; perplexity: skip pair crossing the collection-path change
        s2 = {'answered': set(s['answered']), 'named': {d: set(v) for d, v in s['named'].items()}}
        pc = v1.persistence_counts(s2)
        num = sum(a for a, b in pc.values()); den = sum(b for a, b in pc.values())
        if e == 'perplexity':
            pre = v1.nextday  # exclude pair (09-27 -> 09-28)
            d0 = '2026-09-27'
            if d0 in s['answered'] and '2026-09-28' in s['answered']:
                bn = s['named'].get(d0, set()); nn = s['named'].get('2026-09-28', set())
                num -= len(bn & nn); den -= len(bn)
        ch = v1.churn(s)
        met[e] = dict(asked=len(asked[e]), answered_named=nr[1], name_rate_den=nr[2], p_num=num, p_den=den,
                      churn=ch[0], churn_pairs=ch[2])
    pairs = {}
    for i in range(len(ENG)):
        for j in range(i + 1, len(ENG)):
            a = v1.agreement(ser[ENG[i]][0], ser[ENG[j]][0])
            pairs[(ENG[i], ENG[j])] = a
    allvals = []
    for (a, b) in pairs:
        s1, s2 = ser[a][0], ser[b][0]
        for d in sorted(s1['answered'] & s2['answered']):
            x, y = s1['named'].get(d, set()), s2['named'].get(d, set())
            u = x | y
            if u: allvals.append(len(x & y) / len(u))
    agree_mean = statistics.mean(allvals) if allvals else None
    agree_med = statistics.median(allvals) if allvals else None
    # Google page
    gk = []
    for r in rows:
        if r['kind'] == 'SERP' and r['source_file'] not in quar:
            gk.append(r)
    gok = [r for r in gk if status.get(r['source_file'], (None,))[0] == 'OK' and status[r['source_file']][1]]
    hostdays = defaultdict(set)
    for r in gok:
        for u in status[r['source_file']][1]:
            h = urlparse(u).netloc.lower()
            h = h[4:] if h.startswith('www.') else h
            if not h: continue
            reg = gci.registrable(h)
            if reg in gci.PLATFORM_HOSTS or h in gci.PLATFORM_HOSTS: continue
            hostdays[reg].add(r['date'])
    gtop = sorted(hostdays.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:5]
    gfail = len(gk) - len(gok)
    summary.append(dict(mk=mk, ni=ni, city=city, nilabel=nilabel, dates=dates, table=table, met=met, pairs=pairs, agree_mean=agree_mean,
                        agree_med=agree_med, agree_n=len(allvals), gtop=[(h, len(d)) for h, d in gtop], gok=len(gok), gtot=len(gk), gfail=gfail,
                        notok=dict(notok), tot_asked=tot_asked, nrows=len(api), gate=st, ok_n=len(ok)))
    print('RUNLOG', dict(RUNLOG['denied']), dict(RUNLOG['merged'])) if False else None
    print(city, ni, dates[0], dates[-1], len(dates), 'ok', len(ok), '/', len(api), dict(notok), 'serp ok', len(gok), '/', len(gk))

# ---------------- outputs
def slug(s): return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
INK = colors.HexColor('#1b2430'); MUTE = colors.HexColor('#5b6674'); LINE = colors.HexColor('#d5dbe3'); ACC = colors.HexColor('#0e6b5c'); BG = colors.HexColor('#f2f5f8')
sH = ParagraphStyle('h', fontName='Helvetica-Bold', fontSize=14, leading=17.5, textColor=INK)
sB = ParagraphStyle('b', fontName='Helvetica', fontSize=8.6, leading=11.4, textColor=INK)
sS = ParagraphStyle('s', fontName='Helvetica', fontSize=7.4, leading=9.6, textColor=MUTE)
sK = ParagraphStyle('k', fontName='Helvetica-Bold', fontSize=9, leading=12, textColor=ACC)
sC = ParagraphStyle('c', fontName='Helvetica', fontSize=8, leading=9.6, textColor=INK)
sCB = ParagraphStyle('cb', fontName='Helvetica-Bold', fontSize=7.6, leading=9.2, textColor=INK)
sF = ParagraphStyle('f', fontName='Helvetica-Bold', fontSize=9, leading=12, textColor=INK)
def pct(n, d): return ('%.0f%%' % (100.0 * n / d)) if d else 'n/a'
def xd(d): 
    import datetime
    return datetime.date.fromisoformat(d).strftime('%b %-d, %Y')
files = []
for S in summary:
    city, nl = S['city'], S['nilabel']
    base = '%s_AI_VISIBILITY_%s_%s_%s' % (slug(BRAND).upper().replace('-', ''), city.replace(' ', ''), slug(S['ni']), W1)
    dts = S['dates']; M_ = S['met']
    # finding
    t = S['table']
    pers = {e: (M_[e]['p_num'], M_[e]['p_den']) for e in ENG}
    top1 = t[0] if t else None
    named_total = sum(M_[e]['answered_named'] for e in ENG)
    if top1:
        parts = ['%s %d of %d' % (LAB[e], top1[1][e], M_[e]['asked']) for e in ENG if top1[1][e]]
        none_e = ['%s named no business in %d of %d answers' % (LAB[e], M_[e]['asked'] - M_[e]['answered_named'], M_[e]['asked']) for e in ENG if M_[e]['asked'] and M_[e]['answered_named'] == 0]
        finding = ('%s was named in %d of %d answers received (%s). On the same day, two engines\' name lists shared a mean of %s of the names either listed (%d engine-pair days)%s.' % (
            top1[0], top1[2], S['tot_asked'], ', '.join(parts),
            ('%.0f%%' % (100 * S['agree_mean'])) if S['agree_mean'] is not None else 'n/a', S['agree_n'],
            ('; ' + '; '.join(none_e)) if none_e else ''))
    else:
        finding = 'No business names survived the name filter in this market for the window.'
    S['finding'] = finding
    # CSV table
    tcsv = os.path.join(OUT, 'csv', base + '_table.csv')
    with open(tcsv, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['market', 'niche', 'window_start', 'window_end', 'rank_by_total', 'business', 'openai_named', 'openai_asked', 'anthropic_named', 'anthropic_asked',
                    'perplexity_named', 'perplexity_asked', 'gemini_named', 'gemini_asked', 'total_named', 'total_asked'])
        for i, (nm, c, tot) in enumerate(t, 1):
            w.writerow([S['mk'], S['ni'], dts[0], dts[-1], i, nm] + sum([[c[e], M_[e]['asked']] for e in ENG], []) + [tot, S['tot_asked']])
        w.writerow([])
        w.writerow(['google_page_site', 'days_in_first_page_results', 'ok_pages_in_window', 'extraction_failed_pages'])
        for h, n in S['gtop']:
            w.writerow([h, n, S['gok'], S['gfail']])
    mcsv = os.path.join(OUT, 'csv', base + '_metrics.csv')
    with open(mcsv, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['metric', 'scope', 'value', 'numerator', 'denominator', 'denominator_unit', 'note'])
        for e in ENG:
            m = M_[e]
            w.writerow(['answers_asked_ok', e, m['asked'], m['asked'], m['asked'], 'OK answers in window', ''])
            w.writerow(['name_rate', e, '%.4f' % (m['answered_named'] / m['name_rate_den']) if m['name_rate_den'] else 'n/a', m['answered_named'], m['name_rate_den'], 'answered days (>=1 raw name)', 'v1 N, gated'])
            w.writerow(['next_day_persistence', e, '%.4f' % (m['p_num'] / m['p_den']) if m['p_den'] else 'n/a', m['p_num'], m['p_den'], 'business-day namings with next day answered', 'v1 P, gated' + ('; pair 09-27 to 09-28 excluded (collection path change)' if e == 'perplexity' else '')])
            w.writerow(['churn', e, '%.4f' % m['churn'] if m['churn'] is not None else 'n/a', '', m['churn_pairs'], 'consecutive answered day pairs', 'v1 C, 1 minus Jaccard'])
        for (a, b), (mean, ssum, n) in S['pairs'].items():
            w.writerow(['engine_agreement_jaccard_mean', '%s|%s' % (a, b), '%.4f' % mean if mean is not None else 'n/a', '%.4f' % ssum, n, 'days both engines answered with >=1 name', 'v1 A, gated'])
        w.writerow(['engine_agreement_jaccard_mean', 'all six pairs pooled', '%.4f' % S['agree_mean'] if S['agree_mean'] is not None else 'n/a', '', S['agree_n'], 'engine-pair days', 'median %.4f' % S['agree_med'] if S['agree_med'] is not None else ''])
        w.writerow(['google_page_ok_extractions', 'google_serp', S['gok'], S['gok'], S['gtot'], 'daily Google page pulls in window (excluding quarantined)', 'failed extractions %d' % S['gfail']])
        w.writerow(['not_ok_api_rows', 'all', sum(S['notok'].values()), '', S['nrows'], 'API rows in window', json.dumps({'%s:%s' % k: v for k, v in S['notok'].items()})])
    # PDF
    pdf = os.path.join(OUT, base + '.pdf')
    doc = SimpleDocTemplate(pdf, pagesize=letter, leftMargin=0.6 * inch, rightMargin=0.6 * inch, topMargin=0.5 * inch, bottomMargin=0.45 * inch,
                            title='Who AI names for %s in %s, data through %s' % (nl, city, xd(W1)), author=BRAND)
    el = [Paragraph('%s  |  AI visibility market cut' % clean(BRAND).upper(), ParagraphStyle('x', parent=sS, fontName='Helvetica-Bold', textColor=ACC)), Spacer(1, 3),
          Paragraph('Who AI names for %s in %s, data through %s' % (nl, city, xd(W1)), sH), Spacer(1, 4),
          Paragraph('Window %s to %s, %d days with answers. Each cell reads: answers naming the business / answers received from that engine. Names are as the engines returned them after the Edition 1 name filter (section headings, directories and places removed).' % (xd(dts[0]), xd(dts[-1]), len(dts)), sS), Spacer(1, 7)]
    hdr = [Paragraph('Business named', sCB)] + [Paragraph(LAB[e], sCB) for e in ENG] + [Paragraph('All four', sCB)]
    data_ = [hdr]
    for nm, c, tot in t:
        data_.append([Paragraph(nm, sC)] + ['%d / %d' % (c[e], M_[e]['asked']) for e in ENG] + ['%d / %d' % (tot, S['tot_asked'])])
    tb = Table(data_, colWidths=[2.35 * inch] + [0.96 * inch] * 4 + [1.0 * inch], repeatRows=1)
    tb.setStyle(TableStyle([('FONT', (1, 1), (-1, -1), 'Helvetica', 8), ('FONT', (1, 0), (-1, 0), 'Helvetica-Bold', 7.6), ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
                            ('LINEBELOW', (0, 0), (-1, 0), 0.8, INK), ('LINEBELOW', (0, 1), (-1, -1), 0.3, LINE), ('BACKGROUND', (0, 0), (-1, 0), BG),
                            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('TOPPADDING', (0, 0), (-1, -1), 2.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5)]))
    el += [Paragraph('Businesses named most often (10 shown, ordered by total answers naming them)', sK), Spacer(1, 3), tb, Spacer(1, 8)]
    # metrics table
    mh = [Paragraph(x, sCB) for x in ['Engine', 'Answers received', 'Named >=1 business', 'Next-day persistence']]
    md = [mh]
    for e in ENG:
        m = M_[e]
        md.append([LAB[e], str(m['asked']), '%s (%d / %d)' % (pct(m['answered_named'], m['name_rate_den']), m['answered_named'], m['name_rate_den']),
                   '%s (%d / %d)' % (pct(m['p_num'], m['p_den']), m['p_num'], m['p_den']) if m['p_den'] else 'not computable'])
    mt = Table(md, colWidths=[1.4 * inch, 1.5 * inch, 2.2 * inch, 2.2 * inch])
    mt.setStyle(TableStyle([('FONT', (0, 1), (-1, -1), 'Helvetica', 8), ('LINEBELOW', (0, 0), (-1, 0), 0.8, INK), ('LINEBELOW', (0, 1), (-1, -1), 0.3, LINE),
                            ('BACKGROUND', (0, 0), (-1, 0), BG), ('TOPPADDING', (0, 0), (-1, -1), 2.2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.2)]))
    nk = {}
    for (e_, st_), n_ in S['notok'].items(): nk[LAB[e_]] = nk.get(LAB[e_], 0) + n_
    S['nk_txt'] = ('Answers not received (API error or quota block, not counted): ' + ', '.join('%s %d' % kv for kv in sorted(nk.items())) + '.') if nk else ''
    el += [Paragraph('How steady the answers are', sK), Spacer(1, 3), mt, Spacer(1, 3),
           Paragraph('Persistence: of the businesses an engine named on one day, the share it named again the next day (next day answered). Engine agreement: on the same day, the overlap (Jaccard) between two engines\' name lists, mean %s over %d engine-pair days (all six pairs; OpenAI and Anthropic seldom name a business, which lowers it). Perplexity and Gemini only: %s over %d days.%s' % (
               ('%.2f' % S['agree_mean']) if S['agree_mean'] is not None else 'n/a', S['agree_n'],
               ('%.2f' % S['pairs'][('perplexity', 'gemini')][0]) if S['pairs'][('perplexity', 'gemini')][0] is not None else 'not computable', S['pairs'][('perplexity', 'gemini')][2],
               ' Perplexity changed collection path on Sep 28, 2026; the pair spanning that date is left out of its persistence. ' + S['nk_txt']), sS), Spacer(1, 7)]
    if S['gtop']:
        gd = [[Paragraph('Sites in the Google results page', sCB), Paragraph('Days shown', sCB)]]
        for h, n in S['gtop']:
            gd.append([h, '%d / %d' % (n, S['gok'])])
        gt = Table(gd, colWidths=[3.6 * inch, 1.4 * inch], hAlign='LEFT')
        gt.setStyle(TableStyle([('FONT', (0, 1), (-1, -1), 'Helvetica', 8), ('ALIGN', (1, 0), (1, -1), 'RIGHT'), ('LINEBELOW', (0, 0), (-1, 0), 0.8, INK), ('LINEBELOW', (0, 1), (-1, -1), 0.3, LINE),
                                ('BACKGROUND', (0, 0), (-1, 0), BG), ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]))
        el += [Paragraph('Google results page (directories removed)', sK), Spacer(1, 3), gt, Spacer(1, 2),
               Paragraph('%d of %d daily Google page pulls in the window returned readable results; the rest failed to extract and are not counted.' % (S['gok'], S['gtot']), sS), Spacer(1, 7)]
    else:
        el += [Paragraph('Google results page: not computable for this window (%d of %d pulls returned readable results).' % (S['gok'], S['gtot']), sS), Spacer(1, 7)]
    el += [Paragraph('Finding', sK), Spacer(1, 2), Paragraph(finding, sB), Spacer(1, 7),
           Paragraph('Method. Four AI engines (OpenAI, Anthropic, Perplexity, Gemini) are asked the same fixed question about this market every day through their APIs, plus a daily Google results page pull. OpenAI, Anthropic and Gemini run without a search tool; Perplexity runs with web search on; Anthropic temperature 0.2. These are API calls, not the consumer apps.', sS),
           Spacer(1, 8),
           Paragraph('Prepared by %s.' % clean(BRAND), sF), Spacer(1, 2),
           Paragraph('data: Namebeam public record', sS)]
    doc.build(el)
    files += [pdf, tcsv, mcsv]
    print(base, 'built')
json.dump([{k: v for k, v in S.items() if k in ('city', 'ni', 'finding', 'dates')} for S in summary], open(os.path.join(OUT, 'csv', '_cuts_summary.json'), 'w'), default=str, indent=1)

print('DENIED', dict(RUNLOG['denied']))
print('MERGED', {'%s -> %s' % k: v for k, v in RUNLOG['merged'].items()})

# ---------------- data story (one page, all markets, denominators)
if A.story:
    sp_ = os.path.join(OUT, '%s_AI_VISIBILITY_DATA_STORY_%s.pdf' % (slug(BRAND).upper().replace('-', ''), W1))
    doc = SimpleDocTemplate(sp_, pagesize=letter, leftMargin=0.55 * inch, rightMargin=0.55 * inch, topMargin=0.5 * inch, bottomMargin=0.45 * inch,
                            title='Data story: who AI names across %d markets' % len(summary), author=BRAND)
    rows = [[Paragraph(x, sCB) for x in ['Market and niche', 'Days', 'Answers received', 'Most-named business', 'Named in', 'Engines naming no business']]]
    for S in summary:
        t = S['table'][0] if S['table'] else None
        none_e = [LAB[e] for e in ENG if S['met'][e]['asked'] and S['met'][e]['answered_named'] == 0]
        rows.append([Paragraph('%s, %s' % (S['city'], S['nilabel']), sC), str(len(S['dates'])), str(S['tot_asked']),
                     Paragraph(t[0] if t else 'none after filter', sC), ('%d / %d' % (t[2], S['tot_asked'])) if t else 'n/a',
                     Paragraph(', '.join(none_e) if none_e else 'none', sC)])
    tb = Table(rows, colWidths=[1.75 * inch, 0.42 * inch, 0.8 * inch, 1.85 * inch, 0.8 * inch, 1.5 * inch], repeatRows=1)
    tb.setStyle(TableStyle([('FONT', (1, 1), (-1, -1), 'Helvetica', 8), ('ALIGN', (1, 0), (2, -1), 'RIGHT'), ('ALIGN', (4, 0), (4, -1), 'RIGHT'),
                            ('LINEBELOW', (0, 0), (-1, 0), 0.8, INK), ('LINEBELOW', (0, 1), (-1, -1), 0.3, LINE), ('BACKGROUND', (0, 0), (-1, 0), BG),
                            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('TOPPADDING', (0, 0), (-1, -1), 2.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5)]))
    ag = [S['agree_mean'] for S in summary if S['agree_mean'] is not None]
    tot_ans = sum(S['tot_asked'] for S in summary)
    eng_tot = {e: sum(S['met'][e]['asked'] for S in summary) for e in ENG}
    eng_named = {e: sum(S['met'][e]['answered_named'] for S in summary) for e in ENG}
    eng_tb = [[Paragraph(x, sCB) for x in ['Engine', 'Answers received', 'Answers naming at least one business']]]
    for e in ENG:
        eng_tb.append([LAB[e], str(eng_tot[e]), '%s (%d / %d)' % (pct(eng_named[e], eng_tot[e]), eng_named[e], eng_tot[e])])
    et = Table(eng_tb, colWidths=[1.6 * inch, 1.8 * inch, 3.2 * inch], hAlign='LEFT')
    et.setStyle(TableStyle([('FONT', (0, 1), (-1, -1), 'Helvetica', 8), ('LINEBELOW', (0, 0), (-1, 0), 0.8, INK), ('LINEBELOW', (0, 1), (-1, -1), 0.3, LINE),
                            ('BACKGROUND', (0, 0), (-1, 0), BG), ('TOPPADDING', (0, 0), (-1, -1), 2.2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.2)]))
    nmk = len(summary)
    el = [Paragraph('%s  |  Data story' % clean(BRAND).upper(), ParagraphStyle('x', parent=sS, fontName='Helvetica-Bold', textColor=ACC)), Spacer(1, 3),
          Paragraph('Which businesses AI engines name across %d local markets, data through %s' % (nmk, xd(W1)), sH), Spacer(1, 4),
          Paragraph('Window %s to %s. Four AI engines were asked the same fixed question about each market every day. Every count below is shown as named / answers received, so the denominator is visible.' % (xd(W0), xd(W1)), sS), Spacer(1, 7),
          Paragraph('The ten markets', sK), Spacer(1, 3), tb, Spacer(1, 8),
          Paragraph('Engines across all %d markets (%d answers received)' % (nmk, tot_ans), sK), Spacer(1, 3), et, Spacer(1, 3),
          Paragraph('Answers not received (API error or quota block) are excluded from every denominator.', sS), Spacer(1, 8),
          Paragraph('What the data shows', sK), Spacer(1, 2),
          Paragraph('1. The most-named business in each market was named in %s to %s of the answers received (counts and denominators in the table). Share of answers is not a ranking or a measure of customers.' % (min(pct(S['table'][0][2], S['tot_asked']) for S in summary if S['table']), max(pct(S['table'][0][2], S['tot_asked']) for S in summary if S['table'])), sB), Spacer(1, 2),
          Paragraph('2. The engines disagree with each other. On the same day, two engines\' name lists shared a mean of %s of the names either listed (mean of the %d per-market means; each market\'s own figure is in its page).' % (('%.0f%%' % (100 * statistics.mean(ag))) if ag else 'n/a', len(ag)), sB), Spacer(1, 2),
          Paragraph('3. Engines differ in how often they name a business at all, per the engine table above. Where an engine names none, that market page says so.', sB), Spacer(1, 7),
          Paragraph('Use with care. Counts describe what four API-called engines returned for one fixed question in the window. They are not rankings, not a measure of customers or revenue, and say nothing about why an engine named a business.', sS), Spacer(1, 8),
          Paragraph('Prepared by %s.' % clean(BRAND), sF), Spacer(1, 2), Paragraph('data: Namebeam public record', sS)]
    doc.build(el)
    print('story built', sp_)
