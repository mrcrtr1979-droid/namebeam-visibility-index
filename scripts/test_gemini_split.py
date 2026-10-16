"""Offline self-test for the Gemini free-tier split (model choice, key fallback, pacing).
Run from the repo root: python3 scripts/test_gemini_split.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["GEMINI_API_KEY"] = "paid-dummy-0001"
os.environ.pop("GEMINI_API_KEY_FREE", None)

import engines  # noqa: E402

fails = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


class R:
    status_code = 200
    text = ""

    def json(self):
        return {"modelVersion": "served-x", "candidates": [{"content": {"parts": [{"text": "A"}]}}]}


calls = []


def post(url, **k):
    calls.append(url)
    return R()


panel = engines._load_gemini_panel()
check("panel file has exactly 20 ids", len(panel) == 20)
pid = sorted(panel)[0]
engines.call_gemini("q", _post=post, _sleep=lambda s: None, question_id=pid)
check("panel id -> gemini-3.6-flash (url and requested_model)",
      "models/gemini-3.6-flash:" in calls[-1] and engines.LAST_CALL_META["gemini"]["requested_model"] == "gemini-3.6-flash")
check("served_model still recorded", engines.LAST_CALL_META["gemini"]["served_model"] == "served-x")
engines.call_gemini("q", _post=post, _sleep=lambda s: None, question_id="not_in_panel")
check("other id -> gemini-3.5-flash-lite",
      "models/gemini-3.5-flash-lite:" in calls[-1] and engines.LAST_CALL_META["gemini"]["requested_model"] == "gemini-3.5-flash-lite")

engines.call_gemini("q", _post=post, _sleep=lambda s: None, question_id="x")
check("key falls back to GEMINI_API_KEY when FREE unset", "key=paid-dummy-0001" in calls[-1])
os.environ["GEMINI_API_KEY_FREE"] = "   "
engines.call_gemini("q", _post=post, _sleep=lambda s: None, question_id="x")
check("key falls back when FREE is empty/whitespace", "key=paid-dummy-0001" in calls[-1])
os.environ["GEMINI_API_KEY_FREE"] = "free-dummy-0002"
engines.call_gemini("q", _post=post, _sleep=lambda s: None, question_id="x")
check("GEMINI_API_KEY_FREE preferred when set", "key=free-dummy-0002" in calls[-1])

# pacing with a fake clock: 3.6 flash 12 s apart, flash-lite ~4.29 s apart
engines._gemini_last_call.clear()
t = [1000.0]
slept = []


def fsleep(s):
    slept.append(s)
    t[0] += s


for _ in range(3):
    engines.call_gemini("q", _post=post, _sleep=fsleep, _now=lambda: t[0], question_id=pid)
check("3.6 flash paced to 5/min (two 12s sleeps)", len(slept) == 2 and all(abs(x - 12.0) < 1e-6 for x in slept))
slept.clear()
for _ in range(3):
    engines.call_gemini("q", _post=post, _sleep=fsleep, _now=lambda: t[0], question_id="y")
check("flash-lite paced to 14/min", len(slept) == 2 and all(abs(x - 60 / 14) < 1e-6 for x in slept))

print("ALL PASS" if not fails else "FAILED: " + ", ".join(fails))
sys.exit(1 if fails else 0)
