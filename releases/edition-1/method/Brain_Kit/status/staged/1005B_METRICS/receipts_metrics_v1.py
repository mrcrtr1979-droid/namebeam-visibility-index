#!/usr/bin/env python3
"""Receipts Metrics v1.1 (Namebeam / Carter Enterprise). Read-only on the dated E1 record.
Metrics: P persistence, C churn, H concentration (+1/H), A engine agreement, N name rate,
S snapshot risk (added in v1.1; v1 metrics and the v1 CSV rows are unchanged).
Usage: --selftest | --run [--csv PATH] [--out PATH] [--snap PATH]   (RM_BREAK=P,C,H,A,N,S alters a formula to prove RED)
S (per engine, cell, business named on >=1 answered day): p = named_days / answered_days (that engine and cell, window days);
S = min(p, 1-p) = chance one randomly chosen answered day gives the minority verdict. COIN-FLIP if 0.2 <= p <= 0.8
(tested in exact integers: 5*named >= answered and 5*named <= 4*answered, so no float edge error at 0.2 or 0.8).
Cell = market + niche. Window = API rows 2026-09-01..2026-09-27 (Perplexity method changed 2026-09-28).
Name gate and alias merge are imported from 1005A gen_ai_picks_chart.py (not copied).
Answered day (cell, engine, day) = at least one raw business string returned (the 1005A definition).
Named set S(e,c,d) = gated, alias-merged business keys on that answered day."""
import os, sys, csv, itertools
from collections import defaultdict
from datetime import date, timedelta
VERSION = "receipts_metrics_v1_1"
V1_ROW_TAG = "receipts_metrics_v1"  # tag written on v1 rows so the v1 CSV stays byte-identical
HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
W0, W1 = "2026-09-01", "2026-09-27"
BREAK = set(filter(None, os.environ.get("RM_BREAK", "").split(",")))

def nextday(d):
    return (date.fromisoformat(d) + timedelta(days=1)).isoformat()

def jac(a, b):
    u = a | b
    return None if not u else len(a & b) / len(u)

# series: {"answered": set(days), "named": {day: set(keys)}}
def persistence_counts(s):
    """P(b) = #{d: b in S_d and b in S_d+1, d and d+1 answered} / #{d: b in S_d, d+1 answered}. Returns {b: (num, den)}."""
    out = defaultdict(lambda: [0, 0])
    for d in s["answered"]:
        d2 = nextday(d)
        if d2 not in s["answered"]:
            continue
        for b in s["named"].get(d, ()):
            out[b][1] += 1
            hit = b in s["named"].get(d2, ())
            if "P" in BREAK:
                hit = not hit
            out[b][0] += 1 if hit else 0
    return {b: tuple(v) for b, v in out.items()}

def persistence(s):
    pc = persistence_counts(s)
    num = sum(v[0] for v in pc.values()); den = sum(v[1] for v in pc.values())
    return (num / den if den else None), num, den   # cell P = den-weighted mean of P(b) = num/den

def churn(s):
    """C = mean over consecutive answered pairs (d,d+1) of 1 - Jaccard(S_d,S_d+1); pairs with both sets empty are skipped."""
    vals = []
    for d in sorted(s["answered"]):
        d2 = nextday(d)
        if d2 in s["answered"]:
            j = jac(s["named"].get(d, set()), s["named"].get(d2, set()))
            if j is not None:
                vals.append(j if "C" in BREAK else 1 - j)
    return (sum(vals) / len(vals) if vals else None), sum(vals), len(vals)

def concentration(s):
    """H = sum_b share_b^2, share_b = named-days(b)/sum named-days. Returns (H, 1/H, total named-days, n businesses)."""
    cnt = defaultdict(int)
    for d in s["answered"]:
        for b in s["named"].get(d, ()):
            cnt[b] += 1
    tot = sum(cnt.values())
    if not tot:
        return None, None, 0, 0
    H = sum((c / tot) ** (1 if "H" in BREAK else 2) for c in cnt.values())
    return H, 1 / H, tot, len(cnt)

