"""
Self-test for the LB-20 wall-clock guards in scripts/e1_runner.py. Fully offline: the engines
are fakes. Run from the repo root:

    python3 -I scripts/test_engine_deadline.py

Proves:
  (a) a hung engine times out at the per-call budget, its row is written FAILED with a TIMEOUT
      error and a timeout_s engine_meta, and the test returns in well under the hang time
  (b) a second engine in the same cell still writes an OK row (one hung engine never holds the day)
  (c) once the run budget is spent the cell is written FAILED with TIMEOUT_BUDGET and the engine
      is never called
  (d) E1_DRY_RUN redirects every output folder under dry_run_out/

Exits 0 only when every case passes.
"""
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["E1_CALL_BUDGET_S"] = "0.4"
os.environ["E1_RUN_BUDGET_MIN"] = "170"
os.environ["E1_DRY_RUN"] = "1"
os.environ["PRIVATE_RAW"] = "1"
WORK = tempfile.mkdtemp(prefix="e1_deadline_")
os.chdir(WORK)
os.makedirs("corpus/e1", exist_ok=True)
sys.path.insert(0, HERE)
import e1_runner as r  # noqa: E402

FAILS = []
CALLS = {"hung": 0, "good": 0, "late": 0}


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


def hung(prompt):
    CALLS["hung"] += 1
    time.sleep(3)
    return True, "never seen", [], ""


def good(prompt):
    CALLS["good"] += 1
    return True, "Acme Dental of Testville is often named here.", [], ""


def late(prompt):
    CALLS["late"] += 1
    return True, "should not run", [], ""


BR = {"business": "Acme Dental", "prompt_1": "Who is the best dentist in Testville?",
      "prompt_2": "Which dentist in Testville is recommended?", "market": "Testville",
      "niche": "dentists", "row_type": "business"}
DAY = "2026-11-09"

# (d) dry run redirect
check("dry run redirects OUTPUT_DIR", r.OUTPUT_DIR.startswith("dry_run_out/"))
check("dry run redirects PRIVATE_DIR", r.PRIVATE_DIR.startswith("dry_run_out/"))
check("dry run redirects Q2_OUTPUT_DIR", r.Q2_OUTPUT_DIR.startswith("dry_run_out/"))

# (a) hung engine
t0 = time.monotonic()
path_h, ok_h = r.run_engine(BR, "perplexity", hung, DAY)
el = time.monotonic() - t0
row_h = json.load(open(path_h, encoding="utf-8"))
check("hung engine returns within 2 s (took %.2f s)" % el, el < 2.0)
check("hung engine row is FAILED", (not ok_h) and row_h.get("run_status") == "FAILED")
check("hung engine row names TIMEOUT", "TIMEOUT: engine call exceeded" in row_h.get("session_note", ""))
check("hung engine row carries timeout_s meta", (row_h.get("engine_meta") or {}).get("timeout_s") == 0)
check("hung engine row is under dry_run_out", path_h.replace("\\", "/").startswith("dry_run_out/"))

# (b) the next engine still commits
path_g, ok_g = r.run_engine(BR, "openai", good, DAY)
row_g = json.load(open(path_g, encoding="utf-8"))
check("good engine row is OK after a hung sibling", ok_g and row_g.get("run_status") == "OK")
check("good engine was called once", CALLS["good"] == 1)

# (c) run budget exhausted
r._RUN_T0[0] = time.monotonic() - r.RUN_BUDGET_MIN * 60.0 - 5
path_l, ok_l = r.run_engine(BR, "anthropic", late, DAY)
row_l = json.load(open(path_l, encoding="utf-8"))
check("budget-exhausted cell is FAILED without a call", (not ok_l) and CALLS["late"] == 0)
check("budget-exhausted row names TIMEOUT_BUDGET", "TIMEOUT_BUDGET" in row_l.get("session_note", ""))
check("budget-exhausted row meta says skipped", "skipped" in (row_l.get("engine_meta") or {}))
check("budget helper is negative", r.run_budget_left_s() < 0)

n = len(FAILS)
os.chdir(HERE)
shutil.rmtree(WORK, ignore_errors=True)
print("ALL PASS" if not n else "%d FAILED: %s" % (n, FAILS))
sys.exit(1 if n else 0)
