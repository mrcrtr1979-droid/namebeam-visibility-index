"""
PostHog AI Observability for the E1 collector. Best-effort, never changes the record.

Plain-language notes for a non-coder reading along:

Why this exists. Gemini returned HTTP 402 on every call from 2026-10-19 to
2026-10-21 and nobody saw it for days, because a failed call is (correctly)
logged as a FAILED row and the run still exits 0. This module sends one
PostHog `$ai_generation` event per engine call, so the health of the record
(errors, status codes, latency, tokens, per engine) is a chart and an alert
instead of something found by reading the corpus.

What it does. e1_runner.run_engine calls record() once per engine row, right
after the row is written. record() only appends a small dict to an in-memory
list. flush() (called once at the end of the run) sends the whole list in ONE
POST to PostHog's /batch/ endpoint.

What it never does:
  * It never raises. Every public function swallows every Exception.
  * It never changes a corpus row, a file, a print the workflows depend on, or
    the exit code. The row is read, never written.
  * It never sends the prompt or the answer. $ai_input and $ai_output_choices
    are omitted (PostHog's documented privacy-mode behaviour is to exclude
    exactly those two properties; omitting them is the manual-capture
    equivalent). Only metadata goes out.
  * It never logs the key. Failures print the exception CLASS or HTTP status
    only. The key travels in the request body, never in the URL.
  * It makes no network call at all when POSTHOG_PROJECT_KEY is empty or
    DISABLE_POSTHOG is set. Retries: at most one retry, on a network error or
    a 5xx, 3-second timeout each attempt.

Property names follow PostHog's documented LLM analytics `$ai_generation`
event (posthog.com/docs/llm-analytics/generations, read 2026-10-23):
$ai_trace_id, $ai_span_name, $ai_provider, $ai_model, $ai_latency,
$ai_input_tokens, $ai_output_tokens, $ai_http_status, $ai_is_error, $ai_error.
The project key is PostHog's public write key (the same one the sites ship in
/posthog.js); it is read from the environment so it can be rotated without a
code change.

Environment:
  POSTHOG_PROJECT_KEY  public write key (phc_...). Empty = send nothing.
  DISABLE_POSTHOG      any value other than empty/0/false/no/off = send nothing.
  POSTHOG_HOST         default https://us.i.posthog.com (US cloud).
  GITHUB_RUN_ID        copied into every event as run_id.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_HOST = "https://us.i.posthog.com"
DISTINCT_ID = "namebeam-collector"
EVENT_NAME = "$ai_generation"
SPAN_NAME = "e1_check"
TIMEOUT_S = 3
MAX_ERROR_CHARS = 300

_FALSEY = ("", "0", "false", "no", "off")

# Events recorded in this process, flushed once at the end of the run.
_BUFFER = []


def _disabled():
    return os.environ.get("DISABLE_POSTHOG", "").strip().lower() not in _FALSEY


def _key():
    return os.environ.get("POSTHOG_PROJECT_KEY", "").strip()


def enabled():
    """True only when a key is present and the kill switch is not set."""
    try:
        return bool(_key()) and not _disabled()
    except Exception:
        return False


def _num(x):
    """An int token count, or None. Bools and negatives are not counts."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return int(x) if x >= 0 else None


def tokens_from_usage(engine, usage):
    """(input_tokens, output_tokens) from each provider's usage block, None when absent.

    openai      prompt_tokens / completion_tokens (completion includes reasoning)
    anthropic   input_tokens / output_tokens
    perplexity  input_tokens / output_tokens (Agent API)
    gemini      usageMetadata.promptTokenCount / candidatesTokenCount, plus
                thoughtsTokenCount (billed as output, reported separately by Google)
    """
    if not isinstance(usage, dict):
        return None, None
    if engine == "gemini":
        out = _num(usage.get("candidatesTokenCount"))
        thoughts = _num(usage.get("thoughtsTokenCount"))
        if out is not None and thoughts:
            out += thoughts
        elif out is None:
            out = thoughts
        return _num(usage.get("promptTokenCount")), out
    if engine == "openai":
        return _num(usage.get("prompt_tokens")), _num(usage.get("completion_tokens"))
    return _num(usage.get("input_tokens")), _num(usage.get("output_tokens"))


def http_status(ok, err):
    """200 for an OK call; for a failure, the status the adapter wrote into its error
    string ("... HTTP 402: ..."), 429 for a QUOTA refusal that carries none, else None
    (no key, connection error, unexpected shape, Bedrock exception: no HTTP status exists)."""
    if ok:
        return 200
    text = str(err or "")
    m = re.search(r"HTTP (\d{3})", text)
    if m:
        return int(m.group(1))
    if text.startswith("QUOTA: "):
        return 429
    return None


