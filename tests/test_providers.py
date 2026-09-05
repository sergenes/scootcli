"""The provider layer: model ids, registry and default selection, Responses translation, the pool."""

from __future__ import annotations

import json

from scootcli import config as _config
from scootcli.config import Config
from scootcli.errors import ConfigError
from scootcli.providers import ChatResult, ModelInfo, ProviderPool, qualify, registry, split_model_id
from scootcli.providers.openai_responses import (
    OpenAIResponsesProvider,
    build_result,
    is_reasoning_model,
    translate_messages,
    translate_tool,
)


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(_config, "CREDENTIALS_FILE", tmp_path / "credentials.json")
    for var in ("OPENAI_API_KEY", "OLLAMA_API_KEY", "SCOOT_PROVIDER", "SCOOT_OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)


# ── model ids ──────────────────────────────────────────────────────────────────
def test_split_and_qualify():
    assert split_model_id("openai/gpt-5.3-codex") == ("openai", "gpt-5.3-codex")
    assert split_model_id("ollama/qwen2.5vl:7b") == ("ollama", "qwen2.5vl:7b")
    assert split_model_id("gpt-4o") == (None, "gpt-4o")
    assert split_model_id("") == (None, "")
    assert qualify("openai", "gpt-4o") == "openai/gpt-4o"
    assert qualify("openai", "ollama/llama3.2") == "ollama/llama3.2"  # never double-qualify


# ── registry + defaults ─────────────────────────────────────────────────────────
def test_builtin_specs_and_default_selection(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert set(registry.names()) >= {"openai", "ollama"}
    assert registry.get("openai").wire == "openai_responses"
    assert registry.default_provider_name(Config()) == "ollama"  # no key anywhere
    assert registry.fallback_model(Config()) == "ollama/llama3.2"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-000000000000")
    assert registry.default_provider_name(Config()) == "openai"
    assert registry.fallback_model(Config()) == "openai/gpt-5.3-codex"
    assert registry.default_provider_name(Config().override(provider="ollama")) == "ollama"


def test_unknown_provider_is_a_config_error(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    for fn in (lambda: registry.make_provider("acme", Config()),
               lambda: registry.default_provider_name(Config().override(provider="acme"))):
        try:
            fn()
            assert False, "expected ConfigError"
        except ConfigError as exc:
            assert "openai" in exc.hint


def test_base_url_override_from_env(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("SCOOT_OPENAI_BASE_URL", "https://proxy.example/v1/")
    p = registry.make_provider("openai", Config())
    assert p.spec.base_url == "https://proxy.example/v1/"
    assert p._url("/responses") == "https://proxy.example/v1/responses"


# ── Responses translation ──────────────────────────────────────────────────────
def test_translate_messages_covers_every_role():
    messages = [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "list files"},
        {"role": "assistant", "content": "I'll look.", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "list_dir", "arguments": '{"path": "."}'}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "a.py\nb.py"},
        {"role": "user", "content": [{"type": "text", "text": "and this?"},
                                     {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA", "detail": "high"}}]},
    ]
    instructions, items = translate_messages(messages, "openai/gpt-5.3-codex")
    assert instructions == "be brief"
    assert items[0] == {"role": "user", "content": "list files"}
    assert items[1] == {"role": "assistant", "content": [{"type": "output_text", "text": "I'll look."}]}
    assert items[2] == {"type": "function_call", "call_id": "call_1", "name": "list_dir", "arguments": '{"path": "."}'}
    assert items[3] == {"type": "function_call_output", "call_id": "call_1", "output": "a.py\nb.py"}
    assert items[4]["content"] == [{"type": "input_text", "text": "and this?"},
                                   {"type": "input_image", "image_url": "data:image/png;base64,AA", "detail": "high"}]


def test_provider_items_replay_only_for_the_producing_model():
    replay = {"model": "openai/gpt-5.3-codex", "items": [
        {"type": "reasoning", "id": "rs1", "encrypted_content": "opaque"},
        {"type": "function_call", "id": "fc1", "call_id": "call_1", "name": "list_dir", "arguments": "{}"},
    ]}
    msg = {"role": "assistant", "content": "", "provider_items": replay,
           "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}]}
    _, same = translate_messages([msg], "openai/gpt-5.3-codex")
    assert same == replay["items"]  # verbatim, reasoning included
    _, other = translate_messages([msg], "ollama/llama3.2")
    assert [it.get("type") for it in other] == ["function_call"]  # rebuilt, no foreign reasoning


def test_translate_tool_flattens_function_schema():
    tool = {"type": "function", "function": {"name": "read_file", "description": "Read a file",
                                             "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}
    flat = translate_tool(tool)
    assert flat == {"type": "function", "name": "read_file", "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}
    assert translate_tool(flat) == flat  # already flat passes through


def test_build_result_from_output_items():
    items = [
        {"type": "reasoning", "id": "rs1", "encrypted_content": "x"},
        {"type": "message", "id": "m1", "role": "assistant", "content": [{"type": "output_text", "text": "Hi "},
                                                                          {"type": "output_text", "text": "there"}]},
        {"type": "function_call", "id": "fc1", "call_id": "call_9", "name": "search", "arguments": '{"q": "x"}'},
    ]
    r = build_result(items, "openai/gpt-5.3-codex", {"input_tokens": 5, "output_tokens": 7}, "completed")
    assert r.content == "Hi there" and r.finish_reason == "tool_calls"
    assert r.tool_calls[0]["id"] == "call_9" and r.tool_calls[0]["function"]["name"] == "search"
    assert r.usage == {"input_tokens": 5, "output_tokens": 7, "prompt_tokens": 5, "completion_tokens": 7,
                       "total_tokens": 12}
    assert r.raw_message["provider_items"]["items"] == items
    assert build_result([], "m", None, "incomplete").finish_reason == "length"


class _Recorder:
    def __init__(self, body):
        self.body = body
        self.last = None

    def request(self, method, url, auth_token, auth_scheme="Bearer", body=None, extra_headers=None, cancel_event=None):
        self.last = {"method": method, "url": url, "token": auth_token, "body": body}
        return 200, self.body


_OK = json.dumps({"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
                  "model": "gpt-5.3-codex", "status": "completed", "usage": {"input_tokens": 1, "output_tokens": 1}})


def test_payload_flags_for_openai_reasoning_model(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-000000000000")
    t = _Recorder(_OK)
    p = registry.make_provider("openai", Config().override(effort="high"), transport=t)
    tools = [{"type": "function", "function": {"name": "f", "parameters": {"type": "object", "properties": {}}}}]
    r = p.chat([{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
               model="openai/gpt-5.3-codex", tools=tools, temperature=0.2, max_tokens=4096)
    body = t.last["body"]
    assert t.last["url"] == "https://api.openai.com/v1/responses" and t.last["token"] == "sk-test-000000000000"
    assert body["model"] == "gpt-5.3-codex" and body["store"] is False and body["stream"] is False
    assert body["instructions"] == "sys" and body["input"] == [{"role": "user", "content": "hi"}]
    assert body["tools"][0]["name"] == "f" and body["tool_choice"] == "auto"
    assert body["reasoning"] == {"effort": "high"} and body["max_output_tokens"] == 4096
    assert "temperature" not in body  # reasoning models reject it
    assert r.model == "openai/gpt-5.3-codex"


def test_payload_flags_for_ollama(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    t = _Recorder(_OK.replace("gpt-5.3-codex", "llama3.2"))
    p = registry.make_provider("ollama", Config(), transport=t)
    p.chat([{"role": "user", "content": "hi"}], model="llama3.2", temperature=0.2)
    body = t.last["body"]
    assert t.last["url"] == "http://localhost:11434/v1/responses" and t.last["token"] == ""
    assert body["temperature"] == 0.2 and "reasoning" not in body
    assert is_reasoning_model("gpt-5.3-codex") and not is_reasoning_model("llama3.2")


def test_list_models_qualifies_ids(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-000000000000")
    t = _Recorder(json.dumps({"data": [{"id": "gpt-5.3-codex"}, {"id": "gpt-4.1"}]}))
    p = registry.make_provider("openai", Config(), transport=t)
    infos = p.list_models()
    assert t.last["url"] == "https://api.openai.com/v1/models"
    assert [m.id for m in infos] == ["openai/gpt-5.3-codex", "openai/gpt-4.1"]
    assert infos[0].provider == "openai" and infos[0].name == "gpt-5.3-codex"


# ── the pool ───────────────────────────────────────────────────────────────────
class _FakeProvider:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def chat(self, messages, model=None, **kw):
        self.calls.append(("chat", model))
        return ChatResult(content=f"{self.name}:{model}", model=f"{self.name}/{model}")

    def chat_stream(self, messages, model=None, on_delta=None, **kw):
        self.calls.append(("stream", model))
        return ChatResult(content=f"{self.name}:{model}", model=f"{self.name}/{model}")

    def list_models(self, cancel_event=None):
        if self.name == "ollama":
            from scootcli.errors import NetworkError

            raise NetworkError("connection refused")
        return [ModelInfo(id=f"{self.name}/m1", provider=self.name, name="m1")]


def test_pool_dispatches_by_prefix_and_default(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-000000000000")
    pool = ProviderPool(Config())
    fakes = {"openai": _FakeProvider("openai"), "ollama": _FakeProvider("ollama")}
    monkeypatch.setattr(registry, "make_provider", lambda name, config, transport=None: fakes[name])
    assert pool.default_name == "openai"
    assert pool.chat([], model="ollama/llama3.2").content == "ollama:llama3.2"
    assert pool.chat([], model="gpt-4.1").content == "openai:gpt-4.1"  # bare → default provider
    assert pool.chat_stream([], model=None).content == "openai:gpt-5.3-codex"  # auto → fallback
    infos = pool.list_models()
    assert [m.id for m in infos] == ["openai/m1"]
    assert "ollama" in pool.list_errors  # a dead local server is reported, not fatal
    try:
        pool.chat([], model="acme/x")
        assert False
    except ConfigError:
        pass
