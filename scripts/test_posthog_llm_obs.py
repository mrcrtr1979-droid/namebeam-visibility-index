"""
Self-test for the PostHog AI Observability hook (scripts/posthog_llm_obs.py, wired into
scripts/e1_runner.py run_engine). Fully offline: urlopen is a stub, the engines are stubs,
nothing touches the network. Run from the repo root:

    python3 scripts/test_posthog_llm_obs.py

Proves:
  (a) one $ai_generation event per engine row, with the right properties, and no prompt or answer text
  (b) no exception escapes when the network fails (URLError, timeout, HTTP 500, HTTP 400), one retry at most
  (c) nothing is sent when the key is empty or DISABLE_POSTHOG is set
  (d) the collector's corpus output is byte-identical with and without the hook (fixture run)

Exits 0 only when every case passes.
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import urllib.error
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TEST_KEY = "phc_testkey0000000000000000000000"
for k in ("PERPLEXITY", "GEMINI", "OPENAI", "ANTHROPIC"):
    os.environ[k + "_API_" + "KEY"] = "test-dummy-0123456789"
for k in ("BRIGHTDATA_API_KEY", "GEMINI_API_KEY_FREE", "BEDROCK_MODEL_ID", "GITHUB_RUN_ID",
          "POSTHOG_PROJECT_KEY", "DISABLE_POSTHOG", "POSTHOG_HOST"):
    os.environ.pop(k, None)

import engines  # noqa: E402
import e1_runner  # noqa: E402
import posthog_llm_obs as obs  # noqa: E402

fails = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)


class StubResp:
    def __init__(self, status=200):
        self.status = status

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class StubUrlopen:
    """Records every request; behaviours is a list of per-call outcomes (exception or status int)."""

    def __init__(self, behaviours=None):
        self.calls = []
        self.behaviours = list(behaviours or [])

    def __call__(self, req, timeout=None):
        self.calls.append({"url": req.full_url, "method": req.get_method(), "timeout": timeout,
                           "body": json.loads(req.data.decode("utf-8")), "headers": dict(req.header_items())})
        b = self.behaviours.pop(0) if self.behaviours else 200
        if isinstance(b, Exception):
            raise b
        if b >= 400:
            raise urllib.error.HTTPError(req.full_url, b, "x", {}, io.BytesIO(b"secret-detail"))
        return StubResp(b)


# ---------------------------------------------------------------- fixtures
ROSTER = {"roster": [
    {"business": "Acme Dental", "variants_business_name_forms": ["Acme"], "niche": "dentist",
     "market": "Springfield, MO", "prompt_1": "best dentist in Springfield MO", "active": True},
    {"business": "Beta Roofing", "variants_business_name_forms": [], "niche": "roofer",
     "market": "Tulsa, OK", "prompt_1": "best roofer in Tulsa", "active": True,
     "engines": ["perplexity"], "serp": False},
]}


def stub_engines():
    """Stub adapters with the real contract and the real LAST_CALL_META side effects."""
    def perplexity(prompt):
        engines.LAST_CALL_META["perplexity"] = {
            "served_model": "perplexity/sonar", "response_id": "resp_1",
            "usage": {"input_tokens": 4846, "output_tokens": 300}}
        return True, "**Acme Dental** and **Other Dental** are good.", ["https://a.example/x"], ""

    def gemini(prompt):
        engines.LAST_CALL_META["gemini"] = {"requested_model": "gemini-3.6-flash"}
        return False, "", [], "gemini HTTP 402: {\"error\": \"billing\"}"

    def openai(prompt):
        engines.LAST_CALL_META["openai"] = {
            "served_model": "gpt-5-mini-2025-08-07", "response_id": "chatcmpl-1",
            "usage": {"prompt_tokens": 17, "completion_tokens": 1405}}
        return True, "Try **Acme Dental** or **Third Dental**.", [], ""

    def anthropic(prompt):
        engines.LAST_CALL_META["anthropic"] = {}
        return False, "", [], "QUOTA: anthropic HTTP 429: rate limited"

    return {"perplexity": perplexity, "gemini": gemini, "openai": openai, "anthropic": anthropic}


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 23, 12, 10, 0, tzinfo=tz or timezone.utc)


def run_collector(workdir, env_key, disable=False, urlopen=None):
    """Run e1_runner.main() in workdir with stub engines; returns (exit code, sent requests)."""
    os.makedirs(os.path.join(workdir, "roster"))
    with open(os.path.join(workdir, "roster", "e1_roster.json"), "w", encoding="utf-8") as f:
        json.dump(ROSTER, f)
    saved = (os.getcwd(), e1_runner.ENGINES, e1_runner.datetime, dict(engines.LAST_CALL_META))
    old_env = {k: os.environ.get(k) for k in ("POSTHOG_PROJECT_KEY", "DISABLE_POSTHOG", "GITHUB_RUN_ID")}
    stub = urlopen or StubUrlopen()
    real_urlopen = obs.urllib.request.urlopen
    try:
        os.chdir(workdir)
        e1_runner.ENGINES = stub_engines()
        e1_runner.datetime = FixedDatetime
        e1_runner._GAZ_INDEX.clear()
        for k, v in (("POSTHOG_PROJECT_KEY", env_key), ("DISABLE_POSTHOG", "1" if disable else ""),
                     ("GITHUB_RUN_ID", "987654321")):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        obs.urllib.request.urlopen = stub
        del obs._BUFFER[:]
        buf = io.StringIO()
        real_stdout, sys.stdout = sys.stdout, buf
        try:
            rc = e1_runner.main()
        finally:
            sys.stdout = real_stdout
        return rc, stub
    finally:
        obs.urllib.request.urlopen = real_urlopen
        os.chdir(saved[0])
        e1_runner.ENGINES, e1_runner.datetime = saved[1], saved[2]
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def tree_digest(root):
    out = {}
    for dp, _, fns in os.walk(root):
        for fn in sorted(fns):
            p = os.path.join(dp, fn)
            out[os.path.relpath(p, root)] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return out


tmp = tempfile.mkdtemp(prefix="phobs_")
try:
    # ------------------------------------------------------------ (a) one event per engine row
    wa = os.path.join(tmp, "a")
    os.makedirs(wa)
    stub = StubUrlopen()
    rc, stub = run_collector(wa, TEST_KEY, urlopen=stub)
    rows = []
    for fn in sorted(os.listdir(os.path.join(wa, "corpus", "e1"))):
        d = json.load(open(os.path.join(wa, "corpus", "e1", fn), encoding="utf-8"))
        if d.get("engine") in engines.ENGINES:
            rows.append(d)
    check("a: collector exit code is 0", rc == 0)
    check("a: exactly one /batch/ POST for the whole run", len(stub.calls) == 1 and stub.calls[0]["url"] == "https://us.i.posthog.com/batch/"
          and stub.calls[0]["method"] == "POST")
    call = stub.calls[0]
    evs = call["body"]["batch"]
    check("a: api_key is in the body, not the URL", call["body"]["api_key"] == TEST_KEY and TEST_KEY not in call["url"])
    check("a: 3-second timeout", call["timeout"] == 3)
    # roster: Acme -> 4 engines, Beta -> perplexity only = 5 engine rows
    check("a: one event per engine row (5 rows, 5 events)", len(rows) == 5 and len(evs) == 5)
    check("a: every event is $ai_generation", all(e["event"] == "$ai_generation" for e in evs))
    check("a: trace ids are the row check_ids, one each",
          sorted(e["properties"]["$ai_trace_id"] for e in evs) == sorted(r["check_id"] for r in rows))
    by = {(e["properties"]["engine"], e["properties"]["question_id"]): e["properties"] for e in evs}
    g = by[("gemini", "acme_dental")]
    check("a: failed gemini row -> is_error, http 402, FAILED, requested model, error text",
          g["$ai_is_error"] is True and g["$ai_http_status"] == 402 and g["run_status"] == "FAILED"
          and g["requested_model"] == "gemini-3.6-flash" and g["served_model"] is None
          and g["$ai_model"] == "gemini-3.6-flash" and "HTTP 402" in g["$ai_error"]
          and "$ai_input_tokens" not in g)
    a = by[("anthropic", "acme_dental")]
    check("a: QUOTA row -> is_error, http 429, run_status QUOTA_BLOCKED",
          a["$ai_is_error"] is True and a["$ai_http_status"] == 429 and a["run_status"] == "QUOTA_BLOCKED")
    o = by[("openai", "acme_dental")]
    check("a: OK openai row -> http 200, not error, served model, tokens, no $ai_error",
          o["$ai_is_error"] is False and o["$ai_http_status"] == 200 and o["run_status"] == "OK"
          and o["$ai_model"] == "gpt-5-mini-2025-08-07" and o["served_model"] == "gpt-5-mini-2025-08-07"
          and o["$ai_input_tokens"] == 17 and o["$ai_output_tokens"] == 1405 and "$ai_error" not in o)
    p = by[("perplexity", "beta_roofing")]
    check("a: perplexity row carries market, date, run_id, provider, span name, distinct_id",
          p["market"] == "Tulsa, OK" and p["date_utc"] == "2026-10-23" and p["run_id"] == "987654321"
          and p["$ai_provider"] == "perplexity" and p["$ai_span_name"] == "e1_check"
          and p["distinct_id"] == "namebeam-collector" and p["$ai_input_tokens"] == 4846
          and p["$ai_output_tokens"] == 300 and p["$ai_latency"] >= 0)
    check("a: events carry a timestamp and no person profile", all(e["timestamp"].endswith("Z")
          and e["properties"]["$process_person_profile"] is False for e in evs))
    blob = json.dumps(call["body"])
    check("a: no prompt text, no answer text, no $ai_input / $ai_output_choices anywhere",
          "best dentist" not in blob and "Other Dental" not in blob and "$ai_input\"" not in blob
          and "$ai_output_choices" not in blob and "answer_verbatim" not in blob)
    check("a: no corpus-only fields leak (sources, competitors)", "a.example" not in blob and "competitors" not in blob)
    check("a: no events for SERP/AGREE rows (only engine calls)", all(e["properties"]["engine"] in engines.ENGINES for e in evs))

    # gemini tokens: candidates + thoughts
    check("tokens: gemini output = candidates + thoughts, input = prompt",
          obs.tokens_from_usage("gemini", {"promptTokenCount": 11, "candidatesTokenCount": 878, "thoughtsTokenCount": 978}) == (11, 1856))
    check("tokens: anthropic and perplexity use input/output_tokens",
          obs.tokens_from_usage("anthropic", {"input_tokens": 18, "output_tokens": 227}) == (18, 227)
          and obs.tokens_from_usage("perplexity", {"input_tokens": 5, "output_tokens": 6}) == (5, 6))
    check("tokens: absent or odd usage gives None, never raises",
          obs.tokens_from_usage("openai", None) == (None, None) and obs.tokens_from_usage("openai", {"prompt_tokens": "x"}) == (None, None))

    # ------------------------------------------------------------ (b) network failures never escape
    for label, behaviours, expect_calls in (
            ("URLError", [urllib.error.URLError("dns secret-host"), urllib.error.URLError("dns")], 2),
            ("timeout", [TimeoutError("slow"), TimeoutError("slow")], 2),
            ("HTTP 500 twice", [500, 500], 2),
            ("HTTP 500 then ok (one retry succeeds)", [500, 200], 2),
            ("HTTP 400 (no retry)", [400], 1),
            ("unexpected exception type", [RuntimeError("boom"), RuntimeError("boom")], 2)):
        wb = os.path.join(tmp, "b_" + str(abs(hash(label))))
        os.makedirs(wb)
        errbuf = io.StringIO()
        real_stderr, sys.stderr = sys.stderr, errbuf
        try:
            rc, st = run_collector(wb, TEST_KEY, urlopen=StubUrlopen(behaviours))
            raised = False
        except BaseException:
            raised = True
            rc, st = None, StubUrlopen()
        finally:
            sys.stderr = real_stderr
        msg = errbuf.getvalue()
        check("b: %s: nothing escapes, exit code 0, at most one retry (%d calls)" % (label, expect_calls),
              (not raised) and rc == 0 and len(st.calls) == expect_calls)
        check("b: %s: warning never contains the key or response detail" % label,
              TEST_KEY not in msg and "secret" not in msg)
    # flush() and record() themselves are exception-proof even with garbage input
    del obs._BUFFER[:]
    os.environ["POSTHOG_PROJECT_KEY"] = TEST_KEY
    try:
        obs.record(None, "not a number", None)
        obs.record({"engine": "gemini", "run_status": "OK", "engine_meta": "oops"}, None)
        r1 = obs.flush(urlopen=StubUrlopen([RuntimeError("x"), RuntimeError("x")]))
        check("b: record()/flush() swallow garbage input and a dead sender", r1 == 0)
    finally:
        os.environ.pop("POSTHOG_PROJECT_KEY", None)
        del obs._BUFFER[:]

    # ------------------------------------------------------------ (c) disabled -> nothing sent
    for label, key, disable in (("empty key", "", False), ("whitespace key", "   ", False),
                                ("key unset", None, False), ("DISABLE_POSTHOG set", TEST_KEY, True)):
        wc = os.path.join(tmp, "c_" + label.replace(" ", "_"))
        os.makedirs(wc)
        rc, st = run_collector(wc, key, disable=disable, urlopen=StubUrlopen())
        check("c: %s: zero network calls, exit 0, buffer empty" % label,
              rc == 0 and st.calls == [] and obs._BUFFER == [])
    check("c: DISABLE_POSTHOG=0 / false does not disable", (os.environ.update(DISABLE_POSTHOG="0"), not obs._disabled())[1]
          and (os.environ.update(DISABLE_POSTHOG="false"), not obs._disabled())[1])
    os.environ.pop("DISABLE_POSTHOG", None)

    # ------------------------------------------------------------ (d) corpus byte-identical
    w_on = os.path.join(tmp, "d_on")
    w_off = os.path.join(tmp, "d_off")
    os.makedirs(w_on)
    os.makedirs(w_off)
    rc_on, st_on = run_collector(w_on, TEST_KEY, urlopen=StubUrlopen())
    rc_off, st_off = run_collector(w_off, "", urlopen=StubUrlopen())
    d_on, d_off = tree_digest(os.path.join(w_on, "corpus")), tree_digest(os.path.join(w_off, "corpus"))
    check("d: hook ran in one fixture run and not in the other (1 call vs 0)", len(st_on.calls) == 1 and len(st_off.calls) == 0)
    check("d: same exit code", rc_on == rc_off == 0)
    check("d: same file set (%d files, incl. AGREE row)" % len(d_off), set(d_on) == set(d_off) and len(d_off) >= 6)
    check("d: every corpus file is byte-identical with and without the hook", d_on == d_off)
    # and a failing network must not change the corpus either
    w_net = os.path.join(tmp, "d_net")
    os.makedirs(w_net)
    run_collector(w_net, TEST_KEY, urlopen=StubUrlopen([urllib.error.URLError("x")] * 2))
    check("d: corpus is byte-identical even when the network fails", tree_digest(os.path.join(w_net, "corpus")) == d_off)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("ALL PASS" if not fails else "FAILED: " + ", ".join(fails))
sys.exit(1 if fails else 0)
