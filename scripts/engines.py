"""
E1 ENGINE ADAPTERS. One function per engine, all with the same contract.

Plain-language note for a non-coder reading along:
Until now E1 asked exactly one engine (Perplexity). This file adds the other
rails so the same dated question can be asked of several engines and the
answers compared. That comparison IS the product: when engines disagree
about who to recommend, at most one of them can be right, and only someone
running all of them can see it.

EVERY adapter returns the same 4-tuple so the runner never needs to know
which engine it is talking to:
    (ok: bool, answer_text: str, sources: list[str], error: str)

Nothing here ever invents a result. If a key is missing, a request fails, or
a response has an unexpected shape, the adapter returns ok=False with a
plain error string and the runner logs a FAILED row. A logged failure is a
success of the protocol; a fabricated row is the one unforgivable bug.

No secret is ever printed, logged, or written to a corpus row. Keys are read
from the environment at call time and never stored on an object.
"""

import os
import time
import re
import requests

TIMEOUT = 60
TEMPERATURE = 0.2

# --------------------------------------------------------------------------
# SECRET REDACTION. Load-bearing, not decoration.
#
# Found by testing on 2026-08-05: Gemini passes its key in the URL QUERY
# STRING, so a connection-level exception carries the full URL, key included,
# straight into the error string, which the runner then writes into a corpus
# row that gets committed to a public repo. A fake key proved it.
#
# Every adapter now passes every error through redact() before returning it.
# This scrubs the ACTUAL values of the known secret env vars out of any
# string, whatever path they arrived by. Never return a raw exception string.
# --------------------------------------------------------------------------
SECRET_ENV_VARS = (
    "PERPLEXITY_API_KEY",
    "GEMINI_API_KEY",
    "BRIGHTDATA_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "SERPAPI_KEY",
)


def redact(text):
    """Remove any live secret value from a string. Returns a safe string."""
    if not text:
        return ""
    out = str(text)
    for var in SECRET_ENV_VARS:
        val = os.environ.get(var, "").strip()
        # Guard against a short or empty value nuking the whole message.
        if val and len(val) >= 6:
            out = out.replace(val, "[REDACTED:%s]" % var)
    # Belt and braces: strip any key= query parameter regardless of source.
    import re as _re
    out = _re.sub(r"([?&](?:key|api_key|apikey|token)=)[^&\s\"\']+",
                  r"\1[REDACTED]", out, flags=_re.IGNORECASE)
    return out


# --------------------------------------------------------------------------
# PERPLEXITY (Agent API since 2026-09-27; Sonar Chat Completions retired)
# --------------------------------------------------------------------------
# 2026-09-27 SONAR SUNSET MIGRATION [COWORK-0927]: Perplexity retired Sonar Chat
# Completions ("supported until September 27, 2026", docs.perplexity.ai migrate-from-sonar).
# Same engine, new contract: POST /v1/agent, model "perplexity/sonar" (the documented
# closest match to chat "sonar") with the web_search tool FORCED so every E1 row stays
# grounded. Answer text = output[] message items; citations = the search_results item.
# Live-verified 2026-09-27 from the Brain key: status completed, 15 sources, ~$0.005/call.
PERPLEXITY_URL = "https://api.perplexity.ai/v1/agent"
PERPLEXITY_MODEL = "perplexity/sonar"

# PER-CALL AUDIT FIELDS, forward only [OPUS55-COS-1008C P11]. call_perplexity
# fills LAST_CALL_META["perplexity"] from the parsed response body: which model
# Perplexity actually served, the run id, status, usage, preset and how many
# search results came back. e1_runner.run_engine copies it into the row as
# engine_meta. It never holds the key, the headers or the answer text. The
# 4-tuple returned by call_perplexity does not change.
LAST_CALL_META = {}


def _perplexity_meta(body):
    """Audit fields from a parsed Perplexity body. Never raises."""
    try:
        output = body.get("output")
        if isinstance(output, list):
            items = [i for i in output if isinstance(i, dict)]
            search_count = sum(
                len(i.get("results") or []) for i in items
                if i.get("type") == "search_results")
            item_types = [i.get("type") for i in items]
        else:
            search_count = None
            item_types = []
        return {
            "served_model": body.get("model"),
            "response_id": body.get("id"),
            "status": body.get("status"),
            "usage": body.get("usage"),
            "preset": body.get("preset"),
            "search_results_count": search_count,
            "output_item_types": item_types,
        }
    except Exception:
        return {}


