#!/usr/bin/env python3
"""Selftest for the Workers AI adapter and shadow mode. No network. Run: python3 -I selftest.py"""
import os, sys, json, csv, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import workers_ai as W  # noqa: E402
import shadow as S  # noqa: E402

N = [0]


def ok(c, msg):
    N[0] += 1
    print(('PASS ' if c else 'FAIL ') + msg)
    if not c:
        raise SystemExit(1)


ACCT = 'f' * 32
TOKEN = 'TESTTOKEN_abcdefghijklmnop'
LABELS = json.dumps([{'i': 1, 'label': 'BUSINESS', 'reason': 'plumbing company'},
                     {'i': 2, 'label': 'NOT_BUSINESS', 'reason': 'directory'}])


def chat(text, pt=2000, ct=300):
    return json.dumps({'success': True, 'errors': [], 'result': {'choices': [{'message': {'content': text}}],
                                                               'usage': {'prompt_tokens': pt, 'completion_tokens': ct}}})


def responses(text, pt=11, ct=7):
    return json.dumps({'success': True, 'result': {'output': [{'type': 'reasoning', 'content': [{'type': 'reasoning_text', 'text': 'x'}]},
                       {'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}],
                       'usage': {'input_tokens': pt, 'output_tokens': ct}}})


def stub(seq, calls):
    it = iter(seq)

    def post(url, headers, body, timeout):
        calls.append((url, headers, body, timeout))
        return next(it)
    return post


d = tempfile.mkdtemp()
log = os.path.join(d, 'sub', 'WORKERSAI_COST_LOG.csv')

# cost math: the worked example in CF_AI_WORKERS_AI_1026.md (2000 in, 300 out)
ok(W.cost_usd(2000, 300) == 0.000925, 'cost math 2000 in + 300 out = $0.000925')
ok(W.cost_usd(1_000_000, 1_000_000) == 1.10, 'cost math 1M in + 1M out = $1.10')
ok(W.endpoint(ACCT) == 'https://api.cloudflare.com/client/v4/accounts/%s/ai/run/@cf/openai/gpt-oss-120b' % ACCT, 'endpoint URL exact')
try:
    W.endpoint('not-an-id'); ok(False, 'bad account id rejected')
except ValueError:
    ok(True, 'bad account id rejected before any request')

# JSON contract, chat shape; auth header, URL, body
calls = []
r = W.complete('SYS', 'USER', log_path=log, post=stub([(200, chat(LABELS))], calls), token=TOKEN, account_id=ACCT)
ok(r['ok'] and r['text'] == LABELS and r['prompt_tokens'] == 2000 and r['completion_tokens'] == 300 and r['usd'] == 0.000925, 'chat shape parsed: text, tokens, usd')
ok(calls[0][1]['Authorization'] == 'Bearer ' + TOKEN and calls[0][0].endswith('/ai/run/@cf/openai/gpt-oss-120b'), 'bearer header and run URL')
ok(calls[0][2]['messages'][0] == {'role': 'system', 'content': 'SYS'} and calls[0][2]['messages'][1]['content'] == 'USER', 'system and user messages carried')
# responses shape
r2 = W.complete('S', 'U', log_path=log, post=stub([(200, responses(LABELS))], []), token=TOKEN, account_id=ACCT)
ok(r2['ok'] and r2['text'] == LABELS and (r2['prompt_tokens'], r2['completion_tokens']) == (11, 7), 'responses shape parsed (reasoning item skipped)')
# legacy shape
leg = json.dumps({'success': True, 'result': {'response': 'hi', 'usage': {'prompt_tokens': 3, 'completion_tokens': 1}}})
ok(W.extract(json.loads(leg)) == ('hi', 3, 1), 'legacy response shape parsed')

# cost log: header once, one row per answered call, columns exact
rows = list(csv.reader(open(log)))
ok(rows[0] == ['date_utc', 'lane', 'prompt_tokens', 'completion_tokens', 'usd'] and len(rows) == 3, 'cost log header once, two rows appended')
ok(rows[1][1:] == ['label_names', '2000', '300', '0.000925'] and rows[1][0].endswith('Z'), 'cost log row fields: lane, tokens, usd, UTC stamp')

# retry: 429 then success; backoff slept; one log row only for the success
slept = []
c3 = []
r3 = W.complete('S', 'U', log_path=log, post=stub([(429, 'slow'), (503, 'down'), (200, chat('ok'))], c3), sleep=slept.append, token=TOKEN, account_id=ACCT)
ok(r3['ok'] and len(c3) == 3 and slept == [2, 8], 'retries 429 and 503 with backoff 2s, 8s, then succeeds')
# 4xx not retried; error scrubbed of the token
c4 = []
r4 = W.complete('S', 'U', log_path=log, post=stub([(401, 'bad Bearer %s' % TOKEN)], c4), token=TOKEN, account_id=ACCT)
ok((not r4['ok']) and len(c4) == 1 and TOKEN not in r4['error'], '401 not retried and token scrubbed from error')
# network exception scrubbed
def boom(*a, **k):
    raise OSError('connect failed using %s' % TOKEN)
r5 = W.complete('S', 'U', log_path=log, post=boom, sleep=lambda s: None, token=TOKEN, account_id=ACCT)
ok((not r5['ok']) and TOKEN not in r5['error'], 'network failure returns ok False, token scrubbed, no raise')
# missing token or account never calls out
ok(W.complete('S', 'U', token='', account_id=ACCT, post=boom)['error'] == 'CLOUDFLARE_AI_TOKEN not set', 'no token, no call')
# empty completion still logs the spent tokens but is not ok
n_before = len(list(csv.reader(open(log))))
r6 = W.complete('S', 'U', log_path=log, post=stub([(200, chat('', 50, 4096))], []), token=TOKEN, account_id=ACCT)
ok((not r6['ok']) and len(list(csv.reader(open(log)))) == n_before + 1, 'empty completion: ok False, spent tokens still logged')
# the module never writes the token to the log
ok(TOKEN not in open(log).read(), 'token absent from cost log')

# shadow: contract against the real label_names.py when present
ln = S.load_label_names()
if ln is None:
    print('SKIP label_names.py not found; shadow contract checks not run')
else:
    ok(ln.SYSTEM.startswith('You label names') and ln.CHUNK == 40, 'label_names prompt contract found (system prompt, chunk 40)')
    hrows = [{'name': 'Roto-Rooter', 'market': 'Chicago IL', 'niche': 'plumbers', 'label': 'BUSINESS', 'model': ln.MODEL},
             {'name': 'Yelp Pages', 'market': 'Chicago IL', 'niche': 'plumbers', 'label': 'NOT_BUSINESS', 'model': ln.MODEL}]
    cs = []
    pairs, meta = S.run_shadow(hrows, ln, post=stub([(200, chat(LABELS, 1100, 90))], cs), log_path=log, token=TOKEN, account_id=ACCT)
    sent = cs[0][2]['messages']
    ok(sent[0]['content'] == ln.SYSTEM and sent[1]['content'] == ln.user_prompt([(r['name'], r['market'], r['niche']) for r in hrows]),
       'shadow sends the identical system prompt and user template as the Haiku call')
    ok(pairs == [('Roto-Rooter', 'BUSINESS', 'BUSINESS'), ('Yelp Pages', 'NOT_BUSINESS', 'NOT_BUSINESS')], 'shadow parses the same JSON shape with the same parser')
    ok(meta['calls'] == 1 and meta['prompt_tokens'] == 1100 and meta['usd'] == W.cost_usd(1100, 90), 'shadow meta tokens and usd')

# agreement math and verdict rule
big = [('n%d' % i, 'BUSINESS', 'BUSINESS') for i in range(180)] + [('x%d' % i, 'NOT_BUSINESS', 'NOT_BUSINESS') for i in range(20)]
a = S.agreement(big)
ok(a['n'] == 200 and a['rate'] == 1.0 and a['verdict'] == 'PASS', 'agreement 200/200 over the minimum is PASS')
bad = big[:150] + [('d%d' % i, 'BUSINESS', 'NOT_BUSINESS') for i in range(30)] + big[180:]
ab = S.agreement(bad)
ok(ab['verdict'] == 'NOT YET' and abs(ab['business_rate'] - 150 / 180) < 1e-9 and ab['confusion'] == {'BUSINESS -> NOT_BUSINESS': 30}, 'BUSINESS-class disagreement fails the gate and is counted')
ok(S.agreement(big[:10])['verdict'] == 'NOT YET', 'fewer than 150 names is never PASS')
ok(S.agreement(big[:190] + [('u%d' % i, 'BUSINESS', None) for i in range(10)])['verdict'] == 'NOT YET', 'unanswered over 2 percent fails the gate')
print('SELFTEST OK %d checks' % N[0])
