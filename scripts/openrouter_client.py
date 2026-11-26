"""
OPENROUTER CLIENT PATH for the record's engine calls. OFF BY DEFAULT.

Plain-language note for a non-coder reading along:
OpenRouter is one door to many model makers, paid from one pool of credits
(OpenRouter for Startups, applied for 2026-10-25, credits pending). This file
is the plumbing for sending a question to OpenAI, Anthropic or Gemini THROUGH
that door instead of straight to the maker. It is built and tested but not
switched on. The daily record's method does not change until the COS sets the
flag (see engines.py, ENGINE_VIA_OPENROUTER) after a parity test shows the same
question gets the same kind of answer by both routes.

Same contract as every adapter in engines.py, with one addition: the function
returns the usual 4-tuple (ok, answer_text, sources, error) AND a meta dict.
engines.py stores the meta dict in LAST_CALL_META[engine], which e1_runner.run_engine
copies into the row as engine_meta. So a row served this way says so:
engine_meta.served_via == "openrouter", plus the model and provider OpenRouter
reports it routed to. Rows served direct carry no served_via field, as today.

Nothing here invents a result. A missing key, an unmapped model, an HTTP error, a
timeout, a 200 that carries an error body, or an empty answer all return ok=False
with a plain error string and the runner logs a FAILED row. No secret is printed,
logged or written; every error string passes through engines.redact().

SOURCES (read 2026-10-25 from OpenRouter's own pages; cite when changing anything):
  Request shape (POST /api/v1/chat/completions, Authorization: Bearer, optional
  HTTP-Referer, body model + messages):  https://openrouter.ai/docs/quickstart
  Headers, response fields (id, model, choices[].message.content, usage), error
  object:  https://openrouter.ai/docs/api/reference/overview/
  Title header: the docs name X-OpenRouter-Title and say "X-Title also accepted"
  (overview page above). Both are sent.
  Provider object, data_collection "allow" | "deny" (default "allow"):
  https://openrouter.ai/docs/guides/routing/provider-selection.md
  Error codes (402 insufficient credits, 429 rate limited, 503 no provider meets the
  routing requirements) and errors that arrive inside an HTTP 200 body:
  https://openrouter.ai/docs/api/reference/errors-and-debugging.md
  Slugs (model pages, each read 2026-10-25): see MODEL_SLUGS below.

NOT CONFIRMED FROM THE DOCS, handled defensively: whether a NON-streaming response
carries a top-level "provider" string. The docs only show it in mid-stream error
chunks. It is read with body.get() and recorded as None when absent.
"""

import os
import requests

from engines import TIMEOUT, redact

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
REFERER = "https://namebeam.ai"
TITLE = "Namebeam"

# Our model id (what the direct adapters send today) -> OpenRouter slug.
# A model that is not in this map is NEVER silently swapped for a near neighbour:
# the call fails with a plain error and the row logs FAILED.
MODEL_SLUGS = {
    # https://openrouter.ai/models/anthropic/claude-haiku-4-5 : slug anthropic/claude-haiku-4.5,
    # "Anthropic: Claude Haiku 4.5", released Oct 15, 2025
    "claude-haiku-4-5": "anthropic/claude-haiku-4.5",
    # https://openrouter.ai/models/google/gemini-3.6-flash : slug google/gemini-3.6-flash
    "gemini-3.6-flash": "google/gemini-3.6-flash",
    # https://openrouter.ai/google/gemini-3.5-flash-lite : slug google/gemini-3.5-flash-lite
    "gemini-3.5-flash-lite": "google/gemini-3.5-flash-lite",
    # OpenAI "gpt-5.2-mini" (engines.OPENAI_MODEL default): UNCONFIRMED, deliberately omitted.
    # openrouter.ai/openai/gpt-5-mini exists, but that is a different model, and no
    # gpt-5.2-mini page was found on openrouter.ai on 2026-10-25. Once a slug is
    # confirmed, add it here or set OPENROUTER_MODEL_MAP (JSON) without a commit.
}


