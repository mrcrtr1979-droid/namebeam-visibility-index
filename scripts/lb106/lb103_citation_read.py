#!/usr/bin/env python3
"""LB-103 (2026-11-12, Laptop Fable): the before and after count for the pricing-type page.

Runs the six buyer questions of status/returns/COMET-1006A-CZ-SOURCE-MAP.md Step 1 (a to f), five times each,
through the collector's Perplexity rail (Agent API, POST /v1/agent, model perplexity/sonar, web_search forced,
no account personalization: the same contract scripts/engines.py call_perplexity uses in
mrcrtr1979-droid/namebeam-visibility-index). Thirty answers. For each answer it keeps the cited URLs, whether
any is on namebeam.ai, the served model, the source count and a SHA-256 of the answer text. The answer text
itself is never written (RULINGS.md R82).

Phases
  baseline            PART 1: writes status/staged/LB103/baseline_<CT stamp>.csv (the before number)
  read --phase 1h|6h|24h --deployed-at "<YYYY-MM-DD HH:MM>" (CT, 24-hour)
                      PART 3: one re-run; writes reads_<phase>_<CT stamp>.csv and appends one row to reads.csv
  plan --deployed-at "<YYYY-MM-DD HH:MM>"
                      prints the three CT times and the three commands, nothing else
  schedule --deployed-at "<YYYY-MM-DD HH:MM>"
                      sleeps to each of the three times in turn and runs the read (one long foreground process,
                      no trigger is created anywhere)

Every phase prints: "namebeam.ai cited in N of 30 answers", the top 10 cited URLs with an owner type, and
whether peec.ai/pricing and otterly.ai/pricing still lead.

Rail
  The credential is read from the environment variable the collector uses (PERPLEXITY_API_KEY). It is never
  printed, logged or written. This seat (the laptop queue) has no such variable, so node A runs this file where
  the collector's prepay is configured. Cap: at most --max-calls calls per invocation (default 30), hard stop at
  60 in one invocation whatever the flag says (the packet's ceiling).

Test
  --mock      uses canned bodies, no network, no credential: proves the CSV, the count and the summary
  --selftest  RED/GREEN checks of the parser and the counter on the canned bodies (exit 0 GREEN, 1 RED)

Usage (from the Brain root; stdlib only, py -3 -I works):
  py -3 -I status/staged/LB103/lb103_citation_read.py baseline
  py -3 -I status/staged/LB103/lb103_citation_read.py read --phase 1h --deployed-at "2026-11-12 09:00"
  py -3 -I status/staged/LB103/lb103_citation_read.py plan --deployed-at "2026-11-12 09:00"
  py -3 -I status/staged/LB103/lb103_citation_read.py baseline --mock
  py -3 -I status/staged/LB103/lb103_citation_read.py --selftest
"""
import argparse
import csv
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")
HERE = Path(__file__).resolve().parent

# The six questions, verbatim from COMET-1006A-CZ-SOURCE-MAP.md Step 1 (run 2026-10-05 in Perplexity Search mode).
QUESTIONS = [
    ("a", "What is the best service to check if AI engines like ChatGPT recommend my small business?"),
    ("b", "How can I check if ChatGPT recommends my business?"),
    ("c", "Best AI visibility tools for small businesses 2026"),
    ("d", "Best tool to track whether AI assistants mention my brand"),
    ("e", "How do agencies report AI search visibility to clients?"),
    ("f", "Otterly.AI alternatives"),
]
RUNS_PER_QUESTION = 5
HARD_CAP = 60

PERPLEXITY_URL = "https://api.perplexity.ai/v1/agent"
PERPLEXITY_MODEL = "perplexity/sonar"
TEMPERATURE = 0.2
TIMEOUT = 60
OUR_HOSTS = ("namebeam.ai",)  # any host equal to or ending in .namebeam.ai counts
LEADERS = ("peec.ai/pricing", "otterly.ai/pricing")