def call_perplexity(prompt_text):
    LAST_CALL_META["perplexity"] = {}
    key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    if not key:
        return False, "", [], redact("PERPLEXITY_API_KEY not set")
    try:
        r = requests.post(
            PERPLEXITY_URL,
            headers={"Authorization": "Bearer " + key,
                     "Content-Type": "application/json"},
            json={"model": PERPLEXITY_MODEL, "temperature": TEMPERATURE,
                  "input": prompt_text,
                  "tools": [{"type": "web_search"}],
                  "tool_choice": {"type": "web_search"}},
            timeout=TIMEOUT,
        )
    except requests.exceptions.RequestException as exc:
        return False, "", [], redact("perplexity request failed: %s" % exc)
    if r.status_code != 200:
        prefix = "QUOTA: " if r.status_code == 429 else ""
        return False, "", [], redact(
            "%sperplexity HTTP %s: %s" % (prefix, r.status_code, r.text[:300]))
    try:
        body = r.json()
        LAST_CALL_META["perplexity"] = _perplexity_meta(body)
        if body.get("status") != "completed":
            return False, "", [], redact("perplexity run %s: %s" % (
                body.get("status"), str(body.get("error"))[:300]))
        text = "".join(
            part.get("text", "")
            for item in body.get("output", []) if item.get("type") == "message"
            for part in item.get("content", []) if part.get("type") == "output_text")
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return False, "", [], redact("perplexity response shape unexpected")
    if not text:
        return False, "", [], redact("perplexity returned no answer text")
    cites = []
    for item in body.get("output", []):
        if item.get("type") == "search_results":
            cites += [s.get("url") for s in item.get("results", []) if s.get("url")]
    return True, text, cites, ""


# --------------------------------------------------------------------------
# GEMINI
# Endpoint and key-passing verified against ai.google.dev 2026-08-05.
# MODEL NAME IS PENDING VERIFICATION: model strings change and a wrong one
# returns HTTP 404, which this adapter reports plainly rather than hiding.
# --------------------------------------------------------------------------
GEMINI_MODEL = "gemini-3.6-flash"  # CONFIRMED CORRECT on live runs 2026-08-05
GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
              "%s:generateContent")


# RETRY ADDED 2026-09-19 [FABLE-COS-0919B] (R61): since 2026-09-01 the free tier
# refused 423 of 846 Gemini calls (QUOTA_BLOCKED) and 139 more died on 503,
# because all 47 calls fire in one burst with no pacing and no second try.
# A 429 body carries RetryInfo.retryDelay; when it is short (per-minute
# quota) we wait and ask again. A long or absent delay (per-day quota) is
# NOT waited on: the row logs QUOTA exactly as before, now with the quotaId
# so the next reader can tell per-minute from per-day without guessing.
# The question, the model and the row schema are unchanged.
GEMINI_MAX_TRIES = 4
GEMINI_MAX_WAIT_S = 70
GEMINI_SLEEP_BUDGET_S = 2400   # whole-run ceiling, so a bad day cannot hang the job
_gemini_slept = [0.0]


def _gemini_retry_delay(body_text):
    m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', body_text or "")
    return float(m.group(1)) if m else None


def _gemini_quota_id(body_text):
    m = re.search(r'"quotaId"\s*:\s*"([^"]+)"', body_text or "")
    return m.group(1) if m else "quotaId-not-in-body"


