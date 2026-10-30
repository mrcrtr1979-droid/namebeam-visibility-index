"""
Self-test for the second daily question ([q2], prereg/e1-q2-01.json): scripts/e1_runner.py,
roster/e1_q2.json.  Offline: engine functions and the SERP reader are fakes, corpus and roster live
in a temp dir, no network, no key is read.

    python3 scripts/test_e1_q2.py
    python3 -m pytest scripts/test_e1_q2.py -q

Covers: q2 rows land in their own folder and never in corpus/e1; prompt_variant and the check_id
prefix; no collision with a prompt_1 check_id or with the prompt_1 same-day rerun index; the roster
file validates against the real roster (market, niche, prompt_1, wording rules); the Gemini model
for a q2 id is flash-lite, never the 20-a-day panel model; q2 runs after the prompt_1 pass and is
skipped for Gemini once the Gemini rails were refused for quota, logged as QUOTA_BLOCKED; the kill
switch; a bad q2 file never touches prompt_1.  Version 2 (2026-10-30): the version 1 rows hash to the
sha256 registered in prereg/e1-q2-01.json, the version 2 rows are the agreed set and follow the same
wording frame.  Exits 0 only when every case passes.
"""
import copy
import glob
import hashlib
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
for k in ("PERPLEXITY", "GEMINI", "OPENAI", "ANTHROPIC", "BRIGHTDATA"):
    os.environ[k + "_API_KEY"] = "test-dummy-key"
for k in ("E1_ONLY_ENGINES", "E1_ONLY_FAILED", "E1_DATE_UTC", "E1_DISABLE_Q2", "PRIVATE_RAW"):
    os.environ.pop(k, None)

import e1_runner  # noqa: E402
import engines as engines_mod  # noqa: E402

ENGINE_NAMES = ("perplexity", "gemini", "openai", "anthropic")
CUT_SLUGS = ["atlanta-hvac", "atlanta-personal-injury", "chicago-personal-injury", "dallas-roofing",
             "denver-roofing", "houston-hvac", "houston-personal-injury",
             "los-angeles-ca-ai-marketing-agency", "miami-roofing", "phoenix-hvac"]
# Version 2 (2026-10-30): 21 segments not in version 1 plus the 20 research segments of PRs #66 and #67.
V2_CUT_SLUGS = ["atlanta-dentists", "dallas-dentists", "phoenix-dentists", "pleasanton-dentists",
                "kansas-city-health-insurance", "pleasanton-home-care", "pleasanton-moving-companies",
                "pleasanton-oil-change", "atlanta-orthodontists", "dallas-orthodontists",
                "phoenix-orthodontists", "dallas-personal-injury", "pleasanton-real-estate-agents",
                "branson-short-term-rental", "nashville-short-term-rental", "santa-cruz-short-term-rental",
                "charlotte-tanning-salons", "columbus-tanning-salons", "pleasanton-tanning-salons",
                "tyler-tanning-salons", "pleasanton-tire-shops", "miami-personal-injury",
                "phoenix-personal-injury", "denver-personal-injury", "charlotte-personal-injury",
                "chicago-dentists", "miami-dentists", "denver-dentists", "houston-dentists",
                "charlotte-dentists", "miami-orthodontists", "columbus-personal-injury",
                "kansas-city-personal-injury", "columbus-dentists", "kansas-city-dentists",
                "charlotte-orthodontists", "chicago-orthodontists", "denver-orthodontists",
                "houston-orthodontists", "columbus-orthodontists", "kansas-city-orthodontists"]
ANSWER = "Top picks: **Alpha Realty** and **Beta Homes** serve this area."

CALLS = []          # (engine, prompt, question_id) in call order
GEMINI_MODE = ["ok"]  # ok | quota


def fake(engine):
    def fn(prompt):
        CALLS.append((engine, prompt, engines_mod.CURRENT_QUESTION_ID[0]))
        if engine == "gemini" and GEMINI_MODE[0] == "quota":
            return False, "", [], "QUOTA: gemini HTTP 429 [GenerateRequestsPerDayPerProjectPerModel-FreeTier] tries=1: x"
        return True, ANSWER, [], ""
    return fn


def entry(name, p1, **extra):
    d = {"business": name, "variants_business_name_forms": [name], "niche": "test niche",
         "market": "Test Town TX", "prompt_1": p1, "active": True,
         "domain": "", "domain_confidence": "NONE", "domain_alternates": [], "domain_evidence": "test"}
    d.update(extra)
    return d


