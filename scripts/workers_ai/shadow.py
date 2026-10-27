#!/usr/bin/env python3
"""Shadow mode for lane 1 (name labeling). STAGED 2026-10-26, RUN_ID WORKERSAI-LANE1-1026.

Runs AFTER scripts/label_names.py --run in the same workflow job. The Haiku labels for this run are the rows just
appended to corpus/labels/name_labels.csv (working tree minus HEAD, model != prefilter). This script sends those SAME
names, in the same chunks of 40, with the SAME system prompt and user template, to Workers AI gpt-oss-120b, and writes
an agreement report. It never edits name_labels.csv or any tracked file, so the PR the workflow opens is unchanged.

  python3 -I scripts/workers_ai/shadow.py --repo . --report "$RUNNER_TEMP/WORKERSAI_SHADOW_REPORT.md"

Promotion rule written into the report (a human applies it): 3 consecutive reports, each with at least 150 names, each
PASS. PASS = overall agreement >= 95 percent, BUSINESS-class agreement >= 90 percent, unanswered <= 2 percent.
"""
import os, sys, csv, io, json, argparse, subprocess, importlib.util, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # python -I drops the script dir; add it back explicitly
import workers_ai as W  # noqa: E402

LANE = 'label_names'
MIN_NAMES, T_ALL, T_BUS, T_UNANS = 150, 0.95, 0.90, 0.02
FALLBACK_LN = '/home/claude/mrcrtr1979-droid/namebeam-visibility-index/scripts/label_names.py'


