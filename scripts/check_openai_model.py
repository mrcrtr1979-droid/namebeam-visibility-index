#!/usr/bin/env python3
"""check_openai_model.py: confirm an OpenAI model id against the live API model list.

LB-20 (2026-11-09, LAPTOP-FABLE-LB20). Reads OPENAI_API_KEY and OPENAI_MODEL from the environment,
GETs https://api.openai.com/v1/models/<id> and prints the HTTP status plus the id and created time
the API returns. The key is never printed. Exit 0 when the model exists, 3 when the API says it does
not (HTTP 404), 2 when the key is missing, 4 on any other HTTP status or network failure.

    python3 -I scripts/check_openai_model.py            # live, needs the key in env
    python3 -I scripts/check_openai_model.py --selftest # offline
"""
import json
import os
import sys
import urllib.error
import urllib.request

URL = "https://api.openai.com/v1/models/"


def check(model, key, opener=None):
    """Returns (exit_code, line). opener(request) -> response-like with .status and .read()."""
    if not key:
        return 2, "OPENAI_MODEL=%s HTTP none: OPENAI_API_KEY not set" % model
    req = urllib.request.Request(URL + model, headers={"Authorization": "Bearer " + key,
                                                        "User-Agent": "namebeam-e1/check-openai-model"})
    opener = opener or urllib.request.urlopen
    try:
        with opener(req, timeout=30) as r:
            status = getattr(r, "status", 200)
            body = json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return 3, "OPENAI_MODEL=%s HTTP 404: the API does not list this model id" % model
        return 4, "OPENAI_MODEL=%s HTTP %s" % (model, e.code)
    except Exception as exc:
        return 4, "OPENAI_MODEL=%s request failed: %s" % (model, type(exc).__name__)
    return 0, "OPENAI_MODEL=%s HTTP %s id=%s created=%s owned_by=%s" % (
        model, status, body.get("id"), body.get("created"), body.get("owned_by"))


def selftest():
    class R:
        def __init__(self, status, body):
            self.status, self._b = status, body

        def read(self):
            return json.dumps(self._b).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def ok(req, timeout=0):
        assert "Authorization" in req.headers or "Authorization" in dict(req.header_items())
        return R(200, {"id": "gpt-5.6-terra", "created": 1, "owned_by": "system"})

    def missing(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)

    results = [
        check("gpt-5.6-terra", "k", ok)[0] == 0,
        check("gpt-5-mini", "k", missing)[0] == 3,
        check("x", "", ok)[0] == 2,
        "k" not in check("gpt-5.6-terra", "k", ok)[1],
    ]
    print("selftest", "PASS" if all(results) else "FAIL", results)
    return 0 if all(results) else 1


def main(argv):
    if "--selftest" in argv:
        return selftest()
    model = os.environ.get("OPENAI_MODEL", "").strip() or "gpt-5.6-terra"
    code, line = check(model, os.environ.get("OPENAI_API_KEY", "").strip())
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
