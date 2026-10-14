"""
Self-test for the DQ-5 forward fix: e1_runner.extract_competitors returns the union of bold-span and
plain-text names, headings dropped, from runs dated 2026-10-14. Offline.

Run from the repo root:    python3 scripts/test_extractor_union.py
Exits 0 only when every case passes.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
for k in ("PERPLEXITY", "GEMINI", "OPENAI", "ANTHROPIC", "BRIGHTDATA"):
    os.environ[k + "_API_KEY"] = "test-dummy-key"
import e1_runner  # noqa: E402

FIX = os.path.join(HERE, "..", "tests", "fixtures", "extractor")


def load(name):
    d = json.load(open(os.path.join(FIX, name), encoding="utf-8"))
    return {"business": d["business"], "variants_business_name_forms": []}, d["answer_verbatim"]


FAILS = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


row, text = load("NB-CZ-API_2026-10-11_segment_oil_change_pleasanton_anthropic.json")
old = e1_runner.extract_competitors_old(row, text, [])
new = e1_runner.extract_competitors(row, text, [], "2026-10-14")
check("oil change: old rule misses Jiffy Lube", "Jiffy Lube" not in old)
check("oil change: union extracts Jiffy Lube", "Jiffy Lube" in new)
check("oil change: union has no duplicates", len(new) == len({n.casefold() for n in new}))
check("oil change: union keeps bold names (Yelp)", "Yelp" in new)

row, text = load("NB-CZ-API_2026-10-11_segment_roofing_denver_anthropic.json")
old = e1_runner.extract_competitors_old(row, text, [])
new = e1_runner.extract_competitors(row, text, [], "2026-10-14")
check("denver roofing: old rule records Red flags", "Red flags" in old)
check("denver roofing: union drops Red flags", "Red flags" not in new)
check("denver roofing: union drops Research methods", "Research methods" not in new)

row, text = load("NB-CZ-API_2026-10-11_segment_oil_change_pleasanton_anthropic.json")
check("date guard: 2026-10-13 uses the old rule",
      e1_runner.extract_competitors(row, text, [], "2026-10-13") == e1_runner.extract_competitors_old(row, text, []))
check("date guard: 2026-10-14 uses the union",
      "Jiffy Lube" in e1_runner.extract_competitors(row, text, [], "2026-10-14"))

p = {"business": "Zed Co", "variants_business_name_forms": []}
t = "Top picks: **Alpha Realty** and **Beta Homes**. Also try Gamma Roofing Services near you. Yelp and Airbnb list many."
u = e1_runner.extract_competitors_union(p, t, [])
check("plain-text junk (Yelp, Airbnb) kept out of plain names", "Yelp" not in u and "Airbnb" not in u)
check("bold names kept first", u[:2] == ["Alpha Realty", "Beta Homes"])

d = json.load(open(os.path.join(FIX, "NB-CZ-API_2026-10-01_segment_personal_injury_law_chicago_openai.json"), encoding="utf-8"))
row = {"business": d["business"], "variants_business_name_forms": []}
corpus = os.path.join(HERE, "..", "corpus", "e1")
gaz = e1_runner.build_gazetteer(d["market"], d["niche"], corpus, "2026-10-01")
no_gaz = e1_runner.extract_competitors(row, d["answer_verbatim"], [], "2026-10-14")
with_gaz = e1_runner.extract_competitors(row, d["answer_verbatim"], [], "2026-10-14", gaz)
check("Salvi fixture: text names Salvi", "salvi" in d["answer_verbatim"].lower())
check("Salvi fixture: recorded list on the day lacks Salvi", "salvi" not in str(d["competitors_mentioned"]).lower())
check("Salvi fixture: gazetteer (rows before 2026-10-01) holds Salvi", any("Salvi" in g for g in gaz))
check("Salvi fixture: union without gazetteer lacks Salvi", not any("Salvi" in n for n in no_gaz))
check("Salvi fixture: gazetteer pass adds Salvi", any("Salvi" in n for n in with_gaz))
check("gazetteer: whole-name only", e1_runner.gazetteer_matches("Salvini was here", ["Salvi"]) == [])
check("gazetteer: ordinary lowercase word not matched", e1_runner.gazetteer_matches("Taxes matter; pay taxes.", ["Taxes"]) == [])
check("gazetteer: capped", len(e1_runner.gazetteer_from_counts({"Acme " + chr(65 + i // 26) + chr(97 + i % 26) + "zz Plumbing": 5 for i in range(400)})) == e1_runner.GAZETTEER_CAP)

print("%d failed" % len(FAILS))
sys.exit(1 if FAILS else 0)
