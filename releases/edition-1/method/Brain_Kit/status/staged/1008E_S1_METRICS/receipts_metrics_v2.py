#!/usr/bin/env python3
"""Receipts Metrics v2 (Namebeam / Carter Enterprise), Seat S1-3, 2026-10-08. Read-only on the dated E1 record.

What v2 adds to v1.1 (the v1 formulas and their published September values are unchanged and are re-checked):
  1. Every metric row carries a method_id, a plain definition and the unit of its denominator.
  2. Persistence is reported by two methods that give different numbers for the same data:
       P.*.pername      share of (business, day) namings that recur the next day (v1 P)
       J.*.pair_*       overlap of the whole named list on two consecutive days (Jaccard), mean and median
     each on two name sets: raw (the strings the engine returned, case folded) and gated (v1 name gate + alias merge).
  3. Windows are separate where the method changed: WA 2026-09-01..09-27 (Perplexity Sonar chat completions),
     WB 2026-09-28..last day (Perplexity Agent API with a forced web search tool), WE the Edition 1 window
     (2026-09-01..2026-10-13, clipped to the data) which mixes both Perplexity regimes and is marked so.
  4. Answer coverage Y = answered cell days / asked cell days, and the own-name table (Namebeam named on how many days).
  5. A public-safe sentence for every metric, window and engine, checked against the copy laws.
  6. RED/GREEN: --selftest (hand-computed fixture), --redproof (each break flag must turn the selftest RED), --regress
     (reproduces the v1 September table and the W2-3 raw numbers: 2881/7017, median 0.2922 over 1008 pairs).

Cell = market + niche, US nationwide excluded (38 cells in September), kind = API rows only, quarantined files excluded.
Answered day (cell, engine, day) = at least one raw business string returned. Gated sets come from the 1005A name gate
(imported by receipts_metrics_v1.py, not copied). Raw sets are the same strings, split on ';', trimmed, case folded.

Usage:
  python3 -I receipts_metrics_v2.py --selftest
  python3 -I receipts_metrics_v2.py --redproof
  python3 -I receipts_metrics_v2.py --run [--csv PATH] [--out DIR] [--through YYYY-MM-DD] [--own-name PATH]
Standard library only (plus the Brain's v1 and gate modules).
"""
import csv
import hashlib
import importlib.util
import io
import itertools
import os
import re
import statistics
import sys
from collections import defaultdict

sys.dont_write_bytecode = True
VERSION = "receipts_metrics_v2"
HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
V1_PATH = os.path.join(HERE, "..", "1005B_METRICS", "receipts_metrics_v1.py")
GATE_PATH = os.path.join(BRAIN, "status", "staged", "1005A_AI_PICKS_CHART", "gen_ai_picks_chart.py")
CITE_PATH = os.path.join(BRAIN, "status", "staged", "0929C_ACTOR", "gen_citation_index.py")
PANEL_PATH = os.path.join(BRAIN, "status", "staged", "0929B_MARKET_PANELS", "gen_market_panels.py")
DEFAULT_CSV = os.path.join(BRAIN, "corpus", "namebeam", "Namebeam_E1_Dataset_LATEST.csv")
E1_END = "2026-10-13"
BREAK2 = set(filter(None, os.environ.get("RM2_BREAK", "").split(",")))   # JMED, PRAW, DEN, SCOPE, YDEN

# ---------------------------------------------------------------- v1 and gate modules
_spec = importlib.util.spec_from_file_location("receipts_metrics_v1", V1_PATH)
v1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v1)
_G = None
_ORIG_GENERIC = None


def gate_module():
    """Import the 1005A gate once and remember the unpatched make_generic_set (v1.load_series patches it per call)."""
    global _G, _ORIG_GENERIC
    if _G is None:
        sys.path.insert(0, os.path.dirname(GATE_PATH))
        import gen_ai_picks_chart as g
        _G = g
        _ORIG_GENERIC = g.gci.make_generic_set
    return _G


def gated_series(csv_path, w0, w1):
    g = gate_module()
    g.gci.make_generic_set = _ORIG_GENERIC          # same generic-phrase set rule as a fresh v1 run, whatever ran before
    v1.W0, v1.W1 = w0, w1
    ser, engines, cells, nrows = v1.load_series(csv_path)
    return ser, list(engines), cells, nrows


# ---------------------------------------------------------------- methods registry
# method_id -> (metric, name set, statistic, denominator unit, definition)
M = {}


def _m(mid, metric, names, stat, unit, definition):
    M[mid] = dict(method_id=mid, metric=metric, name_set=names, statistic=stat, denominator_unit=unit, definition=definition)


