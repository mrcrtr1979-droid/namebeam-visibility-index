"""
Self-test for the OpenRouter opt-in path (scripts/openrouter_client.py + the
ENGINE_VIA_OPENROUTER branch in engines.py). Fully offline: requests.post is
replaced with a stub, so no key and no network are needed. Run from the repo root:

    python3 scripts/test_openrouter_client.py

Exits 0 only when every case passes.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for k in ("OPENAI", "ANTHROPIC", "GEMINI", "PERPLEXITY"):
    os.environ[k + "_API_" + "KEY"] = "test-dummy-0123456789"
os.environ.pop("GEMINI_API_KEY_FREE", None)
os.environ.pop("GCP_SA_KEY", None)
os.environ.pop("BEDROCK_MODEL_ID", None)
os.environ.pop("OPENAI_MODEL", None)
os.environ.pop("ANTHROPIC_MODEL", None)
os.environ.pop("OPENROUTER_MODEL_MAP", None)

import engines  # noqa: E402
import openrouter_client  # noqa: E402

OR_KEY = "or-test-dummy-9876543210"
OR_URL = "https://openrouter.ai/api/v1/chat/completions"


class FakeResponse:
    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class Recorder:
    """Stub for requests.post. Records every call; answers with `reply`, or raises `exc`."""

    def __init__(self, reply=None, exc=None):
        self.reply, self.exc, self.calls = reply, exc, []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.exc:
            raise self.exc
        return self.reply


OK_BODY = {"id": "gen-1", "model": "anthropic/claude-4.5-haiku-20251001", "provider": "Google",
           "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
           "choices": [{"finish_reason": "stop", "message": {"content": "ANSWER"}}]}

fails = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)


def run(rec, fn, *a, flag="", key=OR_KEY, **kw):
    """Call fn with requests.post stubbed and the env flags set; restore everything after."""
    saved = {k: os.environ.get(k) for k in ("ENGINE_VIA_OPENROUTER", "OPENROUTER_API_KEY")}
    real = engines.requests.post
    engines.requests.post = rec
    for k, v in (("ENGINE_VIA_OPENROUTER", flag), ("OPENROUTER_API_KEY", key)):
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    try:
        return fn(*a, **kw)
    finally:
        engines.requests.post = real
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---- (a) flag empty (or key absent, or engine not named): original path, new client never called
sentinel = []
real_client = openrouter_client.call_openrouter
openrouter_client.call_openrouter = lambda *a, **k: sentinel.append(a) or (True, "X", [], "", {})
try:
    direct = {"id": "chatcmpl-1", "model": "gpt-5.2-mini-2026-01-01", "usage": {"total_tokens": 7},
              "choices": [{"message": {"content": "DIRECT"}}]}
    rec = Recorder(FakeResponse(200, direct))
    res = run(rec, engines.call_openai, "q", flag="")
    check("a1 flag empty: openai goes to api.openai.com, answer is the direct one",
          res == (True, "DIRECT", [], "") and [c[0] for c in rec.calls] == [engines.OPENAI_URL])
    check("a1 flag empty: new client never called, no served_via in engine_meta",
          not sentinel and "served_via" not in engines.LAST_CALL_META["openai"])
    rec = Recorder(FakeResponse(200, direct))
    res = run(rec, engines.call_openai, "q", flag="openai", key="")
    check("a2 flag set but OPENROUTER_API_KEY absent: original path, client not called",
          res[0] and rec.calls[0][0] == engines.OPENAI_URL and not sentinel)
    rec = Recorder(FakeResponse(200, {"id": "msg_1", "model": "claude-haiku-4-5-20251001",
                                      "usage": {}, "content": [{"text": "B"}]}))
    res = run(rec, engines.call_anthropic, "q", flag="openai")
    check("a3 flag names another engine: anthropic stays direct, client not called",
          res[0] and rec.calls[0][0] == engines.ANTHROPIC_URL and not sentinel)
    rec = Recorder(FakeResponse(200, direct))
    res = run(rec, engines.call_openai, "q", flag=" , ,")
    check("a4 flag of only commas and spaces counts as empty", res[0] and not sentinel)
    # Perplexity never reads the flag
    rec = Recorder(FakeResponse(500, text="x"))
    run(rec, engines.call_perplexity, "q", flag="perplexity,openai,anthropic,gemini")
    check("a5 perplexity ignores the flag", rec.calls[0][0] == engines.PERPLEXITY_URL and not sentinel)
finally:
    openrouter_client.call_openrouter = real_client

# ---- (b) flag set: request JSON and headers, parsed answer, row shape
rec = Recorder(FakeResponse(200, OK_BODY))
res = run(rec, engines.call_anthropic, "the question", flag="openai, Anthropic")
url, kw = rec.calls[0]
check("b1 anthropic: posts to the OpenRouter chat completions URL", url == OR_URL and len(rec.calls) == 1)
check("b2 request JSON has the mapped slug", kw["json"]["model"] == "anthropic/claude-haiku-4.5")
check("b3 request JSON refuses data retention: provider.data_collection == deny",
      kw["json"]["provider"] == {"data_collection": "deny"})
check("b4 Authorization is Bearer <OPENROUTER_API_KEY>", kw["headers"]["Authorization"] == "Bearer " + OR_KEY)
check("b5 optional headers HTTP-Referer and X-Title are set",
      kw["headers"]["HTTP-Referer"] == "https://namebeam.ai" and kw["headers"]["X-Title"] == "Namebeam")
check("b6 prompt lands as the single user message; anthropic keeps temperature 0.2 and 2048 max tokens",
      kw["json"]["messages"] == [{"role": "user", "content": "the question"}]
      and kw["json"]["temperature"] == 0.2 and kw["json"]["max_tokens"] == 2048)
check("b7 existing 4-tuple shape: (True, text, [], '')", res == (True, "ANSWER", [], ""))
m = engines.LAST_CALL_META["anthropic"]
check("b8 engine_meta carries served_via=openrouter, served model, id, usage, routed provider",
      m["served_via"] == "openrouter" and m["served_model"] == OK_BODY["model"]
      and m["response_id"] == "gen-1" and m["usage"]["total_tokens"] == 7
      and m["routed_provider"] == "Google" and m["requested_model"] == "claude-haiku-4-5"
      and m["openrouter_slug"] == "anthropic/claude-haiku-4.5")
check("b9 request body is JSON-serialisable and carries no key", OR_KEY not in json.dumps(kw["json"]))

rec = Recorder(FakeResponse(200, OK_BODY))
run(rec, engines.call_gemini, "q", flag="gemini", question_id="not-in-panel", _panel=frozenset())
check("b10 gemini: flash-lite question maps to google/gemini-3.5-flash-lite, no temperature sent",
      rec.calls[0][1]["json"]["model"] == "google/gemini-3.5-flash-lite"
      and "temperature" not in rec.calls[0][1]["json"])
rec = Recorder(FakeResponse(200, OK_BODY))
run(rec, engines.call_gemini, "q", flag="gemini", question_id="p1", _panel=frozenset({"p1"}))
check("b11 gemini: continuity-panel question maps to google/gemini-3.6-flash",
      rec.calls[0][1]["json"]["model"] == "google/gemini-3.6-flash")
check("b12 gemini engine_meta keeps requested_model and adds served_via",
      engines.LAST_CALL_META["gemini"]["requested_model"] == "gemini-3.6-flash"
      and engines.LAST_CALL_META["gemini"]["served_via"] == "openrouter")

# OpenAI default model has no confirmed slug: refuse, never substitute, never post
rec = Recorder(FakeResponse(200, OK_BODY))
res = run(rec, engines.call_openai, "q", flag="openai")
check("b13 openai gpt-5.2-mini has no confirmed slug: FAILED row text, nothing posted",
      res[0] is False and "no confirmed OpenRouter slug" in res[3] and not rec.calls)
os.environ["OPENROUTER_MODEL_MAP"] = json.dumps({"gpt-5.2-mini": "openai/test-slug"})
try:
    rec = Recorder(FakeResponse(200, OK_BODY))
    res = run(rec, engines.call_openai, "q", flag="openai")
    check("b14 OPENROUTER_MODEL_MAP env supplies a slug without a commit; no temperature sent",
          res[0] and rec.calls[0][1]["json"]["model"] == "openai/test-slug"
          and "temperature" not in rec.calls[0][1]["json"])
finally:
    os.environ.pop("OPENROUTER_MODEL_MAP", None)

# ---- (c) failures: FAILED-row behaviour, never raises
import requests as _rq  # noqa: E402

for label, rec, want in (
    ("c1 timeout", Recorder(exc=_rq.exceptions.Timeout("read timed out")), "request failed"),
    ("c2 connection error", Recorder(exc=_rq.exceptions.ConnectionError("down")), "request failed"),
    ("c3 HTTP 500", Recorder(FakeResponse(500, text="boom")), "HTTP 500"),
    ("c4 HTTP 503 (no provider meets deny policy)", Recorder(FakeResponse(503, text="no provider")), "HTTP 503"),
    ("c5 non-JSON 200", Recorder(FakeResponse(200, None, text="<html>")), "response shape unexpected"),
    ("c6 200 with error object", Recorder(FakeResponse(200, {"error": {"code": 502, "message": "x"}})), "error in body"),
    ("c7 200 with finish_reason error", Recorder(FakeResponse(200, {"choices": [{"finish_reason": "error", "error": {"m": 1}}]})), "choice error"),
    ("c8 200 with empty content", Recorder(FakeResponse(200, {"choices": [{"message": {"content": ""}}]})), "no answer text"),
    ("c9 200 with no choices", Recorder(FakeResponse(200, {"id": "g"})), "response shape unexpected"),
):
    try:
        res = run(rec, engines.call_anthropic, "q", flag="anthropic")
        raised = False
    except Exception as exc:  # noqa: BLE001
        res, raised = None, exc
    check("%s: returns (False, '', [], error) without raising" % label,
          not raised and res[0] is False and res[1] == "" and res[2] == [] and want in res[3]
          and not res[3].startswith("QUOTA: "))

for label, code in (("c10 HTTP 429", 429), ("c11 HTTP 402 insufficient credits", 402)):
    res = run(Recorder(FakeResponse(code, text="slow down")), engines.call_anthropic, "q", flag="anthropic")
    check("%s: QUOTA-prefixed (runner logs QUOTA_BLOCKED, not FAILED)" % label,
          res[0] is False and res[3].startswith("QUOTA: "))

res = run(Recorder(FakeResponse(401, text="bad key " + OR_KEY)), engines.call_anthropic, "q", flag="anthropic")
check("c12 the key never appears in an error string (redacted)", OR_KEY not in res[3] and "REDACTED" in res[3])
check("c13 a failed OpenRouter call still records served_via in engine_meta",
      engines.LAST_CALL_META["anthropic"].get("served_via") == "openrouter")

# the client itself, with no env at all
res = openrouter_client.call_openrouter("anthropic", "claude-haiku-4-5", "q", _post=Recorder(FakeResponse(200, OK_BODY)), _env={})
check("c14 client with no OPENROUTER_API_KEY fails plainly and posts nothing", res[0] is False and "not set" in res[3])

# e1_runner (which consumes the 4-tuple and LAST_CALL_META) still imports cleanly
import e1_runner  # noqa: E402
check("c15 e1_runner still imports and exposes run_engine", callable(e1_runner.run_engine))

print("\n%d failed" % len(fails) if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
