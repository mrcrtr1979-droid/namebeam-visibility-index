#!/usr/bin/env python3
"""Fulfil one Namebeam sale: validate, build with make_cuts_v2.py, check, and email through Brevo REST.
TEST MODE build. DRY_RUN defaults to true: nothing is emailed unless DRY_RUN=false.
Env: PAYLOAD_JSON (required), DRY_RUN, BREVO_API_KEY (secret name only), TEST_TO, SENDER_EMAIL, REPO_ROOT, OUT_DIR."""
import base64, glob, hashlib, json, os, re, subprocess, sys, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
BRAND = 'Namebeam'
MAX_ATTACH = 4 * 1024 * 1024
DASHES = (b'\xe2\x80\x93', b'\xe2\x80\x94')
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


class Fail(Exception):
    def __init__(self, status, note):
        super().__init__(note)
        self.status, self.note = status, note


def parse_payload(raw):
    try:
        p = json.loads(raw)
    except Exception:
        raise Fail('NEEDS_HUMAN', 'payload is not json')
    for k in ('sale_id', 'email', 'pairs', 'end_date'):
        if not p.get(k):
            raise Fail('NEEDS_HUMAN', 'payload missing ' + k)
    if not EMAIL_RE.match(p['email']):
        raise Fail('NEEDS_HUMAN', 'bad buyer email')
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', p['end_date']):
        raise Fail('NEEDS_HUMAN', 'bad end_date')
    if not isinstance(p['pairs'], list) or not 1 <= len(p['pairs']) <= 5:
        raise Fail('NEEDS_HUMAN', 'pairs must hold 1 to 5 entries')
    return p


def corpus_cells(repo):
    """Map lowercase (market, niche) to the corpus spelling, from collected API rows."""
    cells = {}
    for path in glob.glob(os.path.join(repo, 'corpus/e1/NB-CZ-API_*.json')):
        try:
            with open(path) as fh:
                d = json.load(fh)
        except Exception:
            continue
        m, n = d.get('market'), d.get('niche')
        if m and n:
            cells.setdefault((m.lower(), n.lower()), (m, n))
    return cells


def resolve_pairs(pairs, cells):
    out, missing = [], []
    for p in pairs:
        hit = cells.get((p['market'].strip().lower(), p['niche'].strip().lower()))
        if hit:
            out.append(hit)
        else:
            missing.append('%s|%s' % (p['market'], p['niche']))
    if missing:
        raise Fail('NEEDS_INTAKE_CORRECTION', 'no collected data for: ' + '; '.join(missing))
    return out


def build(repo, out, pairs, end_date):
    os.makedirs(out, exist_ok=True)
    spec = ';'.join('%s|%s' % p for p in pairs)
    cmd = [sys.executable, '-I', os.path.join(HERE, 'make_cuts_v2.py'), repo, out, '--brand', BRAND, '--markets', spec, '--end', end_date]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise Fail('NEEDS_HUMAN', 'make_cuts_v2 failed: ' + (r.stderr or r.stdout)[-300:].replace('\n', ' '))


def check_files(out, n_pairs):
    pdfs = sorted(glob.glob(os.path.join(out, '*.pdf')))
    csvs = sorted(glob.glob(os.path.join(out, 'csv', '*_table.csv')) + glob.glob(os.path.join(out, 'csv', '*_metrics.csv')))
    if len(pdfs) != n_pairs:
        raise Fail('NEEDS_HUMAN', 'expected %d pdf, found %d' % (n_pairs, len(pdfs)))
    from pypdf import PdfReader
    for f in pdfs:
        if len(PdfReader(f).pages) != 1:
            raise Fail('NEEDS_HUMAN', 'pdf is not one page: ' + os.path.basename(f))
    files = pdfs + csvs
    shas = []
    for f in files:
        with open(f, 'rb') as fh:
            b = fh.read()
        if any(d in b for d in DASHES):
            raise Fail('NEEDS_HUMAN', 'en or em dash bytes in ' + os.path.basename(f))
        if len(b) > MAX_ATTACH:
            raise Fail('NEEDS_HUMAN', 'attachment over 4MB: ' + os.path.basename(f))
        shas.append((os.path.basename(f), hashlib.sha256(b).hexdigest()))
    return files, shas