def _requested_model(engine, meta):
    m = (meta or {}).get("requested_model")
    if m:
        return m
    try:
        import engines as E
        if engine == "anthropic":
            return os.environ.get("BEDROCK_MODEL_ID", "").strip() or E.ANTHROPIC_MODEL
        if engine == "openai":
            return E.OPENAI_MODEL
        if engine == "perplexity":
            return E.PERPLEXITY_MODEL
        if engine == "gemini":
            return E.GEMINI_MODEL
    except Exception:
        pass
    return None


def _safe_error(err):
    text = str(err or "")
    try:
        from engines import redact
        text = redact(text)
    except Exception:
        pass
    return text[:MAX_ERROR_CHARS]


def build_event(row, latency_s, err="", run_id=None, now=None):
    """One $ai_generation event for one finalized corpus row. Reads the row, never changes it.

    The caller passes `err` (the adapter's error string) because a failed row only carries it
    inside session_note prose. The prompt and the answer are not read at all."""
    engine = row.get("engine") or ""
    ok = row.get("run_status") == "OK"
    meta = row.get("engine_meta") or {}
    requested = _requested_model(engine, meta)
    served = meta.get("served_model") or None
    tin, tout = tokens_from_usage(engine, meta.get("usage"))
    props = {
        "distinct_id": DISTINCT_ID,
        "$process_person_profile": False,
        "$lib": "namebeam-collector",
        "$ai_trace_id": row.get("check_id"),
        "$ai_span_name": SPAN_NAME,
        "$ai_provider": engine,
        "$ai_model": served or requested,
        "$ai_latency": round(float(latency_s), 3),
        "$ai_http_status": http_status(ok, err),
        "$ai_is_error": not ok,
        "engine": engine,
        "served_model": served,
        "requested_model": requested,
        "run_status": row.get("run_status"),
        "question_id": _slug(row.get("business")),
        "market": row.get("market"),
        "date_utc": row.get("date_utc"),
        "check_id": row.get("check_id"),
        # 2026-10-28: 1 for the daily record, 2 for the separate second-question series (e1_q2),
        # so the two never blur in one dashboard. Additive.
        "prompt_variant": row.get("prompt_variant", 1),
        "run_id": run_id if run_id is not None else (os.environ.get("GITHUB_RUN_ID", "").strip() or None),
    }
    if tin is not None:
        props["$ai_input_tokens"] = tin
    if tout is not None:
        props["$ai_output_tokens"] = tout
    if not ok:
        props["$ai_error"] = _safe_error(err)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return {"event": EVENT_NAME, "properties": props, "timestamp": stamp}


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def record(row, latency_s, err=""):
    """Queue one event. No-op when disabled. Never raises."""
    try:
        if not enabled():
            return
        _BUFFER.append(build_event(row, latency_s, err))
    except Exception:
        pass


def _post(url, body, urlopen):
    """One POST. Returns None on success, else a short key-free reason string."""
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "namebeam-collector/1"})
    try:
        with urlopen(req, timeout=TIMEOUT_S) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
        return None if 200 <= int(code) < 300 else "HTTP %s" % code
    except urllib.error.HTTPError as exc:
        return "HTTP %s" % exc.code
    except Exception as exc:  # URLError, timeout, ssl, reset: class name only, never the message
        return type(exc).__name__


def flush(urlopen=None):
    """Send everything queued in one /batch/ POST, then clear the queue. Never raises.

    Returns the number of events sent (0 when nothing was queued or the send failed)."""
    try:
        events = list(_BUFFER)
        del _BUFFER[:]
        if not events or not enabled():
            return 0
        opener = urlopen or urllib.request.urlopen
        host = os.environ.get("POSTHOG_HOST", "").strip().rstrip("/") or DEFAULT_HOST
        body = json.dumps({"api_key": _key(), "batch": events}, ensure_ascii=False).encode("utf-8")
        reason = _post(host + "/batch/", body, opener)
        # One retry, only for a network failure or a server error. A 4xx (bad key, bad body)
        # would fail the same way again.
        if reason is not None and not re.match(r"HTTP 4\d\d$", reason):
            reason = _post(host + "/batch/", body, opener)
        if reason is not None:
            print("posthog llm-obs: send failed (%s), %d events dropped; the record is unaffected"
                  % (reason, len(events)), file=sys.stderr)
            return 0
        print("posthog llm-obs: sent %d events" % len(events))
        return len(events)
    except Exception:
        return 0
