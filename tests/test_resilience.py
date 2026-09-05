"""Resilience of the provider plumbing: status classification, retry with backoff, auth failures."""

from __future__ import annotations

import json

from scootcli.config import Config
from scootcli.errors import (
    ApiError,
    AuthError,
    ContextLengthError,
    ModelUnavailableError,
    QuotaError,
    RateLimitError,
    ServerError,
)
from scootcli.providers import registry
from scootcli.providers.base import raise_for_status


# ── HTTP status classification ─────────────────────────────────────────────────
def test_status_classification():
    def expect(status, body, exc):
        try:
            raise_for_status(status, body, "openai")
            assert False, f"expected {exc.__name__} for status {status}"
        except exc:
            pass

    expect(401, "", AuthError)
    expect(402, '{"error":{"message":"no credit"}}', QuotaError)
    expect(429, "", RateLimitError)
    expect(503, "", ServerError)
    expect(400, '{"error":{"code":"context_length_exceeded","message":"too long"}}', ContextLengthError)
    expect(400, '{"error":{"message":"The model `x` does not exist"}}', ModelUnavailableError)
    expect(400, '{"error":{"message":"bad request"}}', ApiError)
    expect(404, "", ApiError)
    raise_for_status(200, '{"output":[]}', "openai")  # success → no raise


def test_error_hints_name_the_provider():
    try:
        raise_for_status(401, "", "openai")
    except AuthError as exc:
        assert "openai" in exc.hint and "scoot auth" in exc.hint


# ── retry with a fake transport ────────────────────────────────────────────────
class _FakeTransport:
    def __init__(self, script):
        self.script = script
        self.i = 0

    def request(self, *a, **k):
        s = self.script[min(self.i, len(self.script) - 1)]
        self.i += 1
        return s


def _provider(script, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-000000000000")
    p = registry.make_provider("openai", Config(), transport=_FakeTransport(script))
    p._backoff = lambda attempt, ce: None  # no real sleeping in tests
    return p


_OK = (200, json.dumps({"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
                        "model": "gpt-5.3-codex", "status": "completed",
                        "usage": {"input_tokens": 3, "output_tokens": 1}}))


def test_retry_on_server_error_then_success(monkeypatch):
    p = _provider([(503, ""), _OK], monkeypatch)
    result = p.chat([{"role": "user", "content": "hi"}], model="gpt-5.3-codex")
    assert result.content == "ok" and p.transport.i == 2  # retried once
    assert result.usage["prompt_tokens"] == 3 and result.usage["completion_tokens"] == 1


def test_retry_gives_up_after_cap(monkeypatch):
    p = _provider([(503, "")], monkeypatch)
    try:
        p.chat([{"role": "user", "content": "hi"}], model="gpt-5.3-codex")
        assert False, "expected ServerError after retries exhausted"
    except ServerError:
        assert p.transport.i == 3


def test_401_is_not_retried(monkeypatch):
    p = _provider([(401, ""), _OK], monkeypatch)
    try:
        p.chat([{"role": "user", "content": "hi"}], model="gpt-5.3-codex")
        assert False, "expected AuthError"
    except AuthError:
        assert p.transport.i == 1  # a bad key does not get better by retrying


def test_missing_key_fails_before_any_request(monkeypatch, tmp_path):
    from scootcli import config as _config

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(_config, "CREDENTIALS_FILE", tmp_path / "credentials.json")
    p = registry.make_provider("openai", Config(), transport=_FakeTransport([_OK]))
    try:
        p.chat([{"role": "user", "content": "hi"}], model="gpt-5.3-codex")
        assert False, "expected AuthError"
    except AuthError as exc:
        assert "OPENAI_API_KEY" in exc.hint and p.transport.i == 0


def test_keyless_provider_sends_no_authorization(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)

    class _Recorder(_FakeTransport):
        def request(self, method, url, auth_token, **k):
            self.last = (url, auth_token)
            return super().request()

    t = _Recorder([_OK])
    p = registry.make_provider("ollama", Config(), transport=t)
    p.chat([{"role": "user", "content": "hi"}], model="llama3.2")
    assert t.last == ("http://localhost:11434/v1/responses", "")
