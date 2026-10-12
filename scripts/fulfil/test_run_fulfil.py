"""Offline tests. Run: python3 scripts/fulfil/test_run_fulfil.py  (no network, no email)."""
import io, json, os, sys, tempfile, unittest, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_fulfil as R

GOOD = {'sale_id': 'cs_test_1', 'email': 'buyer@example.com', 'pairs': [{'market': 'Phoenix AZ', 'niche': 'hvac repair and replacement'}], 'end_date': '2026-10-11'}


class Resp:
    def __init__(self, body): self.b = body
    def read(self): return self.b
    def __enter__(self): return self
    def __exit__(self, *a): return False


class T(unittest.TestCase):
    def test_payload_ok_and_bad(self):
        self.assertEqual(R.parse_payload(json.dumps(GOOD))['sale_id'], 'cs_test_1')
        for bad in ({'email': 'x'}, dict(GOOD, email='nope'), dict(GOOD, end_date='10/11'), dict(GOOD, pairs=[])):
            with self.assertRaises(R.Fail):
                R.parse_payload(json.dumps(bad))
        with self.assertRaises(R.Fail):
            R.parse_payload('not json')

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

    def test_dry_run_sends_nothing(self):
        called = []
        orig = R.send_brevo
        R.send_brevo = lambda *a, **k: called.append(1)
        try:
            with tempfile.TemporaryDirectory() as d:
                orig_build, orig_check = R.build, R.check_files
                R.corpus_cells = lambda repo: {('phoenix az', 'hvac repair and replacement'): ('Phoenix AZ', 'hvac repair and replacement')}
                R.build = lambda *a: None
                pdf = os.path.join(d, 'a.pdf'); open(pdf, 'wb').write(b'x')
                R.check_files = lambda out, n: ([pdf], [('a.pdf', 'ef' * 32)])
                rc = R.main({'PAYLOAD_JSON': json.dumps(GOOD), 'REPO_ROOT': d, 'OUT_DIR': d})
                self.assertEqual(rc, 0)
                self.assertEqual(called, [])
                rc = R.main({'PAYLOAD_JSON': json.dumps(GOOD), 'REPO_ROOT': d, 'OUT_DIR': d, 'DRY_RUN': 'false'})
                self.assertEqual(rc, 1)  # no BREVO_API_KEY: NEEDS_HUMAN, still no send
                self.assertEqual(called, [])
                R.build, R.check_files = orig_build, orig_check
        finally:
            R.send_brevo = orig

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
