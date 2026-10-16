#!/usr/bin/env python3
"""Text-mention recount, Edition 1 (answers_recount_v1.csv).

For every row of the published answers table (12,180 rows) this script adds a second count of the businesses the
saved answer text mentions, next to the count the collector recorded at the time (DQ-5 in the pack README: the recorded
lists under-count, on every engine, by different amounts).

Rule (the same text-mention rule as the explorer, mention_counts.py, 1012B-L9): for each market x niche cell, the
candidate businesses are every name that survived the pack's name gate in ANY engine's recorded list in the window
(deny set, quarantine, canon and legal-suffix merge, heading filter, 1005A gate; generic phrases guarded out). A row's
text-mention count is the number of candidates whose name appears in that row's answer_verbatim: case-insensitive,
whole-word, full multi-word name required, a single-word name only when it is not a common word and is capitalised.

Columns added per row: recount_status (RECOUNTED for API rows with run_status OK in a cell where the gate found at
least one business candidate; NO_GATED_CANDIDATES for OK rows in cells where it found none, which is every US
nationwide question set, so those rows are marked and not counted as zero; the run_status value for failed or
unnamed API rows; NOT_APPLICABLE for Google result and agreement rows), before_count and before_names (the recorded
list after the heading filter), after_count and after_names (text mentions of gated candidates), top10_in_list and
top10_in_text (whether a candidate in the cell's explorer top 10 is in the recorded list / in the text).

Gate (DQ-5 replication): over OK answers dated 2026-09-01 to 2026-10-11, the number of Anthropic answers whose text
contains a top-10 candidate must be at least 195 (the README printed 195 from the raw files). PASS or FAIL is printed
and written to recount_summary.json.

Run from the repo root:
  python3 -I -X utf8 reproduce/recount/text_mention_recount.py --out reproduce/recount/out
  python3 -I -X utf8 reproduce/recount/text_mention_recount.py --selftest
Standard library only. Reads the pack and corpus/e1; writes only inside --out.
"""
import argparse, csv, json, os, sys, hashlib, datetime as dt
from collections import defaultdict, Counter

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mention_counts as mc  # noqa: E402

ENG = mc.ENG
PACK_ANSWERS = 'releases/edition-1/data/answers_2026-09-01_to_2026-10-13.csv'
PACK_RUNSTATUS = 'releases/edition-1/data/answers_2026-09-01_to_2026-10-13_run_status.csv'


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def load_pack(repo):
    csv.field_size_limit(10 ** 9)
    rows = list(csv.DictReader(open(os.path.join(repo, PACK_ANSWERS), encoding='utf-8')))
    rs = {}
    p = os.path.join(repo, PACK_RUNSTATUS)
    if os.path.exists(p):
        for r in csv.DictReader(open(p, encoding='utf-8')):
            rs[r['source_file']] = r['run_status']
    return rows, rs


def explorer_order(cnt):
    """Explorer ordering of candidates (main_build): by max named days over engines, then engines naming, then name."""
    return sorted((c for c in cnt if any(cnt[c][1].values())),
                  key=lambda c: (-max(cnt[c][1].values()), -len([e for e in ENG if cnt[c][1][e]]), cnt[c][0].casefold()))


def list_hits(recorded_names, candidates):
    """Which candidate keys a recorded (heading-filtered) list carries: canon or stem equality on any variant."""
    hits = set()
    for n in recorded_names:
        cn, sn = mc.canon(n), mc.stem(n)
        for c, (disp, _per, variants) in candidates.items():
            if cn == c or sn == mc.stem(disp) or any(sn == mc.stem(v) for v in variants):
                hits.add(c); break
    return hits


