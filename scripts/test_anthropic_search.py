"""
Self-test for the anthropic_search engine adapter. Offline: the HTTP post is a
fake, so no key and no network are used. Proves text + citations parse into the
existing row format, usage and tool version land in engine_meta, a failure logs
cleanly, the key never leaks, and the dry run makes zero calls.

Run from the repo root:    python3 scripts/test_anthropic_search.py
Exits 0 only when every case passes.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["ANTHROPIC_API_KEY"] = "test-dummy-key-0123456789"

import engines  # noqa: E402
import e1_runner  # noqa: E402
import e1_claude_search  # noqa: E402

KEY = os.environ["ANTHROPIC_API_KEY"]
FAILS = []


def check(name, cond):
    print("%s  %s" % ("PASS" if cond else "FAIL", name))
    if not cond:
        FAILS.append(name)


class Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code, self._b, self.text = status, body, text

    def json(self):
        return self._b


BODY = {
    "id": "msg_test1", "model": "claude-haiku-5-5", "stop_reason": "end_turn",
    "content": [
        {"type": "text", "text": "Let me check."},
        {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
         "input": {"query": "best roofer Tulsa"}},
        {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": [
            {"type": "web_search_result", "url": "https://a.example/roof", "title": "A",
             "encrypted_content": "x", "page_age": "May 1, 2026"},
            {"type": "web_search_result", "url": "https://b.example/roof", "title": "B",
             "encrypted_content": "y", "page_age": None}]},
        {"type": "text", "text": "**Acme Roofing** is well reviewed.", "citations": [
            {"type": "web_search_result_location", "url": "https://a.example/roof",
             "title": "A", "encrypted_index": "i", "cited_text": "Acme Roofing ..."}]},
        {"type": "text", "text": " Beta Roofing is also named.", "citations": [
            {"type": "web_search_result_location", "url": "https://b.example/roof",
             "title": "B", "encrypted_index": "j", "cited_text": "Beta ..."},
            {"type": "web_search_result_location", "url": "https://a.example/roof",
             "title": "A", "encrypted_index": "k", "cited_text": "dup"}]},
    ],
    "usage": {"input_tokens": 105, "output_tokens": 320,
              "server_tool_use": {"web_search_requests": 1}},
}


def call(fake_resp, captured=None):
    def post(url, headers=None, json=None, timeout=None):
        if captured is not None:
            captured.append((url, headers, json))
        return fake_resp
    return engines.call_anthropic_search("test prompt", _post=post)


cap = []
ok, text, srcs, err = call(Resp(200, BODY), cap)
check("ok and no error", ok and err == "")
check("text blocks joined verbatim",
      text == "Let me check.**Acme Roofing** is well reviewed. Beta Roofing is also named.")
check("citations deduped in order", srcs == ["https://a.example/roof", "https://b.example/roof"])
check("request carries tool web_search_20250305 max_uses 3",
      cap[0][2]["tools"] == [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}])
check("request model is claude-haiku-5-5 and sends no temperature",
      cap[0][2]["model"] == "claude-haiku-5-5" and "temperature" not in cap[0][2])
check("key only in x-api-key header", cap[0][1]["x-api-key"] == KEY and KEY not in json.dumps(cap[0][2]))
m = engines.LAST_CALL_META["anthropic_search"]
check("engine_meta model, tool version, web_search_requests, usage",
      m["model"] == "claude-haiku-5-5" and m["tool_version"] == "web_search_20250305"
      and m["web_search_requests"] == 1 and m["usage"]["output_tokens"] == 320
      and m["search_results_count"] == 2 and m["stop_reason"] == "end_turn")

# Row format through the real runner path.
br = {"business": "Acme Roofing", "variants_business_name_forms": ["Acme Roofing"],
      "niche": "roofing", "market": "Tulsa", "prompt_1": "Best roofer in Tulsa?"}
with tempfile.TemporaryDirectory() as d:
    e1_runner.OUTPUT_DIR = d
    path, good = e1_runner.run_engine(br, "anthropic_search", lambda p: call(Resp(200, BODY)), "2026-10-12")
    row = json.load(open(path))
check("row written OK with engine anthropic_search", good and row["engine"] == "anthropic_search"
      and row["run_status"] == "OK" and path.endswith("NB-CZ-API_2026-10-12_acme_roofing_anthropic_search.json"))
check("row has the existing fields",
      all(k in row for k in ("check_id", "date_utc", "business", "prompt_text", "run_channel",
                             "schema_version", "business_mentioned", "competitors_mentioned",
                             "engine_stated_criteria", "sources_cited", "factor_scores",
                             "answer_verbatim", "session_note")))
check("row sources_cited from citations, answer kept, business mentioned",
      row["sources_cited"] == ["https://a.example/roof", "https://b.example/roof"]
      and row["business_mentioned"] is True and "Acme Roofing" in row["answer_verbatim"])
check("row engine_meta records usage and tool version",
      row["engine_meta"]["tool_version"] == "web_search_20250305"
      and row["engine_meta"]["usage"]["web_search_requests"] == 1)

# pause_turn continuation accumulates usage.
seq = [dict(BODY, stop_reason="pause_turn", content=BODY["content"][:3]),
       dict(BODY, stop_reason="end_turn", content=BODY["content"][3:])]
calls = []
def post2(url, headers=None, json=None, timeout=None):
    calls.append(json)
    return Resp(200, seq[len(calls) - 1])
ok2, t2, s2, _ = engines.call_anthropic_search("p", _post=post2)
check("pause_turn resumed once, usage summed, text complete",
      ok2 and len(calls) == 2 and engines.LAST_CALL_META["anthropic_search"]["web_search_requests"] == 2
      and "Acme Roofing" in t2 and len(s2) == 2)

# Failures log cleanly and never leak the key.
bad, _, _, e = call(Resp(429, text="rate limited " + KEY))
check("429 -> QUOTA and key redacted", (not bad) and e.startswith("QUOTA: ") and KEY not in e)
bad, _, _, e = call(Resp(200, {"content": "nope"}))
check("odd shape -> clean failure", (not bad) and "shape unexpected" in e)
bad, _, _, e = call(Resp(200, dict(BODY, content=[{"type": "text", "text": "  "}])))
check("empty text -> failure, not a fabricated row", (not bad) and "no answer text" in e)
os.environ["ANTHROPIC_API_KEY"] = ""
bad, _, _, e = engines.call_anthropic_search("p", _post=lambda *a, **k: 1 / 0)
check("missing key -> failure before any call", (not bad) and "not set" in e)
os.environ["ANTHROPIC_API_KEY"] = KEY

# Dry run: zero API calls, nothing written, cap honored.
boom = []
real = engines.requests.post
engines.requests.post = lambda *a, **k: boom.append(1)
with tempfile.TemporaryDirectory() as d:
    e1_runner.OUTPUT_DIR = d
    rc = e1_claude_search.main(["--max-questions", "3"], roster_path="roster/e1_roster.json")
    written = os.listdir(d)
engines.requests.post = real
check("dry run: zero API calls, nothing written", rc == 0 and not boom and not written)
check("cap: plan honors max_questions", len(e1_claude_search.plan([{"business": str(i)} for i in range(73)], "2026-10-12", 5)) == 5)
s_, t_ = e1_claude_search.cost_per_question()
check("worst-case cost per full 73-question run is about 2.37 USD", abs((s_ + t_) * 73 - 2.3694) < 0.01)

print("\n%s" % ("ALL PASS" if not FAILS else "FAILED: %s" % FAILS))
sys.exit(0 if not FAILS else 1)
