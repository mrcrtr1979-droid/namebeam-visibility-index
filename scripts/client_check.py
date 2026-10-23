"""
NAMEBEAM CLIENT CHECK RUNNER (dispatch only, one-off, dated).

Plain-language notes for a non-coder reading along:

1. Reads one JSON file: a list of items, each one client business and the one
   buyer question a customer would type. Shape:
   [{"check_id": "...", "client": "...", "market": "city, state",
     "niche": "...", "question": "<buyer question verbatim>"}]
2. Asks every item of every engine (perplexity, gemini, openai, anthropic by
   default) in ONE dated run. The question goes to the engine verbatim, the same
   way the daily record sends its prompt_1.
3. Writes one JSON per item per engine to client_checks/<run_date>/, hashes the
   answer text with SHA-256, and writes a RECEIPT.md and a SUMMARY.json next to
   them. This is the product behind the Namebeam Client Check: one client
   business, one buyer question, four engines, dated, SHA-256 per answer.
4. It reuses the daily record's own adapters (scripts/engines.py) and name
   extractors (scripts/e1_runner.py), so the rows are comparable with the
   record. It NEVER writes to corpus/e1, and it refuses an output folder under
   corpus/.
5. An engine that errors writes run_status FAIL with the error class and the
   redacted error text. A fabricated answer is the one unforgivable bug.

No secret is ever printed, logged, or written to a file. Keys are read from the
environment by the adapters; every error string passes through engines.redact().

Run from the repo root:
    python -I scripts/client_check.py --input client_checks/inputs/AGENCY_1027.json --dry-run
    python -I scripts/client_check.py --input <file> [--engines perplexity,gemini,openai,anthropic]
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

# -I (isolated mode) does not put this script's folder on sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import e1_runner  # noqa: E402  (extractors, is_mentioned, slugify, RUN_CHANNEL_CAVEAT)
import engines as _engines_mod  # noqa: E402
from engines import ENGINES, LAST_CALL_META, redact  # noqa: E402
from extract_named_entities import extract_stated_criteria  # noqa: E402

OUT_ROOT = "client_checks"
DEFAULT_ENGINES = ("perplexity", "gemini", "openai", "anthropic")
SCHEMA_VERSION = "v4-cc1"
ROW_TYPE = "client_check"
RUN_CHANNEL = "api"
REQUIRED_KEYS = ("check_id", "client", "market", "niche", "question")
CHECK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_STOP = {"the", "and", "of", "llc", "inc", "pc", "co", "corp", "ltd", "a", "an"}

FIRST_THREE_NOTE = (
    "first_three_named: the record's name extractor (scripts/e1_runner.py "
    "extract_competitors, client NOT excluded), ordered by first appearance in the "
    "answer; names that are only the market's place (city or state) or that sit "
    "inside a longer name in the same list are dropped. Mechanical, not a human "
    "read: headings and look-alike names can slip through. competitors_mentioned "
    "is the record's own field (client excluded) and is not altered by this rule.")


# ---------------------------------------------------------------- input


def load_items(path):
    """Read and validate the input file. Raises ValueError with a plain message."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list) or not data:
        raise ValueError("input must be a non-empty JSON list")
    seen = set()
    for i, it in enumerate(data):
        if not isinstance(it, dict):
            raise ValueError("item %d is not an object" % i)
        for k in REQUIRED_KEYS:
            if not isinstance(it.get(k), str) or not it[k].strip():
                raise ValueError("item %d missing non-empty string %r" % (i, k))
        if not CHECK_ID_RE.match(it["check_id"]):
            raise ValueError("item %d check_id %r has characters outside A-Za-z0-9._-"
                             % (i, it["check_id"]))
        if it["check_id"] in seen:
            raise ValueError("duplicate check_id %r" % it["check_id"])
        seen.add(it["check_id"])
    return data


# ---------------------------------------------------------------- matching


def _norm(s):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (s or "").lower().replace("&", " and ")).split())


def _stem(w):
    """Trailing-s fold only, so "Milestones DFW" matches "Milestone DFW"."""
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def client_named_exact(client, text):
    """Exact string match, case-insensitive. Same rule as the AG-PPLX-AGENCY-CHECK file."""
    return bool(client) and bool(text) and client.lower() in text.lower()


def client_named_loose(client, text):
    """Looser flag, for a human to confirm. True when the client name appears with
    punctuation ignored (so "Doerner Saunders Daniel & Anderson" matches the comma
    spelling), or when every distinctive word of the name (a trailing s ignored,
    so "Milestones DFW" matches "Milestone DFW") sits on one line of the
    answer. It can match a look-alike, so it never replaces the exact flag."""
    if not client or not text:
        return False
    nc, nt = _norm(client), _norm(text)
    if nc and (" " + nc + " ") in (" " + nt + " "):
        return True
    toks = [_stem(t) for t in nc.split() if t not in _STOP and t != "and"]
    if not toks:
        return False
    for line in text.splitlines():
        words = {_stem(w) for w in _norm(line).split()}
        if all(t in words for t in toks):
            return True
    return False