def q2doc(entries):
    return {"series": "e1_q2", "version": 1, "rows": [
        {"cut_slug": "cut-" + slugify(e["business"]), "business": e["business"], "market": e["market"],
         "niche": e["niche"], "prompt_1_ref": e["prompt_1"],
         "prompt_2": "I need help in %s. Which specific companies should I call for %s, and why?"
                     % (e1_runner._city_of(e["market"]).title(), e["niche"])}
        for e in entries]}


def slugify(n):
    return e1_runner.slugify(n)


def run(entries, q2=True, env=None, seed_files=None, gemini="ok", q2_doc=None):
    """Runs e1_runner.main() in a temp dir. Returns (calls, q1_files, q2_files, tmp, stdout)."""
    del CALLS[:]
    GEMINI_MODE[0] = gemini
    e1_runner.ENGINES = {k: fake(k) for k in ENGINE_NAMES}
    e1_runner.run_serp = lambda br, d: ("", True)
    tmp = tempfile.mkdtemp()
    old = os.getcwd()
    saved = {k: os.environ.get(k) for k in (env or {})}
    os.chdir(tmp)
    try:
        os.makedirs("roster")
        json.dump({"roster": entries}, open("roster/e1_roster.json", "w"))
        if q2:
            json.dump(q2_doc if q2_doc is not None else q2doc(entries), open("roster/e1_q2.json", "w"))
        for rel, data in (seed_files or {}).items():
            os.makedirs(os.path.dirname(rel) or ".", exist_ok=True)
            json.dump(data, open(rel, "w"))
        e1_runner.ROSTER_PATH = "roster/e1_roster.json"
        e1_runner.Q2_ROSTER_PATH = "roster/e1_q2.json"
        e1_runner.OUTPUT_DIR = os.path.join(tmp, "corpus", "e1")
        e1_runner.Q2_OUTPUT_DIR = os.path.join(tmp, "corpus", "e1_q2")
        os.environ.update(env or {})
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            e1_runner.main()
    finally:
        os.chdir(old)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    q1f = sorted(os.listdir(e1_runner.OUTPUT_DIR)) if os.path.isdir(e1_runner.OUTPUT_DIR) else []
    q2f = sorted(os.listdir(e1_runner.Q2_OUTPUT_DIR)) if os.path.isdir(e1_runner.Q2_OUTPUT_DIR) else []
    return list(CALLS), q1f, q2f, tmp, buf.getvalue()


fails = []


def check(label, ok):
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        fails.append(label)


def load(dirpath, name):
    return json.load(open(os.path.join(dirpath, name), encoding="utf-8"))


def real_roster():
    return e1_runner.load_active_roster(os.path.join(REPO, "roster", "e1_roster.json"))


def real_q2():
    return json.load(open(os.path.join(REPO, "roster", "e1_q2.json"), encoding="utf-8"))


# --- A. the real roster/e1_q2.json validates against the real roster -----------------------------
doc = real_q2()
problems = e1_runner.validate_q2(doc, real_roster())
check("A1 the real roster/e1_q2.json has no problems %s" % (problems or ""), problems == [])
v1_doc_rows = [r for r in doc["rows"] if r.get("version", 1) == 1]
v2_doc_rows = [r for r in doc["rows"] if r.get("version") == 2]
check("A2 version 1: ten rows, one per market cut, in the agreed slugs",
      sorted(r["cut_slug"] for r in v1_doc_rows) == sorted(CUT_SLUGS))
rows, probs = e1_runner.load_q2_rows(real_roster(), os.path.join(REPO, "roster", "e1_q2.json"))
check("A3 load_q2_rows returns every version 1 and version 2 row, each joined to exactly one active roster row",
      len(rows) == len(CUT_SLUGS) + len(V2_CUT_SLUGS) and probs == []
      and all(r.get("prompt_2") and r.get("prompt_1") for r in rows))
check("A4 every prompt_2 differs from its prompt_1", all(r["prompt_2"] != r["prompt_1"] for r in rows))
check("A5 every prompt_2 is ASCII, one line, under 25 words, no dash character",
      all(r["prompt_2"].isascii() and "\n" not in r["prompt_2"] and len(r["prompt_2"].split()) < 25
          and not re.search("[\u2013\u2014]", r["prompt_2"]) for r in rows))
