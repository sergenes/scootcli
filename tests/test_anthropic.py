"""The Anthropic Messages adapter: translation, request rules per model, parsing, streaming, errors."""

from __future__ import annotations

import json

from scootcli import config as _config
from scootcli.config import Config
from scootcli.errors import ApiError, AuthError, ContextLengthError, ModelUnavailableError
from scootcli.providers import registry
from scootcli.providers.anthropic import (
    API_VERSION,
    FALLBACK_BETA,
    build_result,
    effort_param,
    fallbacks_enabled,
    thinking_param,
    translate_messages,
    translate_tool,
)


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(_config, "CREDENTIALS_FILE", tmp_path / "credentials.json")
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SCOOT_PROVIDER", "SCOOT_ANTHROPIC_FALLBACKS"):
        monkeypatch.delenv(var, raising=False)


class _Recorder:
    def __init__(self, body, status=200):
        self.body, self.status, self.last = body, status, None

    def request(self, method, url, auth_token, auth_scheme="Bearer", body=None, extra_headers=None, cancel_event=None):
        self.last = {"method": method, "url": url, "token": auth_token, "headers": extra_headers or [], "body": body}
        return self.status, self.body


_OK = json.dumps({"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5",
                  "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
                  "usage": {"input_tokens": 9, "output_tokens": 1, "cache_read_input_tokens": 0}})


def _provider(monkeypatch, tmp_path, body=_OK, status=200, effort="medium"):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-000000000000")
    t = _Recorder(body, status)
    p = registry.make_provider("anthropic", Config().override(effort=effort), transport=t)
    p._backoff = lambda attempt, ce: None
    return p, t