def call_gemini(prompt_text, _post=None, _sleep=None):
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return False, "", [], redact("GEMINI_API_KEY not set")
    post = _post or requests.post
    sleep = _sleep or time.sleep
    last = ""
    for attempt in range(1, GEMINI_MAX_TRIES + 1):
        try:
            r = post(
                (GEMINI_URL % GEMINI_MODEL) + "?key=" + key,
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt_text}]}]},
                timeout=TIMEOUT,
            )
        except requests.exceptions.RequestException as exc:
            return False, "", [], redact("gemini request failed: %s" % exc)
        if r.status_code == 200:
            try:
                body = r.json()
                text = body["candidates"][0]["content"]["parts"][0]["text"]
            except (ValueError, KeyError, IndexError, TypeError):
                return False, "", [], redact("gemini response shape unexpected")
            # Gemini's plain generateContent returns no citation list. We do NOT
            # invent one. Sources stay empty and that is an honest empty, not a zero.
            return True, text, [], ""
        # Never echo the URL back, it carries the key in the query string.
        # A QUOTA refusal is not an engine failure and must never be counted
        # as one (doctrine 122: a zero produced by a refusal is not a finding).
        if r.status_code == 429:
            delay = _gemini_retry_delay(r.text)
            last = "QUOTA: gemini HTTP 429 [%s] tries=%d: %s" % (
                _gemini_quota_id(r.text), attempt, r.text[:300])
            # 2026-09-19 run graded: every refusal named GenerateRequestsPerDay...FreeTier and 28 of 29
            # blocked rows burned retries (run time 42 min -> 109 min). A per-day quota cannot be
            # waited out inside a run, so it is never retried.
            wait = None if (delay is None or 'perday' in _gemini_quota_id(r.text).lower()) else delay + 2.0
        elif r.status_code in (500, 502, 503, 504):
            last = "gemini HTTP %s tries=%d: %s" % (r.status_code, attempt, r.text[:300])
            wait = 8.0 * attempt
        else:
            return False, "", [], redact(
                "gemini HTTP %s: %s" % (r.status_code, r.text[:300]))
        if (attempt == GEMINI_MAX_TRIES or wait is None or wait > GEMINI_MAX_WAIT_S
                or _gemini_slept[0] + wait > GEMINI_SLEEP_BUDGET_S):
            break
        _gemini_slept[0] += wait
        sleep(wait)
    return False, "", [], redact(last)


# --------------------------------------------------------------------------
# BRIGHT DATA SERP
# This is NOT a chat engine. It returns a Google results page, which gives
# the ORGANIC TOP RESULTS. That is exactly what DW-2 needs: the delta
# between what ranks on Google and what AI actually cites.
#
# HONEST LIMITATION, stated in code so nobody "improves" it into a lie:
# Bright Data's public docs do NOT document a parsed-JSON parameter or an
# AI Overview field. So this adapter does NOT claim to extract AI Overview.
# It extracts organic result URLs from the returned HTML and reports
# ai_overview_detected as a best-effort boolean. When it cannot tell, it
# says so rather than guessing.
# --------------------------------------------------------------------------
BRIGHTDATA_URL = "https://api.brightdata.com/request"
# CI can supply this as SET-BUT-EMPTY, which defeats a plain default
# (that exact failure cost 13 SERP rows on 2026-08-05). Doctrine-120 form:
BRIGHTDATA_ZONE = os.environ.get("BRIGHTDATA_ZONE", "").strip() or "serp_api1"