check("A6 prompt_2 values are unique", len({r["prompt_2"] for r in rows}) == len(rows))
sp = {p["slug"]: p["questions"] for p in json.load(open(os.path.join(REPO, "roster", "segment_pages.json")))["pages"]}
check("A7 version 1: market and niche equal roster/segment_pages.json for every slug that file lists",
      all(sp[r["cut_slug"]] == [{"market": r["market"], "niche": r["niche"]}] for r in v1_doc_rows if r["cut_slug"] in sp))
check("A8 no prompt_2 names a brand or a roster business",
      not [r for r in rows if any(b in r["prompt_2"].lower() for b in ("namebeam", "trunkline", "atrix"))])
check("A9 none of the q2 businesses is engine-limited in the roster (every engine is asked)",
      all("engines" not in r for r in rows))

# --- A10 to A15: version 2 rows; version 1 stays exactly as registered ---------------------------------
prereg = json.load(open(os.path.join(REPO, "prereg", "e1-q2-01.json"), encoding="utf-8"))
reg_sha = {r["cut_slug"]: r["prompt_2_sha256"] for r in prereg["rows"]}
check("A10 version 1 untouched: each of the ten prompt_2 hashes to the sha256 registered in prereg/e1-q2-01.json",
      sorted(reg_sha) == sorted(CUT_SLUGS)
      and all(hashlib.sha256(r["prompt_2"].encode("utf-8")).hexdigest() == reg_sha[r["cut_slug"]]
              for r in v1_doc_rows))
check("A11 the file is version 2, the version 1 rows carry no version key, and the v2 block has a start_date key",
      doc.get("version") == 2 and all("version" not in r for r in v1_doc_rows)
      and isinstance(doc.get("v2"), dict) and "start_date" in doc["v2"])
check("A12 version 2: 41 rows marked version 2, in the agreed slugs, none repeating a version 1 slug or business",
      sorted(r["cut_slug"] for r in v2_doc_rows) == sorted(V2_CUT_SLUGS) and len(v2_doc_rows) == 41
      and not ({r["cut_slug"] for r in v2_doc_rows} & set(CUT_SLUGS))
      and not ({r["business"] for r in v2_doc_rows} & {r["business"] for r in v1_doc_rows})
      and len(v1_doc_rows) + len(v2_doc_rows) == len(doc["rows"]))
frame = re.compile(r"^[^.?!]+\. Which specific [A-Za-z ]+ should I call, and why\?$")
check("A13 paired wording: every prompt_2 is one situation sentence then 'Which specific X should I call, and why?'",
      all(frame.match(r["prompt_2"]) for r in doc["rows"]))
check("A14 version 2: market and niche equal roster/segment_pages.json for every slug that file lists",
      all({"market": r["market"], "niche": r["niche"]} in sp[r["cut_slug"]]
          for r in v2_doc_rows if r["cut_slug"] in sp))
check("A15 version 2: every row is a research_segment row of the active roster",
      all(r.get("row_type") == "research_segment" for r in rows if r["q2_cut_slug"] in V2_CUT_SLUGS))

# --- B. validation rejects what it must ------------------------------------------------------------
roster = real_roster()


def bad(mutator):
    d = copy.deepcopy(real_q2())
    mutator(d)
    return e1_runner.validate_q2(d, roster)


check("B1 unknown business rejected", bool(bad(lambda d: d["rows"][0].update(business="SEGMENT: HVAC, Nowhere"))))
check("B2 wrong market rejected", bool(bad(lambda d: d["rows"][0].update(market="Elsewhere TX"))))
check("B3 wrong niche rejected", bool(bad(lambda d: d["rows"][0].update(niche="plumbing"))))
check("B4 moved roster prompt_1 rejected", bool(bad(lambda d: d["rows"][0].update(prompt_1_ref="Who is best?"))))
check("B5 non-ASCII rejected", bool(bad(lambda d: d["rows"][0].update(
    prompt_2="I need a new air conditioner in Atlanta \u2014 which HVAC companies should I call?"))))
check("B6 25 words or more rejected", bool(bad(lambda d: d["rows"][0].update(
    prompt_2="I need a new air conditioner installed in Atlanta this month " + "really " * 12 + "which companies?"))))
check("B7 prompt_2 equal to prompt_1 rejected", bool(bad(lambda d: d["rows"][0].update(
    prompt_2=d["rows"][0]["prompt_1_ref"]))))
check("B8 a brand name rejected", bool(bad(lambda d: d["rows"][0].update(
    prompt_2="Is Namebeam the right pick in Atlanta? Which HVAC companies should I call?"))))
