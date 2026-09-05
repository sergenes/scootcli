"""Network-free tests for SSE streaming: the Chat Completions accumulator, the Responses stream
parser, and the DONE-suppressing printer."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

from scootcli.config import Config
from scootcli.errors import QuotaError
from scootcli.providers import registry
from scootcli.providers.base import ProviderSpec
from scootcli.providers.openai_chat import OpenAIChatProvider, StreamAccumulator as _StreamAccumulator
from scootcli.providers.openai_responses import OpenAIResponsesProvider
from scootcli.repl import _StreamPrinter
from scootcli.status import Status


# ── helpers ────────────────────────────────────────────────────────────────────
def _sse(delta: dict, model: str = "m") -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta}], "model": model})


class _StreamTransport:
    """Yields a fixed list of raw body lines from stream_request()."""

    def __init__(self, lines):
        self.lines = lines
        self.calls = 0

    def stream_request(self, *a, **k):
        self.calls += 1
        for line in self.lines:
            yield line


_CHAT_SPEC = ProviderSpec(name="compat", base_url="https://compat.example/v1", wire="openai_chat",
                          key_required=False, capabilities=frozenset({"tools", "streaming"}))


def _client(lines):
    c = OpenAIChatProvider(_CHAT_SPEC, Config(), transport=_StreamTransport(lines), api_key="")
    c._backoff = lambda attempt, ce: None
    return c


# ── accumulator ─────────────────────────────────────────────────────────────────
def test_accumulator_content_and_tool_calls():
    acc = _StreamAccumulator()
    acc.add({"choices": [{"delta": {"content": "Hel"}}], "model": "m"})
    acc.add({"choices": [{"delta": {"content": "lo"}}]})
    acc.add({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "tc1", "type": "function",
         "function": {"name": "read_file", "arguments": '{"pa'}}]}}]})
    acc.add({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "function": {"arguments": 'th":"a.txt"}'}}]}}]})
    acc.add({"choices": [{"finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 5}})

    result = acc.result("m")
    assert result.content == "Hello"
    assert result.finish_reason == "tool_calls"
    assert result.usage["prompt_tokens"] == 5
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call["id"] == "tc1"
    assert call["function"]["name"] == "read_file"
    assert json.loads(call["function"]["arguments"]) == {"path": "a.txt"}


# ── chat_stream success path ────────────────────────────────────────────────────
def test_chat_stream_emits_deltas_and_accumulates():
    lines = [
        _sse({"content": "Hel"}),
        "",
        _sse({"content": "lo!"}),
        "data: [DONE]",
        "HTTP_STATUS:200",
    ]
    c = _client(lines)
    got = []
    result = c.chat_stream([{"role": "user", "content": "hi"}], model="m",
                           on_delta=got.append)
    assert got == ["Hel", "lo!"]          # tokens surfaced live, in order
    assert result.content == "Hello!"     # fully accumulated
    assert result.model == "compat/m"
    assert c.transport.calls == 1


def test_chat_stream_assembles_tool_calls():
    lines = [
        _sse({"tool_calls": [{"index": 0, "id": "tc1", "type": "function",
                              "function": {"name": "list_dir", "arguments": '{"pa'}}]}),
        _sse({"tool_calls": [{"index": 0, "function": {"arguments": 'th":"."}'}}]}),
        "data: [DONE]",
        "HTTP_STATUS:200",
    ]
    c = _client(lines)
    result = c.chat_stream([{"role": "user", "content": "hi"}], model="m")
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["function"]["name"] == "list_dir"
    assert json.loads(result.tool_calls[0]["function"]["arguments"]) == {"path": "."}


# ── chat_stream error path (no deltas emitted) ──────────────────────────────────
def test_chat_stream_error_body_raises_without_emitting():
    lines = [
        '{"error":{"message":"no seats"}}',
        "HTTP_STATUS:403",
    ]
    c = _client(lines)
    got = []
    try:
        c.chat_stream([{"role": "user", "content": "hi"}], model="m", on_delta=got.append)
        assert False, "expected QuotaError"
    except QuotaError:
        assert got == []  # nothing was printed before the error surfaced


# ── Responses API stream (event sequence as observed from Ollama 0.32 / OpenAI) ────
def _ev(obj: dict) -> str:
    return "data: " + json.dumps(obj)


def _responses_provider(lines, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-000000000000")
    p = registry.make_provider("openai", Config(), transport=_StreamTransport(lines))
    p._backoff = lambda attempt, ce: None
    return p


def test_responses_stream_text_and_usage(monkeypatch):
    lines = [
        "event: response.created", _ev({"type": "response.created", "response": {"id": "r1"}}),
        _ev({"type": "response.output_item.added", "item": {"type": "message", "id": "m1"}}),
        _ev({"type": "response.output_text.delta", "delta": "Hel"}),
        _ev({"type": "response.output_text.delta", "delta": "lo!"}),
        _ev({"type": "response.output_item.done", "item": {"type": "message", "id": "m1", "role": "assistant",
             "content": [{"type": "output_text", "text": "Hello!"}]}}),
        _ev({"type": "response.completed", "response": {"status": "completed", "model": "gpt-5.3-codex",
             "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}}}),
        "HTTP_STATUS:200",
    ]
    p = _responses_provider(lines, monkeypatch)
    got = []
    result = p.chat_stream([{"role": "user", "content": "hi"}], model="gpt-5.3-codex", on_delta=got.append)
    assert got == ["Hel", "lo!"]
    assert result.content == "Hello!" and result.finish_reason == "stop"
    assert result.model == "openai/gpt-5.3-codex"
    assert result.usage["prompt_tokens"] == 12 and result.usage["completion_tokens"] == 3
    # Output items are kept for replay, tagged with the producing model.
    assert result.raw_message["provider_items"]["model"] == "openai/gpt-5.3-codex"
    assert [it["type"] for it in result.raw_message["provider_items"]["items"]] == ["message"]


def test_responses_stream_function_call_and_reasoning(monkeypatch):
    lines = [
        _ev({"type": "response.output_item.added", "item": {"type": "reasoning", "id": "rs1"}}),
        _ev({"type": "response.output_item.done", "item": {"type": "reasoning", "id": "rs1",
             "encrypted_content": "opaque", "summary": []}}),
        _ev({"type": "response.output_item.added", "item": {"type": "function_call", "id": "fc1",
             "call_id": "call_1", "name": "list_dir", "arguments": ""}}),
        _ev({"type": "response.function_call_arguments.delta", "delta": '{"path"'}),
        _ev({"type": "response.function_call_arguments.done", "arguments": '{"path": "."}'}),
        _ev({"type": "response.output_item.done", "item": {"type": "function_call", "id": "fc1",
             "call_id": "call_1", "name": "list_dir", "arguments": '{"path": "."}', "status": "completed"}}),
        _ev({"type": "response.completed", "response": {"status": "completed", "model": "gpt-5.3-codex",
             "usage": {"input_tokens": 172, "output_tokens": 17,
                       "output_tokens_details": {"reasoning_tokens": 10}}}}),
        "HTTP_STATUS:200",
    ]
    p = _responses_provider(lines, monkeypatch)
    got = []
    result = p.chat_stream([{"role": "user", "content": "ls"}], model="gpt-5.3-codex", on_delta=got.append)
    assert got == []  # no text was streamed
    assert result.finish_reason == "tool_calls"
    assert result.tool_calls == [{"id": "call_1", "type": "function",
                                  "function": {"name": "list_dir", "arguments": '{"path": "."}'}}]
    items = result.raw_message["provider_items"]["items"]
    assert [it["type"] for it in items] == ["reasoning", "function_call"]  # both travel with the message


def test_responses_stream_error_event_raises(monkeypatch):
    lines = [
        _ev({"type": "response.created", "response": {"id": "r1"}}),
        _ev({"type": "response.failed", "response": {"status": "failed", "error": {
            "code": "context_length_exceeded", "message": "Your input exceeds the context window."}}}),
        "HTTP_STATUS:200",
    ]
    from scootcli.errors import ContextLengthError

    p = _responses_provider(lines, monkeypatch)
    try:
        p.chat_stream([{"role": "user", "content": "hi"}], model="gpt-5.3-codex")
        assert False, "expected ContextLengthError"
    except ContextLengthError:
        pass


def test_responses_stream_http_error_body(monkeypatch):
    lines = ['{"error":{"message":"insufficient_quota"}}', "HTTP_STATUS:429"]
    from scootcli.errors import RateLimitError

    p = _responses_provider(lines, monkeypatch)
    try:
        p.chat_stream([{"role": "user", "content": "hi"}], model="gpt-5.3-codex")
        assert False, "expected RateLimitError"
    except RateLimitError:
        assert p.transport.calls == 3  # retried up to the cap, nothing was printed


# ── DONE-suppressing printer ────────────────────────────────────────────────────
def _render(chunks):
    buf = io.StringIO()
    printer = _StreamPrinter(Status(enabled=False))
    with redirect_stdout(buf):
        for ch in chunks:
            printer.delta(ch)
        printer.close()
    return buf.getvalue()


def test_stream_printer_suppresses_trailing_done():
    out = _render(["Here is the answer.", "\n", "DONE"])
    assert "DONE" not in out
    assert out.startswith("Here is the answer.")
    assert out.endswith("\n")


def test_stream_printer_keeps_inline_done_word():
    # "DONE" mid-text (followed by more content) must NOT be stripped.
    out = _render(["well DONE everyone, ", "all good\n", "DONE"])
    assert "well DONE everyone" in out
    assert out.rstrip().endswith("all good")


def test_stream_printer_noop_without_tokens():
    assert _render([]) == ""


if __name__ == "__main__":
    import types

    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            fn()
            print(f"ok  {name}")
            passed += 1
    print(f"\n{passed} passed")

