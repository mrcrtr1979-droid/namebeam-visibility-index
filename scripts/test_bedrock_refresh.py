"""
Self-test for the Bedrock credential refresh in engines.call_anthropic
([SONNET-BEDROCK-EXPIRY-1024]). Offline: boto3 is a fake module in sys.modules and the GitHub
OIDC endpoint is a fake requests.get, so no key, no AWS credentials and no network are used.
Every credential in here is a sentinel string; the "no secret is logged" cases search every
returned error, stdout, stderr and log record for them.

    python3 scripts/test_bedrock_refresh.py
    python3 -m pytest scripts/test_bedrock_refresh.py -q

Exits 0 only when every case passes.
"""
import contextlib
import io
import json
import logging
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["ANTHROPIC_API_" + "KEY"] = "test-dummy-0123456789"

import engines  # noqa: E402

OIDC_URL = "https://oidc.example.test/token?api-version=2.0"
REQ_TOKEN = "SENTINEL-OIDC-REQUEST-TOKEN-aaaa"
WEB_TOKEN = "SENTINEL-OIDC-WEB-IDENTITY-JWT-bbbb"
ROLE = "arn:aws:iam::123456789012:role/test-role"
NEW_CREDS = {"AccessKeyId": "SENTINELNEWACCESSKEY1", "SecretAccessKey": "SENTINEL-NEW-SECRET-KEY-2",
             "SessionToken": "SENTINEL-NEW-SESSION-TOKEN-3"}
SENTINELS = (REQ_TOKEN, WEB_TOKEN) + tuple(NEW_CREDS.values())
ENV_KEYS = ("BEDROCK_MODEL_ID", "AWS_REGION", "AWS_ROLE_ARN", "AWS_CREDS_MINTED_AT",
            "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN")


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body
        self.text = "should never be copied into an error"

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeClientError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.response = {"Error": {"Code": code, "Message": msg}}


def expired():
    return FakeClientError("ExpiredTokenException",
                           "An error occurred (ExpiredTokenException) when calling the "
                           "InvokeModel operation: The security token included in the "
                           "request is expired")


def ok_body(**kw):
    text = "Bedrock answer"
    body = {"id": "msg_bdrk_1", "model": "claude-haiku-4-5-20251001",
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 5, "output_tokens": 3}}
    return {"body": io.BytesIO(json.dumps(body).encode())}


class World:
    """Fake boto3 + fake OIDC endpoint. `invoke_plan` is a list of callables or exceptions
    consumed one per invoke_model call (the last one repeats)."""

    def __init__(self, invoke_plan=None, oidc=None, sts=None):
        self.invoke_plan = list(invoke_plan or [ok_body])
        self.oidc = oidc or (lambda: FakeResponse(200, {"value": WEB_TOKEN}))
        self.sts = sts
        self.bedrock_clients = []   # kwargs of each bedrock-runtime client built
        self.invokes = 0
        self.oidc_calls = []
        self.sts_calls = []

    def install(self):
        world = self
        mod = types.ModuleType("boto3")

        class Bedrock:
            def __init__(self, kw):
                self.kw = kw

            def invoke_model(self, **kw):
                world.invokes += 1
                step = world.invoke_plan[min(world.invokes - 1, len(world.invoke_plan) - 1)]
                if isinstance(step, Exception):
                    raise step
                return step(**kw)

        class Sts:
            def assume_role_with_web_identity(self, **kw):
                world.sts_calls.append(kw)
                if world.sts is not None:
                    return world.sts(**kw)
                return {"Credentials": dict(NEW_CREDS, Expiration="2099-01-01T00:00:00Z")}

        def client(name, **kw):
            if name == "sts":
                return Sts()
            assert name == "bedrock-runtime"
            world.bedrock_clients.append(kw)
            return Bedrock(kw)

        mod.client = client
        sys.modules["boto3"] = mod

        def fake_get(url, **kw):
            world.oidc_calls.append((url, kw))
            return world.oidc()
        engines.requests.get = fake_get


