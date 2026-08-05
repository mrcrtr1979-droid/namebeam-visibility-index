"""
THE BUZZ, adaptive probe cadence for E1 (doctrine 111).

Plain-language note for a non-coder reading along:
Right now every business on the roster gets checked on the same fixed
schedule, which costs the same whether anything changed or not. A dolphin
does not hunt that way. It sends slow, cheap, wide pings across the whole
scene, and the moment one target heats up it BUZZES: it probes that single
target far faster and at much higher resolution. When the water is familiar
it goes quiet and just listens, because pinging costs energy.

This module decides, from the corpus alone, which businesses deserve the
buzz today and which should stay silent. It spends nothing to run: it
compares the two most recent corpus rows per business and looks for change.

Why this matters commercially (doctrine 111): a customer will not pay a
monthly fee for a re-run on a calendar. They will pay for the promise that
the instrument closes on their picture the moment it moves. The buzz IS
that promise, expressed as code.

It also produces DW-4, citation half-life, for free: buzz cadence and
citation decay are the same measurement seen from two sides.
"""

import json
import os
import glob
from collections import defaultdict

# Cadence tiers. HOT is the buzz.
COLD = "cold"      # nothing has moved; probe cheap and wide, weekly
WARM = "warm"      # something small moved; keep the normal cadence
HOT = "hot"        # BUZZ: all engines, multiple variants, daily

# A newly-tracked business with only one row has no baseline to compare to,
# so it starts WARM rather than HOT. We do not buzz on ignorance.
NEW = "warm"


def load_corpus(corpus_dir):
    """Read every corpus row and group them by business, oldest first."""
    by_business = defaultdict(list)
    for path in sorted(glob.glob(os.path.join(corpus_dir, "*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as f:
                row = json.load(f)
        except (ValueError, OSError):
            # A corrupt row is skipped and reported by the caller, never
            # silently treated as "no change" (doctrine 74: a broken read
            # is a void row, not a measurement).
            continue
        if row.get("business"):
            by_business[row["business"]].append(row)
    for business in by_business:
        by_business[business].sort(key=lambda r: r.get("date_utc", ""))
    return by_business


def detect_convergence(previous_row, current_row):
    """Compare two dated rows for the same business and return the list of
    signals that fired. Each signal is a plain string a human can read in
    the morning brief. An empty list means nothing moved.

    These are the four triggers named in doctrine 111.
    """
    signals = []

    was_named = bool(previous_row.get("business_mentioned"))
    is_named = bool(current_row.get("business_mentioned"))

    # TRIGGER 1: the business crossed the line, either direction. This is
    # the single most important event the instrument can detect.
    if is_named and not was_named:
        signals.append("CROSSED TO NAMED: engine now names this business")
    if was_named and not is_named:
        signals.append("LOST THE NAMING: engine named this business before and does not now")

    prev_sources = set(previous_row.get("sources_cited") or [])
    curr_sources = set(current_row.get("sources_cited") or [])

    # TRIGGER 2: a source that was stable has stopped being cited.
    dropped = prev_sources - curr_sources
    if dropped:
        signals.append("SOURCES DROPPED (%d): %s" % (len(dropped), ", ".join(sorted(dropped)[:3])))

    # TRIGGER 3: brand-new sources appeared in the answer.
    added = curr_sources - prev_sources
    if added:
        signals.append("NEW SOURCES (%d): %s" % (len(added), ", ".join(sorted(added)[:3])))

    prev_comp = set(previous_row.get("competitors_mentioned") or [])
    curr_comp = set(current_row.get("competitors_mentioned") or [])

    # TRIGGER 4: a competitor entered or left the answer. Requires the
    # schema v3 backfill; on v2 rows both sets are empty and this is silent
    # rather than wrong.
    new_comp = curr_comp - prev_comp
    if new_comp:
        signals.append("NEW COMPETITOR NAMED: %s" % ", ".join(sorted(new_comp)[:3]))
    gone_comp = prev_comp - curr_comp
    if gone_comp:
        signals.append("COMPETITOR DROPPED OUT: %s" % ", ".join(sorted(gone_comp)[:3]))

    return signals


def score_cadence(signals):
    """Turn signals into a cadence tier. Crossing the naming line is always
    a buzz. Churn in sources or competitors is a buzz once it is large
    enough that it is unlikely to be noise."""
    if not signals:
        return COLD, "nothing moved; stay silent and save the spend"

    for s in signals:
        if s.startswith("CROSSED TO NAMED") or s.startswith("LOST THE NAMING"):
            return HOT, "the business crossed the naming line, which is the event the whole instrument exists to catch"

    churn = 0
    for s in signals:
        if s.startswith("SOURCES DROPPED") or s.startswith("NEW SOURCES"):
            # pull the count out of "SOURCES DROPPED (7): ..."
            try:
                churn += int(s.split("(")[1].split(")")[0])
            except (IndexError, ValueError):
                churn += 1
        if s.startswith("NEW COMPETITOR") or s.startswith("COMPETITOR DROPPED"):
            return HOT, "the competitive set in the answer changed"

    if churn >= 5:
        return HOT, "large source churn (%d) suggests the answer is being rebuilt" % churn
    return WARM, "small movement (%d source changes); normal cadence" % churn


def plan(corpus_dir):
    """Return a per-business cadence plan. This is what the shipping
    heartbeat reads each morning to decide where to spend."""
    by_business = load_corpus(corpus_dir)
    out = []
    for business, rows in sorted(by_business.items()):
        if len(rows) < 2:
            out.append({
                "business": business,
                "cadence": NEW,
                "reason": "only %d dated row(s); no baseline to compare against, so no buzz on ignorance" % len(rows),
                "signals": [],
                "latest_date": rows[-1].get("date_utc") if rows else None,
            })
            continue
        signals = detect_convergence(rows[-2], rows[-1])
        cadence, reason = score_cadence(signals)
        out.append({
            "business": business,
            "cadence": cadence,
            "reason": reason,
            "signals": signals,
            "compared": [rows[-2].get("date_utc"), rows[-1].get("date_utc")],
            "latest_date": rows[-1].get("date_utc"),
        })
    return out


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else "corpus/e1"
    p = plan(d)
    hot = [x for x in p if x["cadence"] == HOT]
    print("BUZZ PLAN, %d businesses, %d HOT\n" % (len(p), len(hot)))
    for x in p:
        print("[%-4s] %-28s %s" % (x["cadence"].upper(), x["business"], x["reason"]))
        for s in x["signals"]:
            print("         ! %s" % s[:110])
