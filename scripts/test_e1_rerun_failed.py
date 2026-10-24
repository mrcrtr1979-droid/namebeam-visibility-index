"""
Self-test for the re-run-only-failed-cells option of scripts/e1_runner.py
([SONNET-BEDROCK-EXPIRY-1024]): E1_ONLY_ENGINES, E1_ONLY_FAILED, E1_DATE_UTC.
Offline: engine functions and the SERP reader are fakes, corpus and roster live in a temp dir.

    python3 scripts/test_e1_rerun_failed.py
    python3 -m pytest scripts/test_e1_rerun_failed.py -q

Exits 0 only when every case passes.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for k in ("PERPLEXITY", "GEMINI", "OPENAI", "ANTHROPIC", "BRIGHTDATA"):
    os.environ[k + "_API_KEY"] = "test-dummy-key"
RERUN_ENV = ("E1_ONLY_ENGINES", "E1_ONLY_FAILED", "E1_DATE_UTC")

import e1_runner  # noqa: E402

DATE = "2026-10-24"
CALLS = []
ANTHROPIC_RESULT = [True]


def fake(engine):
    def fn(prompt):
        CALLS.append((engine, prompt))
        if engine == "anthropic" and not ANTHROPIC_RESULT[0]:
            return False, "", [], "anthropic bedrock failed: boom"
        return True, "Top picks: **Alpha Realty** and **Beta Homes** serve this area.", [], ""
    return fn


def entry(name):
    return {"business": name, "variants_business_name_forms": [name], "niche": "test niche",
            "market": "Test Town", "prompt_1": "prompt for " + name, "active": True,
            "domain": "", "domain_confidence": "NONE", "domain_alternates": [], "domain_evidence": "test"}


def row(slug, engine, status, suffix=""):
    cid = "NB-CZ-API_%s_%s_%s%s" % (DATE, slug, engine, suffix)
    return cid, {"check_id": cid, "date_utc": DATE, "engine": engine, "run_status": status,
                 "answer_verbatim": "orig" if status == "OK" else ""}


class Env:
    def __init__(self, env, seed):
        self.env, self.seed = env, seed

    def __enter__(self):
        self.old_cwd = os.getcwd()
        self.saved = {k: os.environ.get(k) for k in RERUN_ENV}
        for k in RERUN_ENV:
            os.environ.pop(k, None)
        os.environ.update(self.env)
        self.tmp = tempfile.mkdtemp()
        os.chdir(self.tmp)
        os.makedirs("roster")
        names = ["Biz Failed", "Biz Ok", "Biz FailedThenOk", "Biz NoRows"]
        json.dump({"roster": [entry(n) for n in names]}, open("roster/e1_roster.json", "w"))
        e1_runner.ROSTER_PATH = "roster/e1_roster.json"
        e1_runner.OUTPUT_DIR = os.path.join(self.tmp, "corpus")
        os.makedirs(e1_runner.OUTPUT_DIR)
        for cid, body in self.seed:
            json.dump(body, open(os.path.join(e1_runner.OUTPUT_DIR, cid + ".json"), "w"))
        e1_runner.ENGINES = {k: fake(k) for k in ("perplexity", "gemini", "openai", "anthropic")}
        self.serp = []
        e1_runner.run_serp = lambda br, d: (self.serp.append(br["business"]) or ("", True))
        del CALLS[:]
        ANTHROPIC_RESULT[0] = True
        return self

    def run(self):
        self.rc = e1_runner.main()
        return sorted(os.listdir(e1_runner.OUTPUT_DIR))

    def read(self, cid):
        return json.load(open(os.path.join(e1_runner.OUTPUT_DIR, cid + ".json")))

    def raw(self, cid):
        return open(os.path.join(e1_runner.OUTPUT_DIR, cid + ".json"), "rb").read()

    def __exit__(self, *a):
        os.chdir(self.old_cwd)
        for k in RERUN_ENV:
            os.environ.pop(k, None)
            if self.saved[k] is not None:
                os.environ[k] = self.saved[k]


def seed_corpus():
    s = []
    s.append(row("biz_failed", "anthropic", "FAILED"))
    s.append(row("biz_ok", "anthropic", "OK"))
    s.append(row("biz_failedthenok", "anthropic", "FAILED"))
    s.append(row("biz_failedthenok", "anthropic", "OK", "_r2"))
    s.append(row("biz_failed", "openai", "FAILED"))       # other engine, must not be touched
    return s


GOOD = {"E1_ONLY_ENGINES": "anthropic", "E1_ONLY_FAILED": "true", "E1_DATE_UTC": DATE}


def test_only_failed_cells_are_asked_and_failed_rows_are_kept():
    with Env(GOOD, seed_corpus()) as e:
        before = {c: e.raw(c) for c, _ in seed_corpus()}
        files = e.run()
        assert CALLS == [("anthropic", "prompt for Biz Failed")]
        assert e.serp == []
        new = sorted(set(files) - {c + ".json" for c in before})
        assert new == ["NB-CZ-API_%s_biz_failed_anthropic_r2.json" % DATE], new
        for c, raw in before.items():   # nothing existing was modified
            assert e.raw(c) == raw
        r2 = e.read("NB-CZ-API_%s_biz_failed_anthropic_r2" % DATE)
        assert r2["run_status"] == "OK" and r2["run"] == 2 and r2["date_utc"] == DATE
        assert r2["rerun_supersedes"] == "NB-CZ-API_%s_biz_failed_anthropic" % DATE
        assert r2["rerun_collected_at_utc"].endswith("Z") and len(r2["rerun_collected_at_utc"]) == 20


def test_repeat_dispatch_is_idempotent_and_a_second_failure_gets_r3():
    with Env(GOOD, seed_corpus()) as e:
        e.run()
        del CALLS[:]
        e.run()                       # the cell now has an OK row: nothing to re-ask
        assert CALLS == []
    with Env(GOOD, seed_corpus()) as e:
        ANTHROPIC_RESULT[0] = False
        e.run()                       # re-ask fails: logged FAILED _r2, still no OK
        assert e.read("NB-CZ-API_%s_biz_failed_anthropic_r2" % DATE)["run_status"] == "FAILED"
        ANTHROPIC_RESULT[0] = True
        del CALLS[:]
        files = e.run()               # asked again, lands as _r3
        assert CALLS == [("anthropic", "prompt for Biz Failed")]
        assert "NB-CZ-API_%s_biz_failed_anthropic_r3.json" % DATE in files
        assert e.read("NB-CZ-API_%s_biz_failed_anthropic_r3" % DATE)["run_status"] == "OK"


def test_date_defaults_to_today_when_omitted():
    today = e1_runner.datetime.now(e1_runner.timezone.utc).strftime("%Y-%m-%d")
    seed = [(c.replace(DATE, today), dict(b, date_utc=today)) for c, b in seed_corpus()]
    env = {"E1_ONLY_ENGINES": "anthropic", "E1_ONLY_FAILED": "1"}
    with Env(env, seed) as e:
        e.run()
        assert CALLS == [("anthropic", "prompt for Biz Failed")]


def test_validation_errors_write_and_ask_nothing():
    bad = [
        {"E1_ONLY_FAILED": "true"},                                          # needs engines
        {"E1_ONLY_ENGINES": "anthropic", "E1_DATE_UTC": DATE},               # date needs failed
        dict(GOOD, E1_DATE_UTC="2999-01-01"),                                # future
        dict(GOOD, E1_DATE_UTC="24-10-2026"),                                # bad format
        dict(GOOD, E1_DATE_UTC="2026-02-31"),                                # not a date
        dict(GOOD, E1_ONLY_ENGINES="anthropic,claudee"),                     # unknown engine
    ]
    for env in bad:
        with Env(env, seed_corpus()) as e:
            n_before = len(os.listdir(e1_runner.OUTPUT_DIR))
            files = e.run()
            assert CALLS == [] and len(files) == n_before and e.rc == 2, env


def test_only_engines_alone_asks_that_engine_for_every_business_and_skips_serp():
    with Env({"E1_ONLY_ENGINES": "anthropic"}, []) as e:
        e.run()
        assert sorted(c[0] for c in CALLS) == ["anthropic"] * 4
        assert e.serp == []
    with Env({"E1_ONLY_ENGINES": "anthropic,serp"}, []) as e:
        e.run()
        assert len(e.serp) == 4


def test_normal_run_unchanged_without_env():
    with Env({}, []) as e:
        e.run()
        assert len(CALLS) == 16 and len(e.serp) == 4   # 4 engines x 4 businesses + SERP each


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
