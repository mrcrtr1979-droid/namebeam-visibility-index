#!/usr/bin/env python3
"""claims_registry.py: every public number of an edition, generated from the pack, each with its receipt (2026-10-14,
[FABLE-PARALLEL-1013E]).

Why: numbers typed by hand into pages, press notes, pitches and method notes drift from the record (the 2026-10-13 and
10-14 waves found a reversed finding, typed press figures, a blended four-engine denominator and stale windows on
eleven send-copy files). A registry built from the pack, and a checker (scripts/check_claims.py) that flags any number
on a surface that is not in it, closes that class: a surface may state a number only if the registry holds it.

What it writes (into the pack, so MANIFEST.sha256 pins it from the next build):
  CLAIMS.json   one object per claim: id, kind, sentence (ASCII, per-engine denominator), engine, window, value,
                numerator, denominator, unit, method_id, source (file in the pack and its manifest line or PACK.json
                field), script, generated_from (pack window and status).
  CLAIMS.md     the same, one line per claim, for people.
  claims.jsonld schema.org Dataset with one Observation per claim (measuredProperty = method id, value, denominator
                and window in the observation), for machines that read the page.

Claim kinds:
  pack      counts from PACK.json (answer rows, rows by kind, run days, sources rows, prompts, markets, niches, files).
  metric    every row of metrics/metrics_v2.csv (value, numerator, denominator, unit, engine, window, method id).
  business  one business in one cell under one rule, from scripts/dq5_counts.py business_counts (answers and days),
            given on the command line as --business "NAME|MARKET|NICHE|RULE" (repeatable). Needs corpus/e1.

Usage:
    python3 -I scripts/claims_registry.py --pack releases/edition-1 [--corpus corpus/e1] \
        [--business "Extreme Roofing|Miami FL|residential roofing|contains"] [--out DIR]
    python3 -I scripts/claims_registry.py --selftest
Standard library only. Writes only CLAIMS.json, CLAIMS.md and claims.jsonld in --out (default: the pack).
"""
import argparse
import csv
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
ENGINE_NAMES = {'openai': 'OpenAI', 'anthropic': 'Anthropic', 'gemini': 'Gemini', 'perplexity': 'Perplexity',
                'google_serp': 'Google results', 'agree': 'engine agreement'}


def fmt(n):
    try:
        return '{:,}'.format(int(n))
    except (TypeError, ValueError):
        return str(n)


def pct(value):
    try:
        return '%.1f percent' % (100.0 * float(value))
    except (TypeError, ValueError):
        return ''


def read_manifest(pack):
    m = {}
    p = os.path.join(pack, 'MANIFEST.sha256')
    if os.path.exists(p):
        for line in io.open(p, encoding='utf-8'):
            line = line.rstrip('\n')
            if '  ' in line:
                h, rel = line.split('  ', 1)
                m[rel] = h
    return m


def pack_claims(meta, manifest):
    w0, w1 = meta['window']
    src = {'file': 'PACK.json', 'sha256': manifest.get('PACK.json', '')}
    out = []

    def add(cid, field, value, sentence, unit):
        out.append({'id': cid, 'kind': 'pack', 'sentence': sentence, 'engine': '', 'window': [w0, w1], 'value': value,
                    'numerator': value, 'denominator': None, 'unit': unit, 'method_id': '', 'source': dict(src, field=field),
                    'script': 'scripts/build_edition.py'})
    add('pack.answers_rows', 'answers_rows', meta['answers_rows'], '%s answer rows, %s to %s.' % (fmt(meta['answers_rows']), w0, w1), 'answer rows')
    add('pack.run_days', 'run_days', meta['run_days'], '%d run days, %s to %s.' % (meta['run_days'], w0, w1), 'run days')
    for k, v in sorted((meta.get('rows_by_kind') or {}).items()):
        add('pack.rows_by_kind.' + k, 'rows_by_kind.' + k, v, '%s %s rows, %s to %s.' % (fmt(v), k, w0, w1), k + ' rows')
    if 'sources_rows' in meta:
        add('pack.sources_rows', 'sources_rows', meta['sources_rows'], '%s source rows, %s to %s.' % (fmt(meta['sources_rows']), w0, w1), 'source rows')
    if 'prompts_rows' in meta:
        add('pack.prompts_rows', 'prompts_rows', meta['prompts_rows'], '%d questions asked in the window.' % meta['prompts_rows'], 'questions')
    c = meta.get('counts') or {}
    for k in ('us_local_markets', 'nationwide_market_labels', 'niche_labels', 'segment_questions', 'named_business_questions'):
        if k in c:
            add('pack.counts.' + k, 'counts.' + k, c[k], '%s %s.' % (fmt(c[k]), k.replace('_', ' ')), k)
    nfiles = len(manifest) + (1 if manifest else 0)   # manifest lines plus MANIFEST.sha256 itself
    if manifest:
        add('pack.files', 'MANIFEST.sha256', len(manifest), '%d files in the manifest.' % len(manifest), 'files')
    return out


