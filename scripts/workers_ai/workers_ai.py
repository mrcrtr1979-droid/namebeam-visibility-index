#!/usr/bin/env python3
"""Workers AI adapter, lane 1 (name labeling). Stdlib only. STAGED 2026-10-26, RUN_ID WORKERSAI-LANE1-1026.

One call surface for Cloudflare Workers AI gpt-oss-120b:
  POST https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/ai/run/@cf/openai/gpt-oss-120b
  Authorization: Bearer $CLOUDFLARE_AI_TOKEN   (account id from env CLOUDFLARE_ACCOUNT_ID)

complete(system, user, ...) returns a dict:
  ok, text, prompt_tokens, completion_tokens, usd, error
and appends one row per answered call to the cost log CSV:
  date_utc, lane, prompt_tokens, completion_tokens, usd
The token is read from the environment only, never printed, never written; any error text is scrubbed of it.

Price (CF_AI_RULING_1026.md, Cloudflare pricing page "Last updated Oct 20, 2026"): $0.35 in / $0.75 out per million
tokens. The usd column is GROSS list price; the 10,000 free neurons a day and the startup credit are not netted.

Response shapes handled (the exact REST shape for this model is UNVERIFIED offline, see the record file):
  chat completions  result.choices[0].message.content   usage.prompt_tokens / completion_tokens
  responses API     result.output[*].content[*].text    usage.input_tokens / output_tokens
  legacy            result.response                      usage.prompt_tokens / completion_tokens
Request style is env WORKERS_AI_STYLE = messages (default) | responses.
"""
import os, re, json, csv, time, datetime
import urllib.request, urllib.error

MODEL = '@cf/openai/gpt-oss-120b'
PRICE_IN = 0.35    # USD per million input tokens
PRICE_OUT = 0.75   # USD per million output tokens
TIMEOUT = 120      # seconds, same as label_names.api()
RETRIES = 3        # attempts per call; 429 and 5xx and network errors only
BACKOFF = (2, 8, 30)
MAX_TOKENS = 4096  # gpt-oss spends completion tokens on reasoning first; Haiku lane used 1800
LOG_DEFAULT = 'status/returns/WORKERSAI_COST_LOG.csv'
LOG_FIELDS = ['date_utc', 'lane', 'prompt_tokens', 'completion_tokens', 'usd']
ACCT_RE = re.compile(r'^[0-9a-f]{32}$')


def cost_usd(prompt_tokens, completion_tokens):
    return round((prompt_tokens * PRICE_IN + completion_tokens * PRICE_OUT) / 1e6, 6)


def endpoint(account_id):
    if not ACCT_RE.match(account_id or ''):
        raise ValueError('CLOUDFLARE_ACCOUNT_ID missing or not 32 hex characters')
    return 'https://api.cloudflare.com/client/v4/accounts/%s/ai/run/%s' % (account_id, MODEL)


def scrub(text, token):
    t = str(text)
    if token:
        t = t.replace(token, '[token]')
    return re.sub(r'(?i)bearer\s+[A-Za-z0-9_\-\.]{8,}', 'Bearer [token]', t)[:300]


def build_body(system, user, max_tokens=MAX_TOKENS, temperature=None, style=None):
    style = style or os.environ.get('WORKERS_AI_STYLE', 'messages')
    msgs = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]
    if style == 'responses':
        body = {'input': msgs, 'max_output_tokens': max_tokens}
    else:
        body = {'messages': msgs, 'max_tokens': max_tokens}
    if temperature is not None:
        body['temperature'] = temperature
    eff = os.environ.get('WORKERS_AI_REASONING_EFFORT', '').strip()
    if eff in ('low', 'medium', 'high'):
        body['reasoning'] = {'effort': eff}
    return body


