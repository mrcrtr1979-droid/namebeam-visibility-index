"""Offline self-test for the Gemini rail chain: free AI Studio key, then Vertex AI, then the paid key.
No network. Fakes stand in for requests.post and the Vertex token minting.
Run from the repo root: python3 scripts/test_gemini_rails.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("GEMINI_API_KEY_FREE", None)

import engines  # noqa: E402

MODEL = "gemini-3.5-flash-lite"
FREE = "FREE-dummy-key-0001"
PAID = "PAID-dummy-key-0002"
SA = '{"type":"service_account","dummy":"SA-dummy-0003"}'
TOKEN = "BEARER-dummy-0004"

fails = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


class Resp:
    def __init__(self, status, text="", body=None):
        self.status_code = status
        self.text = text
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


OK_BODY = {"candidates": [{"content": {"parts": [{"text": "answer text"}]}}],
           "modelVersion": "v-test", "responseId": "r-1", "usageMetadata": {}}


def scripted(responses):
    """post() fake: returns the next scripted outcome per URL. An Exception instance is raised."""
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers})
        key = "vertex" if "aiplatform" in url else ("free" if FREE in url else ("paid" if PAID in url else "?"))
        out = responses[key].pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    return post, calls


def run(env, responses, bearer=lambda sa: TOKEN if SA else None):
    rails = engines._gemini_rails(MODEL, env=env, vertex_bearer=bearer)
    post, calls = scripted(responses)
    res = engines.call_gemini("q", _post=post, _sleep=lambda s: None, _now=lambda: 1000.0, _rails=rails)
    return res, calls, rails


FULL = {"GEMINI_API_KEY_FREE": FREE, "GCP_SA_KEY": SA, "GCP_PROJECT": "proj-1",
        "GCP_LOCATION": "us-central1", "GEMINI_API_KEY": PAID}
QUOTA_DAY = Resp(429, '{"error":{"message":"per day quota"}}')

# 1. rail order with everything configured
rails = engines._gemini_rails(MODEL, env=FULL, vertex_bearer=lambda sa: TOKEN)
check("order: studio_free, vertex, studio_paid", [r[0] for r in rails] == ["studio_free", "vertex", "studio_paid"])
check("vertex URL has project and location, no key param",
      "projects/proj-1/locations/us-central1" in rails[1][1] and "?key=" not in rails[1][1])
check("vertex carries a bearer header", rails[1][2].get("Authorization") == "Bearer " + TOKEN)

# 2. vertex skipped without a project, without a service account, or when the token cannot be minted
check("vertex skipped without project",
      [r[0] for r in engines._gemini_rails(MODEL, env={**FULL, "GCP_PROJECT": ""}, vertex_bearer=lambda s: TOKEN)]
      == ["studio_free", "studio_paid"])
check("vertex skipped without service account",
      [r[0] for r in engines._gemini_rails(MODEL, env={**FULL, "GCP_SA_KEY": ""}, vertex_bearer=lambda s: TOKEN)]
      == ["studio_free", "studio_paid"])
check("vertex skipped when bearer minting returns None",
      [r[0] for r in engines._gemini_rails(MODEL, env=FULL, vertex_bearer=lambda s: None)]
      == ["studio_free", "studio_paid"])

# 3. no keys at all
res, _, _ = run({}, {"free": [], "paid": [], "vertex": []})
check("no rails: clean not-set error", res[0] is False and "not set" in res[3])

# 4. free key answers: no fallthrough
res, calls, _ = run(FULL, {"free": [Resp(200, body=OK_BODY)], "paid": [], "vertex": []})
check("free answers: ok", res[0] is True and res[1] == "answer text")
check("free answers: only one call", len(calls) == 1)
check("free answers: rail recorded", engines.LAST_CALL_META["gemini"].get("rail") == "studio_free")

# 5. free per-day quota (429) falls to vertex, which answers
res, calls, _ = run(FULL, {"free": [QUOTA_DAY], "vertex": [Resp(200, body=OK_BODY)], "paid": []})
check("429 on free falls through to vertex", res[0] is True and len(calls) == 2)
check("fallthrough rail recorded as vertex", engines.LAST_CALL_META["gemini"].get("rail") == "vertex")

# 6. billing refusal (403) on free and vertex falls to the paid key
res, calls, _ = run(FULL, {"free": [Resp(403, "denied")], "vertex": [Resp(402, "billing")],
                          "paid": [Resp(200, body=OK_BODY)]})
check("403 then 402 falls to paid", res[0] is True and len(calls) == 3)
check("paid rail recorded", engines.LAST_CALL_META["gemini"].get("rail") == "studio_paid")

# 7. any other status stops the chain (no fallthrough on 400)
res, calls, _ = run(FULL, {"free": [Resp(400, "bad request")], "vertex": [], "paid": []})
check("400 does not fall through", res[0] is False and len(calls) == 1)

# 8. all rails refuse: error comes back, last rail recorded
res, calls, _ = run(FULL, {"free": [QUOTA_DAY], "vertex": [Resp(403, "no")], "paid": [QUOTA_DAY]})
check("all refused: failure", res[0] is False and len(calls) == 3)
check("all refused: last rail recorded", engines.LAST_CALL_META["gemini"].get("rail") == "studio_paid")

# 9. network exception on the first rail does not fall through
res, calls, _ = run(FULL, {"free": [engines.requests.exceptions.ConnectionError("down")], "vertex": [], "paid": []})
check("network error does not fall through", res[0] is False and len(calls) == 1)

# 10. no secret value or key-bearing URL appears in any returned error or the recorded rail
res, calls, _ = run(FULL, {"free": [QUOTA_DAY], "vertex": [Resp(403, "no")], "paid": [Resp(400, "boom")]})
blob = repr(res) + repr(engines.LAST_CALL_META.get("gemini", {}))
check("no key value in result or meta", not any(s in blob for s in (FREE, PAID, TOKEN, SA)))

# 11. paid-only setup still works exactly as before
res, calls, _ = run({"GEMINI_API_KEY": PAID}, {"free": [], "vertex": [], "paid": [Resp(200, body=OK_BODY)]})
check("paid-only: one rail, ok", res[0] is True and len(calls) == 1 and PAID in calls[0]["url"])

print("ALL PASS" if not fails else "FAILED: %s" % fails)
sys.exit(1 if fails else 0)