def metric_claims(pack, manifest):
    rel = 'metrics/metrics_v2.csv'
    p = os.path.join(pack, *rel.split('/'))
    if not os.path.exists(p):
        return []
    methods = {}
    mp = os.path.join(pack, 'metrics', 'metrics_v2_methods.csv')
    if os.path.exists(mp):
        for r in csv.DictReader(io.open(mp, encoding='utf-8', newline='')):
            methods[r.get('method_id', '')] = r
    out = []
    for i, r in enumerate(csv.DictReader(io.open(p, encoding='utf-8', newline='')), start=2):
        eng = ENGINE_NAMES.get(r['engine'], r['engine'])
        num, den = r.get('numerator', ''), r.get('denominator', '')
        unit = r.get('denominator_unit', '')
        desc = (methods.get(r['method_id']) or {}).get('description', '') or r['method_id']
        if num != '' and den != '':
            sentence = '%s, %s: %s of %s %s (%s), %s to %s, method %s.' % (
                eng, desc.rstrip('.'), fmt(num), fmt(den), unit, pct(r['value']), r['window_from'], r['window_to'], r['method_id'])
        else:
            sentence = '%s, %s: %s, %s to %s, method %s.' % (eng, desc.rstrip('.'), r['value'], r['window_from'], r['window_to'], r['method_id'])
        out.append({'id': 'metric.%s.%s.%s' % (r['window_id'], r['engine'], r['method_id']), 'kind': 'metric', 'sentence': sentence,
                    'engine': r['engine'], 'window': [r['window_from'], r['window_to']], 'value': _num(r['value']),
                    'numerator': _num(num), 'denominator': _num(den), 'unit': unit, 'method_id': r['method_id'],
                    'source': {'file': rel, 'sha256': manifest.get(rel, ''), 'line': i, 'n_cells': r.get('n_cells', '')},
                    'script': 'method/Brain_Kit/status/staged/1008E_S1_METRICS/receipts_metrics_v2.py'})
    return out


def _num(s):
    if s in ('', None):
        return None
    try:
        f = float(s)
        return int(f) if f.is_integer() and '.' not in str(s) else f
    except ValueError:
        return s


