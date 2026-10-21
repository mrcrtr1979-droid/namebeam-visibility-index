"""
LIVE TEST of the Anthropic batch mode on at most 3 roster questions.

Used only by .github/workflows/anthropic-batch-test.yml (workflow_dispatch). It
submits a real batch, waits for it, builds the rows with the collector's own
function (e1_runner.run_engine), and writes them ONLY to --out (the workflow
uploads that folder as a run artifact). It never writes to corpus/e1, never
commits, never pushes. It checks that nothing under corpus/ changed.

Also prints a schema comparison against the newest real live Anthropic row in
corpus/e1: same column set, same value types, same run_status vocabulary.

Run from the repo root:
    python3 scripts/anthropic_batch_test.py --out test_out --max-questions 3
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engines  # noqa: E402
import anthropic_batch as ab  # noqa: E402
import e1_runner  # noqa: E402

HARD_CAP = 3


def corpus_fingerprint():
    return sorted(glob.glob("corpus/**/*", recursive=True))


def newest_live_row():
    for path in sorted(glob.glob("corpus/e1/NB-CZ-API_*_anthropic.json"), reverse=True):
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        if d.get("run_status") == "OK":
            return path, d
    return None, None


def type_name(v):
    return type(v).__name__


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-questions", type=int, default=3)
    ap.add_argument("--deadline-minutes", type=int, default=20)
    ap.add_argument("--poll-seconds", type=int, default=30)
    a = ap.parse_args()
    n = max(1, min(a.max_questions, HARD_CAP))

    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        print("ERROR: ANTHROPIC_API_KEY is not set in this step (the workflow maps it from the repo secret).")
        return 2

    date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    before = corpus_fingerprint()
    roster = e1_runner.load_active_roster(e1_runner.ROSTER_PATH)
    rows = [r for r in roster if not (isinstance(r.get("engines"), list) and "anthropic" not in r["engines"])][:n]
    print("TEST date=%s model=%s questions=%d (cap %d)" % (date_utc, engines.ANTHROPIC_MODEL, len(rows), HARD_CAP))
    for r in rows:
        print("  question: %s | %s" % (r["business"], r["prompt_1"]))

    out_dir = os.path.join(a.out, "e1")
    os.makedirs(out_dir, exist_ok=True)
    e1_runner.OUTPUT_DIR = out_dir
    # Same competitor extraction as the real run: read the gazetteer from the real corpus.
    e1_runner._GAZ_INDEX[out_dir] = e1_runner._gaz_rows("corpus/e1")

    t0 = time.time()
    by_row, summary = ab.prefetch(rows, date_utc, e1_runner.slugify,
                                  deadline_s=a.deadline_minutes * 60, poll_s=a.poll_seconds)
    if summary is None:
        print("ERROR: nothing was batched")
        return 2

    written = []
    for br in rows:
        pre = by_row.get(id(br))
        path, good = e1_runner.run_engine(br, "anthropic", engines.call_anthropic, date_utc, prefetched=pre)
        written.append((path, good, "batch" if pre is not None else "live_fallback"))
        print("[anthropic-batch] %s mode=%s ok=%s" % (os.path.basename(path),
                                                       "batch" if pre is not None else "live_fallback", good))
    elapsed = time.time() - t0

    # ---- report -------------------------------------------------------------
    rep = {
        "batch_id": summary["batch_id"],
        "created_at": summary["created_at"],
        "submitted": summary["submitted"],
        "final_request_counts": summary["counts"],
        "result_types": summary["types"],
        "used_from_batch": len(summary["outcomes"]),
        "asked_live_after_batch": len(summary["redo"]),
        "cancelled_at_deadline": summary["cancelled"],
        "fallback_reason": summary["fallback_reason"],
        "batch_elapsed_seconds": round(summary["elapsed_s"], 1),
        "total_elapsed_seconds": round(elapsed, 1),
        "rows": [{"file": os.path.basename(p), "ok": g, "mode": m} for p, g, m in written],
    }

    ref_path, ref = newest_live_row()
    schema = {"reference_live_row": ref_path, "match": None}
    if ref is not None and written:
        problems = []
        for p, g, m in written:
            row = json.load(open(p, encoding="utf-8"))
            if g:
                if set(row) != set(ref):
                    problems.append("%s: column set differs: only batch %s, only live %s"
                                    % (os.path.basename(p), sorted(set(row) - set(ref)), sorted(set(ref) - set(row))))
                for k in ref:
                    if k in row and k not in ("engine_meta",) and type_name(row[k]) != type_name(ref[k]) \
                            and row[k] is not None and ref[k] is not None:
                        problems.append("%s: type of %s differs" % (os.path.basename(p), k))
                em, rm = row.get("engine_meta") or {}, ref.get("engine_meta") or {}
                if set(em) != set(rm):
                    problems.append("%s: engine_meta keys differ: %s vs %s" % (os.path.basename(p), sorted(em), sorted(rm)))
                if row.get("run_status") != "OK":
                    problems.append("%s: run_status %s" % (os.path.basename(p), row.get("run_status")))
            else:
                if row.get("run_status") not in ("FAILED", "QUOTA_BLOCKED"):
                    problems.append("%s: failed row has run_status %s" % (os.path.basename(p), row.get("run_status")))
        schema["match"] = not problems
        schema["problems"] = problems
        ru, bu = (ref.get("engine_meta") or {}).get("usage") or {}, None
        for p, g, m in written:
            if g:
                bu = (json.load(open(p, encoding="utf-8")).get("engine_meta") or {}).get("usage")
                break
        schema["usage_keys_live"] = sorted(ru)
        schema["usage_keys_batch"] = sorted(bu or {})
    rep["schema_check"] = schema

    after = corpus_fingerprint()
    rep["corpus_untouched"] = (before == after)

    with open(os.path.join(a.out, "batch_test_report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1)
    print("\n===== BATCH TEST REPORT =====")
    print(json.dumps(rep, indent=1))

    bad = (not rep["corpus_untouched"]) or schema.get("match") is False or not summary["batch_id"]
    if bad:
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
