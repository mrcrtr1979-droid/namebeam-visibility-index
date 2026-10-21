"""
ANTHROPIC BATCH MODE for the E1 collector (added 2026-10-21, branch batch-anthropic-1021).

Plain-language notes for a non-coder reading along:

1. The collector normally asks Anthropic one question per call and waits for
   each answer. The Message Batches API takes all the day's Anthropic
   questions at once, answers them in the background, and charges 50 percent
   of the normal price. Source: platform.claude.com/docs/en/build-with-claude/batch-processing
   (50 percent of standard price, most batches finish within 1 hour,
   results available when done or after 24 hours, expired requests not billed).
2. This is OFF unless the environment variable ANTHROPIC_MODE is set to
   "batch". Any other value, or no value, means "live": the collector behaves
   exactly as it did before this file existed.
3. Batch mode changes HOW the Anthropic answers are fetched. It does not
   change WHAT is asked (same model, same max_tokens, same temperature, same
   prompt text: engines.anthropic_params) or HOW rows are written (the rows are
   built by e1_runner.run_engine, the same function the live path uses, so the
   columns and run_status values are identical).
4. The dated record never loses a day. If the batch cannot be created, cannot
   be polled, or is not finished by the deadline, the unfinished questions are
   cancelled and asked through the live path, one at a time, as before.
5. A request Anthropic reports as errored is treated exactly like a failed
   live call: it becomes a FAILED row (or QUOTA_BLOCKED for a rate limit
   error) with the error detail in session_note. Nothing is deleted or hidden.
6. The mode is logged to the run log (lines starting with "[anthropic-batch]").
   It is NOT written into the record rows: the record schema (v4) is read by
   the Edition pipeline, and an extra column would change every Anthropic row
   in a way the published method does not describe.

No secret is ever printed, logged, or written into a row: every error string
goes through engines.redact, and the key is only ever sent to api.anthropic.com.

Settings (all optional, read from the environment):
    ANTHROPIC_MODE                    live (default) or batch
    ANTHROPIC_BATCH_DEADLINE_MINUTES  default 90, allowed 1 to 1380
    ANTHROPIC_BATCH_POLL_SECONDS      default 30, allowed 1 to 300
"""

import hashlib
import json
import os
import sys
import time

import requests

import engines
from engines import redact

BATCH_URL = "https://api.anthropic.com/v1/messages/batches"
ALLOWED_URL_PREFIX = "https://api.anthropic.com/"
API_VERSION = "2023-06-01"
HTTP_TIMEOUT = 60
DEFAULT_DEADLINE_MINUTES = 90
DEFAULT_POLL_SECONDS = 30
MAX_POLL_ERRORS = 6        # consecutive failed status checks before giving up on the batch
CANCEL_WAIT_SECONDS = 180  # how long to wait for a cancelled batch to report "ended"
RESULT_FETCH_TRIES = 3
TAG = "[anthropic-batch]"


def log(msg):
    print("%s %s" % (TAG, msg), flush=True)


def mode():
    """'batch' only when ANTHROPIC_MODE is exactly batch (any case, spaces ignored).
    Everything else, including an unknown value, is 'live'."""
    v = os.environ.get("ANTHROPIC_MODE", "").strip().lower()
    return "batch" if v == "batch" else "live"


def _int_env(name, default, lo, hi):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        log("ignoring %s=%r (not a whole number); using %d" % (name, raw, default))
        return default
    if v < lo or v > hi:
        log("ignoring %s=%d (allowed %d to %d); using %d" % (name, v, lo, hi, default))
        return default
    return v


def deadline_minutes():
    return _int_env("ANTHROPIC_BATCH_DEADLINE_MINUTES", DEFAULT_DEADLINE_MINUTES, 1, 1380)


def poll_seconds():
    return _int_env("ANTHROPIC_BATCH_POLL_SECONDS", DEFAULT_POLL_SECONDS, 1, 300)