_m("P.raw.pername", "P", "raw", "share", "business-day namings with the next day answered",
   "Of the business names returned on a day (raw strings), the share also returned on the next day; pooled over cells.")
_m("P.gated.pername", "P", "gated", "share", "business-day namings with the next day answered",
   "The same share after the name gate and alias merge (v1 P).")
_m("P.raw.pername_nextasked", "P", "raw", "share", "business-day namings with a row for the next day (a next day with no names counts as not recurring)",
   "The W2-3 form of raw P: the next day only needs to have been asked, so an empty next day counts against persistence.")
_m("P.gated.pername_nextasked", "P", "gated", "share", "business-day namings with a row for the next day (a next day with no names counts as not recurring)",
   "The same next-day-asked form on gated names.")
_m("J.raw.pair_bothnamed.mean", "J", "raw", "mean", "consecutive day pairs on which both days named at least one business",
   "Mean overlap (Jaccard) of the raw name lists on two consecutive days, pairs where both days named someone.")
_m("J.raw.pair_bothnamed.median", "J", "raw", "median", "consecutive day pairs on which both days named at least one business",
   "Median of the same overlap.")
_m("J.gated.pair_bothnamed.mean", "J", "gated", "mean", "consecutive day pairs on which both days named at least one business",
   "Mean overlap of the gated name lists, pairs where both days named someone.")
_m("J.gated.pair_bothnamed.median", "J", "gated", "median", "consecutive day pairs on which both days named at least one business",
   "Median of the same overlap.")
_m("C.gated.pair_union", "C", "gated", "mean", "consecutive answered day pairs with at least one gated name on either day",
   "Mean of 1 minus overlap over consecutive answered days where either day has a gated name (v1 C; a pair with one empty day counts as full churn).")
_m("H.gated.cellmean", "H", "gated", "mean", "cells with at least one gated name",
   "Mean over cells of the concentration index H, the sum of squared shares of named days (v1 H).")
_m("invH.gated.cellmean", "invH", "gated", "mean", "cells with at least one gated name",
   "Mean over cells of 1/H, the number of equally common names the cell behaves like (v1).")
_m("N.gated.day", "N", "gated", "share", "answered cell days",
   "Share of answered days on which at least one name survived the gate (v1 N).")
_m("Y.answered.day", "Y", "raw", "share", "asked cell days",
   "Share of asked days (a row exists) on which the engine returned at least one business name.")
_m("S.gated.coinflip", "S", "gated", "share", "business-cell pairs named on at least one answered day",
   "Share of businesses named on 20 to 80 percent of answered days, so one day's answer is close to a coin flip (v1 S).")
_m("A.raw.pair", "A", "raw", "mean", "days on which both engines answered and at least one named someone",
   "Mean overlap of two engines' raw name lists on the same day.")
_m("A.gated.pair", "A", "gated", "mean", "days on which both engines answered and at least one gated name exists",
   "Mean overlap of two engines' gated name lists on the same day (v1 A).")

WINDOWS_DOC = {
    "WA": "Perplexity Sonar chat completions with web search",
    "WB": "Perplexity Agent API with a forced web search tool",
    "WE": "Edition 1 window; Perplexity mixes both regimes",
}


def windows(last):
    last = min(last, E1_END)
    return [("WA", "2026-09-01", "2026-09-27"), ("WB", "2026-09-28", last), ("WE", "2026-09-01", last)]


def regime(wid, eng):
    if eng != "perplexity":
        return "api_no_search_tool"
    return {"WA": "sonar_chat_completions", "WB": "agent_api_forced_web_search", "WE": "mixed_two_regimes"}[wid]


# ---------------------------------------------------------------- raw series
def raw_series(rows, w0, w1, scope="38", quar=frozenset(), engines=("openai", "gemini", "perplexity", "anthropic")):
    """scope '38': v1 cells (named market, not US nationwide, quarantine out). scope 'all': W2-3 repro (every market)."""
    if "SCOPE" in BREAK2:
        scope = "all"
    min_date = "2026-09-01"
    ser = defaultdict(lambda: {"answered": set(), "asked": set(), "named": defaultdict(set)})
    for r in rows:
        if r["kind"] != "API" or r["engine"] not in engines or not (w0 <= r["date"] <= w1):
            continue
        mk = r["market"].strip()
        if scope == "38":
            if not mk or mk.casefold() == "us nationwide" or r["date"] < min_date:
                continue
            if os.path.basename(r.get("source_file", "")) in quar:
                continue
        s = ser[((mk, r["niche"].strip()), r["engine"])]
        s["asked"].add(r["date"])
        names = [n.strip().casefold() for n in r["businesses_named"].split(";") if n.strip()]
        if names:
            s["answered"].add(r["date"])
        s["named"][r["date"]] |= set(names)
    return ser


