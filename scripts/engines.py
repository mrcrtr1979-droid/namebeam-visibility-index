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
        return False, "", [], redact("perplexity HTTP %s: %s" % (r.status_code, r.text[:300]))
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
GEMINI_MODEL = "gemini-3.6-flash"  # PENDING VERIFICATION on first live run
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
        return False, "", [], redact("gemini HTTP %s: %s" % (r.status_code, r.text[:300]))
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
BRIGHTDATA_ZONE = os.environ.get("BRIGHTDATA_ZONE", "serp_api1")


def fetch_serp(query_text, top_n=10):
    """Return (ok, html, organic_urls, error). Never raises."""
    key = os.environ.get("BRIGHTDATA_API_KEY", "").strip()
    if not key:
        return False, "", [], redact("BRIGHTDATA_API_KEY not set")
    import urllib.parse
    target = "https://www.google.com/search?q=" + urllib.parse.quote(query_text)
    try:
        r = requests.post(
            BRIGHTDATA_URL,
            headers={"Authorization": "Bearer " + key,
                     "Content-Type": "application/json"},
            json={"zone": BRIGHTDATA_ZONE, "url": target, "format": "raw"},
            timeout=TIMEOUT,
        )
    except requests.exceptions.RequestException as exc:
        return False, "", [], redact("brightdata request failed: %s" % exc)
    if r.status_code != 200:
        return False, "", [], redact("brightdata HTTP %s: %s" % (r.status_code, r.text[:300]))
    html = r.text or ""
    return True, html, extract_organic_urls(html, top_n), ""


def extract_organic_urls(html, top_n=10):
    """Pull result URLs out of a Google results page, in order, deduplicated.

    Mechanical and reproducible: it takes href targets that look like real
    outbound results and drops Google's own properties. A missed result is a
    false negative, which is the safe direction of error for a corpus whose
    whole value is that it never overstates.
    """
    import re
    import urllib.parse
    if not html:
        return []
    skip = ("google.", "gstatic.", "googleusercontent.", "youtube.com/redirect",
            "accounts.google", "policies.google", "support.google", "webcache.")
    out = []
    for m in re.finditer(r'href="(https?://[^"]+)"', html):
        u = m.group(1)
        if u.startswith("https://www.google.com/url?"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query).get("q")
            if not q:
                continue
            u = q[0]
        if any(s in u for s in skip):
            continue
        u = u.split("&")[0]
        if u not in out:
            out.append(u)
        if len(out) >= top_n:
            break
    return out


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


ENGINES = {
    "perplexity": call_perplexity,
    "gemini": call_gemini,
}
