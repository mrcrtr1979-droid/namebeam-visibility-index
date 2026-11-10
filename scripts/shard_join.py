#!/usr/bin/env python3
"""
SHARD JOIN (LB-29, 2026-11-10): one commit from five parallel collector shards.

The production shard runner (.github/workflows/e1-shard-runner.yml) runs scripts/e1_runner.py once per
engine shard (perplexity, gemini, openai, anthropic, serp) with E1_DRY_RUN=1, so each shard's rows land
under its own dry_run_out/ and nothing is committed by a shard. The shards upload ONLY the public rows
(dry_run_out/corpus_e1 and dry_run_out/corpus_e1_q2) as artifacts; the private raw text (R82) is pushed
by the shard itself to the private Brain and is never an artifact of this public repository.

This script runs in the join job and does, in order:
  1. read every shard's public rows; refuse (and report) any row whose check_id already exists in the
     corpus, so a re-run can never overwrite a published row;
  2. copy the new rows byte for byte into corpus/e1 and corpus/e1_q2;
  3. build the DW-1 agreement rows (NB-CZ-AGREE_<date>_<slug>) exactly as the serial runner does, from
     the OK engine rows of each business on the day; a Perplexity row takes part only when its full text
     is present under --private (fetched from the private Brain in production), otherwise it is left out
     of the comparison and the report says so;
  4. print a report and write a JSON summary.

With --dry-run nothing is written anywhere; the report is the only output. Standard library plus the
repo's own scripts/agreement.py. Selftest:  python3 -I scripts/test_shard_join.py
"""
import argparse
import glob
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from agreement import build_agreement_row  # noqa: E402

