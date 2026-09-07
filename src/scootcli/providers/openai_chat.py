"""OpenAI Chat Completions adapter (``POST {base}/chat/completions``).

Scoot's neutral format *is* this wire format, so translation is a pass-through apart from stripping
scoot-private keys. Kept for OpenAI-compatible endpoints that do not implement the Responses API
(``wire="openai_chat"`` on their registry row); nothing in the default set uses it.
"""

from __future__ import annotations

from typing import Callable, List

from ..errors import ApiError
from .base import (
    BaseProvider,
    ChatRequest,
    ChatResult,
    decode_json,
    finish_stream,
    iter_sse_json,
    normalize_usage,
)

_PRIVATE_KEYS = ("provider_items",)


def strip_private(messages: List[dict]) -> List[dict]:
    """Drop scoot-private keys the API would reject."""
    out = []
    for m in messages:
        if any(k in m for k in _PRIVATE_KEYS):
            m = {k: v for k, v in m.items() if k not in _PRIVATE_KEYS}
        out.append(m)
    return out


class StreamAccumulator:
    """Reassembles a streamed chat completion from SSE ``choices[0].delta`` fragments."""

    def __init__(self) -> None:
        self._content: List[str] = []
        self._tool_calls: dict = {}  # index -> {id, type, function:{name, arguments}}
        self.finish_reason = ""
        self.model = ""
        self.usage: dict = {}

    def add(self, obj: dict) -> str:
        """Fold one SSE chunk in; return any new content text to emit live."""
        if obj.get("model"):
            self.model = obj["model"]
        if obj.get("usage"):
            self.usage = obj["usage"]
        choices = obj.get("choices") or []
        if not choices:
            return ""
        choice = choices[0]
        if choice.get("finish_reason"):
            self.finish_reason = choice["finish_reason"]
        delta = choice.get("delta") or {}
        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index", 0)
            slot = self._tool_calls.setdefault(
                idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
            )
            if tc.get("id"):
                slot["id"] = tc["id"]
            if tc.get("type"):
                slot["type"] = tc["type"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["function"]["name"] = fn["name"]
            if fn.get("arguments"):
                slot["function"]["arguments"] += fn["arguments"]
        text = delta.get("content") or ""
        if text:
            self._content.append(text)
        return text

    def result(self, model_sent: str) -> ChatResult:
        calls = [self._tool_calls[i] for i in sorted(self._tool_calls)]
        content = "".join(self._content)
        message: dict = {"role": "assistant", "content": content}
        if calls:
            message["tool_calls"] = calls
        return ChatResult(content=content, model=self.model or model_sent, tool_calls=calls,
                          finish_reason=self.finish_reason, usage=normalize_usage(self.usage),
                          raw_message=message)


def parse_chat(data: dict, model_sent: str) -> ChatResult:
    try:
        choice = data["choices"][0]
        message = choice.get("message", {})
    except (KeyError, IndexError, TypeError) as exc:
        raise ApiError(f"unexpected chat response shape: {str(data)[:200]}") from exc
    return ChatResult(
        content=message.get("content") or "",
        model=data.get("model", model_sent),
        tool_calls=message.get("tool_calls") or [],
        finish_reason=choice.get("finish_reason", ""),
        usage=normalize_usage(data.get("usage", {})),
        raw_message=message,
    )


class OpenAIChatProvider(BaseProvider):
    wire = "openai_chat"

    def _payload(self, req: ChatRequest, stream: bool) -> dict:
        payload: dict = {"model": req.model, "messages": strip_private(req.messages)}
        if req.temperature is not None and self.spec.has("temperature"):
            payload["temperature"] = req.temperature
        if req.max_tokens is not None:
            payload["max_tokens"] = req.max_tokens
        if req.tools:
            payload["tools"] = req.tools
            payload["tool_choice"] = req.tool_choice or "auto"
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _complete(self, req: ChatRequest) -> ChatResult:
        payload = self._payload(req, stream=False)
        status, body = self._http("POST", "/chat/completions", payload, req.cancel_event)
        result = parse_chat(decode_json(status, body), req.model)
        result.model = self.qualified(result.model)
        return result

    def _stream(self, req: ChatRequest, on_delta: Callable[[str], None]) -> ChatResult:
        payload = self._payload(req, stream=True)
        acc = StreamAccumulator()
        holder: dict = {}
        leftovers: List[str] = []
        for obj in iter_sse_json(self._http_stream("/chat/completions", payload, req.cancel_event),
                                 holder, leftovers):
            text = acc.add(obj)
            if text:
                on_delta(text)
        finish_stream(holder, leftovers, self.name)
        if not acc.finish_reason and not holder.get("done"):
            acc.finish_reason = "incomplete"  # neither a finish_reason nor [DONE]: the stream broke off
        result = acc.result(req.model)
        result.model = self.qualified(result.model)
        return result