check("B9 missing city rejected", bool(bad(lambda d: d["rows"][0].update(
    prompt_2="I need a new air conditioner. Which HVAC companies should I call, and why?"))))
check("B10 repeated cut_slug rejected", bool(bad(lambda d: d["rows"][1].update(cut_slug=d["rows"][0]["cut_slug"]))))
check("B11 empty rows rejected", e1_runner.validate_q2({"rows": []}, roster) != [])
inactive = copy.deepcopy(roster)
for r in inactive:
    if r["business"] == "SEGMENT: HVAC, Atlanta":
        r["active"] = False
check("B12 an inactive roster row is rejected", e1_runner.validate_q2(real_q2(), inactive) != [])

# --- C. Gemini model choice for q2 ids ---------------------------------------------------------------
panel = engines_mod._load_gemini_panel()
q2_ids = [slugify(r["business"]) + "_q2" for r in rows]
check("C1 the continuity panel is not empty (test is meaningful)", len(panel) == 20)
check("C2 no q2 question id is on the 20-a-day panel", not [i for i in q2_ids if i in panel])
check("C3 every q2 id gets gemini-3.5-flash-lite, never gemini-3.6-flash",
      all(engines_mod.gemini_model_for(i, panel) == engines_mod.GEMINI_REST_MODEL for i in q2_ids))
check("C4 the same ten businesses as prompt_1 ids can be panel members (why the _q2 suffix matters)",
      any(slugify(r["business"]) in panel for r in rows))

# --- D. a normal day: separate folder, prefix, variant, ordering ------------------------------------
E2 = [entry("Alpha Co", "Who is the best test in Alpha?"), entry("Beta Co", "Who is the best test in Beta?")]
for e in E2:
    e["market"] = "Alpha TX" if e["business"] == "Alpha Co" else "Beta TX"
calls, q1f, q2f, tmp, out = run(E2)
q1dir, q2dir = os.path.join(tmp, "corpus", "e1"), os.path.join(tmp, "corpus", "e1_q2")
q1api = [f for f in q1f if f.startswith("NB-CZ-API_")]
check("D1 prompt_1 pass is unchanged: 4 API rows per business, plus AGREE and no q2 file in corpus/e1",
      len(q1api) == 8 and not [f for f in q1f if "Q2" in f])
check("D2 q2 writes 4 rows per business into corpus/e1_q2 and nothing else (no SERP, no AGREE)",
      len(q2f) == 8 and all(f.startswith("NB-CZ-Q2API_") for f in q2f))
check("D3 q2 file names use their own prefix, never NB-CZ-API_",
      not [f for f in q2f if f.startswith("NB-CZ-API_")] and not set(q1f) & set(q2f))
r2 = load(q2dir, q2f[0])
check("D4 q2 rows carry prompt_variant 2, series e1_q2, the prereg path, run 1",
      all(load(q2dir, f)["prompt_variant"] == 2 and load(q2dir, f)["series"] == "e1_q2"
          and load(q2dir, f)["prereg"] == "prereg/e1-q2-01.json" and load(q2dir, f)["run"] == 1 for f in q2f))
check("D5 every prompt_1 row still says prompt_variant 1 and has no series field",
      all(load(q1dir, f).get("prompt_variant") == 1 and "series" not in load(q1dir, f) for f in q1api))
check("D6 q2 prompt_text is prompt_2, q1_prompt_text is the roster prompt_1",
      r2["prompt_text"].startswith("I need help in") and r2["q1_prompt_text"].startswith("Who is the best test in"))
check("D7 a q2 row's check_id equals its file name and is in none of the prompt_1 names",
      all(load(q2dir, f)["check_id"] == f[:-5] and f[:-5] not in {x[:-5] for x in q1f} for f in q2f))
first_q2 = min(i for i, c in enumerate(calls) if c[1].startswith("I need help"))
last_q1 = max(i for i, c in enumerate(calls) if c[1].startswith("Who is the best"))
check("D8 every prompt_2 call comes after the last prompt_1 call", first_q2 > last_q1)
check("D9 q2 asks all four engines for each business (8 calls)",
      sorted(c[0] for c in calls if c[1].startswith("I need help")) == sorted(list(ENGINE_NAMES) * 2))
check("D10 the gemini q2 calls carry the _q2 question id (flash-lite), the q1 ones do not",
      all(c[2].endswith("_q2") for c in calls if c[0] == "gemini" and c[1].startswith("I need help"))
      and not any(c[2].endswith("_q2") for c in calls if c[1].startswith("Who is the best")))
