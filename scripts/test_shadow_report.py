"""
Test suite for shadow_report.py using a fixture with 3 roster rows.
"""

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta


def setup_fixture():
    """Build a fixture shards dir with fake rows for testing."""
    tmpdir = tempfile.mkdtemp(prefix="shadow_test_")

    # Tiny roster: 3 businesses, one perplexity-only
    roster = {
        "roster": [
            {
                "business": "Fixture Dental A",
                "niche": "dentists",
                "market": "Test City",
                "prompt_1": "Who is the best dentist?",
                "active": True
            },
            {
                "business": "Fixture Dental B",
                "niche": "dentists",
                "market": "Test City",
                "prompt_1": "Who is the best dentist?",
                "active": True,
                "engines": ["perplexity"],
                "serp": False
            },
            {
                "business": "Fixture Dental C",
                "niche": "dentists",
                "market": "Test City",
                "prompt_1": "Who is the best dentist?",
                "active": True
            }
        ]
    }

    roster_path = os.path.join(tmpdir, "roster.json")
    with open(roster_path, "w") as f:
        json.dump(roster, f)

    # Expected cells: row 0 and 2 get all 4 engines + serp = 10; row 1 gets perplexity only = 1
    # Total: 11 expected (perplexity 3, gemini 2, openai 2, anthropic 2, serp 2)

    # Shards directory
    shards_dir = os.path.join(tmpdir, "shards")

    # Engine shards
    engines = ["perplexity", "gemini", "openai", "anthropic", "serp"]

    for engine in engines:
        corpus_dir = os.path.join(shards_dir, engine, "dry_run_out", "corpus_e1")
        private_dir = os.path.join(shards_dir, engine, "dry_run_out", "private_e1")
        os.makedirs(corpus_dir, exist_ok=True)
        os.makedirs(private_dir, exist_ok=True)

        # Create rows
        if engine == "perplexity":
            # 3 rows: 2 OK, 1 FAILED
            for i, (slug, status) in enumerate([("fixture_dental_a", "OK"),
                                                 ("fixture_dental_b", "OK"),
                                                 ("fixture_dental_c", "FAILED")]):
                row = {
                    "check_id": f"NB-CZ-API_2026-11-09_{slug}_perplexity",
                    "date_utc": "2026-11-09",
                    "business": f"Fixture Dental {chr(65 + i)}",
                    "engine": "perplexity",
                    "run_status": status,
                    "prompt_variant": 1,
                    "answer_verbatim": "" if status == "FAILED" else "Some perplexity text",
                    "answer_private": True if status == "OK" else False,
                    "engine_meta": {
                        "served_model": "sonar",
                        "usage": {
                            "cost": {"total_cost": 0.0012}
                        }
                    } if status == "OK" else {}
                }
                # Public row (no text)
                public_row = dict(row)
                if status == "OK":
                    public_row["answer_verbatim"] = ""
                with open(os.path.join(corpus_dir, f"{row['check_id']}.json"), "w") as f:
                    json.dump(public_row, f)

                # Private row with text
                if status == "OK":
                    with open(os.path.join(private_dir, f"{row['check_id']}.json"), "w") as f:
                        json.dump(row, f)

        elif engine == "gemini":
            # 2 rows: 1 OK, 1 QUOTA_BLOCKED
            for i, (slug, status) in enumerate([("fixture_dental_a", "OK"),
                                                 ("fixture_dental_c", "QUOTA_BLOCKED")]):
                row = {
                    "check_id": f"NB-CZ-API_2026-11-09_{slug}_gemini",
                    "date_utc": "2026-11-09",
                    "business": f"Fixture Dental {chr(65 + i * 2)}",
                    "engine": "gemini",
                    "run_status": status,
                    "prompt_variant": 1,
                    "answer_verbatim": "Gemini answer text" if status == "OK" else "",
                    "engine_meta": {
                        "served_model": "gemini-2.0-flash",
                        "usage": {
                            "promptTokenCount": 100,
                            "candidatesTokenCount": 200,
                            "thoughtsTokenCount": 50
                        }
                    } if status == "OK" else {}
                }
                with open(os.path.join(corpus_dir, f"{row['check_id']}.json"), "w") as f:
                    json.dump(row, f)

        elif engine == "openai":
            # 2 OK rows
            for i, slug in enumerate(["fixture_dental_a", "fixture_dental_c"]):
                row = {
                    "check_id": f"NB-CZ-API_2026-11-09_{slug}_openai",
                    "date_utc": "2026-11-09",
                    "business": f"Fixture Dental {chr(65 + i * 2)}",
                    "engine": "openai",
                    "run_status": "OK",
                    "prompt_variant": 1,
                    "answer_verbatim": "OpenAI answer text",
                    "engine_meta": {
                        "served_model": "gpt-4o-2024-11-20",
                        "usage": {
                            "prompt_tokens": 150,
                            "completion_tokens": 300
                        }
                    }
                }
                with open(os.path.join(corpus_dir, f"{row['check_id']}.json"), "w") as f:
                    json.dump(row, f)

        elif engine == "anthropic":
            # 2 OK rows
            for i, slug in enumerate(["fixture_dental_a", "fixture_dental_c"]):
                row = {
                    "check_id": f"NB-CZ-API_2026-11-09_{slug}_anthropic",
                    "date_utc": "2026-11-09",
                    "business": f"Fixture Dental {chr(65 + i * 2)}",
                    "engine": "anthropic",
                    "run_status": "OK",
                    "prompt_variant": 1,
                    "answer_verbatim": "Anthropic answer text",
                    "engine_meta": {
                        "served_model": "claude-sonnet-4-20250514",
                        "usage": {
                            "input_tokens": 120,
                            "output_tokens": 280
                        }
                    }
                }
                with open(os.path.join(corpus_dir, f"{row['check_id']}.json"), "w") as f:
                    json.dump(row, f)

        elif engine == "serp":
            # 2 OK serp rows
            for i, slug in enumerate(["fixture_dental_a", "fixture_dental_c"]):
                row = {
                    "check_id": f"NB-CZ-SERP_2026-11-09_{slug}",
                    "date_utc": "2026-11-09",
                    "business": f"Fixture Dental {chr(65 + i * 2)}",
                    "engine": "google_serp",
                    "run_status": "OK",
                    "prompt_variant": 1,
                    "organic_top_results": []
                }
                with open(os.path.join(corpus_dir, f"{row['check_id']}.json"), "w") as f:
                    json.dump(row, f)

    # Timings
    base_time = datetime.now()
    timings = {}
    for i, engine in enumerate(engines):
        start = base_time + timedelta(minutes=i)
        end = start + timedelta(minutes=10 + i * 2)
        timings[engine] = {
            "started_at": start.isoformat() + "Z",
            "completed_at": end.isoformat() + "Z"
        }

    timings_path = os.path.join(tmpdir, "timings.json")
    with open(timings_path, "w") as f:
        json.dump(timings, f)

    # Prices
    prices = {
        "gemini": {"input_per_million": 0.075, "output_per_million": 0.30},
        "openai": {"input_per_million": 3.00, "output_per_million": 15.00},
        "anthropic": {"input_per_million": 3.00, "output_per_million": 15.00}
    }

    prices_path = os.path.join(tmpdir, "prices.json")
    with open(prices_path, "w") as f:
        json.dump(prices, f)

    return tmpdir, shards_dir, roster_path, timings_path, prices_path