def agreement(s1, s2):
    """A = mean over days both engines answered of Jaccard(S1_d,S2_d); days with both sets empty skipped."""
    vals = []
    for d in sorted(s1["answered"] & s2["answered"]):
        a, b = s1["named"].get(d, set()), s2["named"].get(d, set())
        u = a | b
        if not u:
            continue
        vals.append(len(a & b) / len(a) if ("A" in BREAK and a) else len(a & b) / len(u))
    return (sum(vals) / len(vals) if vals else None), sum(vals), len(vals)

def name_rate(s):
    """N = #answered days with >=1 gated name / #answered days."""
    den = len(s["answered"])
    num = sum(1 for d in s["answered"] if s["named"].get(d))
    if "N" in BREAK:
        num = den - num
    return (num / den if den else None), num, den

def snapshot_rows(s):
    """Per business named on >=1 answered day: (key, named_days, answered_days, p, S, coin_flip)."""
    ans = len(s["answered"])
    cnt = defaultdict(int)
    for d in s["answered"]:
        for b in s["named"].get(d, ()):
            cnt[b] += 1
    out = []
    for b in sorted(cnt):
        n = cnt[b]
        p = n / ans
        sv = p if "S" in BREAK else min(p, 1 - p)
        coin = 5 * n >= ans and 5 * n <= 4 * ans
        out.append((b, n, ans, p, sv, coin))
    return out

def S(answered, named):
    return {"answered": set(answered), "named": {d: set(v) for d, v in named.items()}}

def selftest():
    D = lambda n: "2026-09-%02d" % n
    e1 = S([D(1), D(2), D(3), D(4), D(6)], {D(1): "ab", D(2): "a", D(3): "b", D(4): "a", D(6): "a"})
    e2 = S([D(1), D(2), D(3), D(4)], {D(1): "a", D(2): "a", D(3): "bc", D(4): ""})
    fails = []
    def chk(name, got, want):
        ok = got is not None and abs(got - want) < 1e-9
        print(("  ok   " if ok else "  FAIL ") + "%s got=%s want=%s" % (name, got, want))
        if not ok: fails.append(name)
    # P: a: d1 stays, d2 not (d4->d5 unanswered, excluded) = 1/2; b: d1 no, d3 no = 0/2; pooled 1/4
    p = persistence(e1); chk("P num/den pooled 1/4", p[0], 0.25); chk("P den=4", p[2], 4)
    pc = persistence_counts(e1); chk("P(a)=1/2", pc["a"][0] / pc["a"][1], 0.5); chk("P(b)=0", pc["b"][0] / pc["b"][1], 0.0)
    # C: pairs (1,2) J=1/2 ->.5; (2,3) J=0 ->1; (3,4) J=0 ->1; (4,6) not consecutive. mean 2.5/3
    chk("C=5/6", churn(e1)[0], 2.5 / 3)
    # H: a=4 named-days, b=2; shares 2/3,1/3; H=5/9; 1/H=1.8
    h = concentration(e1); chk("H=5/9", h[0], 5 / 9); chk("1/H=1.8", h[1], 1.8)
    # A: days1-4 both answered: J=1/2,1,1/2, d4 {a} vs {} =0 -> 0.5 (d6 only e1)
    chk("A=0.5", agreement(e1, e2)[0], 0.5)
    # N: e2 answered 4 days, d4 empty after gate -> 3/4 ; e1 5/5
    chk("N e2=3/4", name_rate(e2)[0], 0.75); chk("N e1=1", name_rate(e1)[0], 1.0)
    # S: e1 answered 5 days (d1,2,3,4,6): a named 4 -> p=4/5=.8, S=.2, COIN-FLIP (boundary); b named 2 -> p=.4, S=.4, COIN-FLIP
    sr = {r[0]: r for r in snapshot_rows(e1)}
    chk("S(a) p=0.8 S=0.2", sr["a"][4], 0.2); chk("S(b) p=0.4 S=0.4", sr["b"][4], 0.4)
    chk("S(a) coin-flip at 0.8", 1.0 if sr["a"][5] else 0.0, 1.0); chk("S(b) coin-flip", 1.0 if sr["b"][5] else 0.0, 1.0)
    # S: e2 answered 4 days: a named 2 -> p=.5, S=.5; b,c named 1 -> p=.25, S=.25 coin-flip (>=.2); d4 empty adds no business
    sr2 = {r[0]: r for r in snapshot_rows(e2)}
    chk("S e2 a=0.5", sr2["a"][4], 0.5); chk("S e2 b=0.25", sr2["b"][4], 0.25); chk("S e2 n businesses=3", float(len(sr2)), 3.0)
    # S: always named (p=1) -> S=0, not coin-flip; named 1 of 10 days -> p=.1, S=.1, not coin-flip
    f = S([D(i) for i in range(1, 11)], {D(i): ("z" + ("y" if i == 1 else "")) for i in range(1, 11)})
    sf = {r[0]: r for r in snapshot_rows(f)}
    chk("S z p=1 -> S=0", sf["z"][4], 0.0); chk("S z not coin-flip", 0.0 if not sf["z"][5] else 1.0, 0.0)
    chk("S y p=.1 -> S=.1", sf["y"][4], 0.1); chk("S y not coin-flip", 0.0 if not sf["y"][5] else 1.0, 0.0)
    # edge: empty-empty pair skipped, no pairs -> None
    chk("C edge skip", 0.0 if churn(S([D(1), D(2)], {D(1): "", D(2): ""}))[0] is None else 1.0, 0.0)
    print("SELFTEST " + ("RED: " + ",".join(fails) if fails else "GREEN"))
    return not fails

