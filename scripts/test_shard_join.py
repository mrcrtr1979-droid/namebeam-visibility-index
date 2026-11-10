#!/usr/bin/env python3
"""Selftest for scripts/shard_join.py (LB-29, 2026-11-10). Offline, writes only under a temp dir.

    python3 -I scripts/test_shard_join.py

Proves: rows copy byte for byte; an existing check_id is never overwritten; rows of another date are
refused; q2 rows go to their own folder; the agreement row is built from two OK engine rows and carries
the pair; a Perplexity row without private text is left out and reported; with the private copy it
takes part; --dry-run writes nothing; a FAILED row never enters the agreement.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shard_join  # noqa: E402

DATE = "2026-11-11"
BIZ = "Alpha Realty"


def row(engine, status="OK", text="", private=False, variant=1):
    cid = "NB-CZ-API_%s_%s_%s" % (DATE, shard_join.slugify(BIZ), engine)
    if variant == 2:
        cid = "NB-CZ-Q2API_%s_%s_%s" % (DATE, shard_join.slugify(BIZ), engine)
    r = {"check_id": cid, "date_utc": DATE, "business": BIZ, "engine": engine, "run_status": status,
         "prompt_variant": variant, "run_channel": "api", "row_type": "business",
         "answer_verbatim": "" if private else text, "business_mentioned": True,
         "competitors_mentioned": ["Beta Homes"], "sources_cited": []}
    if private:
        r["answer_private"] = True
    return r


def write(dirpath, name, doc):
    os.makedirs(dirpath, exist_ok=True)
    p = os.path.join(dirpath, name + ".json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
    return p


def fixture(td, perplexity_private_text=None):
    shards = os.path.join(td, "shards")
    o = row("openai", text="Top picks: **Beta Homes** and **Gamma Group** serve this area.")
    g = row("gemini", text="Top picks: **Beta Homes** and **Delta LLC** serve this area.")
    a = row("anthropic", status="FAILED")
    p = row("perplexity", private=True)
    write(os.path.join(shards, "openai", "dry_run_out", "corpus_e1"), o["check_id"], o)
    write(os.path.join(shards, "gemini", "dry_run_out", "corpus_e1"), g["check_id"], g)
    write(os.path.join(shards, "anthropic", "dry_run_out", "corpus_e1"), a["check_id"], a)
    write(os.path.join(shards, "perplexity", "dry_run_out", "corpus_e1"), p["check_id"], p)
    q2 = row("openai", text="x", variant=2)
    write(os.path.join(shards, "openai", "dry_run_out", "corpus_e1_q2"), q2["check_id"], q2)
    serp = {"check_id": "NB-CZ-SERP_%s_%s" % (DATE, shard_join.slugify(BIZ)), "date_utc": DATE, "business": BIZ,
            "engine": "google_serp", "run_status": "OK", "run_channel": "serp", "row_type": "business"}
    write(os.path.join(shards, "serp", "dry_run_out", "corpus_e1"), serp["check_id"], serp)
    roster = os.path.join(td, "roster.json")
    with open(roster, "w", encoding="utf-8") as fh:
        json.dump({"roster": [{"business": BIZ, "active": True, "prompt_1": "Who is the best realtor?"}]}, fh)
    private = None
    if perplexity_private_text is not None:
        private = os.path.join(td, "private")
        full = dict(p)
        full["answer_verbatim"] = perplexity_private_text
        write(private, p["check_id"], full)
    return shards, roster, private


def main():
    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    with tempfile.TemporaryDirectory() as td:
        shards, roster, _ = fixture(td)
        corpus, q2 = os.path.join(td, "corpus", "e1"), os.path.join(td, "corpus", "e1_q2")
        rep = shard_join.join(shards, roster, DATE, corpus, q2, None, dry_run=True)
        check(not os.path.exists(corpus), "dry run must write nothing")
        check(rep["copied"] == 5 and rep["copied_q2"] == 1, "dry run counts: %r" % rep)
        check(rep["agreement_rows"] == 1, "dry run builds one agreement row in memory")

        rep = shard_join.join(shards, roster, DATE, corpus, q2, None)
        check(len(os.listdir(corpus)) == 6, "5 public rows + 1 agreement row in corpus: %r" % os.listdir(corpus))
        check(len(os.listdir(q2)) == 1, "q2 row in its own folder")
        src = os.path.join(shards, "openai", "dry_run_out", "corpus_e1", row("openai")["check_id"] + ".json")
        dst = os.path.join(corpus, row("openai")["check_id"] + ".json")
        check(open(src, "rb").read() == open(dst, "rb").read(), "copy is byte for byte")
        ag_path = os.path.join(corpus, "NB-CZ-AGREE_%s_%s.json" % (DATE, shard_join.slugify(BIZ)))
        check(os.path.isfile(ag_path), "agreement row named like the serial runner")
        ag = json.load(open(ag_path, encoding="utf-8"))
        check(ag["engines_compared"] == ["gemini", "openai"], "FAILED anthropic and textless perplexity left out: %r" % ag["engines_compared"])
        check(len(ag["pairs"]) == 1 and ag["pairs"][0]["shared_count"] >= 1, "one pair with a shared name: %r" % ag["pairs"])
        check(rep["perplexity_without_text"] == 1, "textless Perplexity row reported")

        rep2 = shard_join.join(shards, roster, DATE, corpus, q2, None)
        check(rep2["copied"] == 0 and len(rep2["skipped_existing"]) == 6, "second join refuses every existing row: %r" % rep2["skipped_existing"])
        check(len(os.listdir(corpus)) == 7, "a second join adds only an _r2 agreement row, never overwrites")
        check(os.path.isfile(os.path.join(corpus, "NB-CZ-AGREE_%s_%s_r2.json" % (DATE, shard_join.slugify(BIZ)))), "_r2 agreement id")

        rep3 = shard_join.join(shards, roster, "2026-11-12", os.path.join(td, "c2"), os.path.join(td, "q"), None, dry_run=True)
        check(rep3["copied"] == 0 and len(rep3["wrong_date"]) == 5, "rows of another date are refused")

    with tempfile.TemporaryDirectory() as td:
        shards, roster, private = fixture(td, perplexity_private_text="Top picks: **Beta Homes** and **Epsilon Group** serve this area.")
        corpus, q2 = os.path.join(td, "corpus", "e1"), os.path.join(td, "corpus", "e1_q2")
        rep = shard_join.join(shards, roster, DATE, corpus, q2, private)
        ag = json.load(open(os.path.join(corpus, "NB-CZ-AGREE_%s_%s.json" % (DATE, shard_join.slugify(BIZ))), encoding="utf-8"))
        check(ag["engines_compared"] == ["gemini", "openai", "perplexity"], "with the private copy Perplexity takes part: %r" % ag["engines_compared"])
        check(len(ag["pairs"]) == 3, "three pairs")
        check(rep["perplexity_without_text"] == 0, "no textless note with the private copy")
        pub = json.load(open(os.path.join(corpus, row("perplexity")["check_id"] + ".json"), encoding="utf-8"))
        check(pub["answer_verbatim"] == "" and pub.get("answer_private") is True, "the public Perplexity row stays redacted after the join")

    if fails:
        print("SELFTEST FAILED:")
        for f in fails:
            print("  - " + f)
        return 1
    print("SELFTEST PASSED: byte copies, no overwrite, other-date refusal, q2 folder, agreement from OK rows only, "
          "Perplexity in only with its private text, dry run writes nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
