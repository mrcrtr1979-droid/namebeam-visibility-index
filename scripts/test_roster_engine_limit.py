"""
Self-test for the optional per-entry limits in roster/e1_roster.json:

    "engines": ["perplexity"]   ask only these engines for this entry
    "serp": false               do not read the Google results page for this entry

An entry with neither key behaves exactly as before. Offline: the engine
functions and the SERP reader are fakes, the roster and output are in a temp dir.

Run from the repo root:    python3 scripts/test_roster_engine_limit.py
Exits 0 only when every case passes.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for k in ("PERPLEXITY", "GEMINI", "OPENAI", "ANTHROPIC", "BRIGHTDATA"):
    os.environ[k + "_API_KEY"] = "test-dummy-key"

import e1_runner  # noqa: E402

CALLS = []


def fake(engine):
    def fn(prompt):
        CALLS.append((engine, prompt))
        return True, "Top picks: **Alpha Realty** and **Beta Homes** serve this area.", [], ""
    return fn


def entry(name, **extra):
    d = {"business": name, "variants_business_name_forms": [name], "niche": "test niche",
         "market": "Test Town", "prompt_1": "prompt for " + name, "active": True,
         "domain": "", "domain_confidence": "NONE", "domain_alternates": [], "domain_evidence": "test"}
    d.update(extra)
    return d


def run(entries):
    del CALLS[:]
    serp = []
    e1_runner.ENGINES = {k: fake(k) for k in ("perplexity", "gemini", "openai", "anthropic")}
    e1_runner.run_serp = lambda br, d: (serp.append(br["business"]) or ("", True))
    tmp = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(tmp)
    try:
        os.makedirs("roster")
        json.dump({"roster": entries}, open("roster/e1_roster.json", "w"))
        e1_runner.ROSTER_PATH = "roster/e1_roster.json"
        e1_runner.OUTPUT_DIR = os.path.join(tmp, "corpus")
        e1_runner.main()
        files = sorted(os.listdir(e1_runner.OUTPUT_DIR)) if os.path.isdir(e1_runner.OUTPUT_DIR) else []
    finally:
        os.chdir(old)
    return list(CALLS), serp, files


fails = []


def check(label, ok):
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        fails.append(label)


calls, serp, files = run([entry("Plain Co"), entry("Limited Co", engines=["perplexity"], serp=False)])
plain = sorted(e for e, p in calls if p == "prompt for Plain Co")
lim = sorted(e for e, p in calls if p == "prompt for Limited Co")
check("an entry with no limit asks all four engines", plain == ["anthropic", "gemini", "openai", "perplexity"])
check("an entry limited to perplexity asks only perplexity", lim == ["perplexity"])
check("serp is read for the plain entry", "Plain Co" in serp)
check("serp is skipped when serp is false", "Limited Co" not in serp)
check("plain entry writes 4 API rows and 1 AGREE row", sum(f.startswith("NB-CZ-API") and "plain_co" in f for f in files) == 4
      and sum(f.startswith("NB-CZ-AGREE") and "plain_co" in f for f in files) == 1)
check("limited entry writes 1 API row and no AGREE row", sum(f.startswith("NB-CZ-API") and "limited_co" in f for f in files) == 1
      and not any(f.startswith("NB-CZ-AGREE") and "limited_co" in f for f in files))
calls, serp, files = run([entry("Odd Co", engines=[])])
check("an empty engines list asks nothing for that entry (explicit, not 'all')", calls == [])
print("SELFTEST " + ("PASS" if not fails else "FAIL %d" % len(fails)))
sys.exit(0 if not fails else 1)
