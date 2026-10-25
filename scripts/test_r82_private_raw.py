"""
Selftest for R82 (RULINGS.md, Terry 2026-10-24, option B): the raw answer text of Perplexity API
rows and of Google AI Overview rows is held in the private collector (private_e1/); hashes, counts
and flags stay public. Offline: no network.

    python3 scripts/test_r82_private_raw.py

Exits 0 only when every case passes.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for k in ("PERPLEXITY", "GEMINI", "OPENAI", "ANTHROPIC", "BRIGHTDATA"):
    os.environ[k + "_API_KEY"] = "test-dummy-key"

import e1_runner  # noqa: E402

POLICY = "R82 2026-10-24: raw text held in the private collector"
PPLX_TEXT = ("Top picks: **Alpha Realty** and **Beta Homes** serve this area. "
             "Source: https://example.com/a")
OAI_TEXT = "Consider **Gamma Roofing** for this job."
AIO_TEXT = "Local options include Delta Plumbing and Epsilon Electric."


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pplx_row(text=PPLX_TEXT):
    return {"check_id": "NB-CZ-API_2026-10-25_test_biz_perplexity", "engine": "perplexity",
            "run_channel": "api", "run_status": "OK", "business": "Test Biz",
            "answer_verbatim": text, "business_mentioned": False,
            "competitors_mentioned": ["Alpha Realty", "Beta Homes"],
            "sources_cited": ["https://example.com/a"]}


def oai_row():
    return {"check_id": "NB-CZ-API_2026-10-25_test_biz_openai", "engine": "openai",
            "run_channel": "api", "run_status": "OK", "business": "Test Biz",
            "answer_verbatim": OAI_TEXT, "business_mentioned": False,
            "competitors_mentioned": ["Gamma Roofing"], "sources_cited": []}


def serp_row():
    return {"check_id": "NB-CZ-SERP_2026-10-25_test_biz", "engine": "google_serp",
            "run_channel": "serp", "run_status": "OK", "business": "Test Biz",
            "business_mentioned": False, "ai_overview_detected": True,
            "ai_overview_present": True, "ai_overview_text": AIO_TEXT,
            "ai_overview_references": [{"href": "https://example.com/r", "title": "R", "source": "S"}],
            "organic_top_results": [{"rank": 1, "url": "https://example.com/o"}]}


def set_flag(value):
    if value is None:
        os.environ.pop("PRIVATE_RAW", None)
    else:
        os.environ["PRIVATE_RAW"] = value


def test_perplexity_row_is_held():
    set_flag("1")
    pub, full = e1_runner.redact_for_public(pplx_row())
    assert pub["answer_verbatim"] == ""
    assert pub["answer_sha256"] == sha(PPLX_TEXT)
    assert pub["answer_chars"] == len(PPLX_TEXT)
    assert pub["answer_private"] is True
    assert pub["raw_policy"] == POLICY
    assert pub["business_mentioned"] is False
    assert pub["competitors_mentioned"] == ["Alpha Realty", "Beta Homes"]
    assert pub["sources_cited"] == ["https://example.com/a"]
    assert full["answer_verbatim"] == PPLX_TEXT
    assert "answer_private" not in full


def test_perplexity_failed_row_hashes_empty_text():
    set_flag("1")
    pub, _ = e1_runner.redact_for_public(pplx_row(""))
    assert pub["answer_sha256"] == "" and pub["answer_chars"] == 0
    assert pub["answer_private"] is True


def test_openai_row_keeps_its_text():
    set_flag("1")
    pub, full = e1_runner.redact_for_public(oai_row())
    assert pub == oai_row()
    assert pub["answer_verbatim"] == OAI_TEXT
    assert "answer_private" not in pub and "raw_policy" not in pub
    assert full is not None and full["answer_verbatim"] == OAI_TEXT


def test_serp_ai_overview_is_held_and_references_stay():
    set_flag("1")
    pub, full = e1_runner.redact_for_public(serp_row())
    assert pub["ai_overview_text"] == ""
    assert pub["ai_overview_sha256"] == sha(AIO_TEXT)
    assert pub["ai_overview_chars"] == len(AIO_TEXT)
    assert pub["ai_overview_private"] is True
    assert pub["raw_policy"] == POLICY
    assert pub["ai_overview_references"] == serp_row()["ai_overview_references"]
    assert pub["organic_top_results"] == serp_row()["organic_top_results"]
    assert pub["ai_overview_present"] is True and pub["ai_overview_detected"] is True
    assert full["ai_overview_text"] == AIO_TEXT


def test_switch_off_changes_nothing():
    set_flag("0")
    try:
        for row in (pplx_row(), oai_row(), serp_row()):
            pub, full = e1_runner.redact_for_public(row)
            assert pub == row and full == row
            assert "answer_private" not in pub and "ai_overview_private" not in pub
    finally:
        set_flag("1")


def test_write_row_splits_public_and_private_copies():
    set_flag("1")
    old = os.getcwd()
    tmp = tempfile.mkdtemp()
    try:
        os.chdir(tmp)
        e1_runner.write_row(pplx_row(), "NB-CZ-API_2026-10-25_test_biz_perplexity")
        e1_runner.write_row(oai_row(), "NB-CZ-API_2026-10-25_test_biz_openai")
        e1_runner.write_row(serp_row(), "NB-CZ-SERP_2026-10-25_test_biz")
        pub = json.load(open(os.path.join(tmp, "corpus/e1/NB-CZ-API_2026-10-25_test_biz_perplexity.json"),
                             encoding="utf-8"))
        priv = json.load(open(os.path.join(tmp, "private_e1/NB-CZ-API_2026-10-25_test_biz_perplexity.json"),
                              encoding="utf-8"))
        assert pub["answer_verbatim"] == "" and priv["answer_verbatim"] == PPLX_TEXT
        oai = json.load(open(os.path.join(tmp, "corpus/e1/NB-CZ-API_2026-10-25_test_biz_openai.json"),
                             encoding="utf-8"))
        assert oai["answer_verbatim"] == OAI_TEXT
        assert not os.path.exists(os.path.join(tmp, "private_e1/NB-CZ-API_2026-10-25_test_biz_openai.json"))
        spub = json.load(open(os.path.join(tmp, "corpus/e1/NB-CZ-SERP_2026-10-25_test_biz.json"),
                              encoding="utf-8"))
        spriv = json.load(open(os.path.join(tmp, "private_e1/NB-CZ-SERP_2026-10-25_test_biz.json"),
                               encoding="utf-8"))
        assert spub["ai_overview_text"] == "" and spriv["ai_overview_text"] == AIO_TEXT
        # The public files must not contain the raw text anywhere.
        for name in os.listdir(os.path.join(tmp, "corpus/e1")):
            blob = open(os.path.join(tmp, "corpus/e1", name), encoding="utf-8").read()
            assert PPLX_TEXT not in blob and AIO_TEXT not in blob, name
    finally:
        os.chdir(old)
        shutil.rmtree(tmp, ignore_errors=True)


def test_switch_off_writes_no_private_copy():
    set_flag("0")
    old = os.getcwd()
    tmp = tempfile.mkdtemp()
    try:
        os.chdir(tmp)
        e1_runner.write_row(pplx_row(), "NB-CZ-API_2026-10-25_test_biz_perplexity")
        pub = json.load(open(os.path.join(tmp, "corpus/e1/NB-CZ-API_2026-10-25_test_biz_perplexity.json"),
                             encoding="utf-8"))
        assert pub["answer_verbatim"] == PPLX_TEXT
        assert not os.path.exists(os.path.join(tmp, "private_e1"))
    finally:
        os.chdir(old)
        shutil.rmtree(tmp, ignore_errors=True)
        set_flag("1")


if __name__ == "__main__":
    fails = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS " + name)
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                print("FAIL %s: %r" % (name, exc))
                fails.append(name)
    print("ALL PASS" if not fails else "FAILED: %s" % ", ".join(fails))
    sys.exit(1 if fails else 0)