def fetch_serp(query_text, top_n=10):
    """Return (ok, html, organic_urls, error). Never raises.

    RETRY ADDED 2026-08-07 (doctrine-148-era audit): 30 of 97 SERP rows in
    the corpus carried html_bytes=0 with HTTP 200, a transient Bright Data
    empty-body response. A 200 with an empty or sub-2000-byte body now
    retries up to 2 more times with a short backoff before the row is
    surrendered to EXTRACTION_FAILED. A retry that recovers is a receipt
    saved; a retry that fails changes nothing about honesty, the row still
    prints failed with its diagnostics.
    """
    key = os.environ.get("BRIGHTDATA_API_KEY", "").strip()
    if not key:
        return False, "", [], redact("BRIGHTDATA_API_KEY not set")
    import time as _time
    import urllib.parse
    # 2026-09-14 [FABLE-COWORK-0914C] ROOT CAUSE of 36 of 47 SERP rows a day
    # EXTRACTION_FAILED since 2026-08-27 (blank rate 5 pct before, 77 pct
    # after; every failed row carries looks_like_consent_wall=True, 80 hrefs,
    # 0 redirect hrefs): Bright Data started exiting from a geo where Google
    # serves the "Before you continue" consent interstitial. Pin the request
    # to a US exit and a US/English results page. A US page has no consent
    # wall; the 11 rows a day that still extract are the ones that happened
    # to exit from the US.
    target = ("https://www.google.com/search?q=" + urllib.parse.quote(query_text)
              + "&hl=en&gl=us&pws=0")
    # 2026-09-15 [SONNET-B2-0915]. UNVERIFIED, OFF BY DEFAULT, and it stays that way
    # until someone runs it with a real key and reads the rows.
    # Why it exists: PR #7's geo fix is already live and already ran (commit 18a4d064
    # at 06:34Z; the 09-14 slice ran at 18:02Z on that exact head_sha) and the failure
    # rate did not move: 38 of 47 rows still EXTRACTION_FAILED, on 574 KB pages
    # carrying 94 hrefs and redirect_hrefs=0. Pages that big are not consent walls.
    # The hypothesis this flag tests is that Bright Data "format: raw" returns the
    # rendered shell while the organic results are injected client-side, so they are
    # not anchors in the HTML at all. Bright Data's SERP zones expose a parsed-JSON
    # output; if this zone supports it, the results arrive as data instead of markup.
    # Set the repo variable BRIGHTDATA_PARSED=1 to try it. If the run comes back with
    # organic results, this is the cure and the flag becomes the default. If it comes
    # back HTTP 400, this zone does not support parsed output, the flag is deleted,
    # and the next move is a different SERP provider. Either answer is worth one run.
    if os.environ.get("BRIGHTDATA_PARSED", "").strip() == "1":
        target += "&brd_json=1"
    # 2026-09-19 [FABLE-COS-0919B] (R61). The 09-15 hypothesis above was finally run:
    # manual workflow serp_parsed_smoke.yml, run 35419280907, same zone, same query.
    #   raw HTML            -> 0 bytes
    #   brd_json=1, raw     -> 209 bytes "This query recently failed ... minimum of 15 seconds"
    #   brd_json=1, json    -> 118,297 bytes, organic_n=9 with real links, plus snack_pack
    # So the results ARE available as data. Parsed output is now asked FIRST. If it does
    # not come back with organic links, the raw path below runs exactly as it did before,
    # so this can only add rows, never remove one. serp_diagnostics records which payload
    # a row came from ("payload": parsed_json or raw_html) so the series change is visible.
    # The 209-byte body also explains the "empty response" rows: Bright Data wants 15 s
    # between tries and the old backoff was 3 s and 6 s.
    parsed_text, parsed_urls = _fetch_serp_parsed(key, target, top_n, query_text=query_text)
    if parsed_urls:
        return True, parsed_text, parsed_urls, ""
    if _PARSED_DRIFT[0]:
        # three tries all answered a different question: fail the row, do not let the
        # raw path store a fourth unguarded answer as OK (2026-10-04, FABLE-COS-1004D)
        return False, "", [], redact("query drift after 3 tries: " + _PARSED_DRIFT[0])
    html = ""
    for attempt in range(3):
        try:
            r = requests.post(
                BRIGHTDATA_URL,
                headers={"Authorization": "Bearer " + key,
                         "Content-Type": "application/json"},
                json={"zone": BRIGHTDATA_ZONE, "url": target, "format": "raw",
                      "country": "us"},
                timeout=TIMEOUT,
            )
        except requests.exceptions.RequestException as exc:
            if attempt < 2:
                _time.sleep(3 * (attempt + 1))
                continue
            return False, "", [], redact("brightdata request failed: %s" % exc)
        if r.status_code != 200:
            prefix = "QUOTA: " if r.status_code == 429 else ""
            return False, "", [], redact(
                "%sbrightdata HTTP %s: %s" % (prefix, r.status_code, r.text[:300]))
        html = r.text or ""
        if len(html) >= 2000:
            break
        if attempt < 2:
            _time.sleep(16 if "recently failed" in html else 3 * (attempt + 1))
    return True, html, extract_organic_urls(html, top_n), ""


def _parse_serp_json(text, top_n=10):
    """Organic links, in rank order, from Bright Data parsed SERP JSON. [] when
    the text is not that. Accepts the bare object or the {"body": "..."} wrapper."""
    import json as _json
    try:
        j = _json.loads(text or "")
        if isinstance(j, dict) and isinstance(j.get("body"), str):
            j = _json.loads(j["body"])
    except ValueError:
        return []
    org = j.get("organic") if isinstance(j, dict) else None
    if not isinstance(org, list):
        return []
    out, seen = [], set()
    for o in org:
        u = o.get("link") if isinstance(o, dict) else None
        if not isinstance(u, str) or not u.startswith("http"):
            continue
        if any(s in u for s in GOOGLE_SKIP):
            continue
        k = u.split("#")[0].rstrip("/")
        if k in seen:
            continue
        seen.add(k)
        out.append(u)
        if len(out) >= top_n:
            break
    return out


_PARSED_NOTE = [""]   # why the last parsed-JSON attempt did or did not yield links; lands in serp_diagnostics


