"""Offline tests for tools/gold_scorer.py. Run: py -3 tools/test_gold_scorer.py"""
import csv
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gold_scorer as gs  # noqa: E402


class Normalise(unittest.TestCase):
    def test_case_punctuation_and_curly_quotes(self):
        self.assertEqual(gs.normalize("Arnold ’s  Plumbing, LLC."), "arnold s plumbing llc")
        self.assertEqual(gs.normalize("Sutliff & Stout"), "sutliff & stout")
        self.assertEqual(gs.normalize("  "), "")

    def test_dash_variants_match(self):
        self.assertEqual(gs.normalize("Rescue–Rooter"), gs.normalize("Rescue-Rooter"))


class ScoreRow(unittest.TestCase):
    def test_exact_match_after_normalisation(self):
        self.assertEqual(gs.score_row(["Valvoline."], ["valvoline"]), (1, 0, 0))

    def test_miss_and_extra(self):
        self.assertEqual(gs.score_row(["Acme", "Other"], ["Acme", "Bode"]), (1, 1, 1))

    def test_duplicate_of_matched_gold_is_fp(self):
        self.assertEqual(gs.score_row(["Acme", "Acme"], ["Acme"]), (1, 1, 0))

    def test_blank_predicted_names_are_ignored(self):
        self.assertEqual(gs.score_row(["", "  "], ["Acme"]), (0, 0, 1))


class Aggregate(unittest.TestCase):
    def test_ratios_and_na(self):
        self.assertEqual(gs.ratio(1, 4), "0.250")
        self.assertEqual(gs.ratio(0, 0), "N/A")

    def test_all_rows_including_missing_prediction(self):
        gold = {("R01", "openai"): ["Acme", "Bode"], ("R02", "gemini"): ["Valvoline"]}
        pred = {("R01", "openai"): ["Acme", "Junk"]}
        res = gs.score_all(gold, pred)
        self.assertEqual((res["gold_n"], res["tp"], res["fp"], res["fn"]), (3, 1, 1, 2))
        self.assertEqual(res["precision"], "0.500")
        self.assertEqual(res["recall"], "0.333")

    def test_no_predictions_recall_zero_precision_na(self):
        res = gs.score_all({("R01", "openai"): ["Acme"]}, {})
        self.assertEqual((res["precision"], res["recall"]), ("N/A", "0.000"))


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.gold = os.path.join(self.d, "gold.csv")
        self.pred = os.path.join(self.d, "pred.csv")
        self.out = os.path.join(self.d, "GOLD_SCORE.csv")
        with open(self.gold, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["row_id", "engine", "gold_name"])
            w.writerow(["R01", "openai", "Acme"])
            w.writerow(["R01", "openai", "Bode"])
        with open(self.pred, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["row_id", "engine", "predicted_names"])
            w.writerow(["R01", "openai", "Acme;Junk"])

    def test_missing_gold_writes_nothing(self):
        rc = gs.main(["--gold", os.path.join(self.d, "nope.csv"), "--predicted", self.pred,
                      "--out", self.out, "--date", "2026-10-28"])
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(self.out))

    def test_one_line_per_run_with_header_once(self):
        for day in ("2026-10-28", "2026-10-29"):
            self.assertEqual(gs.main(["--gold", self.gold, "--predicted", self.pred, "--out", self.out,
                                      "--date", day, "--run-id", "r"]), 0)
        rows = list(csv.reader(open(self.out, encoding="utf-8")))
        self.assertEqual(rows[0][0], "date_utc")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][2:6], ["2", "1", "1", "1"])
        self.assertEqual(rows[1][6:8], ["0.500", "0.500"])


if __name__ == "__main__":
    unittest.main()