def load_label_names(explicit=None):
    for p in (explicit, os.environ.get('NB_LABEL_NAMES'), os.path.join(HERE, '..', 'label_names.py'), FALLBACK_LN):
        if p and os.path.exists(p):
            spec = importlib.util.spec_from_file_location('label_names', p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    return None


def read_rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def new_haiku_rows(repo, ln):
    path = os.path.join(repo, ln.LABELS_PATH)
    cur = read_rows(open(path, encoding='utf-8').read()) if os.path.exists(path) else []
    try:
        old = subprocess.run(['git', '-C', repo, 'show', 'HEAD:' + ln.LABELS_PATH], capture_output=True, text=True,
                             timeout=60)
        base = read_rows(old.stdout) if old.returncode == 0 else []
    except Exception:
        base = []
    seen = {(r['name'], r['market'], r['niche']) for r in base}
    return [r for r in cur if r.get('model') not in ('prefilter', '') and (r['name'], r['market'], r['niche']) not in seen]


def agreement(pairs):
    """pairs: [(name, haiku_label, workers_label_or_None)]. Pure function, no I/O."""
    n = len(pairs)
    ans = [p for p in pairs if p[2] is not None]
    agree = sum(1 for p in ans if p[1] == p[2])
    by = {}
    for p in ans:
        b = by.setdefault(p[1], [0, 0])
        b[0] += 1
        b[1] += 1 if p[1] == p[2] else 0
    conf = {}
    for p in ans:
        if p[1] != p[2]:
            k = '%s -> %s' % (p[1], p[2])
            conf[k] = conf.get(k, 0) + 1
    rate = agree / len(ans) if ans else 0.0
    bus = (by['BUSINESS'][1] / by['BUSINESS'][0]) if by.get('BUSINESS') else None
    unans = (n - len(ans)) / n if n else 1.0
    ok = (n >= MIN_NAMES and rate >= T_ALL and (bus is None or bus >= T_BUS) and unans <= T_UNANS)
    return {'n': n, 'answered': len(ans), 'agree': agree, 'rate': rate, 'business_rate': bus, 'unanswered_rate': unans,
            'by_haiku_class': {k: {'n': v[0], 'agree': v[1]} for k, v in by.items()}, 'confusion': conf,
            'verdict': 'PASS' if ok else 'NOT YET'}


def run_shadow(rows, ln, post=None, sleep=None, log_path=None, max_names=400, token=None, account_id=None):
    rows = rows[:max_names]
    pairs, calls, pt, ct, usd, errs = [], 0, 0, 0, 0.0, []
    for c in range(0, len(rows), ln.CHUNK):
        chunk = rows[c:c + ln.CHUNK]
        items = [(r['name'], r['market'], r['niche']) for r in chunk]
        res = W.complete(ln.SYSTEM, ln.user_prompt(items), lane=LANE, max_tokens=ln.MAX_TOKENS * 2 + 2048,
                         post=post, sleep=sleep, log_path=log_path, token=token, account_id=account_id)
        calls += 1
        pt += res['prompt_tokens']; ct += res['completion_tokens']; usd += res['usd']
        got = ln.parse_labels(res['text'], len(chunk)) if res['ok'] else {}
        if not res['ok']:
            errs.append(res['error'])
        for i, r in enumerate(chunk, 1):
            pairs.append((r['name'], r['label'], got[i][0] if i in got else None))
    return pairs, {'calls': calls, 'prompt_tokens': pt, 'completion_tokens': ct, 'usd': round(usd, 6), 'errors': errs}


def render(a, meta, pairs, when):
    L = ['# Workers AI shadow report, lane label_names', '',
         'Generated %s. Model %s vs the Haiku labels appended this run. Public record unchanged.' % (when, W.MODEL), '',
         '| metric | value |', '|---|---|',
         '| names compared | %d |' % a['n'], '| answered by Workers AI | %d |' % a['answered'],
         '| overall agreement | %.1f%% |' % (100 * a['rate']),
         '| BUSINESS-class agreement | %s |' % ('n/a' if a['business_rate'] is None else '%.1f%%' % (100 * a['business_rate'])),
         '| unanswered | %.1f%% |' % (100 * a['unanswered_rate']),
         '| calls / prompt tokens / completion tokens | %d / %d / %d |' % (meta['calls'], meta['prompt_tokens'], meta['completion_tokens']),
         '| Workers AI cost at list price | $%.4f |' % meta['usd'], '| verdict this run | %s |' % a['verdict'], '',
         'Rule: promote only after 3 consecutive runs of at least %d names each reporting PASS (agreement >= %d%%, BUSINESS-class >= %d%%, unanswered <= %d%%).'
         % (MIN_NAMES, 100 * T_ALL, 100 * T_BUS, 100 * T_UNANS), '']
    if a['confusion']:
        L += ['## Disagreements by type'] + ['- %s: %d' % kv for kv in sorted(a['confusion'].items(), key=lambda x: -x[1])] + ['']
        L += ['## First 25 disagreeing names'] + ['- %s: Haiku %s, Workers AI %s' % (n, h, w) for n, h, w in pairs if w is not None and h != w][:25] + ['']
    if meta['errors']:
        L += ['## Call errors (first 5)'] + ['- ' + e for e in meta['errors'][:5]] + ['']
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default='.')
    ap.add_argument('--report', default='WORKERSAI_SHADOW_REPORT.md')
    ap.add_argument('--max-names', type=int, default=400)
    a = ap.parse_args()
    ln = load_label_names()
    if ln is None:
        print('label_names.py not found; shadow skipped')
        return 0
    rows = new_haiku_rows(a.repo, ln)
    print('new Haiku rows this run: %d' % len(rows))
    if not rows:
        open(a.report, 'w').write('# Workers AI shadow report\n\nNo new Haiku rows this run; nothing to compare.\n')
        return 0
    pairs, meta = run_shadow(rows, ln, max_names=a.max_names)
    ag = agreement(pairs)
    rep = render(ag, meta, pairs, datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'))
    open(a.report, 'w', encoding='utf-8').write(rep)
    print(rep)
    sm = os.environ.get('GITHUB_STEP_SUMMARY')
    if sm:
        open(sm, 'a').write(rep + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