# 2026-10-04 [FABLE-COS-1004D] (R61). QUERY-DRIFT GUARD. From 2026-09-28 the parsed SERP
# path returned results for ONE word of our question on 2 to 10 rows a day (read from the
# record: Dallas personal injury -> courts.mo.gov, case.org, caseknives.com, caseih.com for
# "...truck accident case?"; Miami roofing and Pleasanton tanning -> bestbuy.com; Kansas City
# health insurance -> health.com, cdc.gov). Those rows were stored as OK and reached the
# public panels. Google answered a different question, so the row is not a measurement.
# Rule: if Bright Data echoes the query Google ran (general.query) and it shares fewer than
# half (at least 2) of our question's content words, or, with no echo, the top results
# together match at most one content word, the attempt is DRIFT: wait 16 s and ask again;
# after three drifted tries the row is surrendered as EXTRACTION_FAILED with the reason,
# never stored as OK. A real answer always names the trade and the city somewhere in ten
# results, so a correct row cannot match only one word.
_DRIFT_STOP = {
    "what", "which", "where", "when", "best", "good", "great", "near", "with", "that",
    "this", "does", "from", "your", "have", "find", "need", "recommend", "recommends",
    "company", "companies", "service", "services", "there", "they", "them", "about",
    "into", "more", "most", "some", "like", "would", "could", "should", "will", "than",
    "their", "other", "options", "top", "rated", "local", "area",
}
_PARSED_DRIFT = [""]   # set when the parsed path gave up on drift; fetch_serp then fails the row


def _query_tokens(query_text):
    import re as _re
    words = _re.findall(r"[a-z0-9]+", (query_text or "").lower())
    return {w for w in words if len(w) >= 4 and w not in _DRIFT_STOP}