def _business_row(client, extra_variants=None):
    return {"business": client, "variants_business_name_forms": list(extra_variants or [])}


def first_three_named(text, sources, market, date_utc):
    names = e1_runner.extract_competitors(_business_row(""), text, sources, date_utc, None)
    low = (text or "").lower()
    place = set()
    for part in re.split(r"[,]", market or ""):
        p = _norm(part)
        if p:
            place.add(p)
    place.add(_norm(market))
    keep = []
    for n in names:
        nn = _norm(n)
        if not nn or nn in place:
            continue
        keep.append(n)
    # drop a name that sits inside a longer name in the same list
    final = []
    for n in keep:
        nn = _norm(n)
        if any(m is not n and nn != _norm(m) and (" " + nn + " ") in (" " + _norm(m) + " ")
               for m in keep):
            continue
        final.append(n)
    order = sorted(range(len(final)),
                   key=lambda i: (low.find(final[i].lower()) if final[i].lower() in low else 10 ** 9, i))
    return [final[i] for i in order][:3]


def usage_tokens(usage):
    """(input, output, total) tokens from whichever usage shape the engine returned,
    None where not stated. Never raises."""
    if not isinstance(usage, dict):
        return None, None, None
    def pick(*keys):
        for k in keys:
            v = usage.get(k)
            if isinstance(v, (int, float)):
                return int(v)
        return None
    tin = pick("prompt_tokens", "input_tokens", "promptTokenCount")
    tout = pick("completion_tokens", "output_tokens", "candidatesTokenCount")
    tot = pick("total_tokens", "totalTokenCount")
    if tot is None and tin is not None and tout is not None:
        tot = tin + tout
    return tin, tout, tot


# ---------------------------------------------------------------- one request


def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def run_one(item, engine, fn, date_utc):
    """One item x one engine -> the row dict. Never raises, never invents an answer."""
    br = _business_row(item["client"])
    row = {
        "check_id": item["check_id"],
        "date_utc": date_utc,
        "business": item["client"],
        "niche": item["niche"],
        "market": item["market"],
        "row_type": ROW_TYPE,
        "prompt_variant": 1,
        "run": 1,
        "prompt_text": item["question"],
        "engine": engine,
        "run_channel": RUN_CHANNEL,
        "schema_version": SCHEMA_VERSION,
    }
    # call_gemini picks its model from this id; a client check is never on the
    # continuity panel, so it uses the free-tier rest model and its own pacing.
    _engines_mod.CURRENT_QUESTION_ID[0] = e1_runner.slugify(item["check_id"])
    LAST_CALL_META.pop(engine, None)
    err_class = None
    try:
        ok, text, sources, err = fn(item["question"])
    except Exception as exc:  # an adapter should not raise; if one does, log it
        ok, text, sources = False, "", []
        err = redact("%s: %s" % (type(exc).__name__, exc))
        err_class = type(exc).__name__
    row["engine_meta"] = dict(LAST_CALL_META.get(engine) or {})
    if ok and not (text or "").strip():
        ok, err = False, "EmptyAnswer: engine returned ok with no answer text"
        err_class = "EmptyAnswer"
    if not ok:
        err = redact(err)
        if err_class is None:
            err_class = "QuotaBlocked" if str(err).startswith("QUOTA: ") else "EngineError"
        row.update({
            "run_status": "FAIL",
            "error_class": err_class,
            "error_detail": err,
            "business_mentioned": None,
            "competitors_mentioned": [],
            "engine_stated_criteria": [],
            "sources_cited": [],
            "factor_scores": None,
            "answer_verbatim": "",
            "answer_sha256": None,
            "client_named": None,
            "client_named_loose": None,
            "first_three_named": [],
            "session_note": "FAILED RUN, logged per no-fabricated-rows law. %s"
                            % e1_runner.RUN_CHANNEL_CAVEAT,
        })
        return row
    row.update({
        "run_status": "OK",
        "business_mentioned": e1_runner.is_mentioned(br, text),
        "competitors_mentioned": e1_runner.extract_competitors(br, text, sources, date_utc, None),
        "engine_stated_criteria": extract_stated_criteria(text),
        "sources_cited": sources,
        "factor_scores": None,
        "answer_verbatim": text,
        "answer_sha256": sha256_text(text),
        "client_named": client_named_exact(item["client"], text),
        "client_named_loose": client_named_loose(item["client"], text),
        "first_three_named": first_three_named(text, sources, item["market"], date_utc),
        "session_note": e1_runner.RUN_CHANNEL_CAVEAT,
    })
    return row