def load_series(csv_path):
    sys.path.insert(0, os.path.join(BRAIN, "status", "staged", "1005A_AI_PICKS_CHART"))
    import gen_ai_picks_chart as g
    rows = g.read_csv(csv_path)
    rows = [r for r in rows if r["kind"] == "API" and W0 <= r["date"] <= W1]
    excl = g.load_quarantine(g.DEFAULT_QUARANTINE)
    use = [r for r in rows if r["market"].strip() and r["market"].strip().casefold() not in g.SKIP_MARKETS]
    full = g.gci.make_generic_set({r["market"].strip() for r in use})
    g.gci.make_generic_set = lambda m: full  # keep the generic-phrase set identical across per-cell runs
    cells = sorted({(r["market"].strip(), r["niche"].strip()) for r in use})
    out = {}
    for mk, ni in cells:
        sub = [r for r in use if r["market"].strip() == mk and r["niche"].strip() == ni]
        data, _ = g.aggregate(sub, excl)
        for eng in g.ENGINES:
            ser = {"answered": set(), "named": defaultdict(set)}
            for wk in data[mk]:
                c = data[mk][wk][eng]
                ser["answered"] |= c["answered"]
                for key, (disp, days) in c["named"].items():
                    for d in days:
                        ser["named"][d].add(key)
            out[(mk, ni, eng)] = ser
    return out, g.ENGINES, cells, len(rows)