def _serp_drift(text, query_text):
    """Reason string when the results answer a different question than ours, else ''."""
    import json as _json
    want = _query_tokens(query_text)
    if len(want) < 2:
        return ""
    try:
        j = _json.loads(text or "")
        if isinstance(j, dict) and isinstance(j.get("body"), str):
            j = _json.loads(j["body"])
    except ValueError:
        return ""
    if not isinstance(j, dict):
        return ""
    gen = j.get("general")
    gq = gen.get("query") if isinstance(gen, dict) else None
    if isinstance(gq, str) and gq.strip():
        shared = len(_query_tokens(gq) & want)
        if shared < max(2, len(want) // 2):
            return "google searched %r (%d of %d words)" % (gq[:80], shared, len(want))
        return ""
    org = j.get("organic")
    if not isinstance(org, list) or not org:
        return ""
    hit = set()
    for o in org[:10]:
        if not isinstance(o, dict):
            continue
        blob = " ".join(str(o.get(k, "")) for k in ("title", "description", "link", "display_link")).lower()
        hit |= {w for w in want if w in blob}
    if len(hit) <= 1:
        return "results match %d of %d question words (%s)" % (len(hit), len(want), ",".join(sorted(hit)) or "none")
    return ""


def _fetch_serp_parsed(key, target, top_n, _post=None, _sleep=None, query_text=None):
    """Return (text, urls). Never raises; ("", []) means fall back to raw
    (unless _PARSED_DRIFT is set: then the row fails, see the drift guard above)."""
    _PARSED_NOTE[0] = "not attempted"
    _PARSED_DRIFT[0] = ""
    import time as _t
    post = _post or requests.post
    sleep = _sleep or _t.sleep
    sep = "&" if "?" in target else "?"
    url = target if "brd_json=1" in target else target + sep + "brd_json=1"
    # 2026-10-04 [FABLE-COS-1004A] (R61). The 10-03 run carried the new ai_overview_*
    # fields on 47 of 47 SERP rows but an AI Overview on only 1 of 47. Bright Data
    # documents why: Google loads the AI Overview after the page, and the parsed JSON
    # carries it only when the request adds brd_ai_overview=2 ("increases the chances
    # of retrieving Google's Generative AI Overview sections", about 5 to 10 s more per
    # request; docs.brightdata.com/products/serp-api/query-parameters/google and
    # brightdata.com/products/serp-api/google-search/ai-overview, read 2026-10-04).
    # The request count is unchanged. Repo variable BRIGHTDATA_AIO=0 turns it off
    # without a commit; anything else, or unset, asks for it.
    if (os.environ.get("BRIGHTDATA_AIO", "").strip() != "0"
            and "brd_ai_overview=" not in url):
        url += "&brd_ai_overview=2"
    # 2026-09-28 [FABLE-COS-0928B] (R61). Read from the record, 09-24..09-27: 44 of 47
    # EXTRACTION_FAILED rows in four days carried "no organic links in 2xx bytes, try 1:
    # not json" and 4 carried "request failed: ReadTimeout". Both shapes are Bright Data
    # not answering yet (a 200-310 byte non-JSON body, or no body in time), and the old
    # loop retried only when the body said "recently failed", so those rows fell to the
    # raw-HTML path, which has no organic anchors on AI-overview pages. Now: a short
    # non-JSON body or a ReadTimeout waits 16 s and tries again, three tries in all, and
    # the short body's first 120 characters land in the diagnostics so the next reader
    # sees Bright Data's own words. The raw fallback below is unchanged; this can only
    # add rows, never remove one.
    for attempt in range(3):
        try:
            r = post(BRIGHTDATA_URL,
                     headers={"Authorization": "Bearer " + key,
                              "Content-Type": "application/json"},
                     json={"zone": BRIGHTDATA_ZONE, "url": url, "format": "json",
                           "country": "us"},
                     timeout=TIMEOUT)
        except requests.exceptions.RequestException as exc:
            _PARSED_NOTE[0] = "request failed: %s, try %d" % (type(exc).__name__, attempt + 1)
            if attempt < 2:
                sleep(16)
                continue
            return "", []
        if r.status_code != 200:
            _PARSED_NOTE[0] = "HTTP %s" % r.status_code
            return "", []
        text = r.text or ""
        urls = _parse_serp_json(text, top_n)
        if urls:
            drift = _serp_drift(text, query_text) if query_text else ""
            if drift:
                _PARSED_NOTE[0] = "query drift, try %d: %s" % (attempt + 1, drift)
                if attempt < 2:
                    sleep(16)
                    continue
                _PARSED_DRIFT[0] = drift
                return "", []
            _PARSED_NOTE[0] = "ok, %d links, try %d" % (len(urls), attempt + 1)
            return text, urls
        keys = _parsed_keys(text)
        short = len(text) < 2000 and keys == "not json"
        if "recently failed" in text or short:
            _PARSED_NOTE[0] = "%s body, try %d: %r" % (
                "recently-failed" if "recently failed" in text else "short non-json",
                attempt + 1, text[:120])
            if attempt < 2:
                sleep(16)
                continue
        else:
            _PARSED_NOTE[0] = "no organic links in %d bytes, try %d: %s" % (
                len(text), attempt + 1, keys)
        break
    return "", []


def _parsed_keys(text):
    import json as _json
    try:
        j = _json.loads(text or "")
        if isinstance(j, dict) and isinstance(j.get("body"), str):
            j = _json.loads(j["body"])
        return ",".join(sorted(j.keys()))[:160] if isinstance(j, dict) else type(j).__name__
    except ValueError:
        return "not json"


GOOGLE_SKIP = ("google.", "gstatic.", "googleusercontent.", "googleadservices.",
               "youtube.com/redirect", "accounts.google", "policies.google",
               "support.google", "webcache.", "schema.org", "w3.org")


def extract_organic_urls(html, top_n=10):
    """Pull outbound result URLs out of a Google results page, in order,
    deduplicated by domain-and-path.

    REWRITTEN 2026-08-05 after 6 of 13 live SERP rows returned ZERO results.
    The old version had four defects, each verified against real markup:

      1. It required href to start with http, so it MISSED the most common
         Google form of all, the RELATIVE redirect href="/url?q=...".
         This alone accounts for the zeros.
      2. It only matched double-quoted hrefs, missing single-quoted ones.
      3. It never unescaped HTML entities, so &amp; survived into URLs.
      4. It ran url.split("&")[0], which truncated legitimate query strings.
         A Yelp search URL lost everything after find_desc.

    Still mechanical and reproducible. A missed result is a false negative,
    which is the safe direction of error. What is NOT acceptable is a silent
    zero, so this returns diagnostics alongside the URLs; see extract_serp().
    """
    import re
    import html as _html
    import urllib.parse
    if not html:
        return []
    text = _html.unescape(html)

    # Google's own properties and infrastructure are never results.
    # Hoisted to GOOGLE_SKIP (module level) 2026-09-15 so serp_diagnostics can count
    # surviving hrefs with the SAME list the extractor uses. A diagnostic that measures
    # something different from the code it explains is worse than no diagnostic.
    skip = GOOGLE_SKIP
    # Tracking parameters Google appends. Strip ONLY these, never the whole
    # query string, or real search URLs get destroyed.
    junk_params = {"sa", "ved", "usg", "source", "cd", "cad", "uact", "opi",
                   "sca_esv", "gs_lcp", "ei", "oq", "sclient", "bih", "biw"}

    def clean(u):
        try:
            parts = urllib.parse.urlsplit(u)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                return None
            q = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query)
                 if k not in junk_params]
            return urllib.parse.urlunsplit(
                (parts.scheme, parts.netloc, parts.path,
                 urllib.parse.urlencode(q), ""))
        except ValueError:
            return None

    out, seen = [], set()

    def add(u):
        u = clean(u)
        if not u:
            return
        if any(s in u for s in skip):
            return
        parts = urllib.parse.urlsplit(u)
        key = (parts.netloc.lower(), parts.path.rstrip("/"))
        if key in seen:
            return
        seen.add(key)
        out.append(u)

    # Pattern 1: the redirect form, RELATIVE or ABSOLUTE. Most common.
    for m in re.finditer(r'href=["\'](?:https?://[^"\'/]*google\.[^"\'/]*)?/url\?([^"\']+)', text):
        qs = urllib.parse.parse_qs(m.group(1))
        for key in ("q", "url"):
            if key in qs and qs[key]:
                add(qs[key][0])
                break
        if len(out) >= top_n:
            return out[:top_n]

    # Pattern 2: direct outbound href, either quote style.
    for m in re.finditer(r'href=["\'](https?://[^"\']+)["\']', text):
        add(m.group(1))
        if len(out) >= top_n:
            return out[:top_n]

    # Pattern 3: last resort, bare URLs in data attributes some layouts use.
    if not out:
        for m in re.finditer(r'data-(?:href|url)=["\'](https?://[^"\']+)["\']', text):
            add(m.group(1))
            if len(out) >= top_n:
                break

    return out[:top_n]