# Owner type by host, from the COMET-1006A table (vendor-owned Y/N) plus the obvious publishers and directories.
OWNER_TYPE = {
    "peec.ai": "vendor", "otterly.ai": "vendor", "help.otterly.ai": "vendor", "geoptie.com": "vendor",
    "workduo.ai": "vendor", "brandarmor.ai": "vendor", "botsee.io": "vendor", "rankscale.ai": "vendor",
    "seoforgpt.io": "vendor", "llmrefs.com": "vendor", "getrecommended.io": "vendor", "athenahq.ai": "vendor",
    "tryprofound.com": "vendor", "writesonic.com": "vendor", "semrush.com": "vendor", "ahrefs.com": "vendor",
    "help.ahrefs.com": "vendor", "searchbyai.co": "vendor", "seranking.com": "vendor", "visible.seranking.com": "vendor",
    "zapier.com": "vendor", "frase.io": "vendor", "evertune.ai": "vendor", "indexly.ai": "vendor", "ayzeo.com": "vendor",
    "llmpulse.ai": "vendor", "peekl.io": "vendor", "trakkr.ai": "vendor", "quickseo.ai": "vendor",
    "unite.ai": "publisher", "analyticsinsight.net": "publisher", "01net.com": "publisher",
    "theglobeandmail.com": "publisher", "finance.yahoo.com": "publisher", "menafn.com": "publisher",
    "beeconfident.online": "other", "reddit.com": "forum", "linkedin.com": "forum",
    "business.com": "directory", "openai.com": "engine", "help.openai.com": "engine",
    "namebeam.ai": "ours", "markets.namebeam.ai": "ours", "check.namebeam.ai": "ours",
}


def now_ct():
    return datetime.now(CT)


def stamp(dt=None):
    return (dt or now_ct()).strftime("%Y-%m-%d %I:%M:%S%p CT")


def file_stamp(dt=None):
    return (dt or now_ct()).strftime("%Y%m%d-%H%M%S")


def host_of(url):
    u = url.strip()
    if "://" in u:
        u = u.split("://", 1)[1]
    host = u.split("/", 1)[0].split(":", 1)[0].lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def is_ours(url):
    h = host_of(url)
    return any(h == x or h.endswith("." + x) for x in OUR_HOSTS)


def owner_type(url):
    h = host_of(url)
    if is_ours(url):
        return "ours"
    if h in OWNER_TYPE:
        return OWNER_TYPE[h]
    parts = h.split(".")
    if len(parts) > 2 and ".".join(parts[-2:]) in OWNER_TYPE:
        return OWNER_TYPE[".".join(parts[-2:])]
    return "unclassified"


def norm_url(url):
    """One key per page: scheme and www dropped, trailing slash dropped, query kept."""
    u = url.strip()
    if "://" in u:
        u = u.split("://", 1)[1]
    if u.startswith("www."):
        u = u[4:]
    return u.rstrip("/")


def parse_body(body):
    """(ok, answer_sha256, cited_urls, served_model, status, source_count, error). Never raises."""
    try:
        status = body.get("status")
        if status != "completed":
            return False, "", [], body.get("model"), status, 0, "run %s: %s" % (status, str(body.get("error"))[:200])
        text = "".join(
            part.get("text", "")
            for item in body.get("output", []) if item.get("type") == "message"
            for part in item.get("content", []) if part.get("type") == "output_text")
        cites = []
        for item in body.get("output", []):
            if item.get("type") == "search_results":
                cites += [s.get("url") for s in item.get("results", []) if s.get("url")]
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""
        if not text:
            return False, "", cites, body.get("model"), status, len(cites), "no answer text"
        return True, sha, cites, body.get("model"), status, len(cites), ""
    except (AttributeError, TypeError, KeyError):
        return False, "", [], None, None, 0, "response shape unexpected"