def run(csv_path, out_path, snap_path=None):
    ser, engines, cells, nrows = load_series(csv_path)
    R = []
    def add(level, metric, eng, pair, mk, ni, val, num, den, extra=""):
        R.append([V1_ROW_TAG, level, metric, eng, pair, mk, ni, "" if val is None else "%.6f" % val, "%g" % num, den, extra])
    tot = defaultdict(lambda: [0.0, 0])
    def acc(k, num, den): tot[k][0] += num; tot[k][1] += den
    hs = defaultdict(list)
    for mk, ni in cells:
        for e in engines:
            s = ser[(mk, ni, e)]
            v, n, d = persistence(s); add("cell", "P", e, "", mk, ni, v, n, d); acc(("P", e), n, d); acc(("P", "ALL", mk, ni), n, d)
            v, n, d = churn(s); add("cell", "C", e, "", mk, ni, v, n, d); acc(("C", e), n, d)
            H, inv, t, nb = concentration(s); add("cell", "H", e, "", mk, ni, H, t, nb); add("cell", "invH", e, "", mk, ni, inv, t, nb)
            if H is not None: hs[e].append((H, inv))
            v, n, d = name_rate(s); add("cell", "N", e, "", mk, ni, v, n, d); acc(("N", e), n, d)
        for e1, e2 in itertools.combinations(engines, 2):
            v, n, d = agreement(ser[(mk, ni, e1)], ser[(mk, ni, e2)])
            add("cell", "A", "", e1 + "|" + e2, mk, ni, v, n, d); acc(("A", e1 + "|" + e2), n, d)
    for k, (n, d) in sorted(tot.items()):
        if k[0] == "P" and k[1] == "ALL":
            add("cell_all_engines", "P", "ALL", "", k[2], k[3], n / d if d else None, n, d)
    for (m, k), (n, d) in sorted((k, v) for k, v in tot.items() if k[0] in "PCN" and k[1] != "ALL"):
        add("pooled", m, k, "", "ALL", "ALL", n / d if d else None, n, d)
    for e in engines:
        add("pooled", "H", e, "", "ALL", "ALL", sum(h for h, _ in hs[e]) / len(hs[e]), 0, len(hs[e]), "mean of cell H")
        add("pooled", "invH", e, "", "ALL", "ALL", sum(i for _, i in hs[e]) / len(hs[e]), 0, len(hs[e]), "mean of cell 1/H")
    for (m, k), (n, d) in sorted((k, v) for k, v in tot.items() if k[0] == "A"):
        add("pooled", "A", "", k, "ALL", "ALL", n / d if d else None, n, d)
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["version", "level", "metric", "engine", "engine_pair", "market", "niche", "value", "numerator", "denominator", "note"])
        w.writerows(R)
    print("rows_in_window", nrows, "cells", len(cells), "csv_rows", len(R), "->", out_path)
    if snap_path:
        write_snapshot(ser, engines, cells, snap_path)

def write_snapshot(ser, engines, cells, snap_path):
    rows = []
    for mk, ni in cells:
        for e in engines:
            for b, n, a, p, sv, coin in snapshot_rows(ser[(mk, ni, e)]):
                rows.append([e, mk, ni, b, n, a, "%.6f" % p, "%.6f" % sv, "COIN-FLIP" if coin else ""])
    with open(snap_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["engine", "market", "niche", "business_key", "named_days", "answered_days", "p", "S", "coin_flip"])
        w.writerows(rows)
    print("snapshot_rows", len(rows), "->", snap_path)
    print("POOLED (window %s..%s; every share = numerator/denominator)" % (W0, W1))
    print("engine | named_businesses | coin_flip n/d (share) | mean_S (sum/n) | named_every_answered_day n/d (share)")
    groups = [(e, [r for r in rows if r[0] == e]) for e in engines] + [("ALL", rows)]
    for e, g in groups:
        nb = len(g); cf = sum(1 for r in g if r[8]); full = sum(1 for r in g if r[4] == r[5])
        ssum = sum(float(r[7]) for r in g)
        print("%s | %d | %d/%d (%.4f) | %.4f (%.4f/%d) | %d/%d (%.4f)" % (e, nb, cf, nb, cf / nb if nb else 0, ssum / nb if nb else 0, ssum, nb, full, nb, full / nb if nb else 0))

if __name__ == "__main__":
    a = sys.argv[1:]
    csvp = os.path.join(BRAIN, "corpus", "namebeam", "Namebeam_E1_Dataset_LATEST.csv")
    outp = os.path.join(HERE, "metrics_v1_sept.csv")
    snapp = os.path.join(HERE, "snapshot_v1_1_sept.csv")
    if "--snap" in a: snapp = a[a.index("--snap") + 1]
    if "--csv" in a: csvp = a[a.index("--csv") + 1]
    if "--out" in a: outp = a[a.index("--out") + 1]
    if "--selftest" in a: sys.exit(0 if selftest() else 1)
    elif "--run" in a: run(csvp, outp, snapp)
    else: print(__doc__)