def serp_diagnostics(html, organic):
    """Why did extraction return what it returned? Recorded on every SERP row
    so a zero is SELF-EXPLAINING and never mistaken for a finding (doctrine 122).
    """
    import re
    import html as _html
    if html is None:
        html = ""
    text = _html.unescape(html)
    low = text.lower()
    return {
        # ADDED 2026-09-19: which Bright Data payload this row was read from.
        # 2026-09-19: the first version looked for the literal "organic" key and missed the
        # Bright Data wrapper, where the body is an escaped string; 23 parsed rows were
        # labelled raw_html. Ask the parser itself.
        "payload": ("parsed_json" if _parse_serp_json(html, 1) else "raw_html"),
        "parsed_attempt": _PARSED_NOTE[0],
        "html_bytes": len(html),
        "href_total": len(re.findall(r'href=', text)),
        "redirect_hrefs": len(re.findall(r'href=["\'][^"\']*?/url\?', text)),
        # ADDED 2026-09-15 [SONNET-B2-0915]. This is the number that decides the
        # 38-of-47 failure, and nothing was measuring it. href_total counts every
        # anchor including Google's own navigation; this counts only the ones that
        # could ever BE a result. If this is ~0 on a 574 KB page, the organic results
        # are not in the payload at all and no parser change can recover them.
        "nongoogle_hrefs": len([
            u for u in re.findall(r'href=["\'](https?://[^"\']+)["\']', text)
            if not any(s in u for s in GOOGLE_SKIP)]),
        # The consent phrase and a consent WALL are not the same thing. Measured on
        # the 2026-09-14 slice: "before you continue" was present on 29 of 38 FAILED
        # rows AND on OK rows that extracted fine, on pages of 574 to 592 KB. A real
        # interstitial is a small page. Reporting the raw phrase as a wall sent the
        # 09-14 seat after a geo fix (PR #7, US exit + gl/hl pins) that shipped at
        # 06:34Z, ran in the 18:02Z slice on its own commit, and changed nothing.
        "consent_phrase_present": ("before you continue" in low
                                   or "consent.google" in low),
        "looks_like_consent_wall": (("before you continue" in low
                                     or "consent.google" in low)
                                    and len(html) < 50000),
        "looks_like_captcha": ("unusual traffic" in low
                               or "recaptcha" in low),
        "extraction_ok": bool(organic),
        "zero_reason": (None if organic else
                        ("empty response" if len(html) < 2000 else
                         "non-empty page, no result links survived the Google-property "
                         "filter: see nongoogle_hrefs. If that is ~0 the results were "
                         "never in the HTML and the fix is at the request layer "
                         "(parsed SERP output), not in this parser")),
    }