def cell_id(date_utc, slug, occurrence=1):
    """Stable id for one (date, business, engine) cell. Batch custom_id must match
    ^[a-zA-Z0-9_-]{1,64}$ and be unique in the batch. Slugs can be long, so the id is
    date + the first 36 slug characters + a 10 character hash of the full
    date|slug|occurrence key (occurrence separates a business listed twice).
    Same inputs always give the same id."""
    h = hashlib.sha1(("%s|%s|%d|anthropic" % (date_utc, slug, occurrence)).encode("utf-8")).hexdigest()[:10]
    return "%s_%s_%s" % (date_utc, slug[:36], h)


def _headers(key):
    return {"x-api-key": key, "anthropic-version": API_VERSION,
            "Content-Type": "application/json"}


def _short(r):
    try:
        return (r.text or "")[:300]
    except Exception:
        return ""


def _classify_line(obj):
    """One results line -> (custom_id, outcome_type, payload).
    outcome_type is one of succeeded, errored, canceled, expired, malformed."""
    cid = obj.get("custom_id") if isinstance(obj, dict) else None
    res = obj.get("result") if isinstance(obj, dict) else None
    if not cid or not isinstance(res, dict):
        return cid, "malformed", None
    t = res.get("type")
    if t in ("succeeded", "errored", "canceled", "expired"):
        return cid, t, res
    return cid, "malformed", res


def _error_parts(res):
    """(error_type, message) from an errored result. Docs show result.error.error.type;
    both nesting levels are read defensively."""
    e = res.get("error") if isinstance(res, dict) else None
    inner = e.get("error") if isinstance(e, dict) and isinstance(e.get("error"), dict) else e
    if not isinstance(inner, dict):
        return "unknown", ""
    return str(inner.get("type") or "unknown"), str(inner.get("message") or "")


def outcome_from_result(outcome_type, res):
    """Turn a succeeded/errored result into the same 4-tuple the live adapter returns,
    plus the engine_meta dict. Returns None for canceled/expired/malformed (those are
    re-asked through the live path by the caller)."""
    if outcome_type == "succeeded":
        msg = (res or {}).get("message")
        try:
            text = msg["content"][0]["text"]
        except (KeyError, IndexError, TypeError):
            # Same wording as the live adapter's shape failure.
            return {"tuple": (False, "", [], redact("anthropic response shape unexpected")),
                    "meta": {}, "type": "succeeded_bad_shape"}
        return {"tuple": (True, text, [], ""),
                "meta": engines._served_meta(msg, "model", "id", "usage"),
                "type": "succeeded"}
    if outcome_type == "errored":
        etype, emsg = _error_parts(res)
        prefix = "QUOTA: " if etype == "rate_limit_error" else ""
        return {"tuple": (False, "", [], redact(
                    "%santhropic batch request errored (%s): %s" % (prefix, etype, emsg[:300]))),
                "meta": {}, "type": "errored"}
    return None


def _post_batch(key, reqs, http):
    try:
        r = http.post(BATCH_URL, headers=_headers(key), json={"requests": reqs}, timeout=HTTP_TIMEOUT)
    except requests.exceptions.RequestException as exc:
        return None, redact("batch create request failed: %s" % exc)
    if r.status_code != 200:
        return None, redact("batch create HTTP %s: %s" % (r.status_code, _short(r)))
    try:
        body = r.json()
        if not body.get("id"):
            raise KeyError("id")
    except (ValueError, KeyError, AttributeError, TypeError):
        return None, redact("batch create response shape unexpected")
    return body, ""


def _get_status(key, batch_id, http):
    try:
        r = http.get("%s/%s" % (BATCH_URL, batch_id), headers=_headers(key), timeout=HTTP_TIMEOUT)
    except requests.exceptions.RequestException as exc:
        return None, redact("batch status request failed: %s" % exc)
    if r.status_code != 200:
        return None, redact("batch status HTTP %s: %s" % (r.status_code, _short(r)))
    try:
        body = r.json()
        body["processing_status"]
    except (ValueError, KeyError, TypeError):
        return None, redact("batch status response shape unexpected")
    return body, ""