# ---------------------------------------------------------------- statistics
def pair_j(s):
    """Jaccard on consecutive days where both days have a non-empty set (answered is implied)."""
    out = []
    for d in sorted(s["named"]):
        d2 = v1.nextday(d)
        a, b = s["named"].get(d, set()), s["named"].get(d2, set())
        if a and b:
            out.append(len(a & b) / len(a | b))
    return out


def stat_of(vals):
    if not vals:
        return None, 0.0, 0
    if "JMED" in BREAK2:
        return statistics.mean(vals), sum(vals), len(vals)
    return statistics.median(vals), sum(vals), len(vals)


def p_counts(s):
    n = d = 0
    for b, (a, c) in v1.persistence_counts(s).items():
        n += a
        d += c
    return (d, n) if "PRAW" in BREAK2 else (n, d)


def p_counts_asked(named, asked):
    """Persistence where the next day only needs a row (asked). named: {day: set}; asked: set of days."""
    n = d = 0
    for day in asked:
        d2 = v1.nextday(day)
        if d2 not in asked:
            continue
        for b in named.get(day, ()):
            d += 1
            n += 1 if b in named.get(d2, ()) else 0
    return (d, n) if "PRAW" in BREAK2 else (n, d)


def day_agreement(s1, s2):
    vals = []
    for d in sorted(s1["answered"] & s2["answered"]):
        a, b = s1["named"].get(d, set()), s2["named"].get(d, set())
        u = a | b
        if u:
            vals.append(len(a & b) / len(u))
    return vals


def compute_window(raw, gated, cells, engines):
    """Returns {(method_id, engine_or_pair): (value, numerator, denominator, n_cells)}."""
    out = {}

    def put(mid, key, num, den, ncell, value=None):
        v = value if value is not None else (num / den if den else None)
        out[(mid, key)] = (v, num, den, ncell)

    for e in engines:
        pr = [0, 0]; pg = [0, 0]; pra = [0, 0]; pga = [0, 0]; jr = []; jg = []; cg = [0.0, 0]; hs = []; ih = []; nn = [0, 0]; ya = [0, 0]
        s_n = s_cf = 0
        for c in cells:
            rs = raw.get((c, e), {"answered": set(), "asked": set(), "named": {}})
            gs = gated[(c[0], c[1], e)]
            n, d = p_counts(rs); pr[0] += n; pr[1] += d
            _, n, d = v1.persistence(gs); pg[0] += n; pg[1] += d
            n, d = p_counts_asked(rs["named"], rs["asked"]); pra[0] += n; pra[1] += d
            n, d = p_counts_asked(gs["named"], rs["asked"]); pga[0] += n; pga[1] += d
            jr += pair_j(rs); jg += pair_j(gs)
            _, sm, k = v1.churn(gs); cg[0] += sm; cg[1] += k
            H, inv, t, nb = v1.concentration(gs)
            if H is not None:
                hs.append(H); ih.append(inv)
            _, n, d = v1.name_rate(gs); nn[0] += n; nn[1] += d
            ya[0] += len(rs["answered"]); ya[1] += len(rs["asked"]) if "YDEN" not in BREAK2 else len(rs["answered"])
            for b, nd, ad, p, sv, coin in v1.snapshot_rows(gs):
                s_n += 1; s_cf += 1 if coin else 0
        nc = len(cells)
        put("P.raw.pername", e, pr[0], pr[1], nc)
        put("P.gated.pername", e, pg[0], pg[1], nc)
        put("P.raw.pername_nextasked", e, pra[0], pra[1], nc)
        put("P.gated.pername_nextasked", e, pga[0], pga[1], nc)
        for mid, vals in (("J.raw.pair_bothnamed", jr), ("J.gated.pair_bothnamed", jg)):
            med, sm, k = stat_of(vals)
            put(mid + ".median", e, 0, k, nc, value=med)
            put(mid + ".mean", e, sm, k, nc, value=(sm / k if k else None))
        den_c = cg[1] if "DEN" not in BREAK2 else cg[1] + 1
        put("C.gated.pair_union", e, cg[0], den_c, nc)
        put("H.gated.cellmean", e, 0, len(hs), nc, value=(sum(hs) / len(hs) if hs else None))
        put("invH.gated.cellmean", e, 0, len(ih), nc, value=(sum(ih) / len(ih) if ih else None))
        put("N.gated.day", e, nn[0], nn[1], nc)
        put("Y.answered.day", e, ya[0], ya[1], nc)
        put("S.gated.coinflip", e, s_cf, s_n, nc)
    for e1, e2 in itertools.combinations(engines, 2):
        ar = []; ag = []
        for c in cells:
            r1 = raw.get((c, e1)); r2 = raw.get((c, e2))
            if r1 and r2:
                ar += day_agreement(r1, r2)
            ag += _gated_agree(gated[(c[0], c[1], e1)], gated[(c[0], c[1], e2)])
        key = e1 + "|" + e2
        put("A.raw.pair", key, sum(ar), len(ar), len(cells))
        put("A.gated.pair", key, sum(ag), len(ag), len(cells))
    return out


