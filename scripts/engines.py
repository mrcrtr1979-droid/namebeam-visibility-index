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
# PERPLEXITY (unchanged behaviour, moved here so all engines live together)
# --------------------------------------------------------------------------
PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
PERPLEXITY_MODEL = "sonar"


def call_perplexity(prompt_text):
    key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    if not key:
        return False, "", [], redact("PERPLEXITY_API_KEY not set")
    try:
        r = requests.post(
            PERPLEXITY_URL,
            headers={"Authorization": "Bearer " + key,
                     "Content-Type": "application/json"},
            json={"model": PERPLEXITY_MODEL, "temperature": TEMPERATURE,
                  "messages": [{"role": "user", "content": prompt_text}]},
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
        text = body["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        return False, "", [], redact("perplexity response shape unexpected")
    cites = body.get("citations", [])
    if not isinstance(cites, list):
        cites = []
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


def call_gemini(prompt_text):
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return False, "", [], redact("GEMINI_API_KEY not set")
    try:
        r = requests.post(
            (GEMINI_URL % GEMINI_MODEL) + "?key=" + key,
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt_text}]}]},
            timeout=TIMEOUT,
        )
    except requests.exceptions.RequestException as exc:
        return False, "", [], redact("gemini request failed: %s" % exc)
    if r.status_code != 200:
        # Never echo the URL back, it carries the key in the query string.
        #
        # A QUOTA refusal is not an engine failure and must never be counted
        # as one. On 2026-08-05 the free tier returned 429 on 12 of 13 calls
        # and every one was logged FAILED, which would have read as "Gemini
        # could not answer" when the truth is "we were not allowed to ask".
        # The prefix lets the runner classify it separately (doctrine 122:
        # a zero produced by a refusal is not a finding).
        prefix = "QUOTA: " if r.status_code == 429 else ""
        return False, "", [], redact(
            "%sgemini HTTP %s: %s" % (prefix, r.status_code, r.text[:300]))
    try:
        body = r.json()
        text = body["candidates"][0]["content"]["parts"][0]["text"]
    except (ValueError, KeyError, IndexError, TypeError):
        return False, "", [], redact("gemini response shape unexpected")
    # Gemini's plain generateContent returns no citation list. We do NOT
    # invent one. Sources stay empty and that is an honest empty, not a zero.
    return True, text, [], ""


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
    target = "https://www.google.com/search?q=" + urllib.parse.quote(query_text)
    html = ""
    for attempt in range(3):
        try:
            r = requests.post(
                BRIGHTDATA_URL,
                headers={"Authorization": "Bearer " + key,
                         "Content-Type": "application/json"},
                json={"zone": BRIGHTDATA_ZONE, "url": target, "format": "raw"},
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
            _time.sleep(3 * (attempt + 1))
    return True, html, extract_organic_urls(html, top_n), ""


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
    skip = ("google.", "gstatic.", "googleusercontent.", "googleadservices.",
            "youtube.com/redirect", "accounts.google", "policies.google",
            "support.google", "webcache.", "schema.org", "w3.org")
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
        "html_bytes": len(html),
        "href_total": len(re.findall(r'href=', text)),
        "redirect_hrefs": len(re.findall(r'href=["\'][^"\']*?/url\?', text)),
        "looks_like_consent_wall": ("before you continue" in low
                                    or "consent.google" in low),
        "looks_like_captcha": ("unusual traffic" in low
                               or "recaptcha" in low),
        "extraction_ok": bool(organic),
        "zero_reason": (None if organic else
                        ("empty response" if len(html) < 2000 else
                         "no parsable result links found in a non-empty page")),
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
            json={"model": OPENAI_MODEL, "temperature": TEMPERATURE,
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