# ── registry ───────────────────────────────────────────────────────────────────
def test_registry_row_and_default_selection(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    spec = registry.get("anthropic")
    assert spec.wire == "anthropic" and spec.key_env == ("ANTHROPIC_API_KEY",)
    assert registry.default_provider_name(Config()) == "ollama"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-000000000000")
    assert registry.default_provider_name(Config()) == "anthropic"
    assert registry.fallback_model(Config()) == "anthropic/claude-opus-5"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-000000000000")
    assert registry.default_provider_name(Config()) == "openai"  # first registered key wins
    assert registry.default_provider_name(Config().override(provider="anthropic")) == "anthropic"
    assert _config.allowed_env_key("ANTHROPIC_API_KEY")  # .env allowlist follows the registry


# ── translation ────────────────────────────────────────────────────────────────
def test_translate_messages_roles_and_tool_results_merge():
    messages = [
        {"role": "system", "content": "be brief"},
        {"role": "system", "content": "and kind"},
        {"role": "user", "content": "list files"},
        {"role": "assistant", "content": "I'll look.", "tool_calls": [
            {"id": "toolu_1", "type": "function", "function": {"name": "list_dir", "arguments": '{"path": "."}'}},
            {"id": "toolu_2", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a"}'}}]},
        {"role": "tool", "tool_call_id": "toolu_1", "content": "a.py\nb.py"},
        {"role": "tool", "tool_call_id": "toolu_2", "content": "print(1)"},
        {"role": "user", "content": [{"type": "text", "text": "and this?"},
                                     {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA", "detail": "high"}}]},
    ]
    system, out = translate_messages(messages, "anthropic/claude-opus-5")
    assert system == "be brief\n\nand kind"
    assert out[0] == {"role": "user", "content": "list files"}
    assert out[1] == {"role": "assistant", "content": [
        {"type": "text", "text": "I'll look."},
        {"type": "tool_use", "id": "toolu_1", "name": "list_dir", "input": {"path": "."}},
        {"type": "tool_use", "id": "toolu_2", "name": "read_file", "input": {"path": "a"}}]}
    # Both results of one step land in a single user message, in order.
    assert out[2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "a.py\nb.py"},
        {"type": "tool_result", "tool_use_id": "toolu_2", "content": "print(1)"}]}
    assert out[3]["content"] == [
        {"type": "text", "text": "and this?"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}]


def test_provider_items_replay_only_for_the_producing_model():
    blocks = [{"type": "thinking", "thinking": "", "signature": "sig"},
              {"type": "tool_use", "id": "toolu_1", "name": "list_dir", "input": {}}]
    msg = {"role": "assistant", "content": "", "provider_items": {"model": "anthropic/claude-opus-5", "items": blocks},
           "tool_calls": [{"id": "toolu_1", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}]}
    _, same = translate_messages([msg], "anthropic/claude-opus-5")
    assert same == [{"role": "assistant", "content": blocks}]  # thinking block travels back verbatim
    _, other = translate_messages([msg], "anthropic/claude-sonnet-5")
    assert other == [{"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_1", "name": "list_dir", "input": {}}]}]  # rebuilt, no foreign thinking


def test_translate_tool_uses_input_schema():
    tool = {"type": "function", "function": {"name": "read_file", "description": "Read",
                                             "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}
    assert translate_tool(tool) == {"name": "read_file", "description": "Read",
                                    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}


# ── per-model request rules ────────────────────────────────────────────────────
def test_thinking_effort_and_fallback_rules(monkeypatch):
    monkeypatch.delenv("SCOOT_ANTHROPIC_FALLBACKS", raising=False)
    assert thinking_param("claude-opus-5") is None            # on by default there
    assert thinking_param("claude-sonnet-5") is None
    assert thinking_param("claude-opus-4-8") == {"type": "adaptive"}
    assert thinking_param("claude-sonnet-4-6") == {"type": "adaptive"}
    assert thinking_param("claude-haiku-4-5") is None
    assert effort_param("claude-opus-5", "xhigh") == "xhigh"
    assert effort_param("claude-opus-4-6", "xhigh") == "high"  # 4.6 caps at high
    assert effort_param("claude-haiku-4-5", "high") is None
    assert effort_param("claude-opus-5", "bogus") is None
    assert fallbacks_enabled("claude-opus-5") and not fallbacks_enabled("claude-sonnet-5")
    monkeypatch.setenv("SCOOT_ANTHROPIC_FALLBACKS", "0")
    assert not fallbacks_enabled("claude-opus-5")


def test_payload_and_headers_for_opus_5(monkeypatch, tmp_path):
    p, t = _provider(monkeypatch, tmp_path, effort="high")
    tools = [{"type": "function", "function": {"name": "f", "parameters": {"type": "object", "properties": {}}}}]
    r = p.chat([{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
               model="anthropic/claude-opus-5", tools=tools, temperature=0.2)
    assert t.last["url"] == "https://api.anthropic.com/v1/messages"
    assert t.last["token"] == ""  # no Authorization header
    assert f"x-api-key: sk-ant-test-000000000000" in t.last["headers"]
    assert f"anthropic-version: {API_VERSION}" in t.last["headers"]
    assert f"anthropic-beta: {FALLBACK_BETA}" in t.last["headers"]
    body = t.last["body"]
    assert body["model"] == "claude-opus-5" and body["system"] == "sys"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["max_tokens"] == 32000 and "stream" not in body
    assert body["tools"][0]["name"] == "f" and body["tool_choice"] == {"type": "auto"}
    assert body["output_config"] == {"effort": "high"} and body["fallbacks"] == "default"
    assert body["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in body and "temperature" not in body
    assert r.content == "ok" and r.model == "anthropic/claude-opus-5"
    assert r.usage["prompt_tokens"] == 9 and r.usage["completion_tokens"] == 1


def test_payload_for_opus_4_8_and_haiku(monkeypatch, tmp_path):
    p, t = _provider(monkeypatch, tmp_path, effort="xhigh")
    p.chat([{"role": "user", "content": "hi"}], model="claude-opus-4-8", max_tokens=4096)
    body = t.last["body"]
    assert body["thinking"] == {"type": "adaptive"} and body["output_config"] == {"effort": "xhigh"}
    assert body["max_tokens"] == 4096 and "fallbacks" not in body
    assert not any(h.startswith("anthropic-beta") for h in t.last["headers"])
    p.chat([{"role": "user", "content": "hi"}], model="claude-haiku-4-5")
    body = t.last["body"]
    assert "thinking" not in body and "output_config" not in body


# ── parsing ────────────────────────────────────────────────────────────────────
def test_build_result_text_tool_use_and_provider_items():
    blocks = [{"type": "thinking", "thinking": "", "signature": "s"},
              {"type": "text", "text": "Let me check."},
              {"type": "tool_use", "id": "toolu_9", "name": "search", "input": {"q": "x"}}]
    r = build_result(blocks, "anthropic/claude-opus-5", {"input_tokens": 5, "output_tokens": 7}, "tool_use")
    assert r.content == "Let me check." and r.finish_reason == "tool_calls"
    assert r.usage["prompt_tokens"] == 5 and r.usage["total_tokens"] == 12
    # Cached input is not in input_tokens; prompt_tokens must be the whole prompt (seen live: 2 vs ~2700).
    cached = build_result([], "m", {"input_tokens": 2, "cache_read_input_tokens": 2600,
                                   "cache_creation_input_tokens": 100, "output_tokens": 9}, "end_turn")
    assert cached.usage["prompt_tokens"] == 2702 and cached.usage["input_tokens"] == 2
    assert cached.usage["completion_tokens"] == 9 and cached.usage["total_tokens"] == 2711
    assert r.tool_calls == [{"id": "toolu_9", "type": "function", "function": {"name": "search", "arguments": '{"q": "x"}'}}]
    assert r.raw_message["provider_items"] == {"model": "anthropic/claude-opus-5", "items": blocks}
    assert build_result([{"type": "text", "text": "x"}], "m", None, "max_tokens").finish_reason == "length"


def test_refusal_is_a_clear_error():
    try:
        build_result([], "anthropic/claude-opus-5", None, "refusal", {"category": "cyber", "explanation": "nope"})
        assert False, "expected ApiError"
    except ApiError as exc:
        assert exc.code == "refusal" and "nope" in str(exc) and "/model" in exc.hint


def test_error_mapping_for_anthropic_bodies(monkeypatch, tmp_path):
    too_long = json.dumps({"type": "error", "error": {"type": "invalid_request_error",
                                                       "message": "prompt is too long: 250000 tokens > 200000 maximum"}})
    p, _ = _provider(monkeypatch, tmp_path, body=too_long, status=400)
    try:
        p.chat([{"role": "user", "content": "hi"}], model="claude-opus-5")
        assert False
    except ContextLengthError:
        pass
    missing = json.dumps({"type": "error", "error": {"type": "not_found_error", "message": "model: claude-nope"}})
    p, _ = _provider(monkeypatch, tmp_path, body=missing, status=404)
    try:
        p.chat([{"role": "user", "content": "hi"}], model="claude-nope")
        assert False
    except ModelUnavailableError:
        pass


def test_missing_key_fails_before_any_request(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    t = _Recorder(_OK)
    p = registry.make_provider("anthropic", Config(), transport=t)
    try:
        p.chat([{"role": "user", "content": "hi"}], model="claude-opus-5")
        assert False
    except AuthError as exc:
        assert "ANTHROPIC_API_KEY" in exc.hint and t.last is None


# ── streaming ──────────────────────────────────────────────────────────────────
class _StreamTransport:
    def __init__(self, lines):
        self.lines, self.calls = lines, 0

    def stream_request(self, *a, **k):
        self.calls += 1
        for line in self.lines:
            yield line


def _ev(obj: dict) -> str:
    return "data: " + json.dumps(obj)


def test_stream_text_tool_use_thinking_and_usage(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-000000000000")
    lines = [
        "event: message_start",
        _ev({"type": "message_start", "message": {"id": "msg_1", "model": "claude-opus-5",
             "usage": {"input_tokens": 40, "output_tokens": 1, "cache_read_input_tokens": 12}}}),
        _ev({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
        _ev({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": ""}}),
        _ev({"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig123"}}),
        _ev({"type": "content_block_stop", "index": 0}),
        _ev({"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}}),
        _ev({"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Let me "}}),
        _ev({"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "look."}}),
        _ev({"type": "content_block_stop", "index": 1}),
        _ev({"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "list_dir", "input": {}}}),
        _ev({"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"pa'}}),
        _ev({"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": 'th": "."}'}}),
        _ev({"type": "content_block_stop", "index": 2}),
        _ev({"type": "ping"}),
        _ev({"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 30}}),
        _ev({"type": "message_stop"}),
        "HTTP_STATUS:200",
    ]
    p = registry.make_provider("anthropic", Config(), transport=_StreamTransport(lines))
    got = []
    r = p.chat_stream([{"role": "user", "content": "ls"}], model="claude-opus-5", on_delta=got.append)
    assert got == ["Let me ", "look."]
    assert r.content == "Let me look." and r.finish_reason == "tool_calls"
    assert r.tool_calls == [{"id": "toolu_1", "type": "function", "function": {"name": "list_dir", "arguments": '{"path": "."}'}}]
    assert r.usage["prompt_tokens"] == 52 and r.usage["completion_tokens"] == 30 and r.usage["cache_read_input_tokens"] == 12
    items = r.raw_message["provider_items"]["items"]
    assert [b["type"] for b in items] == ["thinking", "text", "tool_use"]
    assert items[0]["signature"] == "sig123"  # replayable thinking block
    assert items[2]["input"] == {"path": "."}
    assert r.model == "anthropic/claude-opus-5"


def test_stream_error_event_raises(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-000000000000")
    lines = [_ev({"type": "message_start", "message": {"model": "claude-opus-5", "usage": {}}}),
             _ev({"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}),
             "HTTP_STATUS:200"]
    p = registry.make_provider("anthropic", Config(), transport=_StreamTransport(lines))
    try:
        p.chat_stream([{"role": "user", "content": "hi"}], model="claude-opus-5")
        assert False
    except ApiError as exc:
        assert "Overloaded" in str(exc)
