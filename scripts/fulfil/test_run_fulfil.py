"""Offline tests. Run: python3 scripts/fulfil/test_run_fulfil.py  (no network, no email)."""
import contextlib, io, json, os, sys, tempfile, unittest, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_fulfil as R

PAIRS = [{'market': 'Phoenix AZ', 'niche': 'hvac repair and replacement'}]
ROW = {'sale_id': 'cs_test_1', 'offer': 'market_1', 'buyer_email': 'buyer@example.com', 'pairs_json': json.dumps(PAIRS), 'status': 'DISPATCHED'}
CELLS = {('phoenix az', 'hvac repair and replacement'): ('Phoenix AZ', 'hvac repair and replacement')}


class FakeD1:
    """Implements only the four statements the runner issues."""
    def __init__(self, row=None, fail_updates=False):
        self.rows = {row['sale_id']: dict(row)} if row else {}
        self.log, self.fail_updates = [], fail_updates

    def query(self, sql, params):
        self.log.append((sql, params))
        if sql.startswith('SELECT'):
            r = self.rows.get(params[0])
            return ([dict(r)] if r else []), 0
        if self.fail_updates:
            raise R.Fail('FAILED', 'd1 http 500')
        if "SET status = 'BUILDING'" in sql:
            r = self.rows.get(params[0])
            if r and r['status'] == 'DISPATCHED':
                r['status'] = 'BUILDING'
                return [], 1
            return [], 0
        if sql.startswith('UPDATE sales_events SET status = ?, note = ?'):
            status, note, msg, sha, sid = params
            r = self.rows[sid]
            r.update(status=status, note=note)
            if msg: r['brevo_message_id'] = msg
            if sha: r['sha256'] = sha
            return [], 1
        raise AssertionError(sql)


class Run:
    """Patches build, file check and corpus lookup so main() runs offline."""
    def __init__(self, d, send=None, check_raises=None):
        self.d, self.send, self.check_raises = d, send, check_raises
    def __enter__(self):
        self.saved = (R.build, R.check_files, R.corpus_cells, R.send_brevo)
        pdf = os.path.join(self.d, 'a.pdf'); open(pdf, 'wb').write(b'x')
        R.corpus_cells = lambda repo: CELLS
        R.build = lambda *a: None
        def chk(out, n):
            if self.check_raises: raise self.check_raises
            return [pdf], [('a.pdf', 'ef' * 32)]
        R.check_files = chk
        self.sent = []
        R.send_brevo = self.send or (lambda key, payload, **k: (self.sent.append(payload), '<m1@x>')[1])
        return self
    def __exit__(self, *a):
        R.build, R.check_files, R.corpus_cells, R.send_brevo = self.saved


def run_main(env, d1):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = R.main(env, d1=d1)
    return rc, buf.getvalue()


def base_env(d, **kw):
    e = {'SALE_ID': 'cs_test_1', 'REPO_ROOT': d, 'OUT_DIR': d, 'END_DATE': '2026-10-11'}
    e.update(kw)
    return e


class Resp:
    def __init__(self, body): self.b = body
    def read(self): return self.b
    def __enter__(self): return self
    def __exit__(self, *a): return False