def run(world, env=None, now=None, state=None):
    """Run engines.call_anthropic on the Bedrock path inside a clean, restored environment.
    Returns (result, stdout+stderr text, log text)."""
    base_env = {"BEDROCK_MODEL_ID": "us.anthropic.test-v1:0", "AWS_ROLE_ARN": ROLE,
                "ACTIONS_ID_TOKEN_REQUEST_URL": OIDC_URL,
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": REQ_TOKEN}
    base_env.update(env or {})
    saved = {k: os.environ.get(k) for k in ENV_KEYS}
    saved_boto = sys.modules.get("boto3")
    saved_get, saved_now = engines.requests.get, engines._bedrock_now
    saved_state = dict(engines._BEDROCK_STATE)
    saved_secrets = set(engines._RUNTIME_SECRETS)
    for k in ENV_KEYS:
        os.environ.pop(k, None)
    os.environ.update(base_env)
    engines._BEDROCK_STATE.update({"creds": None, "minted_at": None, "first_use": None})
    engines._BEDROCK_STATE.update(state or {})
    engines._RUNTIME_SECRETS.clear()
    engines._bedrock_now = (lambda: now) if now is not None else (lambda: 1000.0)
    world.install()
    log = io.StringIO()
    handler = logging.StreamHandler(log)
    logging.getLogger().addHandler(handler)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = engines.call_anthropic("hello")
        return result, out.getvalue() + err.getvalue(), log.getvalue()
    finally:
        logging.getLogger().removeHandler(handler)
        engines.requests.get, engines._bedrock_now = saved_get, saved_now
        engines._BEDROCK_STATE.clear()
        engines._BEDROCK_STATE.update(saved_state)
        engines._RUNTIME_SECRETS.clear()
        engines._RUNTIME_SECRETS.update(saved_secrets)
        if saved_boto is None:
            sys.modules.pop("boto3", None)
        else:
            sys.modules["boto3"] = saved_boto
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def assert_no_secret(*texts):
    for t in texts:
        for s in SENTINELS:
            assert s not in str(t), "secret leaked: %s..." % s[:12]


def test_success_path_unchanged_no_refresh():
    w = World()
    (ok, text, cites, err), out, log = run(w)
    assert (ok, text, cites, err) == (True, "Bedrock answer", [], "")
    assert w.invokes == 1 and w.oidc_calls == [] and w.sts_calls == []
    assert w.bedrock_clients == [{"region_name": "us-east-1"}]  # default chain, no explicit creds
    assert engines.LAST_CALL_META["anthropic"]["response_id"] == "msg_bdrk_1"


def test_expired_token_one_refresh_one_retry():
    w = World(invoke_plan=[expired(), ok_body])
    (ok, text, cites, err), out, log = run(w)
    assert (ok, text, cites, err) == (True, "Bedrock answer", [], "")
    assert len(w.oidc_calls) == 1 and len(w.sts_calls) == 1
    assert w.invokes == 2
    # the retry ran on a NEW client built from the fresh credentials
    assert len(w.bedrock_clients) == 2
    assert w.bedrock_clients[0] == {"region_name": "us-east-1"}
    assert w.bedrock_clients[1] == {"region_name": "us-east-1",
                                    "aws_access_key_id": NEW_CREDS["AccessKeyId"],
                                    "aws_secret_access_key": NEW_CREDS["SecretAccessKey"],
                                    "aws_session_token": NEW_CREDS["SessionToken"]}
    assert_no_secret(out, log)


def test_oidc_request_and_assume_role_arguments():
    w = World(invoke_plan=[expired(), ok_body])
    run(w)
    url, kw = w.oidc_calls[0]
    assert url == OIDC_URL + "&audience=sts.amazonaws.com"
    assert kw["headers"]["Authorization"] == "bearer " + REQ_TOKEN
    sts = w.sts_calls[0]
    assert sts["RoleArn"] == ROLE and sts["WebIdentityToken"] == WEB_TOKEN
    assert sts["DurationSeconds"] == 3600 and sts["RoleSessionName"] == "GitHubActions"


def test_second_expiry_after_refresh_is_failed_without_more_refreshes():
    w = World(invoke_plan=[expired()])  # expired on every call
    (ok, text, cites, err), out, log = run(w)
    assert (ok, text, cites) == (False, "", [])
    assert err.startswith("anthropic bedrock failed:") and "ExpiredTokenException" in err
    assert not err.startswith("QUOTA")
    assert len(w.oidc_calls) == 1 and len(w.sts_calls) == 1 and w.invokes == 2
    assert_no_secret(err, out, log)


def test_refresh_failure_leaves_row_failed_with_error_class():
    cases = [
        (World(invoke_plan=[expired()], oidc=lambda: FakeResponse(403)), {}, "OIDCHTTP403", 1, 0),
        (World(invoke_plan=[expired()], oidc=lambda: FakeResponse(200, {"nope": 1})), {},
         "OIDCBadResponse", 1, 0),
        (World(invoke_plan=[expired()],
               sts=lambda **kw: (_ for _ in ()).throw(
                   FakeClientError("AccessDenied", "denied for token " + kw["WebIdentityToken"]))),
         {}, "STS:AccessDenied", 1, 1),
        (World(invoke_plan=[expired()]), {"ACTIONS_ID_TOKEN_REQUEST_URL": ""}, "OIDCEnvMissing", 0, 0),
        (World(invoke_plan=[expired()]), {"AWS_ROLE_ARN": ""}, "RoleArnMissing", 0, 0),
    ]
    for w, env, klass, oidc_n, sts_n in cases:
        (ok, text, cites, err), out, log = run(w, env=env)
        assert (ok, text, cites) == (False, "", []), klass
        assert err.startswith("anthropic bedrock failed:"), err
        assert "ExpiredTokenException" in err, err          # the original error is kept
        assert "credential refresh failed (%s)" % klass in err, err
        assert w.invokes == 1, klass                         # no retry when the refresh failed
        assert len(w.oidc_calls) == oidc_n and len(w.sts_calls) == sts_n, klass
        assert_no_secret(err, out, log)


def test_oidc_network_error_is_class_only():
    import requests as real_requests

    def boom():
        raise real_requests.exceptions.ConnectionError("https://x/?token=" + REQ_TOKEN)
    w = World(invoke_plan=[expired()], oidc=boom)
    (ok, _, _, err), out, log = run(w)
    assert ok is False and "OIDCRequest:ConnectionError" in err
    assert_no_secret(err, out, log)


def test_no_secret_in_error_even_when_provider_echoes_it():
    # the Bedrock error itself echoes the fresh session token on the retry
    bad = FakeClientError("ValidationException", "bad request token=" + NEW_CREDS["SessionToken"]
                          + " key " + NEW_CREDS["SecretAccessKey"])
    w = World(invoke_plan=[expired(), bad])
    (ok, _, _, err), out, log = run(w)
    assert ok is False and err.startswith("anthropic bedrock failed:")
    assert_no_secret(err, out, log)
    assert "[REDACTED" in err


def test_throttle_after_refresh_still_quota():
    w = World(invoke_plan=[expired(), FakeClientError("ThrottlingException", "Too many requests")])
    (ok, _, _, err), _, _ = run(w)
    assert ok is False and err.startswith("QUOTA: anthropic bedrock throttled: Too many requests")
    assert len(w.sts_calls) == 1 and w.invokes == 2


def test_other_errors_do_not_trigger_refresh():
    w = World(invoke_plan=[FakeClientError("AccessDeniedException", "no access")])
    (ok, _, _, err), _, _ = run(w)
    assert ok is False and err.startswith("anthropic bedrock failed:")
    assert w.oidc_calls == [] and w.sts_calls == [] and w.invokes == 1


def test_proactive_refresh_when_credentials_older_than_50_minutes():
    minted = 1000.0
    w = World()
    (ok, text, _, err), out, log = run(
        w, env={"AWS_CREDS_MINTED_AT": str(int(minted))}, now=minted + 51 * 60)
    assert (ok, text, err) == (True, "Bedrock answer", "")
    assert len(w.oidc_calls) == 1 and len(w.sts_calls) == 1 and w.invokes == 1
    assert w.bedrock_clients[0]["aws_session_token"] == NEW_CREDS["SessionToken"]
    assert_no_secret(out, log)


def test_no_proactive_refresh_under_50_minutes():
    minted = 1000.0
    w = World()
    (ok, _, _, _), _, _ = run(w, env={"AWS_CREDS_MINTED_AT": str(int(minted))}, now=minted + 49 * 60)
    assert ok and w.oidc_calls == [] and w.bedrock_clients == [{"region_name": "us-east-1"}]


def test_age_falls_back_to_first_use_without_minted_at():
    w = World()
    (ok, _, _, _), _, _ = run(w, now=5000.0)   # first use sets the clock; age 0, no refresh
    assert ok and w.oidc_calls == []


def test_fresh_credentials_are_reused_by_later_calls_without_another_refresh():
    # state left behind by a refresh 10 minutes ago: the call uses it, mints nothing
    w = World(invoke_plan=[ok_body])
    seeded = {"creds": {"aws_access_key_id": NEW_CREDS["AccessKeyId"],
                        "aws_secret_access_key": NEW_CREDS["SecretAccessKey"],
                        "aws_session_token": NEW_CREDS["SessionToken"]},
              "minted_at": 1000.0}
    (ok, _, _, _), _, _ = run(w, now=1000.0 + 600, state=seeded)
    assert ok is True and w.oidc_calls == [] and w.sts_calls == []
    assert w.bedrock_clients[0]["aws_session_token"] == NEW_CREDS["SessionToken"]


def test_refresh_state_persists_inside_the_module():
    # within ONE process the refreshed credentials are kept, so call 2 reuses them
    w = World(invoke_plan=[expired(), ok_body, ok_body])
    saved_boto = sys.modules.get("boto3")
    saved_get, saved_now = engines.requests.get, engines._bedrock_now
    saved_state = dict(engines._BEDROCK_STATE)
    saved_secrets = set(engines._RUNTIME_SECRETS)
    saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
    try:
        os.environ.update({"BEDROCK_MODEL_ID": "m", "AWS_ROLE_ARN": ROLE,
                           "ACTIONS_ID_TOKEN_REQUEST_URL": OIDC_URL,
                           "ACTIONS_ID_TOKEN_REQUEST_TOKEN": REQ_TOKEN})
        os.environ.pop("AWS_CREDS_MINTED_AT", None)
        engines._BEDROCK_STATE.update({"creds": None, "minted_at": None, "first_use": None})
        engines._bedrock_now = lambda: 1000.0
        w.install()
        r1 = engines.call_anthropic("a")
        r2 = engines.call_anthropic("b")
        assert r1[0] is True and r2[0] is True
        assert len(w.sts_calls) == 1 and len(w.oidc_calls) == 1 and w.invokes == 3
        assert w.bedrock_clients[2]["aws_session_token"] == NEW_CREDS["SessionToken"]
    finally:
        engines.requests.get, engines._bedrock_now = saved_get, saved_now
        engines._BEDROCK_STATE.clear()
        engines._BEDROCK_STATE.update(saved_state)
        engines._RUNTIME_SECRETS.clear()
        engines._RUNTIME_SECRETS.update(saved_secrets)
        if saved_boto is None:
            sys.modules.pop("boto3", None)
        else:
            sys.modules["boto3"] = saved_boto
        for k, v in saved_env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def test_api_key_path_untouched():
    saved = {k: os.environ.get(k) for k in ENV_KEYS}
    saved_post, saved_boto = engines.requests.post, sys.modules.get("boto3")
    try:
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        sys.modules["boto3"] = None  # must not be touched
        engines.requests.post = lambda url, **kw: FakeResponse(
            200, {"id": "m", "model": "x", "content": [{"text": "API answer"}], "usage": {}})
        assert engines.call_anthropic("q") == (True, "API answer", [], "")
    finally:
        engines.requests.post = saved_post
        if saved_boto is None:
            sys.modules.pop("boto3", None)
        else:
            sys.modules["boto3"] = saved_boto
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


if __name__ == "__main__":
    fails = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS " + name)
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                print("FAIL %s: %r" % (name, exc))
                fails.append(name)
    print("ALL PASS" if not fails else "FAILED: %s" % ", ".join(fails))
    sys.exit(1 if fails else 0)
