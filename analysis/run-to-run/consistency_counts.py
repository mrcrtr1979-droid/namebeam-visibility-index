"""consistency_counts.py - run-to-run consistency: how often the SAME engine, asked the SAME question in the SAME market on consecutive run days,
records the same names. The recorded lists can include directories and other non-business entries. Reproduces every figure on the Namebeam "do lists repeat run to run" page.

Reads (read-only) the daily raw archives corpus/e1_raw/<date>.tar.gz (each holds e1/NB-CZ-API_* JSON rows), checks each archive against its
<date>.sha256 sidecar when one sits beside it, and writes nothing except the one CSV named by --csv. The CSV is also printed to stdout.
Needs no private file: it uses only the recorded name lists (competitors_mentioned) and the public trade, market and engine fields.
Answer text of Perplexity and of Google AI Overview is never read into any output: counts and rates only (R82).

Rows used: API rows with row_type research_segment (the fixed market question, one wording per trade and city), run 1, dated 2026-09-28 to 2026-11-23
  (Perplexity moved to the Agent API on 2026-09-28; the same window is used for all four engines). Skipped and counted: nationwide questions (market
  "US nationwide") and single-business pilot slugs (slug carries the word _pilot). Rows whose run_status is not OK (Gemini FAILED rows, QUOTA_BLOCKED
  rows) are excluded and counted. Run 2 rows (the Anthropic repeats of 2026-10-24) are used only for the same-day repeat pair type.
Series: one market question for one engine (trade, city, engine).
Consecutive pair (pair_type consecutive_run_days): two adjacent days of the run calendar (the dates represented by retained research-segment rows; no run on 2026-10-05, so
  2026-10-04 and 2026-10-06 are adjacent) where the series has a row on both days. A pair is dropped, in this order, and counted: the pair straddles
  2026-11-10 for OpenAI (OUR collection change from gpt-5-mini to gpt-5.6-terra: OpenAI pairs are split into before and from that date); a row on
  either day is not OK; the recorded model differs between the two days (ignoring missing values and removing the provider prefix, version suffix and date stamp); both name lists are
  empty after the name rule. What remains is evaluated.
Name rules (the set of names a row lists in competitors_mentioned, as recorded; includes headings, place names, associations and directories):
  Both normalization paths standardize selected punctuation, apply NFKD normalization, discard remaining non-ASCII characters, and collapse whitespace.
  all_recorded_names   : after cleaning and lowercasing, the rule expands ampersands to "and", handles apostrophes, extracts letter-and-digit tokens, drops the, and, llc, inc, corp, ltd, pllc and pc, and joins the remaining tokens. This measures recurrence of normalized strings without resolving business identities.
  single_market_names  : the same, keeping names recorded in exactly one market among OK run-1 rows across all engines in the window (drops directories,
                         associations and national names that recur across markets; a sensitivity, not a business filter).
  exact_clean_string   : the cleaned, lowercased string, preserving the remaining spaces and punctuation.
Measures per cell (pairs pooled across series): Jaccard overlap of the two days' name sets (intersection over union), the share of evaluated pairs with
  identical sets, the share with zero overlap (union non-empty, intersection empty; one empty list and one non-empty list counts as zero overlap).
  The _both_nonempty columns repeat the three measures on the pairs where both days listed at least one name. series_mean_jaccard_* is the spread of
  the per-series mean Jaccard over series with 10 or more evaluated pairs. Pairs are not independent (a series chains its pairs, a market repeats
  across engines), so no interval is given.
Other pair types: matched_lag_1, _7, _14 and _28 start from common candidate anchors with rows at calendar offsets 0, 1, 7, 14 and 28. Eligible anchors have five OK rows, no conflict among recorded model strings after normalization, and one OpenAI collection period. For each eligible anchor on day d, a lag of L calendar days pairs that row with the row on day d + L. Each lag separately drops pairs with two empty name sets. same_day_repeat pairs the Anthropic
  run 2 row of 2026-10-24 with the run 1 row of that day and that series.
Market class is OUR assignment by city-proper population (2020 US Census bands, approximate): large city over 250,000; mid-size city 50,000 to 250,000;
  small town or county: Branson and Santa Cruz County. Trade label is the niche text before " / ".
Usage: py -3 -I consistency_counts.py [--csv OUT.csv] [--dataset DATASET.csv] ARCHIVE [ARCHIVE ...]
  --dataset optionally cross-checks every OK row against corpus/namebeam/Namebeam_E1_Dataset_LATEST.csv (prints counts to stderr).
"""
import collections
import csv
import datetime
import hashlib
import json
import os
import re
import statistics
import sys
import tarfile
import unicodedata

