"""Deterministic exact-match scorer for name extraction against a gold set.

Matching rule (from status/returns/1014D-A9-GOLD.md section 3): a predicted name counts as a true
positive when its normalised form equals a gold name's normalised form, one to one. A second
predicted copy of an already-matched gold name counts as a false positive. Precision = TP / (TP + FP)
over predicted names; recall = TP / gold count. N/A where the denominator is zero.

Inputs (CSV, UTF-8, header row):
  gold.csv        row_id, engine, gold_name      one row per gold name
  predicted.csv   row_id, engine, predicted_names   names separated by ';'

Output: one line appended to status/GOLD_SCORE.csv (date_utc, run_id, gold_n, tp, fp, fn, precision, recall, status).

Usage: python3 tools/gold_scorer.py --gold data/gold/gold_1014d_a9.csv --predicted <predicted.csv> \
         --out status/GOLD_SCORE.csv --date YYYY-MM-DD --run-id ID
Exits 1 with a plain message when the gold file is missing. It never writes a score without gold.
"""
import argparse
import csv
import os
import re
import sys
from collections import defaultdict

# Curly quotes, dashes and the apostrophe variants found in answer text, mapped to ASCII.
_PUNCT = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "‑": "-"}


def normalize(name):
    s = (name or "").strip()
    for k, v in _PUNCT.items():
        s = s.replace(k, v)
    s = s.lower()
    s = re.sub(r"[^a-z0-9& ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def score_row(predicted, gold):
    """predicted, gold: lists of names. Returns (tp, fp, fn) with one-to-one matching."""
    remaining = defaultdict(int)
    for g in gold:
        remaining[normalize(g)] += 1
    tp = fp = 0
    for p in predicted:
        key = normalize(p)
        if not key:
            continue
        if remaining.get(key, 0) > 0:
            remaining[key] -= 1
            tp += 1
        else:
            fp += 1
    fn = sum(remaining.values())
    return tp, fp, fn


def ratio(num, den):
    return "N/A" if den == 0 else "%.3f" % (num / den)


def load_gold(path):
    rows = defaultdict(list)
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rows[(r["row_id"], r["engine"])].append(r["gold_name"])
    return rows


def load_predicted(path):
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            names = [n.strip() for n in (r["predicted_names"] or "").split(";") if n.strip()]
            out[(r["row_id"], r["engine"])] = names
    return out


def score_all(gold, predicted):
    """Aggregate over every gold row. A gold row with no predicted entry scores all its gold as FN."""
    tp = fp = fn = 0
    gold_n = 0
    for key, gnames in gold.items():
        gold_n += len(gnames)
        t, f, n = score_row(predicted.get(key, []), gnames)
        tp, fp, fn = tp + t, fp + f, fn + n
    return {"gold_n": gold_n, "tp": tp, "fp": fp, "fn": fn,
            "precision": ratio(tp, tp + fp), "recall": ratio(tp, gold_n)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--predicted", required=True)
    ap.add_argument("--out", default="status/GOLD_SCORE.csv")
    ap.add_argument("--date", required=True)
    ap.add_argument("--run-id", default="local")
    a = ap.parse_args(argv)
    if not os.path.exists(a.gold):
        print("gold file missing: %s. No score written. The gold set must be committed first." % a.gold)
        return 1
    res = score_all(load_gold(a.gold), load_predicted(a.predicted))
    new = not os.path.exists(a.out)
    with open(a.out, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date_utc", "run_id", "gold_n", "tp", "fp", "fn", "precision", "recall", "status"])
        w.writerow([a.date, a.run_id, res["gold_n"], res["tp"], res["fp"], res["fn"],
                    res["precision"], res["recall"], "OK"])
    print("gold %d tp %d fp %d fn %d precision %s recall %s" % (
        res["gold_n"], res["tp"], res["fp"], res["fn"], res["precision"], res["recall"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
