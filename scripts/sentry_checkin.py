"""
Sentry Crons check-in for a scheduled workflow (2026-10-25, [OPUS55-COS-1025A]).

One POST per run, sent from the workflow's last step with `if: always()`. It reports
ok when the job succeeded and error otherwise, and it upserts the monitor (schedule,
grace period, UTC) so no monitor is created by hand. If a scheduled run never starts
(as on 2026-10-05, when GitHub never acquired a runner), no check-in arrives and Sentry
marks the monitor missed after the grace period, which emails Terry through the alert
rule "Namebeam: any job or uptime issue, email Terry" (project namebeam-jobs).

Best effort by design: this script never exits non-zero and never changes a corpus row.
No prompt, answer or engine text is sent; the body is the status and the schedule only.

Usage (from the repo root):
    python3 scripts/sentry_checkin.py <monitor_slug> <job_status> "<crontab>" <margin_minutes>

Env:
    SENTRY_DSN       the namebeam-jobs DSN (a public client key by Sentry's design)
    DISABLE_SENTRY   kill switch: any value but empty/0/false/no/off skips the send
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

OFF_VALUES = {"", "0", "false", "no", "off"}


def disabled(env=None):
    env = os.environ if env is None else env
    return (env.get("DISABLE_SENTRY") or "").strip().lower() not in OFF_VALUES


def checkin_url(dsn, slug):
    """https://KEY@HOST/PROJECT  ->  https://HOST/api/PROJECT/cron/SLUG/KEY/"""
    parts = urllib.parse.urlsplit(dsn.strip())
    key = parts.username
    project = parts.path.strip("/")
    if parts.scheme != "https" or not key or not parts.hostname or not project:
        raise ValueError("SENTRY_DSN is not in the form https://KEY@HOST/PROJECT")
    return "https://%s/api/%s/cron/%s/%s/" % (parts.hostname, project, slug, key)


def checkin_body(job_status, crontab, margin_minutes):
    status = "ok" if (job_status or "").strip().lower() == "success" else "error"
    return {
        "status": status,
        "monitor_config": {
            "schedule": {"type": "crontab", "value": crontab},
            "checkin_margin": int(margin_minutes),
            "timezone": "UTC",
            "failure_issue_threshold": 1,
            "recovery_threshold": 1,
        },
    }


def send(url, body, opener=urllib.request.urlopen, timeout=15):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    with opener(req, timeout=timeout) as resp:
        return getattr(resp, "status", 0)


def main(argv, env=None, opener=urllib.request.urlopen):
    env = os.environ if env is None else env
    if len(argv) != 5:
        print("usage: sentry_checkin.py <slug> <job_status> <crontab> <margin_minutes>")
        return 0
    slug, job_status, crontab, margin = argv[1], argv[2], argv[3], argv[4]
    if disabled(env):
        print("sentry check-in skipped: DISABLE_SENTRY is set")
        return 0
    dsn = (env.get("SENTRY_DSN") or "").strip()
    if not dsn:
        print("sentry check-in skipped: no SENTRY_DSN")
        return 0
    try:
        body = checkin_body(job_status, crontab, margin)
        code = send(checkin_url(dsn, slug), body, opener=opener)
        print("sentry check-in %s %s http %s" % (slug, body["status"], code))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print("sentry check-in failed, ignored: %s" % exc.__class__.__name__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