def _cancel(key, batch_id, http):
    try:
        r = http.post("%s/%s/cancel" % (BATCH_URL, batch_id), headers=_headers(key),
                      timeout=HTTP_TIMEOUT)
    except requests.exceptions.RequestException as exc:
        return False, redact("batch cancel request failed: %s" % exc)
    if r.status_code != 200:
        return False, redact("batch cancel HTTP %s: %s" % (r.status_code, _short(r)))
    return True, ""


def _fetch_results(key, batch_id, results_url, http):
    url = results_url if isinstance(results_url, str) and results_url else "%s/%s/results" % (BATCH_URL, batch_id)
    if not url.startswith(ALLOWED_URL_PREFIX):
        return None, "results_url is not on api.anthropic.com; not sending the key there"
    try:
        r = http.get(url, headers=_headers(key), timeout=HTTP_TIMEOUT * 2)
    except requests.exceptions.RequestException as exc:
        return None, redact("batch results request failed: %s" % exc)
    if r.status_code != 200:
        return None, redact("batch results HTTP %s: %s" % (r.status_code, _short(r)))
    lines = []
    for raw in (r.text or "").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            lines.append(json.loads(raw))
        except ValueError:
            lines.append(None)
    return lines, ""


def run_batch(key, items, deadline_s=None, poll_s=None, http=requests,
              sleep=time.sleep, clock=time.monotonic):
    """items: list of (custom_id, prompt_text). Returns a summary dict:

        batch_id, submitted, created_at, counts (last request_counts seen),
        elapsed_s, cancelled (True if the deadline cancel ran),
        fallback_reason (None when results were read; else why every item goes live),
        outcomes: {custom_id: {"tuple", "meta", "type"}} for succeeded and errored items,
        redo: [custom_ids] to be asked live (canceled, expired, malformed or missing),
        types: {outcome_type: count}

    Never raises on an API problem: it returns fallback_reason and an empty outcomes dict
    so every item is asked live. Never prints the key."""
    deadline_s = deadline_minutes() * 60 if deadline_s is None else deadline_s
    poll_s = poll_seconds() if poll_s is None else poll_s
    t0 = clock()
    summary = {"batch_id": None, "submitted": len(items), "counts": None, "elapsed_s": 0.0,
               "cancelled": False, "fallback_reason": None, "outcomes": {}, "redo": [],
               "types": {}, "created_at": None}
    ids = [cid for cid, _ in items]
    if len(set(ids)) != len(ids):
        summary["fallback_reason"] = "duplicate custom_id in batch; nothing submitted"
        summary["redo"] = list(ids)
        return summary

    reqs = [{"custom_id": cid, "params": engines.anthropic_params(prompt)} for cid, prompt in items]
    body, err = _post_batch(key, reqs, http)
    if body is None:
        summary["fallback_reason"] = err
        summary["redo"] = list(ids)
        summary["elapsed_s"] = clock() - t0
        log("batch create failed, every question will be asked live: %s" % err)
        return summary
    batch_id = body["id"]
    summary["batch_id"] = batch_id
    summary["created_at"] = body.get("created_at")
    summary["counts"] = body.get("request_counts")
    log("batch %s created: %d requests, deadline %d s, poll %d s" % (batch_id, len(items), deadline_s, poll_s))

    status = body
    poll_errors = 0
    give_up = None
    while status.get("processing_status") != "ended":
        remaining = deadline_s - (clock() - t0)
        if remaining <= 0:
            give_up = "deadline of %d s reached" % deadline_s
            break
        sleep(min(poll_s, max(remaining, 0)))
        got, err = _get_status(key, batch_id, http)
        if got is None:
            poll_errors += 1
            log("status check failed (%d of %d): %s" % (poll_errors, MAX_POLL_ERRORS, err))
            if poll_errors >= MAX_POLL_ERRORS:
                give_up = "status checks kept failing: %s" % err
                break
            continue
        poll_errors = 0
        status = got
        summary["counts"] = got.get("request_counts")
        log("t+%ds status=%s counts=%s" % (int(clock() - t0), got.get("processing_status"),
                                            json.dumps(got.get("request_counts"), sort_keys=True)))

    if give_up:
        summary["cancelled"] = True
        ok, cerr = _cancel(key, batch_id, http)
        log("%s; cancel requested ok=%s%s" % (give_up, ok, "" if ok else " (%s)" % cerr))
        # A cancelled batch ends with partial results. Wait briefly for "ended" so the
        # finished answers are not paid for and thrown away.
        wait_t0 = clock()
        ended = False
        while ok and clock() - wait_t0 < CANCEL_WAIT_SECONDS:
            sleep(min(10, max(poll_s, 1)))
            got, err = _get_status(key, batch_id, http)
            if got is not None:
                status = got
                summary["counts"] = got.get("request_counts")
                if got.get("processing_status") == "ended":
                    ended = True
                    break
        if not ended:
            summary["fallback_reason"] = "%s; batch did not report ended after cancel" % give_up
            summary["redo"] = list(ids)
            summary["elapsed_s"] = clock() - t0
            log(summary["fallback_reason"] + "; every question will be asked live")
            return summary

    lines, err = None, ""
    for attempt in range(RESULT_FETCH_TRIES):
        lines, err = _fetch_results(key, batch_id, status.get("results_url"), http)
        if lines is not None:
            break
        log("results fetch failed (try %d of %d): %s" % (attempt + 1, RESULT_FETCH_TRIES, err))
        sleep(min(poll_s, 10))
    if lines is None:
        summary["fallback_reason"] = "results could not be read: %s" % err
        summary["redo"] = list(ids)
        summary["elapsed_s"] = clock() - t0
        log(summary["fallback_reason"] + "; every question will be asked live")
        return summary

    wanted = set(ids)
    seen = set()
    types = {}
    for obj in lines:
        if obj is None:
            types["malformed"] = types.get("malformed", 0) + 1
            continue
        cid, t, res = _classify_line(obj)
        types[t] = types.get(t, 0) + 1
        if cid not in wanted or cid in seen:
            continue
        seen.add(cid)
        out = outcome_from_result(t, res)
        if out is not None:
            summary["outcomes"][cid] = out
    summary["redo"] = [cid for cid in ids if cid not in summary["outcomes"]]
    summary["types"] = types
    summary["elapsed_s"] = clock() - t0
    log("batch %s ended after %d s: result types %s; %d used from batch, %d to be asked live"
        % (batch_id, int(summary["elapsed_s"]), json.dumps(types, sort_keys=True),
           len(summary["outcomes"]), len(summary["redo"])))
    return summary