def _gated_agree(s1, s2):
    return day_agreement(s1, s2)


# ---------------------------------------------------------------- output
HEADER = ["version", "window_id", "window_from", "window_to", "engine", "engine_regime", "metric", "method_id", "name_set",
          "statistic", "value", "numerator", "denominator", "denominator_unit", "n_cells", "note"]


def rows_for(wid, w0, w1, res):
    rows = []
    for (mid, key), (v, n, d, nc) in sorted(res.items(), key=lambda kv: (list(M).index(kv[0][0]), kv[0][1])):
        m = M[mid]
        eng = key
        note = ""
        if "|" in key:
            note = "engine pair"
            reg = "pair"
        else:
            reg = regime(wid, key)
        if wid == "WE" and "perplexity" in key:
            note = (note + "; " if note else "") + "mixes two Perplexity regimes, use WA and WB"
        if m["statistic"] == "median":
            num = ""
        else:
            num = "%g" % n
        rows.append([VERSION, wid, w0, w1, eng, reg, m["metric"], mid, m["name_set"], m["statistic"],
                     "" if v is None else "%.6f" % v, num, d, m["denominator_unit"], nc, note])
    return rows


# ---------------------------------------------------------------- public-safe sentences
SETTINGS_API = "OpenAI, Anthropic and Gemini were asked through their APIs with no search tool switched on by us."
SETTINGS_PPLX = {
    "WA": "Perplexity was asked through its Sonar chat completions with web search.",
    "WB": "Perplexity was asked through its Agent API with a forced web search tool.",
    "WE": "Perplexity used two methods inside this window (Sonar chat completions to 2026-09-27, the Agent API from 2026-09-28), so read its figures from the two separate windows.",
}
NAME_NOTE = {"raw": "business names exactly as the engine returned them, case folded",
             "gated": "business names after a rules based filter that removes places, platforms and generic phrases, and after merging spelling variants"}
LEAD = {
    "P.raw.pername": "Share of business names returned on one day that were returned again the next day",
    "P.gated.pername": "Share of business names returned on one day that were returned again the next day",
    "P.raw.pername_nextasked": "Share of business names returned on one day that were returned again the next day, counting a next day with no names as not returned",
    "P.gated.pername_nextasked": "Share of business names returned on one day that were returned again the next day, counting a next day with no names as not returned",
    "J.raw.pair_bothnamed.median": "Median overlap between the full lists of businesses named on two consecutive days",
    "J.raw.pair_bothnamed.mean": "Average overlap between the full lists of businesses named on two consecutive days",
    "J.gated.pair_bothnamed.median": "Median overlap between the full lists of businesses named on two consecutive days",
    "J.gated.pair_bothnamed.mean": "Average overlap between the full lists of businesses named on two consecutive days",
    "C.gated.pair_union": "Average share of the combined list that changed between two consecutive days",
    "H.gated.cellmean": "Concentration index of who gets named (1 means one business takes every mention)",
    "invH.gated.cellmean": "Number of equally common businesses that a market and niche behaves like",
    "N.gated.day": "Share of answered days on which at least one business name was returned",
    "Y.answered.day": "Share of asked days on which the engine returned at least one business name",
    "S.gated.coinflip": "Share of named businesses that appear on 20 to 80 percent of days, so a single day's answer is close to a coin flip",
    "A.raw.pair": "Average overlap between two engines' lists of businesses on the same day",
    "A.gated.pair": "Average overlap between two engines' lists of businesses on the same day",
}
UNIT_WORDS = {
    "P.raw.pername": "named businesses", "P.gated.pername": "named businesses",
    "P.raw.pername_nextasked": "named businesses", "P.gated.pername_nextasked": "named businesses",
    "J.raw.pair_bothnamed.median": "day pairs", "J.raw.pair_bothnamed.mean": "day pairs",
    "J.gated.pair_bothnamed.median": "day pairs", "J.gated.pair_bothnamed.mean": "day pairs",
    "C.gated.pair_union": "day pairs", "H.gated.cellmean": "market and niche cells", "invH.gated.cellmean": "market and niche cells",
    "N.gated.day": "answered days", "Y.answered.day": "asked days", "S.gated.coinflip": "named businesses",
    "A.raw.pair": "same day comparisons", "A.gated.pair": "same day comparisons",
}