def extract(envelope):
    """(text, prompt_tokens, completion_tokens) from the Cloudflare JSON envelope; text '' when none found."""
    res = envelope.get('result', envelope) if isinstance(envelope, dict) else {}
    if not isinstance(res, dict):
        return '', 0, 0
    text = ''
    ch = res.get('choices')
    if isinstance(ch, list) and ch and isinstance(ch[0], dict):
        text = ((ch[0].get('message') or {}).get('content')) or ch[0].get('text') or ''
    if not text and isinstance(res.get('output'), list):
        parts = []
        for it in res['output']:
            if isinstance(it, dict) and it.get('type') == 'message':
                for c in it.get('content') or []:
                    if isinstance(c, dict) and isinstance(c.get('text'), str):
                        parts.append(c['text'])
        text = ''.join(parts)
    if not text and isinstance(res.get('output_text'), str):
        text = res['output_text']
    if not text and isinstance(res.get('response'), str):
        text = res['response']
    u = res.get('usage') or (envelope.get('usage') if isinstance(envelope, dict) else None) or {}
    pt = int(u.get('prompt_tokens', u.get('input_tokens', 0)) or 0)
    ct = int(u.get('completion_tokens', u.get('output_tokens', 0)) or 0)
    return (text or '').strip(), pt, ct


def append_cost(path, lane, prompt_tokens, completion_tokens, usd, now=None):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    stamp = (now or datetime.datetime.now(datetime.timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ')
    with open(path, 'a', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh, lineterminator='\n')
        if new:
            w.writerow(LOG_FIELDS)
        w.writerow([stamp, lane, prompt_tokens, completion_tokens, '%.6f' % usd])


def _http_post(url, headers, body, timeout):
    rq = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST', headers=headers)
    try:
        with urllib.request.urlopen(rq, timeout=timeout) as r:
            return r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')


def complete(system, user, lane='label_names', max_tokens=MAX_TOKENS, temperature=None,
             log_path=None, post=None, sleep=None, token=None, account_id=None):
    """Same contract as the Haiku call: system + user in, text out. Never raises; ok False carries error."""
    token = token if token is not None else os.environ.get('CLOUDFLARE_AI_TOKEN', '')
    account_id = account_id if account_id is not None else os.environ.get('CLOUDFLARE_ACCOUNT_ID', '')
    out = {'ok': False, 'text': '', 'prompt_tokens': 0, 'completion_tokens': 0, 'usd': 0.0, 'error': ''}
    if not token:
        out['error'] = 'CLOUDFLARE_AI_TOKEN not set'
        return out
    try:
        url = endpoint(account_id)
    except ValueError as e:
        out['error'] = str(e)
        return out
    post = post or _http_post
    sleep = sleep or time.sleep
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    body = build_body(system, user, max_tokens, temperature)
    for attempt in range(RETRIES):
        try:
            status, raw = post(url, headers, body, TIMEOUT)
        except Exception as exc:  # network, timeout
            status, raw = 0, scrub(exc, token)
        if status == 429 or status >= 500 or status == 0:
            out['error'] = 'HTTP %s: %s' % (status, scrub(raw, token))
            if attempt < RETRIES - 1:
                sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
                continue
            return out
        if status != 200:
            out['error'] = 'HTTP %s: %s' % (status, scrub(raw, token))
            return out
        try:
            env = json.loads(raw)
        except Exception:
            out['error'] = 'response not JSON: ' + scrub(raw, token)
            return out
        if isinstance(env, dict) and env.get('success') is False:
            out['error'] = 'api errors: ' + scrub(json.dumps(env.get('errors')), token)
            return out
        text, pt, ct = extract(env)
        out.update(prompt_tokens=pt, completion_tokens=ct, usd=cost_usd(pt, ct))
        if pt or ct:  # tokens were spent even when the text is empty, so log them
            append_cost(log_path or os.environ.get('WORKERSAI_COST_LOG', LOG_DEFAULT), lane, pt, ct, out['usd'])
        if not text:
            out['error'] = 'empty completion (reasoning may have used the whole max_tokens budget)'
            return out
        out.update(ok=True, text=text)
        return out
    return out