WS, WE = "2026-09-28", "2026-11-23"
OPENAI_SPLIT = "2026-11-10"          # OUR collection change in the OpenAI series
MIN_SERIES_PAIRS = 10
ENGINES = ("anthropic", "openai", "perplexity", "gemini")
STOP = {"the", "and", "llc", "inc", "corp", "ltd", "pllc", "pc"}
MATCHED_LAGS = (1, 7, 14, 28)
PUNCT = {0x2013: "-", 0x2014: "-", 0x2019: "'", 0x2018: "'"}

LARGE_CITY = {"Atlanta GA", "Charlotte NC", "Chicago IL", "Columbus OH", "Dallas TX", "Denver CO", "Houston TX",
              "Kansas City MO", "Miami FL", "Phoenix AZ", "Nashville TN", "St. Louis MO"}
MID_CITY = {"Pleasanton CA", "Tyler TX", "Fort Lauderdale FL", "West Palm Beach FL", "St. Charles MO"}
SMALL_OR_COUNTY = {"Branson MO", "Santa Cruz County CA"}

FNAME = re.compile(r"NB-CZ-API_(\d{4}-\d\d-\d\d)_(segment_.+?)\.json$")
RULES = ("all_recorded_names", "single_market_names", "exact_clean_string")


def market_class(market):
    if market in LARGE_CITY:
        return "large city (over 250,000)"
    if market in MID_CITY:
        return "mid-size city (50,000 to 250,000)"
    if market in SMALL_OR_COUNTY:
        return "small town or county"
    return "unclassified"


def clean(s):
    s = (s or "").translate(PUNCT)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s).strip()


def tokens_of(s):
    t = clean(s).casefold().replace("&", " and ").replace("'s ", "s ").replace("'", "")
    return re.findall(r"[a-z0-9]+", t)


def name_key(s):
    return "".join(t for t in tokens_of(s) if t not in STOP)


def model_family(m):
    """Recorded model string reduced so that a provider prefix, a version suffix and a date stamp do not count as a change."""
    if not m:
        return ""
    m = m.lower()
    m = re.sub(r"^[a-z]+\.", "", m)
    m = re.sub(r"-v\d+:\d+$", "", m)
    m = re.sub(r"-\d{8}$", "", m)
    m = re.sub(r"-\d{4}-\d\d-\d\d$", "", m)
    return m


def add_days(d, n):
    return (datetime.date.fromisoformat(d) + datetime.timedelta(days=n)).isoformat()


# ---- loading -----------------------------------------------------------------------------------------------------------
def verify_sidecars(archives):
    ok = bad = none = 0
    for p in archives:
        side = p[:-7] + ".sha256" if p.endswith(".tar.gz") else p + ".sha256"
        if not os.path.exists(side):
            none += 1
            continue
        want = open(side, encoding="ascii").read().split()[0].lower()
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() == want:
            ok += 1
        else:
            bad += 1
            print("# SIDECAR MISMATCH " + p, file=sys.stderr)
    print("# sidecars verified %d, mismatched %d, absent %d of %d archives" % (ok, bad, none, len(archives)), file=sys.stderr)
    if bad:
        sys.exit("sidecar mismatch: stop")


def load(archives):
    rows = []
    skipped = collections.Counter()
    seen = set()
    for tgz in archives:
        with tarfile.open(tgz, "r:gz") as t:
            for m in t:
                if not m.isfile():
                    continue
                parts = m.name.split("/")
                if len(parts) < 2 or parts[-2] != "e1" or not parts[-1].startswith("NB-CZ-API_"):
                    continue
                fn = parts[-1]
                if fn in seen:
                    raise ValueError("Duplicate file: " + fn)
                seen.add(fn)
                d = json.load(t.extractfile(m))
                date = d.get("date_utc") or ""
                if not (WS <= date <= WE) or d.get("row_type") != "research_segment":
                    continue
                e = d.get("engine")
                mm = FNAME.search(fn)
                slug = mm.group(2) if mm else ""
                market = d.get("market") or ""
                if market == "US nationwide":
                    skipped[(e, "nationwide question")] += 1
                    continue
                if re.search(r"_pilot(_|$)", slug):
                    skipped[(e, "single-business pilot")] += 1
                    continue
                names = [clean(n) for n in (d.get("competitors_mentioned") or []) if isinstance(n, str) and clean(n)]
                rows.append({
                    "fn": fn, "date": date, "engine": e, "status": d.get("run_status") or "", "run": d.get("run") or 1,
                    "niche": d.get("niche") or "", "market": market, "names": names,
                    "model": model_family((d.get("engine_meta") or {}).get("served_model")),
                })
    return rows, skipped