def fmt_val(mid, v):
    if v is None:
        return "no data"
    if mid.startswith(("H.", "invH.")):
        return "%.2f" % v
    return "%d percent" % round(v * 100)


ENGINE_LABEL = {"openai": "OpenAI", "anthropic": "Anthropic", "gemini": "Gemini", "perplexity": "Perplexity"}
SMALL_N = 100


def fmt_one(mid, key, res):
    v, n, d, nc = res[(mid, key)]
    label = " and ".join(ENGINE_LABEL[k] for k in key.split("|"))
    if v is None:
        return "%s no data" % label
    small = ", small sample" if d < SMALL_N else ""
    if mid.startswith(("H.", "invH.")) or M[mid]["statistic"] == "median":
        return "%s %s (%s %s%s)" % (label, fmt_val(mid, v), "{:,}".format(d), UNIT_WORDS[mid], small)
    return "%s %s (%s of %s %s%s)" % (label, fmt_val(mid, v), "{:,}".format(int(n)), "{:,}".format(d), UNIT_WORDS[mid], small)


def sentence(wid, w0, w1, mid, res, engines):
    keys = [k for (m, k) in res if m == mid]
    keys = [k for k in (engines if "|" not in keys[0] else sorted(keys)) if (mid, k) in res]
    parts = [fmt_one(mid, k, res) for k in keys]
    nc = res[(mid, keys[0])][3]
    return "%s, %s to %s (%s): %s. Uses %s across %d market and niche cells. %s %s" % (
        LEAD[mid], w0, w1, "the Sonar window" if wid == "WA" else "the Agent API window" if wid == "WB" else "the whole Edition 1 window",
        "; ".join(parts), NAME_NOTE[M[mid]["name_set"]], nc, SETTINGS_API, SETTINGS_PPLX[wid])


BAD = [("em dash", "—"), ("en dash", "–")]
BAD_WORDS = ["honest", "first", "only"]
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿]")


def copy_law(text):
    """Returns a list of violations (empty = clean)."""
    bad = [n for n, ch in BAD if ch in text]
    low = text.lower()
    bad += [w for w in BAD_WORDS if re.search(r"\b" + w + r"\b", low)]
    if EMOJI.search(text):
        bad.append("emoji")
    return bad


def own_name_rows(path, wins):
    """Reads own_name_daily.csv (scripts/own_name_rate.py) and returns rows per window and engine."""
    with io.open(path, encoding="utf-8", newline="") as fh:
        daily = [{k: (int(v) if k not in ("date", "engine") else v) for k, v in r.items()} for r in csv.DictReader(fh)]
    out = []
    for wid, w0, w1 in wins:
        for e in ("openai", "anthropic", "gemini", "perplexity"):
            rs = [r for r in daily if r["engine"] == e and w0 <= r["date"] <= w1]
            f = lambda k: len({r["date"] for r in rs if r[k] > 0})
            out.append([wid, w0, w1, e, regime(wid, e), f("rows"), f("rows_ok"), f("rows_names"), f("rows_named_us"), f("rows_text_us")])
    return out


OWN_HEADER = ["window_id", "window_from", "window_to", "engine", "engine_regime", "days_asked", "days_run_ok", "days_with_names",
              "days_named_us", "days_text_mentions_us"]


# ---------------------------------------------------------------- selftest
def _fx_rows():
    def r(d, names, mk="Town", ni="Plumber", eng="openai", src="a.json"):
        return {"date": d, "kind": "API", "engine": eng, "market": mk, "niche": ni, "businesses_named": names, "source_file": src}
    D = lambda n: "2026-09-%02d" % n
    return [r(D(1), "Acme;Beta"), r(D(2), "ACME;Gamma"), r(D(3), ""), r(D(4), "Acme"), r(D(5), "Acme;Beta"), r(D(6), "Zed"),
            r(D(1), "Acme", mk="US nationwide"), r(D(1), "Quar", src="q.json"), r(D(1), "Acme", eng="gemini")]


