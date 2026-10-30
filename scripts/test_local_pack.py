"""
Self-test for the google_serp local pack fields (local_pack_top, local_pack_present), added
2026-10-31 [PACK-BUILD-1030]. Offline: Bright Data is a fake, no network, no key is read.

    python3 scripts/test_local_pack.py
    python3 -m pytest scripts/test_local_pack.py -q

Covers:
  - the reader (e1_runner.extract_local_pack): a 4-item snack_pack with mixed missing keys is cut to
    3 items in order; a response with no snack_pack, a non-list snack_pack, an empty one and a raw
    HTML payload read as documented (False / False / False / None); wrongly typed keys read as null.
  - the request is unchanged: exactly one POST to api.brightdata.com/request with the same zone,
    format, country and Google URL (hl, gl, pws, brd_json=1, brd_ai_overview=2) as before.
  - nothing else in the row moves: run_serp is run on the same two fixture responses and every key
    of the row it writes, except the two new ones, equals the row written by the code on main
    before this change (GOLDEN_*, literals). That covers organic_top_results and every ai_overview_*
    value, in the public copy and the private copy.
  - the failure paths: a FAILED row and a raw-HTML row carry local_pack_present None and [].
  - scripts/build_sources.py does not flag local_pack_top as an unmapped URL field and makes no
    URL rows from it.

Regenerating the golden rows (only if a fixture below changes): check out main in a scratch
worktree, copy this file into its scripts/ folder and run
    python3 scripts/test_local_pack.py --dump-golden
there. The output is the old code's rows for both fixtures.

Exits 0 only when every case passes.
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
for k in ("PERPLEXITY", "GEMINI", "OPENAI", "ANTHROPIC", "BRIGHTDATA"):
    os.environ[k + "_API_KEY"] = "test-dummy-key"
for k in ("PRIVATE_RAW", "BRIGHTDATA_AIO", "BRIGHTDATA_ZONE", "BRIGHTDATA_PARSED",
          "E1_ONLY_ENGINES", "E1_ONLY_FAILED", "E1_DATE_UTC", "E1_DISABLE_Q2"):
    os.environ.pop(k, None)

import engines as engines_mod  # noqa: E402
import e1_runner  # noqa: E402
import build_sources  # noqa: E402

DATE = "2026-10-31"
QUERY = "Who does backflow testing and repair near Liberty MO?"
BUSINESS_ROW = {"business": "Backflow Boss", "niche": "backflow testing", "market": "Liberty, MO",
                "row_type": "business", "prompt_1": QUERY}
CHECK_ID = "NB-CZ-SERP_%s_backflow_boss" % DATE
EXPECTED_URL = ("https://www.google.com/search?q=Who%20does%20backflow%20testing%20and%20repair"
                "%20near%20Liberty%20MO%3F&hl=en&gl=us&pws=0&brd_json=1&brd_ai_overview=2")
NEW_KEYS = ("local_pack_present", "local_pack_top")


def organic_items():
    return [{"link": "https://backflowboss.example/", "title": "Backflow Boss", "rank": 1},
            {"link": "https://libertyplumbing.example/backflow", "title": "Liberty Plumbing", "rank": 2},
            {"link": "https://www.google.com/maps/place/x", "title": "Maps", "rank": 3},
            {"link": "https://kcbackflow.example/test", "title": "KC Backflow", "rank": 4}]


def snack_pack_items():
    """Four items, mixed keys: the fourth must be cut, the second and third have keys missing."""
    return [
        {"name": "Backflow Boss", "link": "https://backflowboss.example/", "cid": "111",
         "rating": 4.8, "reviews_cnt": 312, "type": "Backflow testing service", "price": "$$",
         "phone": "+1 816-555-0101", "address": "100 Main St, Liberty, MO 64068",
         "work_status": "Open", "maps_link": "https://www.google.com/maps/place/?q=1",
         "sponsored": False, "tags": [], "global_rank": 1},
        {"name": "Liberty Plumbing", "rating": 4.5, "global_rank": 2},
        {"name": "KC Backflow Pros", "link": "https://kcbackflow.example/", "address": "9 Oak Ave",
         "rating": None, "reviews_cnt": "1,204", "global_rank": 3},
        {"name": "Fourth Plumbing", "link": "https://fourth.example/", "rating": 4.1,
         "reviews_cnt": 9, "global_rank": 4},
    ]


def inner_payload(with_pack):
    j = {"general": {"search_engine": "google", "query": QUERY, "language": "en"},
         "organic": organic_items(),
         "ai_overview": {"texts": [{"type": "paragraph",
                                    "snippet": "Backflow Boss and Liberty Plumbing test backflow assemblies."}],
                         "references": [{"href": "https://backflowboss.example/", "title": "Backflow Boss",
                                         "source": "backflowboss.example"}]},
         "related": [{"text": "backflow testing cost"}]}
    if with_pack:
        j["snack_pack"] = snack_pack_items()
        j["snack_pack_map"] = {"image": "https://maps.example/img", "link": "https://maps.example/m",
                               "lat": 39.24, "lng": -94.42}
    return j


def wrapped(with_pack):
    """Bright Data wrapper: the parsed JSON is an escaped string in body."""
    return json.dumps({"status_code": 200, "headers": {"content-type": "application/json"},
                       "body": json.dumps(inner_payload(with_pack))})


class FakeResp:
    status_code = 200

    def __init__(self, text):
        self.text = text


def run_serp_once(payload, tmp):
    """run_serp on one fake Bright Data response. Returns (public_row, private_row, posts)."""
    posts = []

    def fake_post(url, **kw):
        posts.append({"url": url, **kw})
        return FakeResp(payload)

    old_cwd, old_post = os.getcwd(), engines_mod.requests.post
    os.chdir(tmp)
    engines_mod.requests.post = fake_post
    try:
        path, ok = e1_runner.run_serp(BUSINESS_ROW, DATE)
        assert ok is True
        pub = json.load(open(os.path.join(tmp, "corpus/e1", CHECK_ID + ".json"), encoding="utf-8"))
        priv = json.load(open(os.path.join(tmp, "private_e1", CHECK_ID + ".json"), encoding="utf-8"))
    finally:
        engines_mod.requests.post = old_post
        os.chdir(old_cwd)
    return pub, priv, posts


def dump_golden():
    out = {}
    for name, with_pack in (("with_pack", True), ("no_pack", False)):
        tmp = tempfile.mkdtemp()
        try:
            pub, priv, posts = run_serp_once(wrapped(with_pack), tmp)
            out[name] = {"public": pub, "private_ai_overview_text": priv["ai_overview_text"],
                         "posts": len(posts)}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps(out, indent=1, ensure_ascii=False))


# The rows written by the code on main (601a4130, before this change) for the two fixtures above.
# Produced with --dump-golden on a clean checkout of main; never edit by hand.
GOLDEN = json.loads(r'''{
 "with_pack": {
  "public": {
   "check_id": "NB-CZ-SERP_2026-10-31_backflow_boss",
   "date_utc": "2026-10-31",
   "business": "Backflow Boss",
   "niche": "backflow testing",
   "market": "Liberty, MO",
   "row_type": "business",
   "prompt_variant": 1,
   "run": 1,
   "prompt_text": "Who does backflow testing and repair near Liberty MO?",
   "engine": "google_serp",
   "run_channel": "serp",
   "schema_version": "v4",
   "run_status": "OK",
   "serp_diagnostics": {
    "payload": "parsed_json",
    "parsed_attempt": "ok, 3 links, try 1",
    "html_bytes": 1868,
    "href_total": 0,
    "redirect_hrefs": 0,
    "nongoogle_hrefs": 0,
    "consent_phrase_present": false,
    "looks_like_consent_wall": false,
    "looks_like_captcha": false,
    "extraction_ok": true,
    "zero_reason": null
   },
   "business_mentioned": true,
   "organic_top_results": [
    "https://backflowboss.example/",
    "https://libertyplumbing.example/backflow",
    "https://kcbackflow.example/test"
   ],
   "ai_overview_detected": null,
   "ai_overview_present": true,
   "ai_overview_text": "",
   "ai_overview_references": [
    {
     "href": "https://backflowboss.example/",
     "title": "Backflow Boss",
     "source": "backflowboss.example"
    }
   ],
   "session_note": "Organic results extracted mechanically from the returned results page. Bright Data does not document an AI Overview field, so ai_overview_detected is BEST EFFORT and may be null. Do not publish it as a measurement.",
   "ai_overview_sha256": "2d0675d349934f1d211cd938c974109a8689349230d5fa0fba6c9a908589c3d1",
   "ai_overview_chars": 60,
   "ai_overview_private": true,
   "raw_policy": "R82 2026-10-24: raw text held in the private collector"
  },
  "private_ai_overview_text": "Backflow Boss and Liberty Plumbing test backflow assemblies.",
  "posts": 1
 },
 "no_pack": {
  "public": {
   "check_id": "NB-CZ-SERP_2026-10-31_backflow_boss",
   "date_utc": "2026-10-31",
   "business": "Backflow Boss",
   "niche": "backflow testing",
   "market": "Liberty, MO",
   "row_type": "business",
   "prompt_variant": 1,
   "run": 1,
   "prompt_text": "Who does backflow testing and repair near Liberty MO?",
   "engine": "google_serp",
   "run_channel": "serp",
   "schema_version": "v4",
   "run_status": "OK",
   "serp_diagnostics": {
    "payload": "parsed_json",
    "parsed_attempt": "ok, 3 links, try 1",
    "html_bytes": 930,
    "href_total": 0,
    "redirect_hrefs": 0,
    "nongoogle_hrefs": 0,
    "consent_phrase_present": false,
    "looks_like_consent_wall": false,
    "looks_like_captcha": false,
    "extraction_ok": true,
    "zero_reason": null
   },
   "business_mentioned": true,
   "organic_top_results": [
    "https://backflowboss.example/",
    "https://libertyplumbing.example/backflow",
    "https://kcbackflow.example/test"
   ],
   "ai_overview_detected": null,
   "ai_overview_present": true,
   "ai_overview_text": "",
   "ai_overview_references": [
    {
     "href": "https://backflowboss.example/",
     "title": "Backflow Boss",
     "source": "backflowboss.example"
    }
   ],
   "session_note": "Organic results extracted mechanically from the returned results page. Bright Data does not document an AI Overview field, so ai_overview_detected is BEST EFFORT and may be null. Do not publish it as a measurement.",
   "ai_overview_sha256": "2d0675d349934f1d211cd938c974109a8689349230d5fa0fba6c9a908589c3d1",
   "ai_overview_chars": 60,
   "ai_overview_private": true,
   "raw_policy": "R82 2026-10-24: raw text held in the private collector"
  },
  "private_ai_overview_text": "Backflow Boss and Liberty Plumbing test backflow assemblies.",
  "posts": 1
 }
}''')


def without_new(row):
    return {k: v for k, v in row.items() if k not in NEW_KEYS}


# ------------------------------------------------------------------ the reader
def test_pack_present_cut_to_three_with_nulls():
    present, top = e1_runner.extract_local_pack(wrapped(True))
    assert present is True
    assert top == [
        {"position": 1, "name": "Backflow Boss", "link": "https://backflowboss.example/",
         "address": "100 Main St, Liberty, MO 64068", "rating": 4.8, "reviews": 312},
        {"position": 2, "name": "Liberty Plumbing", "link": None, "address": None,
         "rating": 4.5, "reviews": None},
        {"position": 3, "name": "KC Backflow Pros", "link": "https://kcbackflow.example/",
         "address": "9 Oak Ave", "rating": None, "reviews": 1204},
    ]
    assert len(top) == e1_runner.LOCAL_PACK_MAX == 3


def test_bare_object_payload_reads_the_same():
    inner = json.dumps(inner_payload(True))
    assert e1_runner.extract_local_pack(inner) == e1_runner.extract_local_pack(wrapped(True))


def test_no_snack_pack_is_false_and_empty():
    assert e1_runner.extract_local_pack(wrapped(False)) == (False, [])


def test_non_list_or_empty_snack_pack_is_false_and_empty():
    for bad in ({"a": 1}, "pack", 7, None, [], True):
        j = inner_payload(False)
        j["snack_pack"] = bad
        assert e1_runner.extract_local_pack(json.dumps(j)) == (False, []), repr(bad)


def test_not_parsed_json_is_none_and_empty():
    for payload in ("<html><body>raw page</body></html>", "", None, "[1, 2]", "42", "not json"):
        assert e1_runner.extract_local_pack(payload) == (None, []), repr(payload)


def test_non_object_items_are_skipped_and_keep_their_place():
    j = inner_payload(False)
    j["snack_pack"] = ["x", None, {"name": "Third"}, 5, {"name": "Fifth"}, {"name": "Sixth"},
                       {"name": "Seventh"}]
    present, top = e1_runner.extract_local_pack(json.dumps(j))
    assert present is True
    assert [(t["position"], t["name"]) for t in top] == [(3, "Third"), (5, "Fifth"), (6, "Sixth")]
    j["snack_pack"] = ["x", None, 5]
    assert e1_runner.extract_local_pack(json.dumps(j)) == (False, [])


def test_wrong_types_read_as_null_and_never_raise():
    j = inner_payload(False)
    j["snack_pack"] = [{"name": 5, "link": ["u"], "address": {"a": 1}, "rating": True,
                        "reviews_cnt": False},
                       {"name": "  ", "link": "", "address": "1 A St", "rating": "4.7",
                        "reviews_cnt": 12.0},
                       {"rating": "n/a", "reviews_cnt": "many"},
                       {"rating": float("nan"), "reviews_cnt": -3}]
    present, top = e1_runner.extract_local_pack(json.dumps(j))
    assert present is True
    assert top[0] == {"position": 1, "name": None, "link": None, "address": None,
                      "rating": None, "reviews": None}
    assert top[1] == {"position": 2, "name": None, "link": None, "address": "1 A St",
                      "rating": 4.7, "reviews": 12}
    assert top[2] == {"position": 3, "name": None, "link": None, "address": None,
                      "rating": None, "reviews": None}
    assert len(top) == 3
    j["snack_pack"] = [{"rating": float("nan"), "reviews_cnt": -3}]
    _, top = e1_runner.extract_local_pack(json.dumps(j))
    assert top[0]["rating"] is None and top[0]["reviews"] is None
    json.dumps(top, allow_nan=False)   # the row must stay valid JSON


# ------------------------------------------------------------------ the row
def check_unchanged_and_new(with_pack, name):
    tmp = tempfile.mkdtemp()
    try:
        pub, priv, posts = run_serp_once(wrapped(with_pack), tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    gold = GOLDEN[name]
    # the request: one POST, same endpoint, zone, format, country and Google URL as on main
    assert len(posts) == gold["posts"] == 1
    p = posts[0]
    assert p["url"] == engines_mod.BRIGHTDATA_URL == "https://api.brightdata.com/request"
    assert p["json"] == {"zone": "serp_api1", "url": EXPECTED_URL, "format": "json", "country": "us"}
    assert p["headers"]["Authorization"].startswith("Bearer ")
    # every key except the two new ones equals the old row, in the same order and byte for byte
    # once serialised the way write_row writes it. That covers organic_top_results and every
    # ai_overview_* value in the public copy, and the AI Overview text in the private copy.
    def ser(x):
        return json.dumps(x, indent=1, ensure_ascii=False)

    assert ser(without_new(pub)) == ser(gold["public"])
    assert priv["ai_overview_text"] == gold["private_ai_overview_text"]
    assert ser(pub["organic_top_results"]) == ser(gold["public"]["organic_top_results"])
    assert pub["organic_top_results"] != []
    ai_keys = [k for k in gold["public"] if k.startswith("ai_overview_")]
    assert "ai_overview_present" in ai_keys and "ai_overview_references" in ai_keys
    for k in ai_keys:
        assert ser(pub[k]) == ser(gold["public"][k]), k
    assert pub["ai_overview_present"] is True and pub["ai_overview_references"]
    assert pub["schema_version"] == gold["public"]["schema_version"]
    assert all(k in pub for k in NEW_KEYS)
    return pub


def test_row_with_snack_pack():
    pub = check_unchanged_and_new(True, "with_pack")
    assert pub["local_pack_present"] is True
    assert [t["position"] for t in pub["local_pack_top"]] == [1, 2, 3]
    assert [t["name"] for t in pub["local_pack_top"]] == ["Backflow Boss", "Liberty Plumbing",
                                                          "KC Backflow Pros"]
    assert pub["local_pack_top"][1] == {"position": 2, "name": "Liberty Plumbing", "link": None,
                                        "address": None, "rating": 4.5, "reviews": None}


def test_row_without_snack_pack():
    pub = check_unchanged_and_new(False, "no_pack")
    assert pub["local_pack_present"] is False
    assert pub["local_pack_top"] == []


def test_local_pack_stays_public_with_the_private_split_on():
    # R82 holds only the AI Overview text; the pack is public Google results and is not redacted.
    tmp = tempfile.mkdtemp()
    try:
        pub, priv, _ = run_serp_once(wrapped(True), tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert pub["ai_overview_text"] == "" and priv["ai_overview_text"] != ""
    assert pub["local_pack_top"] == priv["local_pack_top"] != []


def test_raw_html_fallback_row_reads_none_and_empty():
    old_cwd, old_fetch = os.getcwd(), e1_runner.fetch_serp
    tmp = tempfile.mkdtemp()
    os.chdir(tmp)
    e1_runner.fetch_serp = lambda q: (True, "<html><a href='https://a.example/'>a</a></html>",
                                      ["https://a.example/"], "")
    try:
        e1_runner.run_serp(BUSINESS_ROW, DATE)
        pub = json.load(open(os.path.join(tmp, "corpus/e1", CHECK_ID + ".json"), encoding="utf-8"))
    finally:
        e1_runner.fetch_serp = old_fetch
        os.chdir(old_cwd)
        shutil.rmtree(tmp, ignore_errors=True)
    assert pub["run_status"] == "OK"
    assert pub["local_pack_present"] is None and pub["local_pack_top"] == []


def test_failed_row_reads_none_and_empty():
    old_cwd, old_fetch = os.getcwd(), e1_runner.fetch_serp
    tmp = tempfile.mkdtemp()
    os.chdir(tmp)
    e1_runner.fetch_serp = lambda q: (False, "", [], "brightdata HTTP 500: boom")
    try:
        path, ok = e1_runner.run_serp(BUSINESS_ROW, DATE)
        pub = json.load(open(os.path.join(tmp, "corpus/e1", CHECK_ID + ".json"), encoding="utf-8"))
    finally:
        e1_runner.fetch_serp = old_fetch
        os.chdir(old_cwd)
        shutil.rmtree(tmp, ignore_errors=True)
    assert ok is False and pub["run_status"] == "FAILED"
    assert pub["local_pack_present"] is None and pub["local_pack_top"] == []
    assert pub["organic_top_results"] == [] and pub["ai_overview_present"] is None


def test_a_pack_read_error_never_costs_the_row():
    old_cwd, old_read = os.getcwd(), e1_runner.extract_local_pack
    tmp = tempfile.mkdtemp()
    os.chdir(tmp)

    def boom(_):
        raise RuntimeError("boom")

    e1_runner.extract_local_pack = boom
    try:
        posts = []
        old_post = engines_mod.requests.post
        engines_mod.requests.post = lambda url, **kw: (posts.append(url) or FakeResp(wrapped(True)))
        try:
            path, ok = e1_runner.run_serp(BUSINESS_ROW, DATE)
        finally:
            engines_mod.requests.post = old_post
        pub = json.load(open(os.path.join(tmp, "corpus/e1", CHECK_ID + ".json"), encoding="utf-8"))
    finally:
        e1_runner.extract_local_pack = old_read
        os.chdir(old_cwd)
        shutil.rmtree(tmp, ignore_errors=True)
    assert ok is True and pub["run_status"] == "OK" and pub["organic_top_results"]
    assert pub["local_pack_present"] is None and pub["local_pack_top"] == []


# ------------------------------------------------------------------ the sources builder
def test_build_sources_does_not_flag_or_map_local_pack_top():
    tmp = tempfile.mkdtemp()
    try:
        pub, _, _ = run_serp_once(wrapped(True), tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert any("link" in t and t["link"] for t in pub["local_pack_top"])
    rows, unmapped = build_sources.extract(CHECK_ID + ".json", pub)
    assert unmapped == [], unmapped
    assert {r["field"] for r in rows} <= {"organic_top_results", "ai_overview_references"}
    assert not any(r["url"].startswith("https://kcbackflow.example/") and r["field"] == "local_pack_top"
                   for r in rows)


if __name__ == "__main__":
    if "--dump-golden" in sys.argv:
        dump_golden()
        sys.exit(0)
    fails = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:  # noqa: BLE001
                fails.append(name)
                print("FAIL", name, type(exc).__name__, exc)
    print("%d failed" % len(fails) if fails else "all passed")
    sys.exit(1 if fails else 0)
