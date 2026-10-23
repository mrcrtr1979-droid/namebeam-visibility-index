"""
Offline tests for scripts/client_check.py. No network, no keys: ENGINES is replaced
with fake callables. Runs under pytest or directly:

    python -I -X utf8 -m pytest tests/test_client_check.py -q
    python -I -X utf8 tests/test_client_check.py
"""
import hashlib
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import client_check as cc  # noqa: E402

ENGS = ("perplexity", "gemini", "openai", "anthropic")
ITEMS = [
    {"check_id": "T-01-alpha", "client": "Acme Roofing", "market": "Springfield, MO",
     "niche": "roofing", "question": "best roofing company in Springfield MO"},
    {"check_id": "T-02-beta", "client": "Doerner, Saunders, Daniel & Anderson",
     "market": "Oklahoma City, OK", "niche": "law firm",
     "question": "best law firm in Oklahoma City"},
]
TEXTS = {
    # item 1 names the client; item 2 only names the client without commas (loose, not exact)
    "best roofing company in Springfield MO":
        "**Acme Roofing** is a strong pick, then **Zed Roofing** and **Orbit Roofing**.\n",
    "best law firm in Oklahoma City":
        "**Crowe & Dunlevy** leads. Also **Doerner Saunders Daniel & Anderson** and **McAfee & Taft**.\n",
}


def fake_ok(engine):
    def fn(prompt):
        cc.LAST_CALL_META[engine] = {"served_model": engine + "-model-x",
                                     "usage": {"input_tokens": 7, "output_tokens": 11}}
        return True, TEXTS[prompt] + "Answer by " + engine + " — café.", [], ""
    return fn


def fake_boom(prompt):
    raise RuntimeError("boom key=SECRETVALUE123456")


def fake_soft_fail(prompt):
    return False, "", [], "QUOTA: openai HTTP 429: slow down"


def run(engines_map, engines_arg=",".join(ENGS), items=ITEMS):
    tmp = tempfile.mkdtemp()
    inp = os.path.join(tmp, "in.json")
    with open(inp, "w", encoding="utf-8") as f:
        json.dump(items, f)
    out = os.path.join(tmp, "client_checks")
    old = cc.ENGINES
    cc.ENGINES = engines_map
    os.environ["OPENAI_API_KEY"] = "SECRETVALUE123456"
    try:
        rc = cc.main(["--input", inp, "--engines", engines_arg, "--out-root", out,
                      "--run-date", "2026-10-27"])
    finally:
        cc.ENGINES = old
    assert rc == 0
    d = os.path.join(out, "2026-10-27")
    return d, {n: open(os.path.join(d, n), encoding="utf-8").read() for n in os.listdir(d)}


def test_four_files_per_item_and_hashes():
    d, files = run({e: fake_ok(e) for e in ENGS})
    for it in ITEMS:
        for e in ENGS:
            name = "%s_%s.json" % (it["check_id"], e)
            assert name in files, name
            row = json.loads(files[name])
            assert row["run_status"] == "OK" and row["engine"] == e
            assert row["answer_sha256"] == hashlib.sha256(row["answer_verbatim"].encode("utf-8")).hexdigest()
            assert row["prompt_text"] == it["question"]
            for k in ("check_id", "date_utc", "business", "niche", "market", "engine_meta", "run_status",
                      "business_mentioned", "competitors_mentioned", "sources_cited", "answer_verbatim",
                      "answer_sha256", "client_named", "client_named_loose", "first_three_named"):
                assert k in row, k
            assert row["engine_meta"]["served_model"] == e + "-model-x"
    assert "RECEIPT.md" in files and "SUMMARY.json" in files
    assert len([n for n in files if n.endswith(".json") and n != "SUMMARY.json"]) == 8