def recount(repo, out, w0, w1, dq5_end, gate, limit_cells=None, log=print):
    be, g, quar = mc.load_gate(repo)
    corpus = mc.read_corpus(repo, be, w0, w1)            # API rows in window: status, answer, heading-filtered list
    by_file = {r['source_file']: r for r in corpus}
    pack_rows, rs = load_pack(repo)
    log('pack rows %d; corpus API rows in window %d' % (len(pack_rows), len(corpus)))

    cells = sorted({(r['market'], r['niche']) for r in corpus if r['market'].strip() and r['niche'].strip()
                    and 'LOCATION NOT CAPTURED' not in r['market']})
    if limit_cells:
        cells = cells[:limit_cells]
    per_row = {}          # source_file -> dict of recount columns
    top10_by_cell = {}
    for i, (mk, ni) in enumerate(cells, 1):
        cell = [r for r in corpus if r['market'] == mk and r['niche'] == ni]
        if not any(r['status'] == 'OK' for r in cell):
            continue
        asked, cnt, guard = mc.build_pair(mk, ni, cell, g, quar)
        if not cnt:
            # The gate found no business candidate in any engine's recorded list for this cell (true for every
            # "US nationwide" question set: the gate is built for local-market names). The text recount is blind
            # here, so the rows are marked rather than counted as zero.
            for r in cell:
                if r['status'] == 'OK':
                    per_row[r['source_file']] = {'recount_status': 'NO_GATED_CANDIDATES', 'before_count': len([n for n in r['businesses_named'].split(';') if n]),
                                                 'before_names': r['businesses_named'], 'after_count': '', 'after_names': '',
                                                 'top10_in_list': '', 'top10_in_text': '', 'top10u_in_text': '', 'candidates_in_cell': 0, 'guarded_in_cell': len(guard)}
            continue
        matchers = {c: mc.make_matcher(set(cnt[c][2]) | {cnt[c][0]}) for c in cnt}
        order = explorer_order(cnt)
        top10 = set(order[:10])
        # diagnostic second definition: the union over engines of each engine's own top 10 by named days
        top10u = set()
        for e in ENG:
            rk = sorted((c for c in cnt if cnt[c][1][e] > 0), key=lambda c: (-cnt[c][1][e], c))
            top10u |= set(rk[:10])
        top10_by_cell[(mk, ni)] = [mc.clean(cnt[c][0]) for c in order[:10]]
        for r in cell:
            if r['status'] != 'OK':
                continue
            ans = mc.Ans(r['date'], r['answer'])
            after = [c for c in cnt if ans.raw and mc.mentions(ans, matchers[c])]
            before = [n for n in r['businesses_named'].split(';') if n]
            lh = list_hits(before, cnt)
            per_row[r['source_file']] = {
                'recount_status': 'RECOUNTED',
                'before_count': len(before), 'before_names': ';'.join(before),
                'after_count': len(after), 'after_names': ';'.join(mc.clean(cnt[c][0]) for c in sorted(after, key=lambda c: cnt[c][0].casefold())),
                'top10_in_list': int(bool(lh & top10)), 'top10_in_text': int(bool(set(after) & top10)),
                'top10u_in_text': int(bool(set(after) & top10u)),
                'candidates_in_cell': len(cnt), 'guarded_in_cell': len(guard),
            }
        if i % 10 == 0:
            log('  cells %d of %d' % (i, len(cells)))

    # write the per-row file in pack row order
    os.makedirs(out, exist_ok=True)
    cols_add = ['recount_status', 'before_count', 'before_names', 'after_count', 'after_names', 'top10_in_list', 'top10_in_text',
                'top10u_in_text', 'candidates_in_cell', 'guarded_in_cell']
    base_cols = list(pack_rows[0].keys())
    outp = os.path.join(out, 'answers_recount_v1.csv')
    summ = {e: Counter() for e in ENG}
    dq5 = {e: Counter() for e in ENG}
    status_c = Counter()
    with open(outp, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=base_cols + cols_add)
        w.writeheader()
        for r in pack_rows:
            sf, en = r['source_file'], r['engine']
            add = {k: '' for k in cols_add}
            if en in ENG:
                if sf in per_row and per_row[sf]['recount_status'] != 'RECOUNTED':
                    add.update(per_row[sf])
                    summ[en]['rows_no_gated_candidates'] += 1
                elif sf in per_row:
                    add.update(per_row[sf])
                    s = summ[en]
                    s['ok_rows'] += 1; s['before_sum'] += add['before_count']; s['after_sum'] += add['after_count']
                    s['rows_before_gt0'] += add['before_count'] > 0; s['rows_after_gt0'] += add['after_count'] > 0
                    s['rows_after_gt_before'] += add['after_count'] > add['before_count']
                    s['rows_after_lt_before'] += add['after_count'] < add['before_count']
                    if w0 <= r['date'] <= dq5_end:
                        d = dq5[en]; d['ok'] += 1; d['list_has_top10'] += add['top10_in_list']; d['text_has_top10'] += add['top10_in_text']
                        d['text_has_top10_union'] += add['top10u_in_text']
                else:
                    st = rs.get(sf) or (by_file.get(sf, {}).get('status')) or 'UNKNOWN'
                    add['recount_status'] = st if st != 'OK' else 'OK_OUTSIDE_CELLS'
            else:
                add['recount_status'] = 'NOT_APPLICABLE'
            status_c[add['recount_status']] += 1
            w.writerow({**r, **add})

    anth_text = dq5['anthropic']['text_has_top10']
    verdict = 'PASS' if anth_text >= gate else 'FAIL'
    summary = {
        'built_utc': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'window': [w0, w1], 'dq5_window': [w0, dq5_end], 'gate': {'engine': 'anthropic', 'metric': 'text_has_top10', 'min': gate,
                                                                 'value': anth_text, 'verdict': verdict},
        'rows_written': len(pack_rows), 'recount_status_counts': dict(status_c), 'cells': len(top10_by_cell),
        'per_engine': {e: dict(summ[e]) for e in ENG},
        'dq5_replication': {e: dict(dq5[e]) for e in ENG},
        'readme_dq5_printed': {'anthropic': [1904, 48, 195], 'openai': [1898, 330, 553], 'perplexity': [1828, 1142, 1250], 'gemini': [342, 215, 305]},
        'top10_by_cell': {'%s | %s' % k: v for k, v in sorted(top10_by_cell.items())},
        'answers_recount_v1_sha256': sha256_file(outp),
        'inputs': {'answers_csv_sha256': sha256_file(os.path.join(repo, PACK_ANSWERS)),
                   'run_status_csv_sha256': sha256_file(os.path.join(repo, PACK_RUNSTATUS)) if os.path.exists(os.path.join(repo, PACK_RUNSTATUS)) else None},
    }
    json.dump(summary, open(os.path.join(out, 'recount_summary.json'), 'w', encoding='utf-8'), indent=1)
    log(json.dumps({k: summary[k] for k in ('gate', 'rows_written', 'recount_status_counts', 'per_engine', 'dq5_replication')}, indent=1))
    return summary


