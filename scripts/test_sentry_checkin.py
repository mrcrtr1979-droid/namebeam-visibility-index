"""
Self-test for scripts/sentry_checkin.py. Fully offline: the opener is a stub, nothing
touches the network. Run from the repo root:

    python3 scripts/test_sentry_checkin.py

Proves:
  (a) the DSN becomes the documented check-in URL (/api/<project>/cron/<slug>/<key>/)
  (b) success -> status ok, anything else -> status error, with the schedule upsert
  (c) nothing is sent when SENTRY_DSN is empty or DISABLE_SENTRY is set
  (d) a network failure or a bad DSN never raises and always exits 0

Exits 0 only when every case passes.
"""
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sentry_checkin as sc  # noqa: E402

DSN = "https://abc123@o1.ingest.us.sentry.io/42"
FAILS = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


class Resp:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make_opener(sent):
    def opener(req, timeout=0):
        sent.append((req.full_url, json.loads(req.data.decode("utf-8"))))
        return Resp()
    return opener


def boom(req, timeout=0):
    raise urllib.error.URLError("down")


# (a)
check("url", sc.checkin_url(DSN, "e1-daily-record")
      == "https://o1.ingest.us.sentry.io/api/42/cron/e1-daily-record/abc123/")

# (b)
sent = []
rc = sc.main(["x", "e1-daily-record", "success", "10 12 * * *", "480"],
             env={"SENTRY_DSN": DSN}, opener=make_opener(sent))
check("ok exit 0", rc == 0)
check("one send", len(sent) == 1)
body = sent[0][1] if sent else {}
check("status ok", body.get("status") == "ok")
check("schedule", body.get("monitor_config", {}).get("schedule")
      == {"type": "crontab", "value": "10 12 * * *"})
check("margin", body.get("monitor_config", {}).get("checkin_margin") == 480)
check("utc", body.get("monitor_config", {}).get("timezone") == "UTC")
sent = []
sc.main(["x", "s", "failure", "0 1 * * *", "60"], env={"SENTRY_DSN": DSN}, opener=make_opener(sent))
check("failure -> error", sent and sent[0][1]["status"] == "error")
sent = []
sc.main(["x", "s", "cancelled", "0 1 * * *", "60"], env={"SENTRY_DSN": DSN}, opener=make_opener(sent))
check("cancelled -> error", sent and sent[0][1]["status"] == "error")

# (c)
sent = []
sc.main(["x", "s", "success", "0 1 * * *", "60"], env={}, opener=make_opener(sent))
check("no dsn, no send", sent == [])
for v in ["1", "true", "yes", "on"]:
    sent = []
    sc.main(["x", "s", "success", "0 1 * * *", "60"],
            env={"SENTRY_DSN": DSN, "DISABLE_SENTRY": v}, opener=make_opener(sent))
    check("disabled by %s" % v, sent == [])
for v in ["", "0", "false", "no", "off"]:
    sent = []
    sc.main(["x", "s", "success", "0 1 * * *", "60"],
            env={"SENTRY_DSN": DSN, "DISABLE_SENTRY": v}, opener=make_opener(sent))
    check("not disabled by %r" % v, len(sent) == 1)

# (d)
check("network failure exit 0",
      sc.main(["x", "s", "success", "0 1 * * *", "60"], env={"SENTRY_DSN": DSN}, opener=boom) == 0)
check("bad dsn exit 0",
      sc.main(["x", "s", "success", "0 1 * * *", "60"], env={"SENTRY_DSN": "not-a-dsn"}) == 0)
check("bad args exit 0", sc.main(["x"], env={"SENTRY_DSN": DSN}) == 0)

print("ALL PASS" if not FAILS else "FAILED: %s" % FAILS)
sys.exit(0 if not FAILS else 1)