class T(unittest.TestCase):
    def test_validate_row(self):
        email, pairs = R.validate_row(ROW)
        self.assertEqual(email, 'buyer@example.com')
        self.assertEqual(pairs[0]['market'], 'Phoenix AZ')
        for bad in (dict(ROW, buyer_email='nope'), dict(ROW, buyer_email=None), dict(ROW, pairs_json='x'), dict(ROW, pairs_json='[]'),
                    dict(ROW, pairs_json='[{"market":"A"}]')):
            with self.assertRaises(R.Fail):
                R.validate_row(bad)

    def test_resolve_pairs_case_insensitive_and_missing(self):
        cells = {('phoenix az', 'hvac repair and replacement'): ('Phoenix AZ', 'hvac repair and replacement')}
        self.assertEqual(R.resolve_pairs([{'market': 'phoenix az', 'niche': 'HVAC repair and replacement'}], cells), [('Phoenix AZ', 'hvac repair and replacement')])
        with self.assertRaises(R.Fail) as c:
            R.resolve_pairs([{'market': 'Nowhere ZZ', 'niche': 'none'}], cells)
        self.assertEqual(c.exception.status, 'NEEDS_INTAKE_CORRECTION')

    def test_compose_has_no_dashes_and_fills_fields(self):
        s, b = R.compose([('Phoenix AZ', 'hvac')], '2026-10-11', [('a.pdf', 'ab' * 32), ('a.csv', 'cd' * 32)])
        self.assertIn('Phoenix AZ, hvac', b)
        self.assertIn('2026-10-11', b)
        self.assertIn('ab' * 32, b)
        self.assertNotIn('cd' * 32, b)
        self.assertNotIn('{{', b)
        for d in (chr(0x2013), chr(0x2014)):
            self.assertNotIn(d, s + b)

    def test_dry_run_reads_but_never_sends_or_writes(self):
        with tempfile.TemporaryDirectory() as d, Run(d) as r:
            d1 = FakeD1(ROW)
            rc, out = run_main(base_env(d), d1)
            self.assertEqual(rc, 0)
            self.assertIn('DRY_RUN_OK', out)
            self.assertEqual(r.sent, [])
            self.assertEqual(len(d1.log), 1)  # the SELECT only
            self.assertEqual(d1.rows['cs_test_1']['status'], 'DISPATCHED')

    def test_real_run_claims_sends_and_writes_sent_with_message_id(self):
        with tempfile.TemporaryDirectory() as d, Run(d) as r:
            d1 = FakeD1(ROW)
            rc, out = run_main(base_env(d, DRY_RUN='false', BREVO_API_KEY='k'), d1)
            self.assertEqual(rc, 0)
            self.assertEqual(len(r.sent), 1)
            self.assertEqual(r.sent[0]['to'], [{'email': 'buyer@example.com'}])
            row = d1.rows['cs_test_1']
            self.assertEqual((row['status'], row['brevo_message_id'], row['sha256']), ('SENT', '<m1@x>', 'ef' * 32))
            self.assertNotIn('buyer@example.com', out)

    def test_email_never_printed_on_any_path(self):
        with tempfile.TemporaryDirectory() as d, Run(d, check_raises=R.Fail('NEEDS_HUMAN', 'pdf is not one page: a.pdf')):
            rc, out = run_main(base_env(d, DRY_RUN='false', BREVO_API_KEY='k'), FakeD1(ROW))
            self.assertEqual(rc, 1)
            self.assertNotIn('buyer@example.com', out)
            self.assertNotIn('example.com', out)

    def test_failure_after_claim_writes_failed_with_reason_and_no_send(self):
        with tempfile.TemporaryDirectory() as d, Run(d, check_raises=R.Fail('NEEDS_HUMAN', 'pdf is not one page: a.pdf')) as r:
            d1 = FakeD1(ROW)
            rc, out = run_main(base_env(d, DRY_RUN='false', BREVO_API_KEY='k'), d1)
            self.assertEqual(rc, 1)
            self.assertEqual(r.sent, [])
            row = d1.rows['cs_test_1']
            self.assertEqual(row['status'], 'FAILED')
            self.assertIn('pdf is not one page', row['note'])

    def test_unknown_pair_writes_failed_with_intake_reason(self):
        bad = dict(ROW, pairs_json=json.dumps([{'market': 'Nowhere ZZ', 'niche': 'none'}]))
        with tempfile.TemporaryDirectory() as d, Run(d) as r:
            d1 = FakeD1(bad)
            rc, out = run_main(base_env(d, DRY_RUN='false', BREVO_API_KEY='k'), d1)
            self.assertEqual(rc, 1); self.assertEqual(r.sent, [])
            self.assertEqual(d1.rows['cs_test_1']['status'], 'FAILED')
            self.assertIn('NEEDS_INTAKE_CORRECTION', d1.rows['cs_test_1']['note'])

    def test_missing_brevo_key_fails_without_send(self):
        with tempfile.TemporaryDirectory() as d, Run(d) as r:
            d1 = FakeD1(ROW)
            rc, out = run_main(base_env(d, DRY_RUN='false'), d1)
            self.assertEqual(rc, 1); self.assertEqual(r.sent, [])
            self.assertEqual(d1.rows['cs_test_1']['status'], 'FAILED')

    def test_second_run_does_not_double_send(self):
        with tempfile.TemporaryDirectory() as d, Run(d) as r:
            d1 = FakeD1(ROW)
            e = base_env(d, DRY_RUN='false', BREVO_API_KEY='k')
            run_main(e, d1)
            rc, out = run_main(e, d1)
            self.assertEqual(rc, 0); self.assertIn('SKIPPED', out)
            self.assertEqual(len(r.sent), 1)

    def test_send_ok_but_writeback_fails_keeps_the_receipt(self):
        with tempfile.TemporaryDirectory() as d, Run(d) as r:
            d1 = FakeD1(ROW)
            orig = d1.query
            def q(sql, params):
                if sql.startswith('UPDATE sales_events SET status = ?'):
                    raise R.Fail('FAILED', 'd1 http 500')
                return orig(sql, params)
            d1.query = q
            rc, out = run_main(base_env(d, DRY_RUN='false', BREVO_API_KEY='k'), d1)
            self.assertEqual(rc, 1)
            self.assertIn('SENT_BUT_D1_WRITEBACK_FAILED', out)
            self.assertIn('<m1@x>', out)

    def test_bad_sale_id_and_missing_row(self):
        with tempfile.TemporaryDirectory() as d, Run(d):
            self.assertEqual(run_main(base_env(d, SALE_ID='x; rm'), FakeD1(ROW))[0], 1)
            self.assertEqual(run_main(base_env(d, SALE_ID='cs_test_9'), FakeD1(ROW))[0], 1)

    def test_d1_client_request_shape_and_errors(self):
        seen = {}
        def op(req, timeout=0):
            seen['url'], seen['auth'], seen['body'] = req.full_url, req.get_header('Authorization'), json.loads(req.data)
            return Resp(b'{"success":true,"result":[{"results":[{"a":1}],"meta":{"changes":2}}]}')
        c = R.D1('tok', 'acct', 'db1', opener=op)
        self.assertEqual(c.query('SELECT 1 WHERE x = ?', ['y']), ([{'a': 1}], 2))
        self.assertEqual(seen['url'], 'https://api.cloudflare.com/client/v4/accounts/acct/d1/database/db1/query')
        self.assertEqual(seen['auth'], 'Bearer tok')
        self.assertEqual(seen['body'], {'sql': 'SELECT 1 WHERE x = ?', 'params': ['y']})
        def op401(req, timeout=0): raise urllib.error.HTTPError(req.full_url, 401, 'x', {}, io.BytesIO(b''))
        with self.assertRaises(R.Fail): R.D1('t', 'a', 'd', opener=op401).query('SELECT 1', [])
        with self.assertRaises(R.Fail): R.D1.from_env({})

    def test_yesterday_utc(self):
        import datetime
        n = datetime.datetime(2026, 10, 12, 3, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(R.yesterday_utc({}, n), '2026-10-11')
        self.assertEqual(R.yesterday_utc({'END_DATE': '2026-01-02'}, n), '2026-01-02')

    def test_brevo_retries_then_succeeds(self):
        calls, sleeps = [], []
        def op(req, timeout=0):
            calls.append(1)
            if len(calls) < 3:
                raise urllib.error.HTTPError(req.full_url, 429, 'x', {}, io.BytesIO(b''))
            return Resp(b'{"messageId":"<m1@x>"}')
        self.assertEqual(R.send_brevo('k', {'a': 1}, sleep=sleeps.append, opener=op), '<m1@x>')
        self.assertEqual(sleeps, [30, 120])

    def test_brevo_4xx_no_retry_and_5xx_exhausts(self):
        sleeps = []
        def op4(req, timeout=0): raise urllib.error.HTTPError(req.full_url, 401, 'x', {}, io.BytesIO(b''))
        with self.assertRaises(R.Fail): R.send_brevo('k', {}, sleep=sleeps.append, opener=op4)
        self.assertEqual(sleeps, [])
        def op5(req, timeout=0): raise urllib.error.HTTPError(req.full_url, 503, 'x', {}, io.BytesIO(b''))
        with self.assertRaises(R.Fail): R.send_brevo('k', {}, sleep=sleeps.append, opener=op5)
        self.assertEqual(sleeps, [30, 120, 300])

    def test_brevo_body_shape(self):
        with tempfile.NamedTemporaryFile(suffix='.pdf') as f:
            f.write(b'abc'); f.flush()
            b = R.brevo_body('terry@namebeam.ai', 'x@example.com', 's', 't', [f.name], 'cs1')
        self.assertEqual(b['to'], [{'email': 'x@example.com'}])
        self.assertEqual(b['attachment'][0]['content'], 'YWJj')
        self.assertEqual(b['tags'], ['sale:cs1'])


if __name__ == '__main__':
    unittest.main()
