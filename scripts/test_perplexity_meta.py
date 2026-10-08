"""
Self-test for the Perplexity audit fields (engine_meta). Offline: requests.post
is replaced with a fake response, so no key and no network are needed.

Run from the repo root:

    python3 scripts/test_perplexity_meta.py

Exits 0 only when every case passes.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["PERPLEXITY_API_KEY"] = "test-dummy-key-0123456789"

import engines  # noqa: E402

DUMMY_KEY = os.environ["PERPLEXITY_API_KEY"]
URLS = ["https://a.example/1", "https://b.example/2", "https://c.example/3"]


class FakeResponse:
    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        return self._body


def call_with_fake(fake):
    real_post = engines.requests.post
    engines.requests.post = lambda *args, **kwargs: fake
    try:
        return engines.call_perplexity("test prompt")
    finally:
        engines.requests.post = real_post


def case_full_body():
    body = {
        "id": "resp_test_123",
        "status": "completed",
        "model": "perplexity/sonar-x",
        "usage": {"input_tokens": 1},
        "output": [
            {"type": "search_results",
             "results": [{"url": u} for u in URLS]},
            {"type": "message",
             "content": [{"type": "output_text", "text": "hello"}]},
        ],
    }
    result = call_with_fake(FakeResponse(200, body))
    assert result == (True, "hello", URLS, ""), result
    meta = engines.LAST_CALL_META["perplexity"]
    assert meta["served_model"] == "perplexity/sonar-x", meta
    assert meta["search_results_count"] == 3, meta
    assert meta["usage"] == {"input_tokens": 1}, meta
    assert meta["response_id"] == "resp_test_123", meta
    assert meta["status"] == "completed", meta
    assert meta["output_item_types"] == ["search_results", "message"], meta
    assert meta["preset"] is None, meta
    blob = json.dumps(meta)
    assert DUMMY_KEY not in blob, "API key leaked into engine_meta"
    assert "hello" not in blob, "answer text leaked into engine_meta"
    print("PASS full body: 4-tuple unchanged, meta filled, no key or answer text")


def case_missing_keys():
    body = {
        "status": "completed",
        "output": [
            {"type": "message",
             "content": [{"type": "output_text", "text": "hi"}]},
        ],
    }
    result = call_with_fake(FakeResponse(200, body))
    assert result == (True, "hi", [], ""), result
    meta = engines.LAST_CALL_META["perplexity"]
    for key in ("served_model", "response_id", "usage", "preset"):
        assert meta[key] is None, (key, meta)
    # Output list is present but holds no search_results item: zero results.
    assert meta["search_results_count"] == 0, meta
    print("PASS missing model/usage: still ok, missing meta values are None")


def case_no_output_list():
    body = {"status": "completed"}
    result = call_with_fake(FakeResponse(200, body))
    assert result[0] is False, result
    meta = engines.LAST_CALL_META["perplexity"]
    assert meta["search_results_count"] is None, meta
    assert meta["output_item_types"] == [], meta
    print("PASS no output list: fails cleanly, search count is None")


def case_http_500():
    result = call_with_fake(FakeResponse(500, text="boom"))
    assert result[0] is False, result
    assert result[1] == "" and result[2] == [], result
    assert "HTTP 500" in result[3], result
    assert engines.LAST_CALL_META["perplexity"] == {}, engines.LAST_CALL_META
    print("PASS HTTP 500: ok False, no exception, meta empty")


if __name__ == "__main__":
    case_full_body()
    case_missing_keys()
    case_no_output_list()
    case_http_500()
    print("ALL PASS")