# ---------------------------------------------------------------- outputs


def pick_run_dir(root, date_utc):
    """Same-day re-runs ACCUMULATE: a second run gets <date>_r2, never an overwrite."""
    base = os.path.join(root, date_utc)
    if not os.path.exists(base):
        return base
    n = 2
    while os.path.exists("%s_r%d" % (base, n)):
        n += 1
    return "%s_r%d" % (base, n)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
        f.write("\n")


def _cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def build_receipt(run_dir_name, date_utc, input_path, input_sha, items, rows, engines_used):
    by = {(r["check_id"], r["engine"]): r for r in rows}
    ok = sum(1 for r in rows if r["run_status"] == "OK")
    L = []
    L.append("# Namebeam Client Check receipt, %s" % date_utc)
    L.append("")
    L.append("- Run folder: %s" % run_dir_name)
    L.append("- Input: %s (SHA-256 %s)" % (input_path, input_sha))
    L.append("- Engines: %s" % ", ".join(engines_used))
    L.append("- Items: %d | Requests: %d | OK: %d | FAIL: %d"
             % (len(items), len(rows), ok, len(rows) - ok))
    L.append("- Method: each question sent verbatim to each engine, one dated run, API channel "
             "(no memory, no personalization; results can differ from logged-in consumer apps). "
             "SHA-256 is of the answer text as saved (UTF-8). Full answer text is in the per-answer "
             "JSON file named below, not repeated here.")
    L.append("- Client Appears in Answer: exact = the client string, case-insensitive, in the answer. "
             "loose = punctuation-insensitive match or every distinctive word on one line; a human "
             "confirms it. " + FIRST_THREE_NOTE)
    L.append("- Request Cost: tokens as returned by the engine (in / out / total). No price is applied here.")
    L.append("")
    for n, it in enumerate(items, 1):
        L.append("## %d. %s" % (n, it["check_id"]))
        L.append("")
        L.append("- **Question:** %s" % it["question"])
        L.append("- **Target Client:** %s (%s, %s)" % (it["client"], it["market"], it["niche"]))
        L.append("")
        L.append("| Engine | Run Status | Client Appears (exact / loose) | First Three Businesses Named | "
                 "SHA-256 of Answer Text | Request Cost (tokens in/out/total) | Served Model | File |")
        L.append("|---|---|---|---|---|---|---|---|")
        for eng in engines_used:
            r = by.get((it["check_id"], eng))
            if r is None:
                continue
            fname = "%s_%s.json" % (it["check_id"], eng)
            if r["run_status"] == "OK":
                tin, tout, tot = usage_tokens((r.get("engine_meta") or {}).get("usage"))
                cost = "%s / %s / %s" % tuple("n/a" if v is None else v for v in (tin, tout, tot))
                named = "%s / %s" % ("yes" if r["client_named"] else "no",
                                     "yes" if r["client_named_loose"] else "no")
                first3 = "; ".join(r["first_three_named"]) or "none extracted"
                sha = "`%s`" % r["answer_sha256"]
                status = "OK"
            else:
                cost, named, first3, sha = "n/a", "n/a", "n/a", "n/a"
                status = "FAIL (%s)" % r.get("error_class")
            meta = r.get("engine_meta") or {}
            model = meta.get("served_model") or meta.get("requested_model") or "n/a"
            L.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % tuple(
                _cell(x) for x in (eng, status, named, first3, sha, cost, model, fname)))
        L.append("")
    return "\n".join(L) + "\n"


def build_summary(run_dir_name, date_utc, input_path, input_sha, items, rows, engines_used):
    per_engine = {}
    for e in engines_used:
        er = [r for r in rows if r["engine"] == e]
        per_engine[e] = {
            "requests": len(er),
            "ok": sum(1 for r in er if r["run_status"] == "OK"),
            "fail": sum(1 for r in er if r["run_status"] != "OK"),
            "client_named_exact": sum(1 for r in er if r["client_named"] is True),
            "client_named_loose": sum(1 for r in er if r["client_named_loose"] is True),
            "served_models": sorted({str((r.get("engine_meta") or {}).get("served_model")
                                         or (r.get("engine_meta") or {}).get("requested_model"))
                                     for r in er if r["run_status"] == "OK"}),
        }
    by = {(r["check_id"], r["engine"]): r for r in rows}
    out_items = []
    for it in items:
        d = {"check_id": it["check_id"], "client": it["client"], "market": it["market"],
             "question": it["question"], "engines": {}}
        for e in engines_used:
            r = by.get((it["check_id"], e))
            if r is None:
                continue
            d["engines"][e] = {"run_status": r["run_status"], "client_named": r["client_named"],
                               "client_named_loose": r["client_named_loose"],
                               "answer_sha256": r["answer_sha256"],
                               "first_three_named": r["first_three_named"],
                               "error_class": r.get("error_class")}
        out_items.append(d)
    return {"run_date_utc": date_utc, "run_folder": run_dir_name,
            "schema_version": SCHEMA_VERSION, "input_path": input_path, "input_sha256": input_sha,
            "engines": list(engines_used), "items": len(items), "requests": len(rows),
            "ok": sum(1 for r in rows if r["run_status"] == "OK"),
            "fail": sum(1 for r in rows if r["run_status"] != "OK"),
            "per_engine": per_engine, "results": out_items}