def selftest(verbose=True):
    fails = []
    say = print if verbose else (lambda *a, **k: None)

    def chk(name, got, want, tol=1e-9):
        ok = got is not None and (abs(got - want) < tol if isinstance(want, float) else got == want)
        say(("  ok   " if ok else "  FAIL ") + "%s got=%s want=%s" % (name, got, want))
        if not ok:
            fails.append(name)

    rows = _fx_rows()
    # fixture truth (hand computed): cell (Town,Plumber) openai days 1..6, day 3 empty, day 1 unioned with the quarantined row excluded
    ser = raw_series(rows, "2026-09-01", "2026-09-27", "38", quar=frozenset({"q.json"}), engines=("openai",))
    s = ser[(("Town", "Plumber"), "openai")]
    chk("cells in scope 38 (US nationwide dropped)", float(len(ser)), 1.0)
    chk("asked days = 6", float(len(s["asked"])), 6.0)
    chk("answered days = 5", float(len(s["answered"])), 5.0)
    n, d = p_counts(s)
    # P: d1 {acme,beta}->d2 {acme,gamma}: 1/2 ; d2 -> d3 not answered, skipped ; d4 {acme} -> d5 {acme,beta}: 1/1 ; d5 -> d6 {zed}: 0/2 ; total 2/5
    chk("P raw numerator 2", float(n), 2.0); chk("P raw denominator 5", float(d), 5.0)
    na, da = p_counts_asked(s["named"], s["asked"])
    chk("P raw next-day-asked numerator 2", float(na), 2.0); chk("P raw next-day-asked denominator 7", float(da), 7.0)
    j = pair_j(s)
    # J: (d1,d2)=1/3, (d4,d5)=1/2, (d5,d6)=0 ; mean 5/18, median 1/3
    chk("J pairs n=3", float(len(j)), 3.0)
    chk("J raw median = 1/3", stat_of(j)[0], 1 / 3)
    chk("J raw mean = 5/18", sum(j) / len(j), 5 / 18)
    chk("Y = answered/asked = 5/6", len(s["answered"]) / len(s["asked"]), 5 / 6)
    ser_all = raw_series(rows, "2026-09-01", "2026-09-27", "all", engines=("openai",))
    chk("scope all keeps US nationwide cell (2 cells)", float(len(ser_all)), 2.0)
    ser_q = raw_series(rows, "2026-09-01", "2026-09-27", "38", quar=frozenset(), engines=("openai",))
    chk("without quarantine the extra file lands in day 1 set", float(len(ser_q[(('Town', 'Plumber'), 'openai')]["named"]["2026-09-01"])), 3.0)
    # day agreement: openai vs gemini on day 1 only: {acme,beta} vs {acme} = 1/2
    ser2 = raw_series(rows, "2026-09-01", "2026-09-27", "38", quar=frozenset({"q.json"}), engines=("openai", "gemini"))
    chk("A raw openai|gemini day 1 = 1/2", day_agreement(ser2[(("Town", "Plumber"), "openai")], ser2[(("Town", "Plumber"), "gemini")])[0], 0.5)
    # method registry and sentences
    chk("every method has a lead, a unit word and a definition", float(all(m in LEAD and m in UNIT_WORDS and M[m]["definition"] for m in M)), 1.0)
    fake = {("P.raw.pername", "openai"): (0.4, 2, 5, 38), ("J.raw.pair_bothnamed.median", "openai"): (1 / 3, 0, 3, 38),
            ("A.raw.pair", "openai|gemini"): (0.5, 1, 2, 38), ("H.gated.cellmean", "openai"): (0.31, 0, 3, 38)}
    sents = [sentence("WA", "2026-09-01", "2026-09-27", m, fake, ["openai"]) for m in ("P.raw.pername", "J.raw.pair_bothnamed.median", "A.raw.pair", "H.gated.cellmean")]
    chk("generated sentences pass the copy laws", float(not any(copy_law(t) for t in sents)), 1.0)
    chk("copy law catches an em dash, a banned word and an emoji", float(copy_law("a — b")[0] == "em dash" and "honest" in copy_law("An honest result") and "emoji" in copy_law("ok \U0001F600")), 1.0)
    chk("sentence names the denominator and flags a small sample", float("(2 of 5 named businesses, small sample)" in sents[0]), 1.0)
    big = {("P.raw.pername", "openai"): (0.4, 400, 1000, 38)}
    chk("a large denominator is not flagged", float("(400 of 1,000 named businesses)" in sentence("WA", "2026-09-01", "2026-09-27", "P.raw.pername", big, ["openai"])), 1.0)
    chk("window list clips to the data", float(windows("2026-10-07")[1][2] == "2026-10-07" and windows("2026-12-01")[1][2] == E1_END), 1.0)
    # v1 formulas stay GREEN
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        v1ok = v1.selftest()
    chk("v1.1 selftest GREEN", float(v1ok), 1.0)
    say("SELFTEST " + ("RED: " + ",".join(fails) if fails else "GREEN"))
    return not fails


