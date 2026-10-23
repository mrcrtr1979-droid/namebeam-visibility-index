"""
Self-test for the Bedrock path of call_anthropic. Offline: boto3 is replaced
with a fake module in sys.modules and requests.post with a fake response, so
no key, no AWS credentials and no network are needed. Run from the repo root:

    python3 scripts/test_bedrock_engine.py
    python3 -m pytest scripts/test_bedrock_engine.py -q

Exits 0 only when every case passes.
"""
import io
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["ANTHROPIC_API_" + "KEY"] = "test-dummy-0123456789"

import engines  # noqa: E402


class FakeResponse:
    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        return self._body


class FakeClientError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.response = {"Error": {"Code": code, "Message": msg}}


def install_fake_boto3(invoke):
    calls = {}
    mod = types.ModuleType("boto3")

    class Client:
        def invoke_model(self, **kw):
            calls["invoke"] = kw
            return invoke(**kw)

    def client(name, **kw):
        calls["client"] = (name, kw)
        return Client()

    mod.client = client
    sys.modules["boto3"] = mod
    return calls


def bedrock_ok(**kw):
    body = {"id": "msg_bdrk_1", "model": "claude-haiku-4-5-20251001",
            "content": [{"type": "text", "text": "Bedrock answer"}],
            "usage": {"input_tokens": 5, "output_tokens": 3}}
    return {"body": io.BytesIO(json.dumps(body).encode())}


def run(env, fn):
    saved = {k: os.environ.get(k) for k in ("BEDROCK_MODEL_ID", "AWS_REGION")}
    for k in saved:
        os.environ.pop(k, None)
    os.environ.update(env)
    saved_boto = sys.modules.get("boto3")
    saved_post = engines.requests.post
    try:
        return fn()
    finally:
        engines.requests.post = saved_post
        if saved_boto is None:
            sys.modules.pop("boto3", None)
        else:
            sys.modules["boto3"] = saved_boto
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def test_bedrock_returns_text_and_meta():
    def go():
        calls = install_fake_boto3(bedrock_ok)
        out = engines.call_anthropic("hello")
        return out, calls, dict(engines.LAST_CALL_META["anthropic"])
    out, calls, meta = run({"BEDROCK_MODEL_ID": "us.anthropic.test-v1:0"}, go)
    assert out == (True, "Bedrock answer", [], "")
    assert calls["client"][0] == "bedrock-runtime"
    assert calls["client"][1]["region_name"] == "us-east-1"
    assert calls["invoke"]["modelId"] == "us.anthropic.test-v1:0"
    sent = json.loads(calls["invoke"]["body"])
    assert sent["anthropic_version"] == "bedrock-2023-05-31"
    assert sent["max_tokens"] == 2048
    assert sent["temperature"] == engines.TEMPERATURE
    assert sent["messages"] == [{"role": "user", "content": "hello"}]
    assert meta["response_id"] == "msg_bdrk_1"
    assert meta["usage"] == {"input_tokens": 5, "output_tokens": 3}
    assert meta["served_model"] == "claude-haiku-4-5-20251001"


def test_bedrock_region_env_and_missing_fields():
    def go():
        calls = install_fake_boto3(lambda **kw: {"body": json.dumps(
            {"content": [{"text": "T"}]}).encode()})
        out = engines.call_anthropic("q")
        return out, calls, dict(engines.LAST_CALL_META["anthropic"])
    out, calls, meta = run({"BEDROCK_MODEL_ID": "m", "AWS_REGION": "us-west-2"}, go)
    assert out == (True, "T", [], "")
    assert calls["client"][1]["region_name"] == "us-west-2"
    assert meta["served_model"] == "m" and meta["response_id"] is None


def test_bedrock_throttle_is_quota():
    def go():
        def boom(**kw):
            raise FakeClientError("ThrottlingException", "Too many requests")
        install_fake_boto3(boom)
        return engines.call_anthropic("q")
    ok, text, cites, err = run({"BEDROCK_MODEL_ID": "m"}, go)
    assert (ok, text, cites) == (False, "", [])
    assert err.startswith("QUOTA: anthropic bedrock throttled: Too many requests")


def test_bedrock_other_error_and_bad_shape():
    def go_err():
        def boom(**kw):
            raise FakeClientError("AccessDeniedException", "no access")
        install_fake_boto3(boom)
        return engines.call_anthropic("q")
    ok, text, cites, err = run({"BEDROCK_MODEL_ID": "m"}, go_err)
    assert (ok, text, cites) == (False, "", []) and err.startswith("anthropic bedrock failed:")
    assert not err.startswith("QUOTA")

    def go_shape():
        install_fake_boto3(lambda **kw: {"body": b'{"content": []}'})
        return engines.call_anthropic("q")
    ok, _, _, err = run({"BEDROCK_MODEL_ID": "m"}, go_shape)
    assert ok is False and err.startswith("anthropic bedrock failed:")


def test_empty_bedrock_id_uses_api_path():
    def go():
        seen = {}

        def fake_post(url, **kw):
            seen["url"] = url
            seen["headers"] = kw["headers"]
            return FakeResponse(200, {"id": "msg_1", "model": "claude-x",
                                      "content": [{"text": "API answer"}],
                                      "usage": {"input_tokens": 1}})
        engines.requests.post = fake_post
        # boto3 must not be touched on this path
        sys.modules["boto3"] = None
        return engines.call_anthropic("q"), seen
    for env in ({}, {"BEDROCK_MODEL_ID": "   "}):
        out, seen = run(env, go)
        assert out == (True, "API answer", [], "")
        assert seen["url"] == engines.ANTHROPIC_URL
        assert "x-api-key" in seen["headers"]


if __name__ == "__main__":
    fails = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS " + name)
            except Exception as exc:  # noqa: BLE001
                print("FAIL %s: %r" % (name, exc))
                fails.append(name)
    sys.exit(1 if fails else 0)