def trade_of(niche):
    return niche.split(" / ")[0].strip()


# ---- pairs -------------------------------------------------------------------------------------------------------------
def build_series(rows):
    """{(trade, market, engine): {date: row}} for run 1 rows; {same key + date: row} for run 2 rows."""
    s1, s2 = collections.defaultdict(dict), {}
    for r in rows:
        key = (trade_of(r["niche"]), r["market"], r["engine"])
        if r["run"] == 1:
            if r["date"] in s1[key]:
                raise ValueError("duplicate series row %s %s" % (key, r["date"]))
            s1[key][r["date"]] = r
        else:
            s2[key + (r["date"],)] = r
    return s1, s2


def single_market_set(rows):
    mk = collections.defaultdict(set)
    for r in rows:
        if r["status"] == "OK" and r["run"] == 1:
            for n in r["names"]:
                k = name_key(n)
                if k:
                    mk[k].add(r["market"])
    return {k for k, v in mk.items() if len(v) == 1}


def name_sets(r, single):
    exact = frozenset(n.casefold() for n in r["names"])
    keys = frozenset(k for k in (name_key(n) for n in r["names"]) if k)
    return {"all_recorded_names": keys, "single_market_names": frozenset(k for k in keys if k in single), "exact_clean_string": exact}


def period_of(engine, d1, d2):
    if engine != "openai":
        return "all"
    if d2 < OPENAI_SPLIT:
        return "before_" + OPENAI_SPLIT
    if d1 >= OPENAI_SPLIT:
        return "from_" + OPENAI_SPLIT
    return "straddle"


def drop_reason(rs, engine, d_first, d_last):
    """Why a group of rows cannot be evaluated, in the fixed order, or None."""
    if period_of(engine, d_first, d_last) == "straddle":
        return "straddle"
    if any(r["status"] != "OK" for r in rs):
        return "failed_row"
    if len({r["model"] for r in rs if r["model"]}) > 1:
        return "model_change"
    return None


def make_pairs(s1, s2, calendar):
    """Tuples (pair_type, key, d1, d2, r1, r2, forced_reason, period)."""
    pairs = []
    for key, byday in s1.items():
        eng = key[2]
        for i in range(len(calendar) - 1):
            a, b = calendar[i], calendar[i + 1]
            if a in byday and b in byday:
                pairs.append(("consecutive_run_days", key, a, b, byday[a], byday[b], drop_reason([byday[a], byday[b]], eng, a, b), period_of(eng, a, b)))
        for a in byday:
            days = [a, add_days(a, 1), add_days(a, 7), add_days(a, 14), add_days(a, 28)]
            if not all(d in byday for d in days):
                continue
            reason = drop_reason([byday[d] for d in days], eng, days[0], days[-1])
            per = period_of(eng, days[0], days[-1])
            for lag in MATCHED_LAGS:
                b = add_days(a, lag)
                pairs.append(("matched_lag_%d" % lag, key, a, b, byday[a], byday[b], reason, per))
    for k2, r2 in s2.items():
        key, day = k2[:3], k2[3]
        r1 = s1.get(key, {}).get(day)
        if r1 is not None:
            reason = "failed_row" if (r1["status"] != "OK" or r2["status"] != "OK") else None
            pairs.append(("same_day_repeat", key, day, day, r1, r2, reason, "all"))
    return pairs


def evaluate(pair, single):
    """{rule: None (both lists empty) or (jaccard, identical, zero, size1, size2)} for a pair that is not dropped."""
    r1, r2 = pair[4], pair[5]
    s_a, s_b = name_sets(r1, single), name_sets(r2, single)
    out = {}
    for rule in s_a:
        a, b = s_a[rule], s_b[rule]
        u = a | b
        if not u:
            out[rule] = None
            continue
        out[rule] = (len(a & b) / len(u), a == b, not (a & b), len(a), len(b))
    return out


# ---- aggregation -------------------------------------------------------------------------------------------------------
class Cell(object):
    def __init__(self):
        self.pairs = self.failed = self.model = self.straddle = self.empty = self.ev = 0
        self.j, self.sizes, self.series = [], [], collections.defaultdict(list)
        self.ident = self.zero = 0
        self.ne_j, self.ne_ident, self.ne_zero = [], 0, 0
        self.d_lo, self.d_hi = None, None