check("D11 competitors extracted from a q2 answer like a q1 answer",
      "Alpha Realty" in r2["competitors_mentioned"])

# --- E. rerun index: a q2 row is never a rerun of q1, and q2 reruns number within q2 ----------------
date = e1_runner.datetime.now(e1_runner.timezone.utc).strftime("%Y-%m-%d")
seed_q1 = {"corpus/e1/NB-CZ-API_%s_alpha_co_perplexity.json" % date: {"check_id": "x", "run_status": "OK"}}
calls, q1f, q2f, tmp, out = run([E2[0]], seed_files=seed_q1)
q1dir, q2dir = os.path.join(tmp, "corpus", "e1"), os.path.join(tmp, "corpus", "e1_q2")
check("E1 with a q1 base file already present the q1 row becomes _r2 ...",
      "NB-CZ-API_%s_alpha_co_perplexity_r2.json" % date in q1f)
check("E2 ... while the q2 row for the same cell is still run 1 with no _rN suffix",
      "NB-CZ-Q2API_%s_alpha_co_perplexity.json" % date in q2f
      and load(q2dir, "NB-CZ-Q2API_%s_alpha_co_perplexity.json" % date)["run"] == 1)
seed_q2 = {"corpus/e1_q2/NB-CZ-Q2API_%s_alpha_co_perplexity.json" % date: {"check_id": "x", "run_status": "OK"}}
calls, q1f, q2f, tmp, out = run([E2[0]], seed_files=seed_q2)
check("E3 with only a q2 base file present, the q1 row is still run 1 (a q2 row is not a q1 rerun)",
      "NB-CZ-API_%s_alpha_co_perplexity.json" % date in q1f
      and "NB-CZ-API_%s_alpha_co_perplexity_r2.json" % date not in q1f)
check("E4 ... and the second q2 run of the cell is _r2 inside corpus/e1_q2",
      "NB-CZ-Q2API_%s_alpha_co_perplexity_r2.json" % date in q2f)
os.chdir(tmp)
prev = (e1_runner.OUTPUT_DIR, e1_runner.Q2_OUTPUT_DIR)
e1_runner.OUTPUT_DIR, e1_runner.Q2_OUTPUT_DIR = os.path.join(tmp, "corpus", "e1"), os.path.join(tmp, "corpus", "e1_q2")
cid1, n1 = e1_runner.next_run_index("2030-01-01", "zz", "gemini")
cid2, n2 = e1_runner.next_run_index("2030-01-01", "zz", "gemini", 2)
e1_runner.OUTPUT_DIR, e1_runner.Q2_OUTPUT_DIR = prev
os.chdir(HERE)
check("E5 next_run_index: variant 1 keeps NB-CZ-API_, variant 2 uses NB-CZ-Q2API_, same cell never equal",
      cid1 == "NB-CZ-API_2030-01-01_zz_gemini" and cid2 == "NB-CZ-Q2API_2030-01-01_zz_gemini" and n1 == n2 == 1)
check("E6 no NB-CZ-API_* glob over corpus/e1 or corpus/e1_q2 can match a q2 file",
      not any(f.startswith("NB-CZ-API_") for f in q2f)
      and not glob.fnmatch.fnmatch("NB-CZ-Q2API_2026-10-29_x_gemini.json", "NB-CZ-API_*.json")
      and not glob.fnmatch.fnmatch("NB-CZ-Q2API_2026-10-29_x_gemini.json", "NB-CZ-SERP_*")
      and not glob.fnmatch.fnmatch("NB-CZ-Q2API_2026-10-29_x_gemini.json", "NB-CZ-AGREE_*"))

# --- F. Gemini quota: q2 is not asked, logged as QUOTA_BLOCKED, prompt_1 untouched -----------------
calls, q1f, q2f, tmp, out = run(E2, gemini="quota")
q1dir, q2dir = os.path.join(tmp, "corpus", "e1"), os.path.join(tmp, "corpus", "e1_q2")
g2 = [f for f in q2f if f.endswith("_gemini.json")]
check("F1 prompt_1 Gemini calls were made (and refused): 2", sum(1 for c in calls if c[0] == "gemini" and c[1].startswith("Who is")) == 2)
check("F2 no prompt_2 call was sent to Gemini once the rails were refused",
      not [c for c in calls if c[0] == "gemini" and c[1].startswith("I need help")])