def prefetch(roster, date_utc, slugify, key=None, **kw):
    """Collect the roster's Anthropic questions, run one batch, and return
    (by_row, summary). by_row maps id(roster_row) -> outcome dict for every row whose
    answer came from the batch; rows missing from by_row are asked live by the runner.
    Honors the per-entry "engines" limit exactly like the runner loop."""
    key = (key if key is not None else os.environ.get("ANTHROPIC_API_KEY", "")).strip()
    if not key:
        log("ANTHROPIC_API_KEY not set; nothing to batch, the live path will log its usual FAILED rows")
        return {}, None
    occ, items, row_by_cid = {}, [], {}
    for br in roster:
        only = br.get("engines")
        if isinstance(only, list) and "anthropic" not in only:
            continue
        slug = slugify(br["business"])
        occ[slug] = occ.get(slug, 0) + 1
        cid = cell_id(date_utc, slug, occ[slug])
        items.append((cid, br["prompt_1"]))
        row_by_cid[cid] = br
    if not items:
        log("no roster row asks Anthropic; nothing to batch")
        return {}, None
    summary = run_batch(key, items, **kw)
    by_row = {id(row_by_cid[cid]): out for cid, out in summary["outcomes"].items()}
    summary["cell_ids"] = {id(br): cid for cid, br in row_by_cid.items()}
    return by_row, summary


if __name__ == "__main__":  # pragma: no cover
    print("anthropic_batch is a library; see e1_runner.py and anthropic_batch_test.py")
    sys.exit(0)
