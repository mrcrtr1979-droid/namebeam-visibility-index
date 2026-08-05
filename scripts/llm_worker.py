"""
LLM WORKER: run one mechanical text job on Gemini, off the Claude cap.

Why this exists: the front line's standing order is to route mechanical
cognition to non-Claude models. From a cloud session the only reachable
non-Claude LLM rails are the API keys this repo already holds. This worker
turns the GEMINI_API_KEY repo secret into a general-purpose drafting and
summarizing rail: dispatch the workflow with a prompt, read the committed
result. Zero Claude tokens, zero new vendors, zero new credentials.

Secret safety: the key is sent in the x-goog-api-key HEADER, never the URL,
so no exception string can carry it (the URL-leak failure of 2026-08-05 is
documented in doctrine 118). Errors are truncated and key-scrubbed anyway.
"""
import json
import os
import sys
import urllib.request

MODEL = os.environ.get("LLM_WORKER_MODEL", "").strip() or "gemini-3.6-flash"
API = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % MODEL


def redact(text):
    for var in ("GEMINI_API_KEY", "PERPLEXITY_API_KEY", "BRIGHTDATA_API_KEY"):
        val = os.environ.get(var)
        if val:
            text = text.replace(val, "[REDACTED_%s]" % var)
    return text


def main():
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        print("GEMINI_API_KEY not set; nothing run, exiting 0 per no-fabrication law.")
        return 0
    prompt = os.environ.get("LLM_WORKER_PROMPT", "").strip()
    if not prompt:
        print("LLM_WORKER_PROMPT empty; nothing to do.")
        return 0
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    req = urllib.request.Request(
        API, data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.load(r)
        out = data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:  # noqa: BLE001 - deliberate: every error is logged, none raised
        out = "WORKER_FAILED: " + redact(str(e))[:500]
    os.makedirs("jobs", exist_ok=True)
    name = os.environ.get("LLM_WORKER_JOB", "job").strip() or "job"
    path = os.path.join("jobs", "%s.md" % name)
    with open(path, "w") as f:
        f.write(out)
    print("wrote %s (%d chars, model %s)" % (path, len(out), MODEL))
    return 0


if __name__ == "__main__":
    sys.exit(main())
