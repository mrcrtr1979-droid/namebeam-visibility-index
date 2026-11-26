"""Check the Namebeam roofing table and OpenAI split against the release CSV."""
import csv
import re
import sys
from datetime import date
from pathlib import Path

EXPECTED = {
    "reviews": (409, "97.4"),
    "license": (391, "93.1"),
    "insurance": (413, "98.3"),
    "warranty": (386, "91.9"),
    "estimate": (383, "91.2"),
    "years": (315, "75.0"),
}
HEADER = ["answer_id", "date", "engine", "recorded_model", *EXPECTED]
START, SWITCH, END = "2026-09-27", "2026-11-10", "2026-11-22"


def compare(label, rows, field, expected_count, expected_total, expected_pct):
    total = len(rows)
    count = sum(row[field] == "1" for row in rows)
    pct = f"{100 * count / total:.1f}" if total else "n/a"
    match = (count, total, pct) == (expected_count, expected_total, expected_pct)
    verdict = "MATCH" if match else "MISMATCH"
    print(f"{verdict} {label}: {count}/{total} ({pct}%) | "
          f"expected {expected_count}/{expected_total} ({expected_pct}%)")
    return match


def main(path):
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if reader.fieldnames != HEADER:
            raise ValueError("unexpected CSV header")
        rows = list(reader)

    ids = set()
    for line, row in enumerate(rows, 2):
        if None in row or None in row.values():
            raise ValueError(f"wrong field count on line {line}")
        answer_id = row["answer_id"]
        day = row["date"]
        if not re.fullmatch(r"[0-9a-f]{64}", answer_id) or answer_id in ids:
            raise ValueError(f"invalid or duplicate answer_id on line {line}")
        ids.add(answer_id)
        if date.fromisoformat(day).isoformat() != day or not START <= day <= END:
            raise ValueError(f"date outside the sample window on line {line}")
        if row["engine"] not in {"openai", "gemini", "anthropic"}:
            raise ValueError(f"invalid engine on line {line}")
        if not row["recorded_model"] or any(row[key] not in {"0", "1"} for key in EXPECTED):
            raise ValueError(f"missing model label or invalid flag on line {line}")

    checks = [compare(key, rows, key, count, 420, pct)
              for key, (count, pct) in EXPECTED.items()]
    openai = [row for row in rows if row["engine"] == "openai"]
    before = [row for row in openai if row["date"] < SWITCH]
    after = [row for row in openai if row["date"] >= SWITCH]
    checks.append(compare("OpenAI reviews through 2026-11-09", before, "reviews", 113, 114, "99.1"))
    checks.append(compare("OpenAI reviews from 2026-11-10", after, "reviews", 33, 39, "84.6"))
    return 0 if all(checks) else 1


if __name__ == "__main__":
    if len(sys.argv) > 2:
        sys.exit("Usage: python verify_roofing.py [roofing_sample.csv]")
    path = Path(sys.argv[1]) if len(sys.argv) == 2 else Path(__file__).with_name("roofing_sample.csv")
    try:
        sys.exit(main(path))
    except (OSError, ValueError, csv.Error) as error:
        sys.exit(f"MISMATCH CSV: {error}")
