"""
Self-test for the served-model audit fields on OpenAI, Anthropic and Gemini
(engine_meta). Offline: requests.post is replaced with a fake response, so no
key and no network are needed. Run from the repo root:

    python3 scripts/test_served_model.py

Exits 0 only when every case passes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for k in ("OPENAI", "ANTHROPIC", "GEMINI"):
    os.environ[k + "_API_" + "KEY"] = "test-dummy-0123456789"

import engines  # noqa: E402


class FakeResponse:
    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        return self._body


def with_fake(fake, fn, *a):
    real = engines.requests.post
    engines.requests.post = lambda *args, **kwargs: fake
    try:
        return fn(*a)
    finally:
        engines.requests.post = real


fails = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)


ok, text, _, err = with_fake(FakeResponse(200, {"id": "chatcmpl-1", "model": "gpt-5.2-mini-2026-01-01",
                                               "usage": {"total_tokens": 7},
                                               "choices": [{"message": {"content": "A"}}]}), engines.call_openai, "q")
m = engines.LAST_CALL_META["openai"]
check("openai: served_model, response_id and usage come from the body",
      ok and m["served_model"] == "gpt-5.2-mini-2026-01-01" and m["response_id"] == "chatcmpl-1" and m["usage"] == {"total_tokens": 7})
ok, text, _, err = with_fake(FakeResponse(500, text="boom"), engines.call_openai, "q")
check("openai: a failed call leaves engine_meta empty (no stale model from the last call)",
      not ok and engines.LAST_CALL_META["openai"] == {})

ok, text, _, err = with_fake(FakeResponse(200, {"id": "msg_1", "model": "claude-haiku-4-5-20251001",
                                               "usage": {"input_tokens": 3}, "content": [{"text": "B"}]}), engines.call_anthropic, "q")
m = engines.LAST_CALL_META["anthropic"]
check("anthropic: served_model, response_id and usage come from the body",
      ok and m["served_model"] == "claude-haiku-4-5-20251001" and m["response_id"] == "msg_1" and m["usage"] == {"input_tokens": 3})
ok, text, _, err = with_fake(FakeResponse(200, {"content": []}), engines.call_anthropic, "q")
check("anthropic: an unexpected shape fails cleanly with empty meta", not ok and engines.LAST_CALL_META["anthropic"] == {})

ok, text, _, err = engines.call_gemini("q", _post=lambda *a, **k: FakeResponse(200, {
    "modelVersion": "gemini-3.6-flash-001", "responseId": "r1", "usageMetadata": {"totalTokenCount": 9},
    "candidates": [{"content": {"parts": [{"text": "C"}]}}]}), _sleep=lambda s: None)
m = engines.LAST_CALL_META["gemini"]
check("gemini: served_model is modelVersion, response_id is responseId, usage is usageMetadata",
      ok and m["served_model"] == "gemini-3.6-flash-001" and m["response_id"] == "r1" and m["usage"] == {"totalTokenCount": 9})
ok, text, _, err = engines.call_gemini("q", _post=lambda *a, **k: FakeResponse(200, {
    "candidates": [{"content": {"parts": [{"text": "C"}]}}]}), _sleep=lambda s: None)
check("gemini: a body without modelVersion gives served_model None, never a typed string",
      ok and engines.LAST_CALL_META["gemini"]["served_model"] is None)

check("helper never raises on a non-dict body", engines._served_meta(None, "model", "id", "usage") == {})

print("ALL PASS" if not fails else "FAILED: " + ", ".join(fails))
sys.exit(1 if fails else 0)
