"""Offline self-test for the optional Vertex AI backend of the Gemini adapter (GEMINI_BACKEND=vertex).
No network, no credentials: requests.post is a fake and google-auth is replaced by a fake module.
Checks that the backend switch picks the right path, that the default path is unchanged, that the row
records the backend, and that no token or key reaches a returned error or the recorded meta.
Run from the repo root: python3 scripts/test_gemini_vertex.py
(also: python3 -m pytest scripts/test_gemini_vertex.py -q)"""
import contextlib
import io
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for _k in ("GEMINI_API_KEY", "GEMINI_API_KEY_FREE", "GEMINI_BACKEND", "GCP_PROJECT", "GCP_LOCATION", "GCP_SA_KEY"):
    os.environ.pop(_k, None)

import engines  # noqa: E402
import e1_runner  # noqa: E402

MODEL = "gemini-3.5-flash-lite"
FREE = "FREE-dummy-key-0001"
PAID = "PAID-dummy-key-0002"
TOKEN = "ADC-BEARER-dummy-0004"

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


def fake_post(outcomes):
    """post() fake keyed by backend: 'vertex' for an aiplatform URL, else 'studio'. Records every call."""
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json})
        key = "vertex" if "aiplatform.googleapis.com" in url else "studio"
        out = outcomes[key].pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    return post, calls


def reset():
    engines._gemini_last_call.clear()
    engines._gemini_slept[0] = 0.0
    engines._vertex_warned.clear()
    engines._VERTEX_ADC.update({"creds": None, "error": None})


def never_called():
    raise AssertionError("ADC bearer must not be touched unless GEMINI_BACKEND=vertex")


def run_call(env, outcomes, adc=lambda: TOKEN):
    """call_gemini through the real env-driven rail builder, with env patched in os.environ."""
    reset()
    saved = {k: os.environ.get(k) for k in env}
    saved_adc = engines._vertex_adc_bearer
    try:
        os.environ.update(env)
        engines._vertex_adc_bearer = adc
        post, calls = fake_post(outcomes)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            res = engines.call_gemini("q", _post=post, _sleep=lambda s: None, _now=lambda: 1000.0,
                                      question_id="not_in_panel")
        return res, calls, dict(engines.LAST_CALL_META["gemini"]), out.getvalue()
    finally:
        engines._vertex_adc_bearer = saved_adc
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


KEYS = {"GEMINI_API_KEY_FREE": FREE, "GEMINI_API_KEY": PAID}
VX = {"GEMINI_BACKEND": "vertex", "GCP_PROJECT": "proj-1", "GCP_LOCATION": "us-central1"}

# 1. default: unset, empty or whitespace backend leaves the chain exactly as it was; ADC never touched
for label, env in (("unset", dict(KEYS)), ("empty", {**KEYS, "GEMINI_BACKEND": ""}),
                   ("blank", {**KEYS, "GEMINI_BACKEND": "   "})):
    reset()
    rails = engines._gemini_rails(MODEL, env={**env, "GCP_PROJECT": "proj-1"}, adc_bearer=never_called)
    check("default (%s): rails are studio_free, studio_paid" % label, [r[0] for r in rails] == ["studio_free", "studio_paid"])

# 2. default call: AI Studio request body has no role, meta has no method key (rows unchanged)
res, calls, meta, out = run_call(KEYS, {"studio": [Resp(200, body=OK_BODY)], "vertex": []}, adc=never_called)
check("default call answers from studio_free", res[0] is True and meta.get("rail") == "studio_free" and len(calls) == 1)
check("default body is {contents:[{parts:[...]}]} with no role",
      calls[0]["json"] == {"contents": [{"parts": [{"text": "q"}]}]})
check("default meta keys unchanged (no method, no vertex_refused_http)",
      set(meta) == {"served_model", "response_id", "usage", "requested_model", "rail"})
check("default prints no warning", out == "")

# 3. vertex mode: Vertex rail first, then both AI Studio rails
reset()
rails = engines._gemini_rails(MODEL, env={**KEYS, **VX}, adc_bearer=lambda: TOKEN)
check("vertex mode: order vertex, studio_free, studio_paid", [r[0] for r in rails] == ["vertex", "studio_free", "studio_paid"])
check("vertex URL: regional host, project, location, same model id, no key param",
      rails[0][1] == "https://us-central1-aiplatform.googleapis.com/v1/projects/proj-1/locations/us-central1"
                     "/publishers/google/models/%s:generateContent" % MODEL)
check("vertex carries a bearer header", rails[0][2].get("Authorization") == "Bearer " + TOKEN)
check("backend value is case-insensitive", [r[0] for r in engines._gemini_rails(
    MODEL, env={**KEYS, **VX, "GEMINI_BACKEND": " Vertex "}, adc_bearer=lambda: TOKEN)][0] == "vertex")