def business_claims(specs, corpus, w0, w1):
    if not specs:
        return []
    sys.path.insert(0, HERE)
    import dq5_counts as dq  # noqa: E402
    out = []
    cache = {}
    pw0, pw1 = w0, w1
    for spec in specs:
        parts = [x.strip() for x in spec.split('|')]
        name, market, niche, rule, s0, s1 = (parts + ['', '', 'contains', '', ''])[:6]
        w0, w1 = (s0 or pw0), (s1 or pw1)
        if (w0, w1) not in cache:
            cache[(w0, w1)] = dq.load_api_rows(corpus, w0, w1)
        rows = dq.filter_cell(cache[(w0, w1)], market or None, niche or None)
        res = dq.business_counts(rows, name, rule or 'contains')
        for e in sorted(res):
            c = res[e]
            eng = ENGINE_NAMES.get(e, e)
            base = 'business.%s.%s.%s.%s.%s_%s' % (name.casefold().replace(' ', '_'), e, rule, (market or 'all').casefold().replace(' ', '_'), w0, w1)
            src = {'file': 'corpus/e1', 'cell': '%s / %s' % (market, niche), 'rule': rule, 'name': name}
            out.append({'id': base + '.answers', 'kind': 'business', 'engine': e, 'window': [w0, w1], 'value': (c['rec'] / c['ok']) if c['ok'] else None,
                        'numerator': c['rec'], 'denominator': c['ok'], 'unit': 'answers with status OK', 'method_id': 'business.' + rule,
                        'sentence': '%s named %s in %d of its %d answers to the %s %s question, %s to %s (rule %s).' % (eng, name, c['rec'], c['ok'], market or 'all markets', niche or '', w0, w1, rule),
                        'source': src, 'script': 'scripts/dq5_counts.py --business'})
            out.append({'id': base + '.days', 'kind': 'business', 'engine': e, 'window': [w0, w1], 'value': (len(c['days_rec']) / len(c['days_ok'])) if c['days_ok'] else None,
                        'numerator': len(c['days_rec']), 'denominator': len(c['days_ok']), 'unit': 'days answered', 'method_id': 'business.' + rule,
                        'sentence': '%s named %s on %d of the %d days it answered the %s %s question, %s to %s; %d days asked (rule %s).' % (
                            eng, name, len(c['days_rec']), len(c['days_ok']), market or 'all markets', niche or '', w0, w1, len(c['days_asked']), rule),
                        'source': src, 'script': 'scripts/dq5_counts.py --business'})
            out.append({'id': base + '.days_asked', 'kind': 'business', 'engine': e, 'window': [w0, w1], 'value': (len(c['days_rec']) / len(c['days_asked'])) if c['days_asked'] else None,
                        'numerator': len(c['days_rec']), 'denominator': len(c['days_asked']), 'unit': 'days asked', 'method_id': 'business.' + rule,
                        'sentence': '%s named %s on %d of the %d days it was asked the %s %s question, %s to %s (%d days with no answer; rule %s).' % (
                            eng, name, len(c['days_rec']), len(c['days_asked']), market or 'all markets', niche or '', w0, w1, len(c['days_asked']) - len(c['days_ok']), rule),
                        'source': src, 'script': 'scripts/dq5_counts.py --business'})
    return out


def window_claims(pack, meta, manifest):
    """Run days per metrics window (WA, WB, WE ...) from the answers table dates: the pages quote them beside the metric."""
    rel = meta.get('files', {}).get('answers')
    mrel = 'metrics/metrics_v2.csv'
    mp = os.path.join(pack, *mrel.split('/'))
    if not rel or not os.path.exists(os.path.join(pack, *rel.split('/'))) or not os.path.exists(mp):
        return []
    dates = set()
    for r in csv.DictReader(io.open(os.path.join(pack, *rel.split('/')), encoding='utf-8', newline='')):
        dates.add(r['date'])
    windows = {}
    for r in csv.DictReader(io.open(mp, encoding='utf-8', newline='')):
        windows[r['window_id']] = (r['window_from'], r['window_to'])
    out = []
    for wid, (a, b) in sorted(windows.items()):
        n = sum(1 for d in dates if a <= d <= b)
        out.append({'id': 'window.%s.run_days' % wid, 'kind': 'pack', 'sentence': '%d run days in window %s, %s to %s.' % (n, wid, a, b),
                    'engine': '', 'window': [a, b], 'value': n, 'numerator': n, 'denominator': None, 'unit': 'run days',
                    'method_id': '', 'source': {'file': rel, 'sha256': manifest.get(rel, ''), 'field': 'date column, window %s' % wid},
                    'script': 'scripts/claims_registry.py'})
    return out


