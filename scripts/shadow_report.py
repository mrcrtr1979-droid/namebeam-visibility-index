"""
SHADOW REPORT: analyze dry-run shards from the parallel GitHub Actions workflow.

Usage: py -I shadow_report.py --shards <dir> --roster <e1_roster.json>
       --timings <timings.json> --prices <prices.json> --date YYYY-MM-DD
       --out REPORT.md [--json report.json] [--nvi <nvi_path>]
"""

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import datetime


def load_roster(path):
    """Load active roster rows and count expected cells per engine."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    active = [r for r in data.get("roster", []) if r.get("active") is True]

    # Count expected cells per engine
    expected = {"perplexity": 0, "gemini": 0, "openai": 0, "anthropic": 0, "serp": 0}

    for row in active:
        engines_list = row.get("engines")
        serp_flag = row.get("serp")

        # If engines list is present, only those engines are asked
        if isinstance(engines_list, list):
            for eng in engines_list:
                if eng in expected:
                    expected[eng] += 1
        else:
            # No engines list means all available engines are asked
            for eng in ["perplexity", "gemini", "openai", "anthropic"]:
                expected[eng] += 1

        # SERP: asked unless serp is explicitly false
        if serp_flag is not False:
            expected["serp"] += 1

    return active, expected


def load_shard_rows(shards_dir, engine):
    """Load all rows from one engine shard's dry_run_out directories."""
    rows = []
    private_rows = {}

    # Public prompt_1 rows
    public_dir = os.path.join(shards_dir, engine, "dry_run_out", "corpus_e1")
    if os.path.exists(public_dir):
        for path in glob.glob(os.path.join(public_dir, "*.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    rows.append(json.load(f))
            except (OSError, ValueError):
                pass

    # Public q2 rows
    q2_dir = os.path.join(shards_dir, engine, "dry_run_out", "corpus_e1_q2")
    if os.path.exists(q2_dir):
        for path in glob.glob(os.path.join(q2_dir, "*.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    rows.append(json.load(f))
            except (OSError, ValueError):
                pass

    # Private raw rows (for perplexity answer text and SERP AI Overview)
    private_dir = os.path.join(shards_dir, engine, "dry_run_out", "private_e1")
    if os.path.exists(private_dir):
        for path in glob.glob(os.path.join(private_dir, "*.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    row = json.load(f)
                    check_id = row.get("check_id")
                    if check_id:
                        private_rows[check_id] = row
            except (OSError, ValueError):
                pass

    return rows, private_rows


def count_completeness(rows, expected):
    """Count OK, FAILED, QUOTA_BLOCKED per engine."""
    stats = {}

    for engine in ["perplexity", "gemini", "openai", "anthropic", "serp"]:
        engine_rows = [r for r in rows if r.get("engine") == engine or
                       (engine == "serp" and r.get("engine") == "google_serp")]

        ok = sum(1 for r in engine_rows if r.get("run_status") == "OK")
        failed = sum(1 for r in engine_rows if r.get("run_status") == "FAILED")
        quota = sum(1 for r in engine_rows if r.get("run_status") == "QUOTA_BLOCKED")

        stats[engine] = {
            "ok": ok,
            "failed": failed,
            "quota_blocked": quota,
            "expected": expected.get(engine, 0),
            "completeness_pct": round(100.0 * ok / expected[engine], 1) if expected.get(engine, 0) > 0 else None
        }

    return stats


def extract_served_models(rows):
    """Extract served_model distribution per engine from engine_meta."""
    models = defaultdict(lambda: defaultdict(int))

    for row in rows:
        if row.get("run_status") != "OK":
            continue

        engine = row.get("engine")
        if engine == "google_serp":
            engine = "serp"

        meta = row.get("engine_meta") or {}
        served = meta.get("served_model")

        if served:
            models[engine][served] += 1

    return dict(models)


def calculate_costs(rows, prices, private_rows):
    """Calculate cost per answer by engine using tokens and price rates."""
    costs = defaultdict(lambda: {"total_cost": 0.0, "ok_count": 0})

    for row in rows:
        if row.get("run_status") != "OK":
            continue

        engine = row.get("engine")
        if engine == "google_serp":
            continue  # No cost calculation for SERP

        meta = row.get("engine_meta") or {}

        # Perplexity: has cost.total_cost in the meta
        if engine == "perplexity":
            usage = meta.get("usage") or {}
            cost_data = usage.get("cost") or {}
            total = cost_data.get("total_cost")
            if isinstance(total, (int, float)) and total > 0:
                costs[engine]["total_cost"] += total
                costs[engine]["ok_count"] += 1

        # Other engines: calculate from tokens and prices
        elif engine in prices:
            usage = meta.get("usage") or {}

            if engine == "gemini":
                input_tok = usage.get("promptTokenCount", 0)
                output_tok = usage.get("candidatesTokenCount", 0)
                # Include thoughtsTokenCount in output if present
                thought_tok = usage.get("thoughtsTokenCount", 0)
                output_tok += thought_tok
            elif engine == "openai":
                input_tok = usage.get("prompt_tokens", 0)
                output_tok = usage.get("completion_tokens", 0)
            elif engine == "anthropic":
                input_tok = usage.get("input_tokens", 0)
                output_tok = usage.get("output_tokens", 0)
            else:
                continue

            rates = prices[engine]
            input_rate = rates.get("input_per_million", 0)
            output_rate = rates.get("output_per_million", 0)

            cost = (input_tok / 1_000_000.0 * input_rate) + (output_tok / 1_000_000.0 * output_rate)
            costs[engine]["total_cost"] += cost
            costs[engine]["ok_count"] += 1

    # Compute per-answer average
    result = {}
    for engine, data in costs.items():
        if data["ok_count"] > 0:
            result[engine] = {
                "per_answer": round(data["total_cost"] / data["ok_count"], 4),
                "total": round(data["total_cost"], 4),
                "ok_count": data["ok_count"]
            }

    return result


def count_agreement_rows(all_rows, private_rows, roster, nvi_path=None):
    """Count how many agreement rows could be built (in memory only)."""
    if nvi_path:
        sys.path.insert(0, os.path.join(nvi_path, "scripts"))

    try:
        from agreement import build_agreement_row
    except ImportError:
        return {"error": "Could not import agreement.build_agreement_row", "buildable": 0, "not_buildable": 0}

    # Group rows by business
    by_business = defaultdict(list)
    for row in all_rows:
        if row.get("run_status") == "OK" and row.get("prompt_variant") == 1:
            business = row.get("business")
            if business:
                by_business[business].append(row)

    buildable = 0
    not_buildable = 0
    no_text_count = 0

    roster_by_business = {r.get("business"): r for r in roster if r.get("business")}

    for business, engine_rows in by_business.items():
        if len(engine_rows) < 2:
            not_buildable += 1
            continue

        # Check if answer text is available for at least 2 engines
        # For perplexity, need to fetch from private_rows
        usable = []
        for r in engine_rows:
            engine = r.get("engine")
            check_id = r.get("check_id")

            # Check if text is available
            if engine == "perplexity":
                # Text is in private row
                private = private_rows.get(check_id, {})
                if private.get("answer_verbatim"):
                    usable.append(private)
                else:
                    no_text_count += 1
            else:
                # Text should be in public row for openai, anthropic, gemini
                if r.get("answer_verbatim"):
                    usable.append(r)
                else:
                    no_text_count += 1

        if len(usable) >= 2:
            buildable += 1
        else:
            not_buildable += 1

    return {
        "buildable": buildable,
        "not_buildable": not_buildable,
        "no_text_count": no_text_count
    }


def parse_timings(timings_path):
    """Parse timings.json and compute wall minutes."""
    with open(timings_path, "r", encoding="utf-8") as f:
        timings = json.load(f)

    wall_minutes = {}
    for engine, data in timings.items():
        start = datetime.fromisoformat(data["started_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(data["completed_at"].replace("Z", "+00:00"))
        minutes = (end - start).total_seconds() / 60.0
        wall_minutes[engine] = round(minutes, 1)

    wall_max = max(wall_minutes.values()) if wall_minutes else 0
    wall_sum = sum(wall_minutes.values())

    return wall_minutes, wall_max, wall_sum


def generate_report(args):
    """Generate the shadow report."""
    # Load inputs
    roster, expected = load_roster(args.roster)

    all_rows = []
    all_private = {}
    for engine in ["perplexity", "gemini", "openai", "anthropic", "serp"]:
        rows, private = load_shard_rows(args.shards, engine)
        all_rows.extend(rows)
        all_private.update(private)

    completeness = count_completeness(all_rows, expected)
    served_models = extract_served_models(all_rows)

    # Load prices
    with open(args.prices, "r", encoding="utf-8") as f:
        prices = json.load(f)

    costs = calculate_costs(all_rows, prices, all_private)

    # Parse timings
    wall_minutes, wall_max, wall_sum = parse_timings(args.timings)

    # Count agreement rows
    agreement = count_agreement_rows(all_rows, all_private, roster, args.nvi)

    # Generate report
    lines = []
    lines.append("# SHADOW REPORT")
    lines.append("")
    lines.append(f"Date: {args.date}")
    lines.append(f"Method: Parallel GitHub Actions matrix workflow, dry-run mode (E1_DRY_RUN=1)")
    lines.append("")
    lines.append("CRITICAL: Rows under shadow/ are never mixed into the public series. This is a ")
    lines.append("test run only. The method note date is the date of this shadow run, not the date ")
    lines.append("these rows would have been collected in production.")
    lines.append("")

    lines.append("## Completeness")
    lines.append("")
    lines.append("Engine | OK | Failed | Quota Blocked | Expected | Completeness %")
    lines.append("-------|-------|--------|---------------|----------|---------------")
    for engine in ["perplexity", "gemini", "openai", "anthropic", "serp"]:
        s = completeness[engine]
        pct = s["completeness_pct"] if s["completeness_pct"] is not None else "N/A"
        lines.append(f"{engine} | {s['ok']} | {s['failed']} | {s['quota_blocked']} | {s['expected']} | {pct}")
    lines.append("")

    lines.append("## Served Models")
    lines.append("")
    for engine in ["perplexity", "gemini", "openai", "anthropic"]:
        if engine in served_models:
            lines.append(f"### {engine}")
            for model, count in sorted(served_models[engine].items()):
                lines.append(f"- {model}: {count} rows")
            lines.append("")

    lines.append("## Cost Per Answer (list-price estimate)")
    lines.append("")
    lines.append("Engine | Per Answer ($) | Total ($) | OK Count")
    lines.append("-------|----------------|-----------|----------")
    for engine in ["perplexity", "gemini", "openai", "anthropic"]:
        if engine in costs:
            c = costs[engine]
            lines.append(f"{engine} | {c['per_answer']} | {c['total']} | {c['ok_count']}")
        else:
            lines.append(f"{engine} | N/A | N/A | 0")
    lines.append("")
    lines.append("Note: Costs are list-price estimates. Perplexity costs from usage.cost.total_cost; ")
    lines.append("others computed from tokens times rates in prices.json. Gemini output tokens include ")
    lines.append("thoughtsTokenCount when present.")
    lines.append("")

    lines.append("## Wall Time")
    lines.append("")
    lines.append("Shard | Wall Minutes")
    lines.append("------|-------------")
    for engine in ["perplexity", "gemini", "openai", "anthropic", "serp"]:
        minutes = wall_minutes.get(engine, 0)
        lines.append(f"{engine} | {minutes}")
    lines.append("")
    lines.append(f"Parallel run wall time (max): {wall_max:.1f} min")
    lines.append(f"Serial estimate (sum): {wall_sum:.1f} min")
    lines.append(f"Speedup: {wall_sum / wall_max:.1f}x" if wall_max > 0 else "Speedup: N/A")
    lines.append("")

    lines.append("## Agreement Rows")
    lines.append("")
    lines.append(f"Buildable (2+ engines with answer text): {agreement.get('buildable', 0)}")
    lines.append(f"Not buildable (< 2 engines or missing text): {agreement.get('not_buildable', 0)}")
    if agreement.get("no_text_count", 0) > 0:
        lines.append(f"Rows missing answer text: {agreement['no_text_count']}")
    if "error" in agreement:
        lines.append(f"Error: {agreement['error']}")
    lines.append("")
    lines.append("Agreement rows are built in memory only and are never written to the corpus.")
    lines.append("")

    lines.append("## Five Comparison Fields")
    lines.append("")
    lines.append("The packet requested these five fields:")
    lines.append("1. Completeness: see table above")
    lines.append("2. Served model per engine: see distribution above")
    lines.append("3. Failures: see completeness table (Failed and Quota Blocked columns)")
    lines.append("4. Wall time: parallel max vs serial sum shown above")
    lines.append("5. Cost per answer by engine: see cost table above")
    lines.append("")

    report_text = "\n".join(lines)

    # Write report
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report_text)

    # Write JSON if requested
    if args.json:
        json_data = {
            "date": args.date,
            "completeness": completeness,
            "served_models": served_models,
            "costs": costs,
            "wall_minutes": wall_minutes,
            "wall_max": wall_max,
            "wall_sum": wall_sum,
            "agreement": agreement
        }
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=2, ensure_ascii=True)

    return report_text


def main():
    parser = argparse.ArgumentParser(description="Shadow report analyzer")
    parser.add_argument("--shards", required=True, help="Shards directory")
    parser.add_argument("--roster", required=True, help="Path to e1_roster.json")
    parser.add_argument("--timings", required=True, help="Path to timings.json")
    parser.add_argument("--prices", required=True, help="Path to prices.json")
    parser.add_argument("--date", required=True, help="Run date YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="Output report path")
    parser.add_argument("--json", help="Optional JSON output path")
    parser.add_argument("--nvi", help="Path to nvi worktree (for agreement import)")

    args = parser.parse_args()

    generate_report(args)
    print(f"Report written to {args.out}")


if __name__ == "__main__":
    main()
