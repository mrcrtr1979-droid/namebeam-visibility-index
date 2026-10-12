"""
E1 CLAUDE WEB-SEARCH DOOR, driver. [1012A-DOOR] 2026-10-12.

Asks the active roster questions of ONE engine, "anthropic_search" (Claude Haiku
with Anthropic's server-side web search tool, sources cited), and writes one
corpus row per question through the same run_engine path the daily runner uses,
so rows have the identical schema (engine_meta carries model, tool version and
usage including web_search_requests).

Plain-language notes:
- DRY RUN is the default. It prints the planned calls and the worst-case cost and
  makes ZERO API calls. Nothing is written.
- --max-questions is a hard cap on calls, applied after the roster rotation the
  daily runner uses, so a capped run still moves through the roster by date.
- The key is read from ANTHROPIC_API_KEY. The workflow fills that from the repo
  secret ANTHROPIC_API_KEY_CREDIT, never from ANTHROPIC_API_KEY.

Usage (repo root):
    python3 scripts/e1_claude_search.py                      # dry run, 5 questions
    python3 scripts/e1_claude_search.py --live --max-questions 5
"""

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engines  # noqa: E402
import e1_runner  # noqa: E402

# Prices verified 2026-10-12, platform.claude.com models/overview and web-search-tool.
SEARCH_USD_EACH = 0.01            # $10 per 1,000 searches
IN_USD_PER_MTOK = 0.10            # claude-haiku-5-5 input
OUT_USD_PER_MTOK = 0.50           # claude-haiku-5-5 output
# ASSUMPTIONS for the token part (labeled estimates, not measurements):
# every question uses all max_uses searches; search results inflate input to
# about 20,000 tokens; the answer is about 800 tokens. The first live row's
# engine_meta.usage replaces these guesses with a measurement.
EST_IN_TOK = 20000
EST_OUT_TOK = 800


def cost_per_question():
    searches = engines.ANTHROPIC_SEARCH_MAX_USES * SEARCH_USD_EACH
    tokens = (EST_IN_TOK * IN_USD_PER_MTOK + EST_OUT_TOK * OUT_USD_PER_MTOK) / 1e6
    return searches, tokens


def plan(roster, date_utc, max_questions):
    rows = list(roster)
    if len(rows) > 1:
        offset = sum(ord(c) for c in date_utc) % len(rows)
        rows = rows[offset:] + rows[:offset]
    return rows[:max(0, max_questions)]


def main(argv=None, roster_path=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="make real API calls (default is a dry run)")
    ap.add_argument("--max-questions", type=int, default=5)
    a = ap.parse_args(argv)
    date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    roster = e1_runner.load_active_roster(roster_path or e1_runner.ROSTER_PATH)
    chosen = plan(roster, date_utc, a.max_questions)
    s_each, t_each = cost_per_question()
    per = s_each + t_each
    print("E1 claude-search door | %s | roster %d active | planned %d call(s) "
          "(cap %d) | model %s | tool %s max_uses %d | %s"
          % (date_utc, len(roster), len(chosen), a.max_questions,
             engines.ANTHROPIC_SEARCH_MODEL, engines.ANTHROPIC_SEARCH_TOOL,
             engines.ANTHROPIC_SEARCH_MAX_USES,
             "LIVE" if a.live else "DRY RUN, zero API calls"))
    for i, br in enumerate(chosen, 1):
        print("  %2d. %s | %s" % (i, br["business"], br["prompt_1"][:90]))
    print("worst-case cost per question: %d searches x $%.2f = $%.4f + tokens "
          "(%d in x $%.2f + %d out x $%.2f per MTok) = $%.4f -> $%.4f"
          % (engines.ANTHROPIC_SEARCH_MAX_USES, SEARCH_USD_EACH, s_each,
             EST_IN_TOK, IN_USD_PER_MTOK, EST_OUT_TOK, OUT_USD_PER_MTOK, t_each, per))
    print("this run (%d calls): $%.2f worst case | full roster (%d questions): $%.2f worst case"
          % (len(chosen), per * len(chosen), len(roster), per * len(roster)))
    if not a.live:
        print("dry run: no API call made, nothing written.")
        return 0
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        print("ERROR: ANTHROPIC_API_KEY not set. Nothing written.")
        return 0
    wrote = ok_n = 0
    for br in chosen:
        _, good = e1_runner.run_engine(br, "anthropic_search",
                                       engines.call_anthropic_search, date_utc)
        wrote += 1
        ok_n += 1 if good else 0
    print("rows written: %d (ok %d, failed %d)" % (wrote, ok_n, wrote - ok_n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