def redproof():
    global BREAK2
    good = selftest(verbose=False)
    print("baseline selftest:", "GREEN" if good else "RED (unexpected)")
    allred = good
    for flag in ("JMED", "PRAW", "SCOPE"):
        BREAK2 = {flag}
        red = not selftest(verbose=False)
        print("  RM2_BREAK=%s -> selftest %s" % (flag, "RED (proof holds)" if red else "GREEN (PROOF FAILED)"))
        allred = allred and red
    BREAK2 = set()
    # DEN and YDEN act inside compute_window; prove with a two-cell synthetic run through it
    for flag in ("DEN", "YDEN"):
        base = _compute_probe()
        BREAK2 = {flag}
        broken = _compute_probe()
        BREAK2 = set()
        red = base != broken
        print("  RM2_BREAK=%s -> compute_window output %s" % (flag, "differs (proof holds)" if red else "unchanged (PROOF FAILED)"))
        allred = allred and red
    print("REDPROOF " + ("PASS" if allred else "FAIL"))
    return allred


def _compute_probe():
    rows = _fx_rows()
    raw = raw_series(rows, "2026-09-01", "2026-09-27", "38", quar=frozenset({"q.json"}), engines=("openai",))
    c = ("Town", "Plumber")
    S = lambda a, n: {"answered": set(a), "named": {k: set(v) for k, v in n.items()}}
    gs = {("Town", "Plumber", "openai"): S(raw[(c, "openai")]["answered"], raw[(c, "openai")]["named"])}
    res = compute_window(raw, gs, [c], ["openai"])
    return {k: v[:3] for k, v in res.items() if k[0] in ("C.gated.pair_union", "Y.answered.day", "P.raw.pername")}


