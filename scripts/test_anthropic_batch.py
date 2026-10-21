"""
Self-test for the Anthropic batch mode (scripts/anthropic_batch.py + the hook in
e1_runner.py). Offline: HTTP, sleep and the clock are fakes, so no key and no
network are needed. Run from the repo root:

    python3 scripts/test_anthropic_batch.py

Exits 0 only when every case passes.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["ANTHROPIC_API_KEY"] = "test-dummy-0123456789"
os.environ.pop("ANTHROPIC_MODE", None)

import engines  # noqa: E402
import anthropic_batch as ab  # noqa: E402
import e1_runner  # noqa: E402

fails = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)


class Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def line(cid, typ, **kw):
    return json.dumps({"custom_id": cid, "result": dict({"type": typ}, **kw)})


def ok_msg(text, n=7):
    return {"id": "msg_x", "type": "message", "role": "assistant", "model": "claude-haiku-4-5-20251001",
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 10, "output_tokens": n}}


class FakeHttp:
    """Scripted Message Batches API. statuses: list of processing_status values returned by
    successive GETs of the batch; results: callable(requests) -> jsonl text."""

    def __init__(self, statuses, results, create_status=200, results_status=200):
        self.statuses = list(statuses)
        self.results = results
        self.create_status = create_status
        self.results_status = results_status
        self.calls = []
        self.reqs = None
        self.cancelled = False

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url))
        assert headers["x-api-key"], "key missing"
        if url.endswith("/cancel"):
            self.cancelled = True
            return Resp(200, {"id": "msgbatch_t", "processing_status": "canceling"})
        self.reqs = json["requests"]
        if self.create_status != 200:
            return Resp(self.create_status, text="create failed for test")
        return Resp(200, {"id": "msgbatch_t", "processing_status": "in_progress",
                          "request_counts": {"processing": len(self.reqs)},
                          "results_url": None, "created_at": "2026-10-21T00:00:00Z"})

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url))
        if url.endswith("/results"):
            if self.results_status != 200:
                return Resp(self.results_status, text="results failed for test")
            return Resp(200, text=self.results(self.reqs))
        st = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if self.cancelled and st != "ended":
            st = "ended" if len(self.statuses) <= 1 else st
        body = {"id": "msgbatch_t", "processing_status": st,
                "request_counts": {"processing": 0 if st == "ended" else len(self.reqs)},
                "results_url": "https://api.anthropic.com/v1/messages/batches/msgbatch_t/results"
                if st == "ended" else None}
        return Resp(200, body)


def run(http, items, deadline_s=600, poll_s=30):
    c = Clock()
    return ab.run_batch("test-dummy-0123456789", items, deadline_s=deadline_s, poll_s=poll_s,
                        http=http, sleep=c.sleep, clock=c.now), c


# ---- mode flag --------------------------------------------------------------
for v, want in ((None, "live"), ("", "live"), ("live", "live"), ("batch", "batch"), (" Batch ", "batch"),
                ("nonsense", "live")):
    if v is None:
        os.environ.pop("ANTHROPIC_MODE", None)
    else:
        os.environ["ANTHROPIC_MODE"] = v
    check("mode(%r) -> %s" % (v, want), ab.mode() == want)
os.environ.pop("ANTHROPIC_MODE", None)

# ---- custom_id rules ----------------------------------------------------------
import re  # noqa: E402
long_slug = "segment_personal_injury_lawyers_pleasanton_shotland_pilot_extra_long_tail"
a = ab.cell_id("2026-10-21", long_slug, 1)
check("cell_id matches the API pattern and is at most 64 chars",
      bool(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", a)))
check("cell_id is stable and separates a repeated business",
      a == ab.cell_id("2026-10-21", long_slug, 1) and a != ab.cell_id("2026-10-21", long_slug, 2)
      and a != ab.cell_id("2026-10-22", long_slug, 1))

# ---- request params equal the live call --------------------------------------
posted = {}


def fake_post(url, headers=None, json=None, timeout=None):
    posted["json"] = json
    return Resp(200, ok_msg("A"))


real_post = engines.requests.post
engines.requests.post = fake_post
try:
    engines.call_anthropic("Who is the best X in Y?")
finally:
    engines.requests.post = real_post
check("batch params equal the params call_anthropic sends",
      engines.anthropic_params("Who is the best X in Y?") == posted["json"])

# ---- happy path ---------------------------------------------------------------
items = [("c1", "q1"), ("c2", "q2"), ("c3", "q3")]


def all_ok(reqs):
    return "\n".join(line(r["custom_id"], "succeeded", message=ok_msg("answer " + r["custom_id"]))
                     for r in reqs[::-1])  # reversed: order must not matter


h = FakeHttp(["in_progress", "in_progress", "ended"], all_ok)
s, c = run(h, items)
check("happy path: 3 of 3 from the batch, none to redo",
      len(s["outcomes"]) == 3 and s["redo"] == [] and s["types"] == {"succeeded": 3}
      and s["fallback_reason"] is None and not s["cancelled"])
check("happy path: answers matched to the right cell by custom_id",
      s["outcomes"]["c2"]["tuple"] == (True, "answer c2", [], ""))
check("happy path: engine_meta has served_model, response_id, usage like the live row",
      s["outcomes"]["c1"]["meta"] == {"served_model": "claude-haiku-4-5-20251001", "response_id": "msg_x",
                                      "usage": {"input_tokens": 10, "output_tokens": 7}})
check("happy path: three status checks, 30 s apart (90 s of fake time)", c.now() == 90.0)
check("happy path: request params in the batch are the live params",
      all(r["params"] == engines.anthropic_params(p) for r, (_, p) in zip(h.reqs, items)))

# ---- errored results are failed rows, not dropped -----------------------------


def mixed(reqs):
    return "\n".join([
        line("c1", "succeeded", message=ok_msg("fine")),
        line("c2", "errored", error={"type": "error", "error": {"type": "invalid_request_error",
                                                                 "message": "model: bad key=sk-ant-secret"}}),
        line("c3", "errored", error={"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}}),
    ])


h = FakeHttp(["ended"], mixed)
s, c = run(h, items)
check("errored: kept as failed tuples, counted, never in redo",
      s["outcomes"]["c2"]["tuple"][0] is False and s["outcomes"]["c3"]["tuple"][0] is False and s["redo"] == []
      and s["types"] == {"succeeded": 1, "errored": 2})
check("errored: a rate limit error carries the QUOTA prefix like a live 429",
      s["outcomes"]["c3"]["tuple"][3].startswith("QUOTA: "))
check("errored: the live key never appears in an error string",
      "test-dummy-0123456789" not in json.dumps(s["outcomes"]))

# ---- expired / canceled / missing go live -------------------------------------


def partial(reqs):
    return "\n".join([line("c1", "succeeded", message=ok_msg("fine")),
                      line("c2", "expired"), line("c3", "canceled")])


h = FakeHttp(["ended"], partial)
s, c = run(h, items)
check("expired and canceled requests are re-asked live", s["redo"] == ["c2", "c3"] and list(s["outcomes"]) == ["c1"])


def missing(reqs):
    return line("c1", "succeeded", message=ok_msg("fine"))


h = FakeHttp(["ended"], missing)
s, c = run(h, items)
check("a request absent from the results is re-asked live", s["redo"] == ["c2", "c3"])

# ---- deadline: cancel, keep what finished, redo the rest ----------------------
h = FakeHttp(["in_progress"], partial)
s, c = run(h, items, deadline_s=100, poll_s=30)
check("deadline: batch cancelled once the deadline passes", s["cancelled"] and h.cancelled)
check("deadline: stopped waiting at about the deadline (not later than deadline + cancel wait)",
      c.now() <= 100 + 30 + ab.CANCEL_WAIT_SECONDS)
check("deadline: finished answers kept, unfinished redone live",
      list(s["outcomes"]) == ["c1"] and s["redo"] == ["c2", "c3"] and s["fallback_reason"] is None)


class NeverEnds(FakeHttp):
    def get(self, url, headers=None, timeout=None):
        if url.endswith("/results"):
            return super().get(url, headers, timeout)
        self.calls.append(("GET", url))
        return Resp(200, {"id": "msgbatch_t", "processing_status": "canceling" if self.cancelled else "in_progress",
                          "request_counts": {}, "results_url": None})


h = NeverEnds(["in_progress"], partial)
s, c = run(h, items, deadline_s=60, poll_s=30)
check("deadline: if the cancelled batch never reports ended, every question goes live",
      s["fallback_reason"] and s["redo"] == ["c1", "c2", "c3"] and s["outcomes"] == {})

# ---- create / poll / results failures -> everything live ----------------------
h = FakeHttp(["ended"], all_ok, create_status=529)
s, c = run(h, items)
check("create failure: nothing lost, all questions go live",
      s["batch_id"] is None and s["redo"] == ["c1", "c2", "c3"] and "batch create HTTP 529" in s["fallback_reason"])

h = FakeHttp(["ended"], all_ok, results_status=500)
s, c = run(h, items)
check("results failure: all questions go live", s["redo"] == ["c1", "c2", "c3"] and s["fallback_reason"])


class FlakyPoll(FakeHttp):
    def get(self, url, headers=None, timeout=None):
        if url.endswith("/results"):
            return super().get(url, headers, timeout)
        self.calls.append(("GET", url))
        return Resp(503, text="down")


h = FlakyPoll(["in_progress"], all_ok)
s, c = run(h, items, deadline_s=10000, poll_s=30)
check("status checks that keep failing: batch cancelled, all questions go live",
      s["cancelled"] and s["redo"] == ["c1", "c2", "c3"])

# ---- the runner hook: rows are identical to live rows --------------------------
roster_row = {"business": "Test Biz", "niche": "AI marketing agency", "market": "Los Angeles CA",
              "row_type": "business", "prompt_1": "Who is the best AI marketing agency in Los Angeles?",
              "variants_business_name_forms": [], "active": True}
text = "I would look at **Acme Digital** and **Zed Media** first."


def fake_live(prompt):
    engines.LAST_CALL_META["anthropic"] = engines._served_meta(ok_msg(text), "model", "id", "usage")
    return True, text, [], ""


with tempfile.TemporaryDirectory() as tmp:
    e1_runner.OUTPUT_DIR = os.path.join(tmp, "live")
    live_path, live_ok = e1_runner.run_engine(roster_row, "anthropic", fake_live, "2026-10-21")
    e1_runner.OUTPUT_DIR = os.path.join(tmp, "batch")
    engines.LAST_CALL_META["anthropic"] = {"stale": True}
    pre = {"tuple": (True, text, [], ""), "meta": engines._served_meta(ok_msg(text), "model", "id", "usage"),
           "type": "succeeded"}
    batch_path, batch_ok = e1_runner.run_engine(roster_row, "anthropic", None, "2026-10-21", prefetched=pre)
    a, b = json.load(open(live_path)), json.load(open(batch_path))
    check("OK row from the batch equals the live row (every column, same values)", live_ok and batch_ok and a == b)

    e1_runner.OUTPUT_DIR = os.path.join(tmp, "batch_err")
    err_pre = {"tuple": (False, "", [], "anthropic batch request errored (api_error): boom"), "meta": {},
               "type": "errored"}
    p, good = e1_runner.run_engine(roster_row, "anthropic", None, "2026-10-21", prefetched=err_pre)
    r = json.load(open(p))
    check("errored batch result becomes a FAILED row with the live column set",
          (not good) and r["run_status"] == "FAILED" and r["engine_meta"] == {}
          and "FAILED RUN" in r["session_note"] and r["answer_verbatim"] == ""
          and set(r) == set(a) | set())

    e1_runner.OUTPUT_DIR = os.path.join(tmp, "batch_quota")
    q_pre = {"tuple": (False, "", [], "QUOTA: anthropic batch request errored (rate_limit_error): x"),
             "meta": {}, "type": "errored"}
    p, good = e1_runner.run_engine(roster_row, "anthropic", None, "2026-10-21", prefetched=q_pre)
    check("rate limit errored result becomes QUOTA_BLOCKED like a live 429",
          json.load(open(p))["run_status"] == "QUOTA_BLOCKED")

# ---- prefetch honors the per-entry engines limit and the missing key ----------
rows = [dict(roster_row, business="A One"), dict(roster_row, business="B Two", engines=["perplexity"]),
        dict(roster_row, business="A One")]
seen_items = {}


def capture(key, items, **kw):
    seen_items["items"] = items
    return {"outcomes": {cid: {"tuple": (True, "x", [], ""), "meta": {}, "type": "succeeded"} for cid, _ in items},
            "redo": []}


real_run = ab.run_batch
ab.run_batch = capture
try:
    by_row, summ = ab.prefetch(rows, "2026-10-21", e1_runner.slugify, key="test-dummy-0123456789")
finally:
    ab.run_batch = real_run
check("prefetch: skips rows limited to other engines, separates a repeated business",
      len(seen_items["items"]) == 2 and len({c for c, _ in seen_items["items"]}) == 2
      and id(rows[0]) in by_row and id(rows[2]) in by_row and id(rows[1]) not in by_row)
by_row, summ = ab.prefetch(rows, "2026-10-21", e1_runner.slugify, key="")
os.environ.pop("ANTHROPIC_API_KEY", None)
by_row2, _ = ab.prefetch(rows, "2026-10-21", e1_runner.slugify)
check("prefetch: no key means nothing is batched (the live path logs its usual row)", by_row == {} and by_row2 == {})

print("\n%d failure(s)" % len(fails))
sys.exit(1 if fails else 0)