def detect_ai_overview(html):
    """Best-effort only. Returns True, False, or None for 'cannot tell'.
    None is a legitimate answer and must be preserved, never coerced to False.
    """
    if not html:
        return None
    markers = ("AI Overview", "AI overview", "aiOverview", "data-attrid=\"AIOverview\"")
    if any(m in html for m in markers):
        return True
    # A results page that clearly rendered but shows no marker is a real False.
    if "search?q=" in html and len(html) > 20000:
        return False
    return None


# --------------------------------------------------------------------------
# OPENAI (added v8, 2026-08-05). Same contract as every adapter:
# (ok, answer_text, citations, error). Chat Completions returns no citation
# list, so citations are always []. MODEL STRING IS PENDING VERIFICATION on
# the first live run; a wrong string returns HTTP 404 which this adapter
# reports plainly. Override without a commit via repo VARIABLE OPENAI_MODEL
# (doctrine-120 form guards against set-but-empty).
# --------------------------------------------------------------------------
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "").strip() or "gpt-5.2-mini"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def call_openai(prompt_text):
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return False, "", [], redact("OPENAI_API_KEY not set")
    try:
        r = requests.post(
            OPENAI_URL,
            headers={"Authorization": "Bearer " + key,
                     "Content-Type": "application/json"},
            # 2026-08-09 fix: gpt-5-class mini models reject any non-default
            # temperature (HTTP 400 on every call, 0-for-all since 08-08 runs).
            # Param dropped for THIS engine only; openai rows therefore run at
            # provider-default temperature while other engines stay at 0.2, a
            # method delta to note per M3 if cross-engine variance is compared.
            json={"model": OPENAI_MODEL,
                  "messages": [{"role": "user", "content": prompt_text}]},
            timeout=TIMEOUT,
        )
    except requests.exceptions.RequestException as exc:
        return False, "", [], redact("openai request failed: %s" % exc)
    if r.status_code != 200:
        prefix = "QUOTA: " if r.status_code == 429 else ""
        return False, "", [], redact(
            "%sopenai HTTP %s: %s" % (prefix, r.status_code, r.text[:300]))
    try:
        text = r.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        return False, "", [], redact("openai response shape unexpected")
    return True, text, [], ""


# --------------------------------------------------------------------------
# ANTHROPIC (added v8, 2026-08-05). Key travels in the x-api-key HEADER,
# never a URL. MODEL STRING IS PENDING VERIFICATION on the first live run;
# override without a commit via repo VARIABLE ANTHROPIC_MODEL.
# --------------------------------------------------------------------------
ANTHROPIC_MODEL = (os.environ.get("ANTHROPIC_MODEL", "").strip()
                   or "claude-haiku-4-5")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def call_anthropic(prompt_text):
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return False, "", [], redact("ANTHROPIC_API_KEY not set")
    try:
        r = requests.post(
            ANTHROPIC_URL,
            headers={"x-api-key": key,
                     "anthropic-version": "2023-06-01",
                     "Content-Type": "application/json"},
            json={"model": ANTHROPIC_MODEL, "max_tokens": 2048,
                  "temperature": TEMPERATURE,
                  "messages": [{"role": "user", "content": prompt_text}]},
            timeout=TIMEOUT,
        )
    except requests.exceptions.RequestException as exc:
        return False, "", [], redact("anthropic request failed: %s" % exc)
    if r.status_code != 200:
        prefix = "QUOTA: " if r.status_code == 429 else ""
        return False, "", [], redact(
            "%santhropic HTTP %s: %s" % (prefix, r.status_code, r.text[:300]))
    try:
        text = r.json()["content"][0]["text"]
    except (ValueError, KeyError, IndexError, TypeError):
        return False, "", [], redact("anthropic response shape unexpected")
    return True, text, [], ""


ENGINES = {
    "perplexity": call_perplexity,
    "gemini": call_gemini,
    "openai": call_openai,
    "anthropic": call_anthropic,
}