def add(cell, reason, res, rule, key, d1, d2):
    cell.pairs += 1
    if reason == "failed_row":
        cell.failed += 1
    elif reason == "model_change":
        cell.model += 1
    elif reason == "straddle":
        cell.straddle += 1
    else:
        v = res[rule]
        if v is None:
            cell.empty += 1
            return
        cell.ev += 1
        cell.j.append(v[0])
        cell.ident += v[1]
        cell.zero += v[2]
        cell.sizes.extend((v[3], v[4]))
        cell.series[key].append(v[0])
        if v[3] and v[4]:
            cell.ne_j.append(v[0])
            cell.ne_ident += v[1]
            cell.ne_zero += v[2]
        cell.d_lo = d1 if cell.d_lo is None or d1 < cell.d_lo else cell.d_lo
        cell.d_hi = d2 if cell.d_hi is None or d2 > cell.d_hi else cell.d_hi


def fmt(x, nd=3):
    return "" if x is None else round(x, nd)


def pct(n, d):
    return "" if not d else round(100.0 * n / d, 1)


def dims_of(key):
    trade, market, engine = key
    return (("engine", "all local-market series"), ("trade", trade), ("market_class", market_class(market)), ("city", market),
            ("series", trade + " | " + market))


HEAD = ["pair_type", "engine", "period", "dimension", "population", "name_rule", "window_start", "window_end", "series_n",
        "rows_in_period", "rows_not_ok", "run_day_pairs", "dropped_failed_row", "dropped_model_change", "dropped_straddle",
        "dropped_both_empty", "pairs_evaluated", "mean_jaccard", "median_jaccard", "identical_n", "identical_pct", "zero_overlap_n",
        "zero_overlap_pct", "pairs_both_nonempty", "mean_jaccard_both_nonempty", "identical_pct_both_nonempty", "zero_overlap_pct_both_nonempty",
        "mean_names_per_answer", "series_mean_jaccard_median", "series_mean_jaccard_min", "series_mean_jaccard_max",
        "series_with_10_or_more_pairs", "note"]