ENGINES = ("perplexity", "gemini", "openai", "anthropic", "serp")


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower())
    return s.strip("_")


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def dump_row(row, path):
    """Same shape e1_runner.write_row uses: indent 1, UTF-8, non-ASCII kept."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(row, fh, indent=1, ensure_ascii=False)


def collect_shard_rows(shards_dir):
    """Returns (public_rows, q2_files, per_shard) where public_rows is a list of (shard, path, row)."""
    public, q2, per_shard = [], [], {}
    for shard in sorted(os.listdir(shards_dir)):
        base = os.path.join(shards_dir, shard, "dry_run_out")
        if not os.path.isdir(base):
            base = os.path.join(shards_dir, shard)
        files = sorted(glob.glob(os.path.join(base, "corpus_e1", "*.json")))
        q2_files = sorted(glob.glob(os.path.join(base, "corpus_e1_q2", "*.json")))
        per_shard[shard] = {"rows": len(files), "q2_rows": len(q2_files), "ok": 0, "failed": 0}
        for p in files:
            row = load_json(p)
            if row.get("run_status") == "OK":
                per_shard[shard]["ok"] += 1
            else:
                per_shard[shard]["failed"] += 1
            public.append((shard, p, row))
        for p in q2_files:
            q2.append((shard, p))
    return public, q2, per_shard


def load_roster(path):
    data = load_json(path)
    return {r.get("business"): r for r in data.get("roster", []) if r.get("active") is True}


def full_row(row, private_dir):
    """The row with its raw text restored from the private copy when one exists (R82)."""
    if not private_dir or not row.get("answer_private"):
        return row
    p = os.path.join(private_dir, row["check_id"] + ".json")
    if os.path.isfile(p):
        return load_json(p)
    return row


def next_agree_id(date_utc, slug, corpus_dir, taken):
    cid = "NB-CZ-AGREE_%s_%s" % (date_utc, slug)
    n = 2
    while cid in taken or os.path.exists(os.path.join(corpus_dir, cid + ".json")):
        cid = "NB-CZ-AGREE_%s_%s_r%d" % (date_utc, slug, n)
        n += 1
    return cid


def join(shards_dir, roster_path, date_utc, corpus_dir="corpus/e1", q2_dir="corpus/e1_q2",
         private_dir=None, dry_run=False):
    public, q2, per_shard = collect_shard_rows(shards_dir)
    roster = load_roster(roster_path)
    report = {"date": date_utc, "dry_run": dry_run, "shards": per_shard, "copied": 0, "copied_q2": 0,
              "skipped_existing": [], "wrong_date": [], "agreement_rows": 0, "agreement_skipped": [],
              "perplexity_without_text": 0, "engines_seen": sorted({r.get("engine") for _, _, r in public})}

    # 1 and 2: copy public rows, never over an existing check_id.
    for shard, path, row in public:
        name = os.path.basename(path)
        if row.get("date_utc") != date_utc:
            report["wrong_date"].append(name)
            continue
        dest = os.path.join(corpus_dir, name)
        if os.path.exists(dest):
            report["skipped_existing"].append(name)
            continue
        if not dry_run:
            os.makedirs(corpus_dir, exist_ok=True)
            shutil.copyfile(path, dest)
        report["copied"] += 1
    for shard, path in q2:
        name = os.path.basename(path)
        dest = os.path.join(q2_dir, name)
        if os.path.exists(dest):
            report["skipped_existing"].append(name)
            continue
        if not dry_run:
            os.makedirs(q2_dir, exist_ok=True)
            shutil.copyfile(path, dest)
        report["copied_q2"] += 1

    # 3: agreement rows from the OK engine rows (variant 1, API engines) of each business.
    by_business = {}
    for shard, path, row in public:
        if row.get("date_utc") != date_utc or row.get("row_type") == "agreement":
            continue
        if row.get("prompt_variant", 1) != 1 or row.get("run_channel") == "serp":
            continue
        if row.get("engine") in ("google_serp",) or os.path.basename(path).startswith("NB-CZ-SERP_"):
            continue
        if row.get("run_status") != "OK":
            continue
        by_business.setdefault(row.get("business"), []).append(full_row(row, private_dir))
    taken = set()
    for business, rows in sorted(by_business.items()):
        for r in rows:
            if r.get("engine") == "perplexity" and not r.get("answer_verbatim"):
                report["perplexity_without_text"] += 1
        usable = [r for r in rows if r.get("answer_verbatim")]
        if len(usable) < 2:
            report["agreement_skipped"].append(business)
            continue
        br = roster.get(business)
        if br is None:
            report["agreement_skipped"].append(business + " (not an active roster row)")
            continue
        ag = build_agreement_row(br, date_utc, rows)
        cid = next_agree_id(date_utc, slugify(business), corpus_dir, taken)
        taken.add(cid)
        ag["check_id"] = cid
        if not dry_run:
            dump_row(ag, os.path.join(corpus_dir, cid + ".json"))
        report["agreement_rows"] += 1
    return report


def print_report(rep):
    print("SHARD JOIN %s%s" % (rep["date"], " (DRY RUN, nothing written)" if rep["dry_run"] else ""))
    for shard, s in rep["shards"].items():
        print("  shard %-10s rows %4d (ok %d, failed %d), q2 rows %d" % (shard, s["rows"], s["ok"], s["failed"], s["q2_rows"]))
    print("  copied %d rows into corpus/e1, %d into corpus/e1_q2" % (rep["copied"], rep["copied_q2"]))
    print("  agreement rows built: %d; businesses without 2 usable answers: %d"
          % (rep["agreement_rows"], len(rep["agreement_skipped"])))
    if rep["perplexity_without_text"]:
        print("  NOTE: %d Perplexity rows had no private text here; they were left out of the agreement"
              % rep["perplexity_without_text"])
    if rep["skipped_existing"]:
        print("  REFUSED %d rows whose check_id already exists in the corpus (never overwritten): %s%s"
              % (len(rep["skipped_existing"]), ", ".join(rep["skipped_existing"][:5]),
                 " ..." if len(rep["skipped_existing"]) > 5 else ""))
    if rep["wrong_date"]:
        print("  REFUSED %d rows dated other than %s: %s" % (len(rep["wrong_date"]), rep["date"], ", ".join(rep["wrong_date"][:5])))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Join the E1 shard artifacts into one corpus commit.")
    ap.add_argument("--shards", required=True, help="directory holding one sub-directory per shard")
    ap.add_argument("--roster", default="roster/e1_roster.json")
    ap.add_argument("--date", required=True, help="run date YYYY-MM-DD (UTC)")
    ap.add_argument("--corpus", default="corpus/e1")
    ap.add_argument("--q2", default="corpus/e1_q2")
    ap.add_argument("--private", default=None, help="private_e1/<date> copy, for the agreement text (R82)")
    ap.add_argument("--json", default=None, help="write the summary here")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    a = ap.parse_args(argv)
    rep = join(a.shards, a.roster, a.date, a.corpus, a.q2, a.private, a.dry_run)
    print_report(rep)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