def test_client_named_planted():
    d, files = run({e: fake_ok(e) for e in ENGS})
    r1 = json.loads(files["T-01-alpha_openai.json"])
    assert r1["client_named"] is True and r1["client_named_loose"] is True
    assert r1["first_three_named"][0] == "Acme Roofing"
    assert "Acme Roofing" not in r1["competitors_mentioned"]     # record rule: client excluded
    r2 = json.loads(files["T-02-beta_openai.json"])
    assert r2["client_named"] is False          # comma spelling is not in the answer
    assert r2["client_named_loose"] is True     # punctuation-insensitive match
    # a client that is absent entirely
    absent = [{"check_id": "T-03-gamma", "client": "Nowhere Plumbing", "market": "Springfield, MO",
               "niche": "plumbing", "question": "best roofing company in Springfield MO"}]
    d, files = run({e: fake_ok(e) for e in ENGS}, items=absent)
    r3 = json.loads(files["T-03-gamma_anthropic.json"])
    assert r3["client_named"] is False and r3["client_named_loose"] is False


def test_fail_path_and_no_fabrication():
    m = {e: fake_ok(e) for e in ENGS}
    m["gemini"] = fake_boom
    m["openai"] = fake_soft_fail
    d, files = run(m)
    g = json.loads(files["T-01-alpha_gemini.json"])
    assert g["run_status"] == "FAIL" and g["error_class"] == "RuntimeError"
    assert g["answer_verbatim"] == "" and g["answer_sha256"] is None and g["client_named"] is None
    assert "SECRETVALUE123456" not in files["T-01-alpha_gemini.json"]      # redacted
    o = json.loads(files["T-01-alpha_openai.json"])
    assert o["run_status"] == "FAIL" and o["error_class"] == "QuotaBlocked"
    p = json.loads(files["T-01-alpha_perplexity.json"])
    assert p["run_status"] == "OK"             # one engine failing never blocks the others
    s = json.loads(files["SUMMARY.json"])
    assert s["fail"] == 4 and s["ok"] == 4 and s["per_engine"]["gemini"]["fail"] == 2


def test_receipt_lists_every_check_id():
    d, files = run({e: fake_ok(e) for e in ENGS})
    rc = files["RECEIPT.md"]
    for it in ITEMS:
        assert it["check_id"] in rc and it["question"] in rc
    for e in ENGS:
        assert "| %s |" % e in rc
    row = json.loads(files["T-01-alpha_perplexity.json"])
    assert row["answer_sha256"] in rc
    assert "7 / 11 / 18" in rc                 # tokens from engine_meta usage
    assert "Answer by" not in rc               # raw answer text is not repeated in the receipt


def test_engine_subset_and_validation():
    d, files = run({e: fake_ok(e) for e in ENGS}, engines_arg="anthropic")
    assert sorted(n for n in files if n.startswith("T-")) == ["T-01-alpha_anthropic.json", "T-02-beta_anthropic.json"]
    tmp = tempfile.mkdtemp()
    bad = os.path.join(tmp, "bad.json")
    json.dump([ITEMS[0], dict(ITEMS[1], check_id=ITEMS[0]["check_id"])], open(bad, "w"))
    assert cc.main(["--input", bad, "--out-root", os.path.join(tmp, "o")]) == 2     # duplicate id
    assert cc.main(["--input", bad, "--engines", "nope", "--out-root", os.path.join(tmp, "o")]) == 2
    assert cc.main(["--input", bad, "--out-root", os.path.join(tmp, "corpus", "e1")]) == 2   # never corpus/


def test_dry_run_calls_nothing_and_writes_nothing():
    tmp = tempfile.mkdtemp()
    inp = os.path.join(tmp, "in.json")
    json.dump(ITEMS, open(inp, "w"))
    called = []
    old = cc.ENGINES
    cc.ENGINES = {e: (lambda p, e=e: called.append(e)) for e in ENGS}
    try:
        rc = cc.main(["--input", inp, "--dry-run", "--out-root", os.path.join(tmp, "o")])
    finally:
        cc.ENGINES = old
    assert rc == 0 and not called and not os.path.exists(os.path.join(tmp, "o"))


def test_same_day_rerun_does_not_overwrite():
    tmp = tempfile.mkdtemp()
    base = os.path.join(tmp, "2026-10-27")
    os.makedirs(base)
    assert cc.pick_run_dir(tmp, "2026-10-27") == base + "_r2"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:  # noqa: BLE001
                fails += 1
                print("FAIL", name, type(exc).__name__, exc)
    sys.exit(1 if fails else 0)