# ---------------------------------------------------------------- main


def under_corpus(path):
    p = os.path.abspath(path).replace("\\", "/")
    return "/corpus/" in p + "/" or p.endswith("/corpus")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Namebeam Client Check runner (dispatch only).")
    ap.add_argument("--input", required=True, help="JSON list of client check items")
    ap.add_argument("--engines", default=",".join(DEFAULT_ENGINES),
                    help="comma list from: " + ",".join(DEFAULT_ENGINES))
    ap.add_argument("--dry-run", action="store_true", help="build the request list, call nothing")
    ap.add_argument("--run-date", default=None, help="override the UTC run date (YYYY-MM-DD)")
    ap.add_argument("--outdir", default=OUT_ROOT, help="output folder; the dated run folder goes under it (default client_checks)")
    args = ap.parse_args(argv)

    if under_corpus(args.outdir):
        print("ERROR: refusing to write under corpus/. Client checks never touch the daily record.")
        return 2
    try:
        items = load_items(args.input)
    except (OSError, ValueError) as exc:
        print("ERROR: could not read input: %s" % exc)
        return 2
    wanted = [e.strip() for e in args.engines.split(",") if e.strip()]
    unknown = [e for e in wanted if e not in ENGINES]
    if not wanted or unknown:
        print("ERROR: unknown or empty engines %r; available: %s" % (unknown or wanted, ",".join(ENGINES)))
        return 2
    wanted = list(dict.fromkeys(wanted))
    date_utc = args.run_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_utc):
        print("ERROR: --run-date must be YYYY-MM-DD")
        return 2
    with open(args.input, "rb") as f:
        input_sha = hashlib.sha256(f.read()).hexdigest()

    requests_list = [(it, e) for it in items for e in wanted]
    run_dir = pick_run_dir(args.outdir, date_utc)
    run_dir_name = run_dir.replace(os.sep, "/")
    print("client_check %s | input %s | items %d | engines %s | requests %d | run folder %s"
          % (date_utc, args.input, len(items), ",".join(wanted), len(requests_list), run_dir))
    keys = {"perplexity": ("PERPLEXITY_API_KEY",), "gemini": ("GEMINI_API_KEY_FREE", "GEMINI_API_KEY"),
            "openai": ("OPENAI_API_KEY",), "anthropic": ("ANTHROPIC_API_KEY",)}
    for e in wanted:
        present = any(os.environ.get(k, "").strip() for k in keys.get(e, ()))
        print("  engine %-10s key %s" % (e, "present" if present else "MISSING"))
    if args.dry_run:
        print("DRY RUN: no engine called, nothing written. %d items x %d engines = %d requests."
              % (len(items), len(wanted), len(requests_list)))
        return 0

    os.makedirs(run_dir, exist_ok=True)
    rows = []
    for it, eng in requests_list:
        row = run_one(it, eng, ENGINES[eng], date_utc)
        write_json(os.path.join(run_dir, "%s_%s.json" % (it["check_id"], eng)), row)
        rows.append(row)
        print("  %s %-10s %s%s" % (it["check_id"], eng, row["run_status"],
                                   "" if row["run_status"] == "OK" else " " + str(row.get("error_class"))))
    with open(os.path.join(run_dir, "RECEIPT.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(build_receipt(run_dir_name, date_utc, args.input, input_sha, items, rows, wanted))
    write_json(os.path.join(run_dir, "SUMMARY.json"),
               build_summary(run_dir_name, date_utc, args.input, input_sha, items, rows, wanted))
    ok = sum(1 for r in rows if r["run_status"] == "OK")
    print("files written: %d answers + RECEIPT.md + SUMMARY.json (ok %d, fail %d)"
          % (len(rows), ok, len(rows) - ok))
    return 0


if __name__ == "__main__":
    sys.exit(main())