def main(archives, csv_path, dataset_path):
    verify_sidecars(archives)
    rows, skipped = load(archives)
    single = single_market_set(rows)
    s1, s2 = build_series(rows)
    calendar = sorted({r["date"] for r in rows})
    pairs = make_pairs(s1, s2, calendar)

    cells = collections.defaultdict(Cell)      # (pair_type, engine, period, dimension, population, rule) -> Cell
    for pair in pairs:
        ptype, key, d1, d2, r1, r2, reason, per = pair
        res = evaluate(pair, single) if reason is None else None
        for rule in RULES:
            if rule == "exact_clean_string" and ptype != "consecutive_run_days":
                continue
            for dim, pop in dims_of(key):
                if ptype != "consecutive_run_days" and dim != "engine":
                    continue
                if rule == "exact_clean_string" and dim not in ("engine", "trade"):
                    continue
                add(cells[(ptype, key[2], per, dim, pop, rule)], reason, res, rule, key, d1, d2)

    # row counts (all run-1 local-market rows in the window; not-OK rows are excluded from every pair and counted here)
    status_by_engine = collections.defaultdict(collections.Counter)
    sched, notok = collections.defaultdict(int), collections.defaultdict(int)
    for key, byday in s1.items():
        for d, r in byday.items():
            status_by_engine[key[2]][r["status"]] += 1
            per = ("before_" + OPENAI_SPLIT if d < OPENAI_SPLIT else "from_" + OPENAI_SPLIT) if key[2] == "openai" else "all"
            for dim, pop in dims_of(key):
                sched[(key[2], per, dim, pop)] += 1
                notok[(key[2], per, dim, pop)] += r["status"] != "OK"

    out = [HEAD]
    order = {"consecutive_run_days": 0, "matched_lag_1": 1, "matched_lag_7": 2, "matched_lag_14": 3, "matched_lag_28": 4, "same_day_repeat": 5}
    dim_order = {"engine": 0, "trade": 1, "market_class": 2, "city": 3, "series": 4}
    rule_order = {r: i for i, r in enumerate(RULES)}
    eng_order = {e: i for i, e in enumerate(ENGINES)}
    for k in sorted(cells, key=lambda k: (order[k[0]], rule_order[k[5]], dim_order[k[3]], k[4], eng_order[k[1]], k[2])):
        ptype, eng, per, dim, pop, rule = k
        c = cells[k]
        lookup = (eng, per, dim, pop)
        main_row = ptype == "consecutive_run_days" and per != "straddle"
        note = ""
        if per == "straddle" and ptype.startswith("matched_lag"):
            note = "At least one of the five required rows falls on each side of OUR 2026-11-10 OpenAI collection change. The anchor is excluded."
        elif per == "straddle":
            note = "pair of 2026-11-09 and 2026-11-10 straddles OUR OpenAI collection change; dropped"
        elif ptype == "consecutive_run_days" and dim == "engine" and rule == "all_recorded_names":
            st = status_by_engine[eng]
            note = "run_status of all run-1 rows 2026-09-28 to 2026-11-23: " + "; ".join("%s %d" % (s, n) for s, n in sorted(st.items()))
        elif ptype.startswith("matched_lag"):
            note = "Common eligible series-days have OK rows at offsets 0, 1, 7, 14 and 28, with no recorded model conflict. Double-empty pairs are excluded separately at each lag."
        elif ptype == "same_day_repeat":
            note = "run 2 retries of 2026-10-24 against the run 1 row of the same series and day"
        big = [statistics.mean(v) for v in c.series.values() if len(v) >= MIN_SERIES_PAIRS]
        ne = len(c.ne_j)
        out.append([ptype, eng, per, dim, pop, rule, c.d_lo or "", c.d_hi or "", len(c.series),
                    sched.get(lookup, "") if main_row else "", notok.get(lookup, "") if main_row else "",
                    c.pairs, c.failed, c.model, c.straddle, c.empty, c.ev,
                    fmt(statistics.mean(c.j)) if c.j else "", fmt(statistics.median(c.j)) if c.j else "",
                    c.ident, pct(c.ident, c.ev), c.zero, pct(c.zero, c.ev),
                    ne, fmt(statistics.mean(c.ne_j)) if ne else "", pct(c.ne_ident, ne), pct(c.ne_zero, ne),
                    fmt(statistics.mean(c.sizes), 2) if c.sizes else "",
                    fmt(statistics.median(big)) if big else "", fmt(min(big)) if big else "", fmt(max(big)) if big else "", len(big), note])
        assert c.pairs == c.failed + c.model + c.straddle + c.empty + c.ev, k   # every pair sits in exactly one bucket

    csv.writer(sys.stdout, lineterminator="\n").writerows(out)
    if csv_path:
        with open(csv_path, "w", newline="", encoding="ascii") as f:
            csv.writer(f, lineterminator="\n").writerows(out)
    print("# skipped rows (counted, not used): " + "; ".join("%s %s %d" % (e, w, n) for (e, w), n in sorted(skipped.items())), file=sys.stderr)
    print("# series %d, run days %d, rows %d, pairs built %d" % (len(s1), len(calendar), len(rows), len(pairs)), file=sys.stderr)
    if dataset_path:
        cross_check(rows, dataset_path)


def cross_check(rows, path):
    """Match each OK retained API segment row by source filename, then compare the cleaned name count and the ordered concatenation of lowercase ASCII letters and digits. This comparison does not verify individual name boundaries."""
    ds = {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["kind"] == "API":
                ds[r["source_file"]] = r
    n = found = cnt_ok = txt_ok = 0
    for r in rows:
        if r["status"] != "OK":
            continue
        n += 1
        x = ds.get(r["fn"])
        if x is None:
            continue
        found += 1
        cnt_ok += int(x["businesses_named_count"] or 0) == len(r["names"])
        a = re.sub(r"[^a-z0-9]", "", clean(x["businesses_named"]).casefold())
        b = re.sub(r"[^a-z0-9]", "", clean(";".join(r["names"])).casefold())
        txt_ok += a == b
    print("# dataset cross-check: OK rows %d, found in dataset %d, same name count %d, same names %d" % (n, found, cnt_ok, txt_ok), file=sys.stderr)


if __name__ == "__main__":
    usage = "Usage: consistency_counts.py [--csv OUT.csv] [--dataset DATASET.csv] ARCHIVE [ARCHIVE ...]"
    args = sys.argv[1:]
    csv_path = dataset = None
    while args[:1] and args[0] in ("--csv", "--dataset"):
        if len(args) < 2:
            sys.exit(usage)
        if args[0] == "--csv":
            csv_path = args[1]
        else:
            dataset = args[1]
        args = args[2:]
    if not args:
        sys.exit(usage)
    main(args, csv_path, dataset)
