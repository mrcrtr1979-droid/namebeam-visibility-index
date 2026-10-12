#!/usr/bin/env python3
"""Fulfil one Namebeam sale: validate, build with make_cuts_v2.py, check, and email through Brevo REST.
TEST MODE build. DRY_RUN defaults to true: nothing is emailed unless DRY_RUN=false.
The repository_dispatch payload carries only SALE_ID. The buyer email and market pairs are read from D1 over the Cloudflare
HTTP API and are never printed. Env: SALE_ID (required), CF_D1_TOKEN, CF_ACCOUNT_ID, D1_DB_ID, BREVO_API_KEY (secret names
only), DRY_RUN, TEST_TO, SENDER_EMAIL, REPO_ROOT, OUT_DIR, END_DATE (tests), ROW_JSON (local test only, replaces D1)."""
import base64, datetime, glob, hashlib, json, os, re, subprocess, sys, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
BRAND = 'Namebeam'
MAX_ATTACH = 4 * 1024 * 1024
DASHES = (b'\xe2\x80\x93', b'\xe2\x80\x94')
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


class Fail(Exception):
    def __init__(self, status, note):
        super().__init__(note)
        self.status, self.note = status, note


SALE_RE = re.compile(r'^(cs|in)_[A-Za-z0-9_]{4,120}$')


class D1:
    """Cloudflare D1 HTTP API client. Secret values come from the environment and are never printed."""
    def __init__(self, token, account, db, opener=urllib.request.urlopen):
        self.url = 'https://api.cloudflare.com/client/v4/accounts/%s/d1/database/%s/query' % (account, db)
        self.token, self.opener = token, opener

    @classmethod
    def from_env(cls, env):
        vals = [env.get(k, '') for k in ('CF_D1_TOKEN', 'CF_ACCOUNT_ID', 'D1_DB_ID')]
        if not all(vals):
            raise Fail('FAILED', 'D1 credentials not set (CF_D1_TOKEN, CF_ACCOUNT_ID, D1_DB_ID)')
        return cls(*vals)

    def query(self, sql, params):
        req = urllib.request.Request(self.url, data=json.dumps({'sql': sql, 'params': params}).encode(), method='POST',
                                     headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        try:
            with self.opener(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise Fail('FAILED', 'd1 http %d' % e.code)
        except (urllib.error.URLError, ValueError):
            raise Fail('FAILED', 'd1 unreachable or bad reply')
        if not data.get('success') or not data.get('result'):
            raise Fail('FAILED', 'd1 query failed')
        res = data['result'][0]
        return res.get('results') or [], int((res.get('meta') or {}).get('changes') or 0)


def load_row(d1, sale_id, env):
    if env.get('ROW_JSON'):
        return json.loads(env['ROW_JSON'])
    rows, _ = d1.query('SELECT sale_id,offer,buyer_email,pairs_json,status FROM sales_events WHERE sale_id = ?', [sale_id])
    if not rows:
        raise Fail('FAILED', 'no D1 row for sale id')
    return rows[0]


def validate_row(row):
    email = row.get('buyer_email') or ''
    if not EMAIL_RE.match(email):
        raise Fail('FAILED', 'row has no valid buyer email')
    try:
        pairs = json.loads(row.get('pairs_json') or '')
    except ValueError:
        raise Fail('FAILED', 'row pairs are not json')
    if not isinstance(pairs, list) or not 1 <= len(pairs) <= 5 or not all(isinstance(p, dict) and p.get('market') and p.get('niche') for p in pairs):
        raise Fail('FAILED', 'row pairs must hold 1 to 5 market and niche entries')
    return email, pairs


def yesterday_utc(env, now=None):
    if env.get('END_DATE'):
        return env['END_DATE']
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return (now - datetime.timedelta(days=1)).date().isoformat()


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


def mark(d1, sale_id, status, note=None, msg=None, sha=None):
    d1.query('UPDATE sales_events SET status = ?, note = ?, brevo_message_id = COALESCE(?, brevo_message_id), sha256 = COALESCE(?, sha256) WHERE sale_id = ?',
             [status, (note or '')[:300] or None, msg, sha, sale_id])


def main(env=os.environ, d1=None, now=None):
    dry = env.get('DRY_RUN', 'true').lower() != 'false'
    repo = env.get('REPO_ROOT', '.')
    out = env.get('OUT_DIR', os.path.join(repo, 'out_fulfil'))
    sale_id = env.get('SALE_ID', '')
    claimed = False
    try:
        if not SALE_RE.match(sale_id):
            raise Fail('FAILED', 'bad or missing SALE_ID')
        if not env.get('ROW_JSON'):
            d1 = d1 or D1.from_env(env)
        row = load_row(d1, sale_id, env)
        email, raw_pairs = validate_row(row)
        if not dry and not env.get('ROW_JSON'):
            _, changes = d1.query("UPDATE sales_events SET status = 'BUILDING' WHERE sale_id = ? AND status = 'DISPATCHED'", [sale_id])
            if changes == 0:
                summary(['STATUS SKIPPED (sale is not in DISPATCHED state; already claimed or finished)', 'sale ' + sale_id])
                return 0
            claimed = True
        end_date = yesterday_utc(env, now)
        pairs = resolve_pairs(raw_pairs, corpus_cells(repo))
        build(repo, out, pairs, end_date)
        files, shas = check_files(out, len(pairs))
        subject, body = compose(pairs, end_date, shas)
        to = env.get('TEST_TO') or email
        if env.get('TEST_TO'):
            subject = '[TEST] ' + subject
        payload = brevo_body(env.get('SENDER_EMAIL', 'terry@namebeam.ai'), to, subject, body, files, sale_id)
        rows = ['sale %s | pairs %d | files %d | attach bytes %d' % (sale_id, len(pairs), len(files), sum(os.path.getsize(f) for f in files))]
        rows += ['sha256 %s %s' % (h, n) for n, h in shas]
        if dry:
            summary(['STATUS DRY_RUN_OK (no email sent, no D1 write)'] + rows)
            return 0
        key = env.get('BREVO_API_KEY', '')
        if not key:
            raise Fail('FAILED', 'BREVO_API_KEY not set')
        msg = send_brevo(key, payload)
        pdf_sha = ';'.join(h for n, h in shas if n.endswith('.pdf'))
        try:
            mark(d1, sale_id, 'SENT', None, msg, pdf_sha)
        except Fail:
            summary(['STATUS SENT_BUT_D1_WRITEBACK_FAILED', 'brevo messageId ' + msg] + rows)
            return 1
        summary(['STATUS SENT', 'brevo messageId ' + msg] + rows)
        return 0
    except Fail as f:
        note = ('%s: %s' % (f.status, f.note)) if f.status != 'FAILED' else f.note
        if claimed:
            try:
                mark(d1, sale_id, 'FAILED', note)
            except Fail:
                note += ' (D1 write-back also failed)'
        summary(['STATUS FAILED', 'note: ' + note])
        return 1


if __name__ == '__main__':
    sys.exit(main())