def ownsite_claims(pack, meta, manifest):
    """Own-domain citations per engine from data/customer_zero_daily.csv (field sources_cited): answers citing an owned
    domain of engine answers, and of answers that returned any URL; plus the per-domain hit counts from customer_zero_hits.csv."""
    rel = 'data/customer_zero_daily.csv'
    p = os.path.join(pack, *rel.split('/'))
    if not os.path.exists(p):
        return []
    w0, w1 = meta['window']
    agg = {}
    for r in csv.DictReader(io.open(p, encoding='utf-8', newline='')):
        if not (w0 <= r['date'] <= w1):
            continue
        k = (r['engine'], r['field'])
        a = agg.setdefault(k, {'answers': 0, 'with_urls': 0, 'ours': 0, 'days': set()})
        a['answers'] += int(r['engine_answers'] or 0)
        a['with_urls'] += int(r['rows_with_urls'] or 0)
        a['ours'] += int(r['answers_citing_ours'] or 0)
        a['days'].add(r['date'])
    out = []
    for (e, field), a in sorted(agg.items()):
        eng = ENGINE_NAMES.get(e, e)
        src = {'file': rel, 'sha256': manifest.get(rel, ''), 'field': field}
        out.append({'id': 'ownsite.%s.%s.of_answers' % (e, field), 'kind': 'ownsite', 'engine': e, 'window': [w0, w1],
                    'value': (a['ours'] / a['answers']) if a['answers'] else None, 'numerator': a['ours'], 'denominator': a['answers'],
                    'unit': 'engine answers', 'method_id': 'ownsite.' + field,
                    'sentence': '%s answers citing a Namebeam owned domain in %s: %d of %d answers, %s to %s.' % (eng, field, a['ours'], a['answers'], w0, w1),
                    'source': src, 'script': 'method/.../customer_zero.py'})
        out.append({'id': 'ownsite.%s.%s.of_answers_with_urls' % (e, field), 'kind': 'ownsite', 'engine': e, 'window': [w0, w1],
                    'value': (a['ours'] / a['with_urls']) if a['with_urls'] else None, 'numerator': a['ours'], 'denominator': a['with_urls'],
                    'unit': 'answers that returned a URL', 'method_id': 'ownsite.' + field,
                    'sentence': '%s answers citing a Namebeam owned domain in %s: %d of %d answers that returned a URL, %s to %s.' % (eng, field, a['ours'], a['with_urls'], w0, w1),
                    'source': src, 'script': 'method/.../customer_zero.py'})
        out.append({'id': 'ownsite.%s.%s.days' % (e, field), 'kind': 'ownsite', 'engine': e, 'window': [w0, w1], 'value': len(a['days']),
                    'numerator': len(a['days']), 'denominator': None, 'unit': 'run days', 'method_id': 'ownsite.' + field,
                    'sentence': '%s %s rows cover %d run days, %s to %s.' % (eng, field, len(a['days']), w0, w1), 'source': src, 'script': 'method/.../customer_zero.py'})
    hrel = 'data/customer_zero_hits.csv'
    hp = os.path.join(pack, *hrel.split('/'))
    if os.path.exists(hp):
        dom = {}
        for r in csv.DictReader(io.open(hp, encoding='utf-8', newline='')):
            if w0 <= r['date'] <= w1:
                dom[(r['engine'], r['domain'])] = dom.get((r['engine'], r['domain']), 0) + 1
        for (e, d), n in sorted(dom.items()):
            out.append({'id': 'ownsite.hits.%s.%s' % (e, d.replace('.', '_')), 'kind': 'ownsite', 'engine': e, 'window': [w0, w1], 'value': n,
                        'numerator': n, 'denominator': None, 'unit': 'source rows', 'method_id': 'ownsite.hits',
                        'sentence': '%s source rows pointing at %s: %d, %s to %s.' % (ENGINE_NAMES.get(e, e), d, n, w0, w1),
                        'source': {'file': hrel, 'sha256': manifest.get(hrel, '')}, 'script': 'method/.../customer_zero.py'})
    return out