def compose(pairs, end_date, shas):
    with open(os.path.join(HERE, '..', '..', 'offers', 'market_file_v1.txt')) as fh:
        tpl = fh.read()
    subject, body = tpl.split('\n', 1)
    subject = subject.replace('Subject: ', '', 1)
    body = body.lstrip('\n')
    body = body.replace('{{pairs}}', '\n'.join('- %s, %s' % p for p in pairs))
    body = body.replace('{{end_date}}', end_date)
    body = body.replace('{{sha_list}}', '\n'.join('%s  %s' % (h, n) for n, h in shas if n.endswith('.pdf')))
    if any(d.decode() in body + subject for d in DASHES):
        raise Fail('NEEDS_HUMAN', 'dash in email text')
    return subject, body


def _b64(path):
    with open(path, 'rb') as fh:
        return base64.b64encode(fh.read()).decode()


def brevo_body(sender, to, subject, body, files, sale_id):
    return {
        'sender': {'name': 'Namebeam', 'email': sender},
        'to': [{'email': to}],
        'subject': subject,
        'textContent': body,
        'attachment': [{'name': os.path.basename(f), 'content': _b64(f)} for f in files],
        'tags': ['sale:' + sale_id],
    }


def send_brevo(api_key, payload, sleep=time.sleep, opener=urllib.request.urlopen):
    delays = [30, 120, 300]
    for attempt in range(len(delays) + 1):
        req = urllib.request.Request('https://api.brevo.com/v3/smtp/email', data=json.dumps(payload).encode(), method='POST',
                                     headers={'api-key': api_key, 'content-type': 'application/json', 'accept': 'application/json'})
        try:
            with opener(req, timeout=60) as resp:
                msg = json.loads(resp.read().decode()).get('messageId')
                if not msg:
                    raise Fail('NEEDS_HUMAN', 'brevo returned no messageId')
                return msg
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                if attempt < len(delays):
                    sleep(delays[attempt])
                    continue
                raise Fail('NEEDS_HUMAN', 'brevo http %d after retries' % e.code)
            raise Fail('NEEDS_HUMAN', 'brevo http %d' % e.code)
        except urllib.error.URLError:
            if attempt < len(delays):
                sleep(delays[attempt])
                continue
            raise Fail('NEEDS_HUMAN', 'brevo unreachable after retries')


def summary(lines):
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    text = '\n'.join(lines) + '\n'
    if path:
        with open(path, 'a') as fh:
            fh.write(text)
    print(text)


def main(env=os.environ):
    dry = env.get('DRY_RUN', 'true').lower() != 'false'
    repo = env.get('REPO_ROOT', '.')
    out = env.get('OUT_DIR', os.path.join(repo, 'out_fulfil'))
    try:
        p = parse_payload(env.get('PAYLOAD_JSON', ''))
        print('::add-mask::' + p['email'])
        pairs = resolve_pairs(p['pairs'], corpus_cells(repo))
        build(repo, out, pairs, p['end_date'])
        files, shas = check_files(out, len(pairs))
        subject, body = compose(pairs, p['end_date'], shas)
        to = env.get('TEST_TO') or p['email']
        if env.get('TEST_TO'):
            subject = '[TEST] ' + subject
        payload = brevo_body(env.get('SENDER_EMAIL', 'terry@namebeam.ai'), to, subject, body, files, p['sale_id'])
        rows = ['sale %s | pairs %d | files %d | attach bytes %d' % (p['sale_id'], len(pairs), len(files), sum(os.path.getsize(f) for f in files))]
        rows += ['sha256 %s %s' % (h, n) for n, h in shas]
        if dry:
            summary(['STATUS DRY_RUN_OK (no email sent)'] + rows)
            return 0
        key = env.get('BREVO_API_KEY', '')
        if not key:
            raise Fail('NEEDS_HUMAN', 'BREVO_API_KEY not set')
        msg = send_brevo(key, payload)
        summary(['STATUS DELIVERED', 'brevo messageId ' + msg] + rows)
        return 0
    except Fail as f:
        summary(['STATUS ' + f.status, 'note: ' + f.note])
        return 1


if __name__ == '__main__':
    sys.exit(main())