# 4. global location uses the host without a prefix; location defaults to us-central1 when unset
reset()
g = engines._gemini_rails(MODEL, env={**VX, "GCP_LOCATION": "global"}, adc_bearer=lambda: TOKEN)
check("global location: host aiplatform.googleapis.com, path locations/global",
      g[0][1] == "https://aiplatform.googleapis.com/v1/projects/proj-1/locations/global"
                 "/publishers/google/models/%s:generateContent" % MODEL)
d = engines._gemini_rails(MODEL, env={"GEMINI_BACKEND": "vertex", "GCP_PROJECT": "proj-1"}, adc_bearer=lambda: TOKEN)
check("location defaults to us-central1", "/locations/us-central1/" in d[0][1])
check("us-central1 URL is what the old template built",
      engines._vertex_url("p", "us-central1", "m") ==
      "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/us-central1/publishers/google/models/m:generateContent")

# 5. vertex mode with the whole call path: Vertex answers, body carries role, meta records the backend
res, calls, meta, out = run_call({**KEYS, **VX}, {"vertex": [Resp(200, body=OK_BODY)], "studio": []})
check("vertex call answers", res[0] is True and res[1] == "answer text" and len(calls) == 1)
check("vertex call went to aiplatform with the same model id", "aiplatform.googleapis.com" in calls[0]["url"]
      and "models/%s:" % MODEL in calls[0]["url"])
check("vertex body has role user", calls[0]["json"] == {"contents": [{"role": "user", "parts": [{"text": "q"}]}]})
check("no tools or grounding field in the vertex body", "tools" not in calls[0]["json"])
check("meta: rail vertex, method 'gemini via vertex'", meta.get("rail") == "vertex" and meta.get("method") == "gemini via vertex")
check("meta keeps served_model and requested_model", meta.get("served_model") == "v-test" and meta.get("requested_model") == MODEL)
check("no token or key in meta or output", not any(s in repr(meta) + out for s in (TOKEN, FREE, PAID)))

# 6. Vertex refuses (403, then 429): falls through to AI Studio; row says so, no stale 'method'
res, calls, meta, _ = run_call({**KEYS, **VX}, {"vertex": [Resp(403, "denied")], "studio": [Resp(200, body=OK_BODY)]})
check("vertex 403 falls to studio_free and answers", res[0] is True and len(calls) == 2 and meta.get("rail") == "studio_free")
check("fallthrough row: no method, vertex_refused_http recorded", "method" not in meta and meta.get("vertex_refused_http") == 403)
res, calls, meta, _ = run_call({**KEYS, **VX}, {"vertex": [Resp(429, "{}")], "studio": [Resp(200, body=OK_BODY)]})
check("vertex 429 falls through too", res[0] is True and meta.get("vertex_refused_http") == 429)

# 7. any other Vertex status stops the chain and keeps the method on the failed row
res, calls, meta, _ = run_call({**KEYS, **VX}, {"vertex": [Resp(404, "model not found")], "studio": []})
check("vertex 404 does not fall through", res[0] is False and len(calls) == 1)
check("failed vertex row still says method vertex", meta.get("method") == "gemini via vertex" and "404" in res[3])

# 8. Vertex unavailable (no credentials, or no project): rail skipped, warning once, AI Studio answers
res, calls, meta, out = run_call({**KEYS, **VX}, {"vertex": [], "studio": [Resp(200, body=OK_BODY)]}, adc=lambda: None)
check("no ADC: studio answers", res[0] is True and meta.get("rail") == "studio_free" and "method" not in meta)
check("no ADC: one ::warning:: line naming the switch", out.count("::warning::GEMINI_BACKEND=vertex") == 1)
res, calls, meta, out = run_call({**KEYS, "GEMINI_BACKEND": "vertex"}, {"vertex": [], "studio": [Resp(200, body=OK_BODY)]},
                                 adc=never_called)
check("no project: ADC not even asked, studio answers, warning names the project",
      res[0] is True and "GCP_PROJECT is empty" in out)
res, calls, meta, out = run_call({"GEMINI_BACKEND": "vertex", "GCP_PROJECT": "proj-1"}, {"vertex": [], "studio": []}, adc=lambda: None)
check("vertex requested, nothing available: clean error, no call",
      res[0] is False and "not set" in res[3] and "Vertex rail is unavailable" in res[3] and calls == [])

# 9. an unrecognised backend value warns and uses the API-key path
res, calls, meta, out = run_call({**KEYS, "GEMINI_BACKEND": "vertexai"}, {"studio": [Resp(200, body=OK_BODY)], "vertex": []},
                                 adc=never_called)
check("unknown backend value: studio path plus one warning", res[0] is True and meta.get("rail") == "studio_free"
      and out.count("::warning::GEMINI_BACKEND is set but is not 'vertex'") == 1)

# 10. a token that leaks into an error body is scrubbed from the returned error
leak = Resp(400, "bad request token=%s echoed" % TOKEN)
res, calls, meta, _ = run_call({**VX}, {"vertex": [leak], "studio": []})
check("token echoed by the server is redacted from the error", res[0] is False and TOKEN not in res[3])