def index_claims(corpus, start, end):
    """Per cell (market, niche), per engine: rows, answers OK, failed, days asked and answered; and per recorded business
    the answers and days it was named (a recorded name contains the business, case folded). This is what the generated
    AI referral index pages print, so their numbers can be checked against the record. Needs corpus/e1."""
    sys.path.insert(0, HERE)
    import dq5_counts as dq  # noqa: E402
    rows = dq.load_api_rows(corpus, start, end)
    cells = {}
    for r in rows:
        c = cells.setdefault((r['market'], r['niche'], r['engine']), {'rows': 0, 'ok': 0, 'days': set(), 'days_ok': set(), 'names': {}})
        c['rows'] += 1
        c['days'].add(r['date'])
        if r['status'] != 'OK':
            continue
        c['ok'] += 1
        c['days_ok'].add(r['date'])
        seen = set()
        for n in r['recorded']:
            k = ' '.join(str(n).casefold().split())
            if not k or k in seen:
                continue
            seen.add(k)
            d = c['names'].setdefault(k, {'display': n, 'answers': 0, 'days': set()})
            d['answers'] += 1
            d['days'].add(r['date'])
    out = []
    for (market, niche, e), c in sorted(cells.items()):
        eng = ENGINE_NAMES.get(e, e)
        cid = 'cell.%s.%s.%s' % (market.casefold().replace(' ', '_'), niche.casefold().replace(' ', '_'), e)
        src = {'file': 'corpus/e1', 'cell': '%s / %s' % (market, niche)}
        out.append({'id': cid + '.answered', 'kind': 'cell', 'engine': e, 'window': [start, end], 'value': (c['ok'] / c['rows']) if c['rows'] else None,
                    'numerator': c['ok'], 'denominator': c['rows'], 'unit': 'rows asked', 'method_id': 'cell.answered',
                    'sentence': '%s answered %d of %d rows asked for %s %s, %s to %s.' % (eng, c['ok'], c['rows'], market, niche, start, end), 'source': src, 'script': 'scripts/claims_registry.py'})
        out.append({'id': cid + '.failed', 'kind': 'cell', 'engine': e, 'window': [start, end], 'value': None, 'numerator': c['rows'] - c['ok'], 'denominator': c['rows'],
                    'unit': 'rows asked', 'method_id': 'cell.failed',
                    'sentence': '%s failed on %d of %d rows asked for %s %s, %s to %s.' % (eng, c['rows'] - c['ok'], c['rows'], market, niche, start, end), 'source': src, 'script': 'scripts/claims_registry.py'})
        out.append({'id': cid + '.days', 'kind': 'cell', 'engine': e, 'window': [start, end], 'value': None, 'numerator': len(c['days_ok']), 'denominator': len(c['days']),
                    'unit': 'days asked', 'method_id': 'cell.days',
                    'sentence': '%s answered on %d of %d days asked for %s %s, %s to %s.' % (eng, len(c['days_ok']), len(c['days']), market, niche, start, end), 'source': src, 'script': 'scripts/claims_registry.py'})
        for k, d in sorted(c['names'].items(), key=lambda kv: (-kv[1]['answers'], kv[0])):
            nid = cid + '.named.' + k.replace(' ', '_')[:60]
            out.append({'id': nid + '.days_asked', 'kind': 'index', 'engine': e, 'window': [start, end], 'value': None, 'numerator': len(d['days']), 'denominator': len(c['days']),
                        'unit': 'days asked', 'method_id': 'index.contains',
                        'sentence': '%s named %s on %d of %d days asked for %s %s, %s to %s.' % (eng, d['display'], len(d['days']), len(c['days']), market, niche, start, end), 'source': src, 'script': 'scripts/claims_registry.py'})
            out.append({'id': nid + '.answers', 'kind': 'index', 'engine': e, 'window': [start, end], 'value': None, 'numerator': d['answers'], 'denominator': c['ok'],
                        'unit': 'answers with status OK', 'method_id': 'index.contains',
                        'sentence': '%s named %s in %d of %d answers for %s %s, %s to %s.' % (eng, d['display'], d['answers'], c['ok'], market, niche, start, end), 'source': src, 'script': 'scripts/claims_registry.py'})
    return out


def jsonld(meta, claims):
    obs = []
    for c in claims:
        o = {'@type': 'Observation', 'name': c['id'], 'description': c['sentence'], 'observationDate': c['window'][1],
             'measuredProperty': c['method_id'] or c['kind'], 'value': c['value'] if c['value'] is not None else c['numerator']}
        if c['denominator'] is not None:
            o['measurementTechnique'] = '%s of %s %s' % (c['numerator'], c['denominator'], c['unit'])
        if c['engine']:
            o['observationAbout'] = ENGINE_NAMES.get(c['engine'], c['engine'])
        obs.append(o)
    return {'@context': 'https://schema.org', '@type': 'Dataset', 'name': 'Namebeam AI Visibility Record, Edition %s' % meta.get('edition', 1),
            'temporalCoverage': '%s/%s' % tuple(meta['window']), 'license': 'https://creativecommons.org/licenses/by/4.0/',
            'variableMeasured': obs}