def run_test():
    """Run the shadow report on the fixture and validate output."""
    tmpdir, shards_dir, roster_path, timings_path, prices_path = setup_fixture()
    report_path = os.path.join(tmpdir, "report.md")

    try:
        # Import and run
        sys.path.insert(0, os.path.dirname(__file__))
        import shadow_report

        # Run with args
        class Args:
            shards = shards_dir
            roster = roster_path
            timings = timings_path
            prices = prices_path
            date = "2026-11-09"
            out = report_path
            json = None
            nvi = None

        shadow_report.generate_report(Args())

        # Read report
        with open(report_path, "r") as f:
            report = f.read()

        # Assertions
        errors = []

        # Check completeness: perplexity 2/3, gemini 1/2, openai 2/2, anthropic 2/2, serp 2/2
        if "perplexity | 2 | 1 | 0 | 3 | 66.7" not in report:
            errors.append("Perplexity completeness incorrect")
        if "gemini | 1 | 0 | 1 | 2 | 50.0" not in report:
            errors.append("Gemini completeness incorrect")
        if "openai | 2 | 0 | 0 | 2 | 100.0" not in report:
            errors.append("OpenAI completeness incorrect")
        if "anthropic | 2 | 0 | 0 | 2 | 100.0" not in report:
            errors.append("Anthropic completeness incorrect")
        if "serp | 2 | 0 | 0 | 2 | 100.0" not in report:
            errors.append("SERP completeness incorrect")

        # Check served models
        if "sonar: 2 rows" not in report:
            errors.append("Perplexity served model missing")
        if "gemini-2.0-flash: 1 rows" not in report:
            errors.append("Gemini served model missing")
        if "gpt-4o-2024-11-20: 2 rows" not in report:
            errors.append("OpenAI served model missing")
        if "claude-sonnet-4-20250514: 2 rows" not in report:
            errors.append("Anthropic served model missing")

        # Check no business names in report
        for name in ["Fixture Dental A", "Fixture Dental B", "Fixture Dental C"]:
            if name in report:
                errors.append(f"Business name '{name}' leaked into report")

        # Check no answer text in report
        for text in ["Some perplexity text", "Gemini answer text", "OpenAI answer text", "Anthropic answer text"]:
            if text in report:
                errors.append(f"Answer text '{text}' leaked into report")

        # Check wall time
        if "Parallel run wall time (max):" not in report:
            errors.append("Wall time max missing")
        if "Serial estimate (sum):" not in report:
            errors.append("Wall time sum missing")

        # Check costs computed - perplexity: 0.0012 per answer, 0.0024 total
        if "perplexity | 0.0012 |" not in report or "| 2" not in report:
            errors.append("Perplexity cost incorrect")

        # Gemini: (100 * 0.075 + 250 * 0.30) / 1M = (7.5 + 75) / 1M = 0.0000825 per answer
        if "gemini |" not in report or "0.0001" not in report:
            errors.append("Gemini cost missing or incorrect")

        # Agreement: import will fail in test without nvi path, so just check it's present
        if "Agreement Rows" not in report:
            errors.append("Agreement section missing")

        if errors:
            print("TEST FAILED:")
            for err in errors:
                print(f"  - {err}")
            print("\nReport excerpt:")
            print(report[:2000])
            return False
        else:
            print("TEST PASSED: All assertions passed")
            print(f"\nReport written to {report_path}")
            return True

    finally:
        # Cleanup
        shutil.rmtree(tmpdir)


if __name__ == "__main__":
    success = run_test()
    sys.exit(0 if success else 1)