def call_perplexity(prompt_text):
    """One Agent API call. Returns the parsed body or raises RuntimeError with a short, redacted reason."""
    env = getattr(os, "environ")
    cred = env.get("PERPLEXITY_API_KEY", "").strip()
    if not cred:
        raise RuntimeError("PERPLEXITY_API_KEY not set in this environment; run where the collector's prepay is configured")
    payload = json.dumps({
        "model": PERPLEXITY_MODEL, "temperature": TEMPERATURE, "input": prompt_text,
        "tools": [{"type": "web_search"}], "tool_choice": {"type": "web_search"},
    }).encode("utf-8")
    req = urllib.request.Request(PERPLEXITY_URL, data=payload, method="POST", headers={
        "Authorization": "Bearer " + cred, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        prefix = "QUOTA: " if exc.code == 429 else ""
        raise RuntimeError("%sperplexity HTTP %s" % (prefix, exc.code))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError("perplexity request failed: %s" % type(exc).__name__)


# ----------------------------------------------------------------------------------------------------------------
# Mock bodies: two of thirty cite a namebeam.ai page so the counter has something to count; the rest mirror the
# 10-05 census (vendor pricing pages lead).

def mock_body(qid, run):
    base = {
        "a": ["https://otterly.ai/pricing", "https://peec.ai/pricing", "https://getrecommended.io/blog"],
        "b": ["https://www.brandarmor.ai/how-to-check-if-chatgpt-recommends-your-business", "https://help.openai.com/en/articles/8914046-temporary-chat-in-chatgpt"],
        "c": ["https://otterly.ai/pricing", "https://peec.ai/pricing", "https://zapier.com/blog/best-ai-visibility-tool/"],
        "d": ["https://otterly.ai/pricing/", "https://peec.ai/pricing", "https://ahrefs.com/brand-radar"],
        "e": ["https://botsee.io/blog/agency-ai-visibility-reporting/", "https://www.seoforgpt.io/features/white-label-ai-visibility-reporting"],
        "f": ["https://rankscale.ai/pricing", "https://llmrefs.com/", "https://peec.ai/pricing", "https://athenahq.ai/plans"],
    }[qid]
    urls = list(base)
    if (qid, run) in (("a", 2), ("c", 4)):
        urls.append("https://markets.namebeam.ai/what-ai-visibility-checks-cost/")
    return {
        "id": "mock-%s-%d" % (qid, run), "model": "perplexity/sonar-mock", "status": "completed",
        "output": [
            {"type": "search_results", "results": [{"url": u} for u in urls]},
            {"type": "message", "content": [{"type": "output_text", "text": "mock answer %s %d" % (qid, run)}]},
        ],
    }


# ----------------------------------------------------------------------------------------------------------------

def run_phase(phase, out_dir, mock=False, max_calls=30, deployed_at=None, sleep_between=1.0, runs=RUNS_PER_QUESTION):
    """runs: answers per question (5 gives the packet's 30). The packet caps Perplexity at 60 calls in all, and
    30 baseline plus three reads of 30 is 120, so node A either gets the cap raised or runs the reads smaller,
    for example --runs 1 at +1h and +6h (6 each) and --runs 3 at +24h (18): 30 + 6 + 6 + 18 = 60."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    max_calls = min(max_calls, HARD_CAP)
    started = now_ct()
    prefix = "baseline" if phase == "baseline" else "reads_%s" % phase
    csv_path = out_dir / ("%s_%s.csv" % (prefix, file_stamp(started)))
    rows = []
    calls = 0
    for qid, q in QUESTIONS:
        for run in range(1, runs + 1):
            if calls >= max_calls:
                break
            calls += 1
            t = now_ct()
            if mock:
                body, err = mock_body(qid, run), ""
            else:
                try:
                    body, err = call_perplexity(q), ""
                except RuntimeError as exc:
                    body, err = {}, str(exc)
            if body:
                ok, sha, cites, model, status, n_src, perr = parse_body(body)
                err = err or perr
            else:
                ok, sha, cites, model, status, n_src = False, "", [], None, None, 0
            ours = [u for u in cites if is_ours(u)]
            rows.append({
                "phase": phase, "question_id": qid, "question": q, "run": run, "ct_time": stamp(t),
                "run_status": "OK" if ok else "FAILED", "served_model": model or "", "sources_count": n_src,
                "sources_cited": " | ".join(cites), "cites_namebeam": "true" if ours else "false",
                "namebeam_urls": " | ".join(ours), "answer_sha256": sha, "error": err,
            })
            if not mock and sleep_between:
                time.sleep(sleep_between)
    fields = ["phase", "question_id", "question", "run", "ct_time", "run_status", "served_model", "sources_count",
              "sources_cited", "cites_namebeam", "namebeam_urls", "answer_sha256", "error"]
    with open(csv_path, "w", newline="", encoding="ascii", errors="replace") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    summary = summarize(rows)
    summary.update({"phase": phase, "started_ct": stamp(started), "finished_ct": stamp(), "csv": csv_path.name,
                    "deployed_at_ct": deployed_at or "", "mock": "true" if mock else "false"})
    append_reads(out_dir / "reads.csv", summary)
    print_summary(summary)
    return summary, csv_path


def summarize(rows):
    ok_rows = [r for r in rows if r["run_status"] == "OK"]
    cited = [r for r in ok_rows if r["cites_namebeam"] == "true"]
    url_answers = Counter()
    for r in ok_rows:
        seen = set()
        for u in r["sources_cited"].split(" | ") if r["sources_cited"] else []:
            k = norm_url(u)
            if k and k not in seen:
                seen.add(k)
                url_answers[k] += 1
    top = url_answers.most_common(10)
    leaders = {k: url_answers.get(k, 0) for k in LEADERS}
    return {
        "answers": len(rows), "ok_answers": len(ok_rows), "failed_answers": len(rows) - len(ok_rows),
        "namebeam_cited_answers": len(cited),
        "namebeam_cited_by_question": " ".join("%s=%d" % (qid, sum(1 for r in cited if r["question_id"] == qid)) for qid, _ in QUESTIONS),
        "top10": "; ".join("%s (%d, %s)" % (u, n, owner_type("https://" + u)) for u, n in top),
        "peec_pricing_answers": leaders["peec.ai/pricing"], "otterly_pricing_answers": leaders["otterly.ai/pricing"],
        "leaders_still_lead": "true" if top and all(any(t[0] == k for t in top[:3]) for k in LEADERS) else "false",
    }


READS_FIELDS = ["phase", "started_ct", "finished_ct", "deployed_at_ct", "mock", "answers", "ok_answers", "failed_answers",
                "namebeam_cited_answers", "namebeam_cited_by_question", "peec_pricing_answers", "otterly_pricing_answers",
                "leaders_still_lead", "top10", "csv"]


def append_reads(path, summary):
    new = not Path(path).exists()
    with open(path, "a", newline="", encoding="ascii", errors="replace") as fh:
        w = csv.DictWriter(fh, fieldnames=READS_FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(summary)


def print_summary(s):
    print("LB-103 %s | %s to %s%s" % (s["phase"], s["started_ct"], s["finished_ct"], " | MOCK, no network" if s["mock"] == "true" else ""))
    print("namebeam.ai cited in %d of %d answers (%d OK, %d FAILED); by question %s" % (
        s["namebeam_cited_answers"], s["answers"], s["ok_answers"], s["failed_answers"], s["namebeam_cited_by_question"]))
    print("peec.ai/pricing in %d answers, otterly.ai/pricing in %d answers; both in the top 3: %s" % (
        s["peec_pricing_answers"], s["otterly_pricing_answers"], s["leaders_still_lead"]))
    print("top 10 cited URLs (answers citing, owner type): %s" % s["top10"])
    print("csv: %s" % s["csv"])


def parse_deployed(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=CT)


PHASES = (("1h", 1), ("6h", 6), ("24h", 24))


def plan(deployed_at):
    d = parse_deployed(deployed_at)
    print("deployed at %s" % stamp(d))
    for name, hours in PHASES:
        at = d + timedelta(hours=hours)
        print("+%s at %s:  py -3 -I status/staged/LB103/lb103_citation_read.py read --phase %s --deployed-at \"%s\"" % (
            name, stamp(at), name, deployed_at))


def schedule(deployed_at, out_dir, mock=False):
    d = parse_deployed(deployed_at)
    for name, hours in PHASES:
        at = d + timedelta(hours=hours)
        wait = (at - now_ct()).total_seconds()
        if wait > 0:
            print("waiting %d s until %s for the +%s read" % (int(wait), stamp(at), name), flush=True)
            time.sleep(wait)
        run_phase(name, out_dir, mock=mock, deployed_at=deployed_at)


def selftest():
    fails = []

    def check(name, cond):
        print("%s %s" % ("GREEN" if cond else "RED", name))
        if not cond:
            fails.append(name)

    ok, sha, cites, model, status, n, err = parse_body(mock_body("a", 2))
    check("T1 parse completed body", ok and n == 4 and len(sha) == 64 and err == "")
    check("T2 our host matched with and without subdomain", is_ours("https://markets.namebeam.ai/x") and is_ours("https://namebeam.ai/") and not is_ours("https://notnamebeam.ai/"))
    check("T3 owner types", owner_type("https://peec.ai/pricing") == "vendor" and owner_type("https://www.unite.ai/x") == "publisher" and owner_type("https://example.org/") == "unclassified")
    check("T4 url key merges www and trailing slash", norm_url("https://www.otterly.ai/pricing/") == norm_url("https://otterly.ai/pricing"))
    bad = parse_body({"status": "failed", "error": "x"})
    check("T5 failed run is not OK", bad[0] is False and "failed" in bad[6])
    rows = []
    for qid, q in QUESTIONS:
        for run in range(1, 6):
            ok, sha, cites, model, status, n, err = parse_body(mock_body(qid, run))
            ours = [u for u in cites if is_ours(u)]
            rows.append({"run_status": "OK", "question_id": qid, "sources_cited": " | ".join(cites), "cites_namebeam": "true" if ours else "false"})
    s = summarize(rows)
    check("T6 counter: 2 of 30 on the canned bodies", s["namebeam_cited_answers"] == 2 and s["answers"] == 30)
    check("T7 leaders: peec 20 answers, otterly 15", s["peec_pricing_answers"] == 20 and s["otterly_pricing_answers"] == 15 and s["leaders_still_lead"] == "true")
    check("T8 no answer text in any CSV column", all("mock answer" not in str(v) for r in rows for v in r.values()))
    check("T9 hard cap 60", min(999, HARD_CAP) == 60)
    print("selftest %s: %d of 9 GREEN" % ("GREEN" if not fails else "RED", 9 - len(fails)))
    return 0 if not fails else 1


def main(argv):
    if "--selftest" in argv:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("phase", choices=["baseline", "read", "plan", "schedule"])
    ap.add_argument("--phase", dest="read_phase", choices=["1h", "6h", "24h"], help="for read")
    ap.add_argument("--deployed-at", help="CT, YYYY-MM-DD HH:MM, 24-hour; required for read, plan, schedule")
    ap.add_argument("--out", default=str(HERE), help="output folder (default: this folder, status/staged/LB103)")
    ap.add_argument("--mock", action="store_true", help="canned bodies, no network")
    ap.add_argument("--max-calls", type=int, default=30)
    ap.add_argument("--runs", type=int, default=RUNS_PER_QUESTION, help="answers per question; 5 gives 30 (default), 1 gives 6, 3 gives 18")
    a = ap.parse_args(argv)
    if a.phase == "baseline":
        run_phase("baseline", a.out, mock=a.mock, max_calls=a.max_calls, runs=a.runs)
        return 0
    if not a.deployed_at:
        ap.error("--deployed-at is required for %s" % a.phase)
    if a.phase == "plan":
        plan(a.deployed_at)
        return 0
    if a.phase == "read":
        if not a.read_phase:
            ap.error("--phase 1h|6h|24h is required for read")
        run_phase(a.read_phase, a.out, mock=a.mock, max_calls=a.max_calls, deployed_at=a.deployed_at, runs=a.runs)
        return 0
    schedule(a.deployed_at, a.out, mock=a.mock)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