# ---------------------------------------------------------------- regression against published numbers
def regress(csv_path, rows, res_wa, v1_table, quar):
    """Compares v2 WA values to the published v1 table, and the 46-cell raw numbers to W2-3. Returns (lines, ok)."""
    lines = []; ok = True
    # v1 pooled rows
    idx = {}
    with io.open(v1_table, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["level"] == "pooled":
                idx[(r["metric"], r["engine"], r["engine_pair"])] = r
    pairs = {("P", "P.gated.pername"), ("C", "C.gated.pair_union"), ("N", "N.gated.day"), ("H", "H.gated.cellmean"), ("invH", "invH.gated.cellmean")}
    checked = 0
    for (metric, mid) in sorted(pairs):
        for (m, e, p), r in sorted(idx.items()):
            if m != metric or p:
                continue
            got = res_wa.get((mid, e))
            want = float(r["value"])
            good = got is not None and "%.6f" % got[0] == "%.6f" % want
            checked += 1
            ok = ok and good
            lines.append(("MATCH " if good else "DIFF  ") + "v1 %s %s: v2=%s published=%s" % (metric, e, "%.6f" % got[0] if got else None, r["value"]))
    for (m, e, p), r in sorted(idx.items()):
        if m == "A":
            got = res_wa.get(("A.gated.pair", p))
            good = got is not None and "%.6f" % got[0] == "%.6f" % float(r["value"])
            checked += 1; ok = ok and good
            lines.append(("MATCH " if good else "DIFF  ") + "v1 A %s: v2=%s published=%s" % (p, "%.6f" % got[0] if got else None, r["value"]))
    # W2-3 raw reproduction (46 cells: every market incl. US nationwide, no quarantine, case folded)
    s46 = raw_series(rows, "2026-09-01", "2026-09-27", "all", engines=("perplexity",))
    num = den = 0; js = []; den_v1rule = 0
    for k, s in s46.items():
        n, d = p_counts_asked(s["named"], s["asked"]); num += n; den += d; js += pair_j(s)
        den_v1rule += p_counts(s)[1]
    med = statistics.median(js); mean = sum(js) / len(js)
    for name, got, want in (("W2-3 raw P numerator", num, 2881), ("W2-3 raw P denominator", den, 7017), ("W2-3 raw pairs", len(js), 1008),
                            ("W2-3 raw median Jaccard (4 dp)", round(med, 4), 0.2922), ("W2-3 raw mean Jaccard (4 dp)", round(mean, 4), 0.3384),
                            ("W2-3 cells", len(s46), 46)):
        good = got == want
        ok = ok and good
        lines.append(("MATCH " if good else "DIFF  ") + "%s: v2=%s published=%s" % (name, got, want))
    lines.append("note: under the v1 rule (next day must have named someone) the same 46 cells give %d of %d = %.4f" % (num, den_v1rule, num / den_v1rule))
    lines.append("regression rows checked against the v1 table: %d" % checked)
    return lines, ok


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for ch in iter(lambda: fh.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()


def run(csv_path, out_dir, through=None, own_path=None):
    os.makedirs(out_dir, exist_ok=True)
    rows = list(csv.DictReader(io.open(csv_path, encoding="utf-8", newline="")))
    last = max(r["date"] for r in rows)
    wins = windows(through or last)
    g = gate_module()
    quar = g.load_quarantine(g.DEFAULT_QUARANTINE)
    all_rows = []; sentences = []; res_by = {}
    for wid, w0, w1 in wins:
        gated, engines, cells, nrows = gated_series(csv_path, w0, w1)
        raw = raw_series(rows, w0, w1, "38", quar)
        res = compute_window(raw, gated, cells, engines)
        res_by[wid] = res
        all_rows += rows_for(wid, w0, w1, res)
        sentences.append((wid, w0, w1, res, engines, len(cells), nrows))
        print("window %s %s..%s api_rows_in_window=%d cells=%d" % (wid, w0, w1, nrows, len(cells)))
    with open(os.path.join(out_dir, "metrics_v2.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n"); w.writerow(HEADER); w.writerows(all_rows)
    with open(os.path.join(out_dir, "metrics_v2_methods.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n"); w.writerow(["method_id", "metric", "name_set", "statistic", "denominator_unit", "definition"])
        for m in M.values():
            w.writerow([m["method_id"], m["metric"], m["name_set"], m["statistic"], m["denominator_unit"], m["definition"]])
    lines = ["# Metrics v2 sentences (public-safe)", "",
             "Generated by receipts_metrics_v2.py from %s (data through %s). One sentence per metric and window." % (os.path.basename(csv_path), last), ""]
    violations = []
    for wid, w0, w1, res, engines, nc, nrows in sentences:
        lines += ["## %s: %s to %s (%s)" % (wid, w0, w1, WINDOWS_DOC[wid]), ""]
        for mid in M:
            if any(k[0] == mid for k in res):
                t = sentence(wid, w0, w1, mid, res, engines)
                violations += [(mid, wid, v) for v in copy_law(t)]
                lines += ["**%s**  %s" % (mid, t), ""]
    if own_path and os.path.exists(own_path):
        orows = own_name_rows(own_path, wins)
        with open(os.path.join(out_dir, "metrics_v2_own_name.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n"); w.writerow(OWN_HEADER); w.writerows(orows)
        lines += ["## INTERNAL ONLY: Namebeam's own name (do not publish until the Chief of Staff decides)", "",
                  "The rule in the common brief says never publish a line that Namebeam scores zero. The counts below are for the record and for decisions, not for public text.", ""]
        for r in orows:
            wid, w0, w1, e, rg, asked, ok_, names, us, txt = r
            lines += ["- %s, %s to %s, %s: asked on %d days, run OK on %d, named at least one business on %d, named Namebeam on %d, the answer text mentioned Namebeam on %d." % (wid, w0, w1, e, asked, ok_, names, us, txt)]
        lines += [""]
    with open(os.path.join(out_dir, "metrics_v2_sentences.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    with open(os.path.join(out_dir, "metrics_v2_inputs.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n"); w.writerow(["role", "file", "bytes", "sha256"])
        for role, p in (("script", os.path.abspath(__file__)), ("v1_formulas", V1_PATH), ("name_gate", GATE_PATH),
                        ("gate_dep_citation", CITE_PATH), ("gate_dep_panels", PANEL_PATH), ("quarantine", g.DEFAULT_QUARANTINE), ("input_dataset", csv_path)):
            w.writerow([role, os.path.basename(p), os.path.getsize(p), sha(p)])
    v1_table = os.path.join(HERE, "..", "1005B_METRICS", "metrics_v1_sept.csv")
    reg_lines, reg_ok = regress(csv_path, rows, res_by["WA"], v1_table, quar)
    with open(os.path.join(out_dir, "metrics_v2_regress.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(reg_lines) + "\nREGRESS " + ("MATCH" if reg_ok else "DIFF") + "\n")
    print("\n".join(reg_lines)); print("REGRESS " + ("MATCH" if reg_ok else "DIFF"))
    print("copy law violations in sentences:", violations if violations else "none")
    print("metric rows", len(all_rows), "->", out_dir)
    return reg_ok and not violations


if __name__ == "__main__":
    a = sys.argv[1:]
    arg = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    if "--selftest" in a:
        sys.exit(0 if selftest() else 1)
    elif "--redproof" in a:
        sys.exit(0 if redproof() else 1)
    elif "--run" in a:
        sys.exit(0 if run(arg("--csv", DEFAULT_CSV), arg("--out", HERE), arg("--through"), arg("--own-name")) else 1)
    else:
        print(__doc__)