def slug_for(model_id, env=None):
    """OpenRouter slug for one of our model ids, or None. The env var
    OPENROUTER_MODEL_MAP (a JSON object, our id -> slug) is checked first so a
    confirmed slug can be supplied without a commit; bad JSON is ignored."""
    env = os.environ if env is None else env
    raw = (env.get("OPENROUTER_MODEL_MAP") or "").strip()
    if raw:
        try:
            import json
            extra = json.loads(raw)
            if isinstance(extra, dict) and isinstance(extra.get(model_id), str) and extra[model_id].strip():
                return extra[model_id].strip()
        except ValueError:
            pass
    return MODEL_SLUGS.get(model_id)


def call_openrouter(engine, model_id, prompt_text, temperature=None, max_tokens=None,
                    _post=None, _env=None):
    """Send one OpenAI-compatible chat completion through OpenRouter.

    engine      our engine name ("openai", "anthropic", "gemini"); used in error text only.
    model_id    OUR model id (the one the direct adapter would send); mapped to a slug here.
    temperature / max_tokens  passed only when not None, so each engine keeps the exact
                sampling settings its direct adapter uses today.
    Returns (ok, text, sources, error, meta). Never raises on a network or body problem.
    sources is always [] (chat completions carry no citation list, same as the direct
    OpenAI, Anthropic and Gemini adapters). meta always has served_via="openrouter".
    """
    env = os.environ if _env is None else _env
    post = _post or requests.post
    meta = {"served_via": "openrouter", "requested_model": model_id}
    key = (env.get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        return False, "", [], redact("OPENROUTER_API_KEY not set"), meta
    slug = slug_for(model_id, env)
    if not slug:
        return False, "", [], redact(
            "%s via openrouter: no confirmed OpenRouter slug for model %r (refusing to substitute)"
            % (engine, model_id)), meta
    meta["openrouter_slug"] = slug
    payload = {
        "model": slug,
        "messages": [{"role": "user", "content": prompt_text}],
        # Refuse providers that store prompts non-transiently or may train on them.
        # If no provider qualifies, OpenRouter fails the request; we log FAILED, never retry looser.
        "provider": {"data_collection": "deny"},
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    headers = {"Authorization": "Bearer " + key,
               "Content-Type": "application/json",
               "HTTP-Referer": REFERER,
               "X-OpenRouter-Title": TITLE,
               "X-Title": TITLE}
    try:
        r = post(OPENROUTER_URL, headers=headers, json=payload, timeout=TIMEOUT)
    except requests.exceptions.RequestException as exc:
        return False, "", [], redact("%s via openrouter request failed: %s" % (engine, exc)), meta
    if r.status_code != 200:
        # 429 rate limited and 402 insufficient credits are us not being allowed to ask,
        # not the engine failing to answer: QUOTA, same as the direct adapters (doctrine 122).
        prefix = "QUOTA: " if r.status_code in (429, 402) else ""
        return False, "", [], redact("%s%s via openrouter HTTP %s: %s" % (
            prefix, engine, r.status_code, r.text[:300])), meta
    try:
        body = r.json()
    except ValueError:
        return False, "", [], redact("%s via openrouter response shape unexpected" % engine), meta
    if not isinstance(body, dict):
        return False, "", [], redact("%s via openrouter response shape unexpected" % engine), meta
    # What OpenRouter says it served, recorded even when the answer is unusable.
    meta.update({"served_model": body.get("model"), "response_id": body.get("id"),
                 "usage": body.get("usage"), "routed_provider": body.get("provider")})
    if body.get("error"):
        # The docs say an error can arrive inside an HTTP 200 body.
        return False, "", [], redact("%s via openrouter error in body: %s" % (
            engine, str(body.get("error"))[:300])), meta
    try:
        choice = body["choices"][0]
        if choice.get("finish_reason") == "error" or choice.get("error"):
            return False, "", [], redact("%s via openrouter choice error: %s" % (
                engine, str(choice.get("error"))[:300])), meta
        text = choice["message"]["content"]
    except (KeyError, IndexError, TypeError, AttributeError):
        return False, "", [], redact("%s via openrouter response shape unexpected" % engine), meta
    if not isinstance(text, str) or not text:
        return False, "", [], redact("%s via openrouter returned no answer text" % engine), meta
    return True, text, [], "", meta