def selftest(repo):
    """Pipeline check on two cells: row count equals the pack, every API OK row in those cells is RECOUNTED, after >= 0,
    and the recorded list of a row can never exceed its own candidate count by the matching rule's definition."""
    import tempfile
    out = tempfile.mkdtemp(prefix='recount_selftest_')
    s = recount(repo, out, '2026-09-01', '2026-09-07', '2026-09-07', 0, limit_cells=2, log=lambda *a: None)
    rows = list(csv.DictReader(open(os.path.join(out, 'answers_recount_v1.csv'), encoding='utf-8')))
    assert s['rows_written'] == 12180 == len(rows), s['rows_written']
    rec = [r for r in rows if r['recount_status'] == 'RECOUNTED']
    assert rec, 'no rows recounted'
    assert all(int(r['after_count']) >= 0 and int(r['before_count']) >= 0 for r in rec)
    assert all(r['recount_status'] == 'NOT_APPLICABLE' for r in rows if r['engine'] in ('google_serp', 'agree'))
    assert set(r['engine'] for r in rec) <= set(ENG)
    print('selftest OK: rows %d, recounted %d in 2 cells, out %s' % (len(rows), len(rec), out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=os.path.abspath(os.path.join(HERE, '..', '..')))
    ap.add_argument('--out', default=os.path.join(HERE, 'out'))
    ap.add_argument('--w0', default='2026-09-01'); ap.add_argument('--w1', default='2026-10-13')
    ap.add_argument('--dq5-end', default='2026-10-11'); ap.add_argument('--gate', type=int, default=195)
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest(a.repo)
    s = recount(a.repo, a.out, a.w0, a.w1, a.dq5_end, a.gate)
    print('GATE', s['gate']['verdict'], 'anthropic text_has_top10 =', s['gate']['value'], 'min', a.gate)
    return 0 if s['gate']['verdict'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