def generate(pack, out, corpus=None, business=(), index_from=None, index_to=None):
    meta = json.load(io.open(os.path.join(pack, 'PACK.json'), encoding='utf-8'))
    manifest = read_manifest(pack)
    claims = pack_claims(meta, manifest) + window_claims(pack, meta, manifest) + metric_claims(pack, manifest) + ownsite_claims(pack, meta, manifest)
    if business and corpus:
        claims += business_claims(business, corpus, meta['window'][0], meta['window'][1])
    if index_from and corpus:
        claims += index_claims(corpus, index_from, index_to or meta['window'][1])
    reg = {'edition': meta.get('edition', 1), 'status': meta.get('status', ''), 'window': meta['window'], 'generated_by': 'scripts/claims_registry.py',
           'rule': 'A public surface may state a number only if this registry holds it with the same numerator, denominator, engine and window.',
           'claims': claims}
    os.makedirs(out, exist_ok=True)
    with io.open(os.path.join(out, 'CLAIMS.json'), 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(reg, fh, indent=1, sort_keys=True)
        fh.write('\n')
    with io.open(os.path.join(out, 'CLAIMS.md'), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('# Claims registry, Edition %s (%s), window %s to %s\n\n' % (reg['edition'], reg['status'], meta['window'][0], meta['window'][1]))
        fh.write(reg['rule'] + ' Generated by scripts/claims_registry.py; one line per claim: id | sentence | source.\n\n')
        for c in claims:
            s = c['source']
            fh.write('- %s | %s | %s%s\n' % (c['id'], c['sentence'], s.get('file', ''), (' line %s' % s['line']) if s.get('line') else (' field %s' % s['field']) if s.get('field') else ''))
    with io.open(os.path.join(out, 'claims.jsonld'), 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(jsonld(meta, claims), fh, indent=1, sort_keys=True)
        fh.write('\n')
    return reg


def selftest():
    fails = []

    def chk(name, ok):
        print(('  ok   ' if ok else '  FAIL ') + name)
        if not ok:
            fails.append(name)
    tmp = tempfile.mkdtemp()
    pack = os.path.join(tmp, 'pack')
    os.makedirs(os.path.join(pack, 'metrics'))
    meta = {'edition': 1, 'status': 'FINAL', 'window': ['2026-09-01', '2026-09-04'], 'run_days': 3, 'answers_rows': 10,
            'rows_by_kind': {'API': 6, 'SERP': 2, 'AGREE': 2}, 'sources_rows': 7, 'prompts_rows': 2, 'counts': {'us_local_markets': 2, 'niche_labels': 3}}
    json.dump(meta, io.open(os.path.join(pack, 'PACK.json'), 'w', encoding='utf-8'))
    with io.open(os.path.join(pack, 'metrics', 'metrics_v2.csv'), 'w', encoding='utf-8', newline='') as fh:
        w = csv.writer(fh)
        w.writerow('version,window_id,window_from,window_to,engine,engine_regime,metric,method_id,name_set,statistic,value,numerator,denominator,denominator_unit,n_cells,note'.split(','))
        w.writerow(['v2', 'WE', '2026-09-01', '2026-09-04', 'perplexity', 'x', 'N', 'N.gated.day', 'gated', 'share', '0.75', '3', '4', 'answered cell days', '2', ''])
        w.writerow(['v2', 'WE', '2026-09-01', '2026-09-04', 'anthropic', 'x', 'N', 'N.gated.day', 'gated', 'share', '0.25', '1', '4', 'answered cell days', '2', ''])
    with io.open(os.path.join(pack, 'metrics', 'metrics_v2_methods.csv'), 'w', encoding='utf-8', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['method_id', 'description'])
        w.writerow(['N.gated.day', 'Share of answered cell days with a gated business name'])
    io.open(os.path.join(pack, 'MANIFEST.sha256'), 'w', encoding='utf-8').write('a' * 64 + '  PACK.json\n' + 'b' * 64 + '  metrics/metrics_v2.csv\n')
    reg = generate(pack, pack)
    ids = {c['id'] for c in reg['claims']}
    chk('pack claims carry rows, run days, kinds, sources, prompts and counts', {'pack.answers_rows', 'pack.run_days', 'pack.rows_by_kind.API', 'pack.sources_rows', 'pack.prompts_rows', 'pack.counts.us_local_markets'} <= ids)
    m = [c for c in reg['claims'] if c['id'] == 'metric.WE.perplexity.N.gated.day'][0]
    chk('metric claim: numerator, denominator, unit, engine, window, method id and manifest sha from the row', m['numerator'] == 3 and m['denominator'] == 4 and m['unit'] == 'answered cell days' and m['engine'] == 'perplexity' and m['window'] == ['2026-09-01', '2026-09-04'] and m['source']['sha256'] == 'b' * 64 and m['source']['line'] == 2)
    chk('metric sentence names the engine, both numbers, the unit, the percent and the window', m['sentence'] == 'Perplexity, Share of answered cell days with a gated business name: 3 of 4 answered cell days (75.0 percent), 2026-09-01 to 2026-09-04, method N.gated.day.')
    chk('no claim sentence blends engines (every metric sentence names exactly one engine)', all(sum(1 for e in ENGINE_NAMES.values() if e in c['sentence']) == 1 for c in reg['claims'] if c['kind'] == 'metric'))
    chk('files written: CLAIMS.json, CLAIMS.md, claims.jsonld', all(os.path.exists(os.path.join(pack, f)) for f in ('CLAIMS.json', 'CLAIMS.md', 'claims.jsonld')))
    ld = json.load(io.open(os.path.join(pack, 'claims.jsonld'), encoding='utf-8'))
    chk('jsonld: one Observation per claim with the denominator in measurementTechnique', len(ld['variableMeasured']) == len(reg['claims']) and any(o.get('measurementTechnique') == '3 of 4 answered cell days' for o in ld['variableMeasured']))
    txt = io.open(os.path.join(pack, 'CLAIMS.md'), encoding='utf-8').read()
    chk('CLAIMS.md is ASCII', all(ord(ch) < 128 for ch in txt))
    chk('RED: a registry without the metrics table still writes the pack claims (no crash)', bool(generate(_pack_without_metrics(tmp), os.path.join(tmp, 'o2'))['claims']))
    print('SELFTEST ' + ('FAIL: ' + ', '.join(fails) if fails else 'PASS'))
    return not fails


def _pack_without_metrics(tmp):
    p = os.path.join(tmp, 'p2')
    os.makedirs(p, exist_ok=True)
    json.dump({'edition': 1, 'status': 'STAGED', 'window': ['2026-09-01', '2026-09-02'], 'run_days': 2, 'answers_rows': 4}, io.open(os.path.join(p, 'PACK.json'), 'w', encoding='utf-8'))
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pack')
    ap.add_argument('--out')
    ap.add_argument('--corpus', default=os.path.join(REPO, 'corpus', 'e1'))
    ap.add_argument('--business', action='append', default=[], help='"NAME|MARKET|NICHE|RULE", repeatable')
    ap.add_argument('--index-from', help='also register per-cell and per-business counts from corpus/e1 from this date (the referral index pages)')
    ap.add_argument('--index-to', help='end date for --index-from (default: the pack window end)')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if not a.pack:
        ap.error('--pack is required')
    reg = generate(a.pack, a.out or a.pack, a.corpus, a.business, a.index_from, a.index_to)
    kinds = {}
    for c in reg['claims']:
        kinds[c['kind']] = kinds.get(c['kind'], 0) + 1
    print('claims %d (%s) -> %s' % (len(reg['claims']), ', '.join('%s %d' % kv for kv in sorted(kinds.items())), a.out or a.pack))
    return 0


if __name__ == '__main__':
    sys.exit(main())