check("F3 the two skipped gemini q2 cells are still logged, as QUOTA_BLOCKED, with an empty answer",
      len(g2) == 2 and all(load(q2dir, f)["run_status"] == "QUOTA_BLOCKED" and load(q2dir, f)["answer_verbatim"] == ""
                           and load(q2dir, f)["business_mentioned"] is None for f in g2))
check("F4 the other three engines still answered q2 (OK)",
      all(load(q2dir, f)["run_status"] == "OK" for f in q2f if not f.endswith("_gemini.json")))
check("F5 the prompt_1 gemini rows are QUOTA_BLOCKED as before and nothing in corpus/e1 mentions q2",
      all(load(q1dir, f)["run_status"] == "QUOTA_BLOCKED" for f in q1f if f.endswith("_gemini.json"))
      and not any("q2" in open(os.path.join(q1dir, f)).read().lower() for f in q1f))
check("F6 the exhausted flag resets for the next run (a clean run asks gemini q2 again)",
      len([c for c in run(E2, gemini="ok")[0] if c[0] == "gemini" and c[1].startswith("I need help")]) == 2)

# --- G. kill switch, missing and broken q2 file never touch prompt_1 -------------------------------
calls, q1f, q2f, tmp, out = run(E2, env={"E1_DISABLE_Q2": "1"})
check("G1 E1_DISABLE_Q2=1: no q2 call, no corpus/e1_q2 folder, prompt_1 rows intact",
      q2f == [] and len([f for f in q1f if f.startswith("NB-CZ-API_")]) == 8
      and not os.path.isdir(os.path.join(tmp, "corpus", "e1_q2")))
calls, q1f, q2f, tmp, out = run(E2, q2=False)
check("G2 no roster/e1_q2.json: prompt_1 pass identical, nothing written for q2",
      q2f == [] and len([f for f in q1f if f.startswith("NB-CZ-API_")]) == 8 and "not found" in out)
calls, q1f, q2f, tmp, out = run(E2, q2_doc={"rows": [{"cut_slug": "x"}]})
check("G3 an invalid q2 file: logged, q2 skipped, prompt_1 rows intact",
      q2f == [] and "q2 problem" in out and len([f for f in q1f if f.startswith("NB-CZ-API_")]) == 8)
d3 = q2doc(E2)
d3["rows"][1]["prompt_1_ref"] = "A different wording"
calls, q1f, q2f, tmp, out = run(E2, q2_doc=d3)
check("G4 one invalid row is dropped and named; the valid row still runs",
      len(q2f) == 4 and all("alpha_co" in f for f in q2f) and "q2 problem" in out)

# --- H. rerun mode: q2 follows the same only-failed rule --------------------------------------------
fail_seed = {"corpus/e1_q2/NB-CZ-Q2API_2026-10-24_alpha_co_anthropic.json":
             {"check_id": "NB-CZ-Q2API_2026-10-24_alpha_co_anthropic", "run_status": "FAILED", "engine": "anthropic"},
             "corpus/e1/NB-CZ-API_2026-10-24_alpha_co_anthropic.json":
             {"check_id": "NB-CZ-API_2026-10-24_alpha_co_anthropic", "run_status": "FAILED", "engine": "anthropic"}}
calls, q1f, q2f, tmp, out = run([E2[0]], seed_files=fail_seed,
                                env={"E1_ONLY_ENGINES": "anthropic", "E1_ONLY_FAILED": "1", "E1_DATE_UTC": "2026-10-24"})
check("H1 only-failed rerun re-asks the failed q1 cell and the failed q2 cell, each as its own _r2",
      "NB-CZ-API_2026-10-24_alpha_co_anthropic_r2.json" in q1f
      and "NB-CZ-Q2API_2026-10-24_alpha_co_anthropic_r2.json" in q2f and len(calls) == 2)
check("H2 the rerun q2 row says what it supersedes",
      load(os.path.join(tmp, "corpus", "e1_q2"), "NB-CZ-Q2API_2026-10-24_alpha_co_anthropic_r2.json")
      .get("rerun_supersedes") == "NB-CZ-Q2API_2026-10-24_alpha_co_anthropic")

print()
print("SELFTEST PASS" if not fails else "SELFTEST FAIL: %d case(s): %s" % (len(fails), "; ".join(fails)))


def test_every_case_passes():   # pytest entry; the cases above run when the module is imported
    assert not fails, fails


if __name__ == "__main__":
    sys.exit(0 if not fails else 1)