# 11. the legacy service-account rail is not added alongside the ADC rail, and still works without it
reset()
both = engines._gemini_rails(MODEL, env={**VX, "GCP_SA_KEY": '{"x":1}'}, vertex_bearer=lambda sa: "SA-TOKEN-0005",
                             adc_bearer=lambda: TOKEN)
check("vertex mode with GCP_SA_KEY: a single vertex rail (ADC)",
      [r[0] for r in both] == ["vertex"] and both[0][2]["Authorization"] == "Bearer " + TOKEN)
legacy = engines._gemini_rails(MODEL, env={**KEYS, "GCP_SA_KEY": '{"x":1}', "GCP_PROJECT": "proj-1"},
                               vertex_bearer=lambda sa: "SA-TOKEN-0005", adc_bearer=never_called)
check("default mode with GCP_SA_KEY: legacy rail order unchanged", [r[0] for r in legacy] == ["studio_free", "vertex", "studio_paid"])


# 12. _vertex_adc_bearer against a fake google-auth: cached credentials, refresh only when not valid, class-only errors
class FakeCreds:
    def __init__(self, valid):
        self.valid = valid
        self.token = TOKEN if valid else None
        self.refreshes = 0

    def refresh(self, request):
        self.refreshes += 1
        self.valid = True
        self.token = TOKEN


def install_fake_google_auth(default):
    google = types.ModuleType("google")
    auth = types.ModuleType("google.auth")
    transport = types.ModuleType("google.auth.transport")
    treq = types.ModuleType("google.auth.transport.requests")
    treq.Request = lambda: object()
    auth.default = default
    google.auth = auth
    auth.transport = transport
    transport.requests = treq
    saved = {k: sys.modules.get(k) for k in ("google", "google.auth", "google.auth.transport", "google.auth.transport.requests")}
    sys.modules.update({"google": google, "google.auth": auth, "google.auth.transport": transport,
                        "google.auth.transport.requests": treq})
    return saved


def restore_modules(saved):
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


scopes_seen = []
creds = FakeCreds(valid=False)


def default_ok(scopes=None):
    scopes_seen.append(scopes)
    return creds, "proj-1"


saved = install_fake_google_auth(default_ok)
try:
    reset()
    t1 = engines._vertex_adc_bearer()
    check("ADC: first call refreshes an invalid credential and returns the token", t1 == TOKEN and creds.refreshes == 1)
    check("ADC: asked for the cloud-platform scope", scopes_seen == [["https://www.googleapis.com/auth/cloud-platform"]])
    t2 = engines._vertex_adc_bearer()
    check("ADC: a valid cached credential is reused, no second refresh, no second default()",
          t2 == TOKEN and creds.refreshes == 1 and len(scopes_seen) == 1)
    creds.valid = False
    engines._vertex_adc_bearer()
    check("ADC: refreshes again once the credential is no longer valid", creds.refreshes == 2)

    def default_fail(scopes=None):
        raise RuntimeError("secret-looking detail that must not be kept")

    sys.modules["google.auth"].default = default_fail
    reset()
    check("ADC: no credentials returns None", engines._vertex_adc_bearer() is None)
    check("ADC: only the exception class is kept", engines._VERTEX_ADC["error"] == "RuntimeError")
finally:
    restore_modules(saved)

reset()
check("ADC: google-auth missing returns None, never raises", engines._vertex_adc_bearer() is None
      and engines._VERTEX_ADC["error"] in ("ModuleNotFoundError", "ImportError"))

# 13. e1_runner counts Gemini as available on the Vertex backend, and only then
check("runner: no gemini credential, not available", e1_runner.engine_available("gemini", {}) is False)
check("runner: AI Studio keys still count", e1_runner.engine_available("gemini", {"GEMINI_API_KEY_FREE": "x"}) is True
      and e1_runner.engine_available("gemini", {"GEMINI_API_KEY": "x"}) is True)
check("runner: vertex backend with a project counts", e1_runner.engine_available(
    "gemini", {"GEMINI_BACKEND": "vertex", "GCP_PROJECT": "p"}) is True)
check("runner: vertex backend without a project does not", e1_runner.engine_available("gemini", {"GEMINI_BACKEND": "vertex"}) is False)
check("runner: GEMINI_BACKEND has no effect on other engines", e1_runner.engine_available(
    "anthropic", {"GEMINI_BACKEND": "vertex", "GCP_PROJECT": "p"}) is False
      and e1_runner.engine_available("anthropic", {"ANTHROPIC_API_KEY": "k"}) is True)

print("ALL PASS" if not fails else "FAILED: %s" % fails)


def test_gemini_vertex():
    """pytest entry point: the checks above ran at import."""
    assert not fails, fails


if __name__ == "__main__":
    sys.exit(1 if fails else 0)
