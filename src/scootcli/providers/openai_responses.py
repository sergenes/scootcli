"""OpenAI Responses API adapter (``POST {base}/responses``).

This is the wire OpenAI recommends for new work and the only one that serves its Codex models. Ollama
(0.32+) implements the same endpoint, so one adapter covers both. Translation from scoot's neutral
format:

  * ``system`` messages → ``instructions``
  * ``user`` messages → message items (``input_text`` / ``input_image`` parts)
  * ``assistant`` messages → an ``output_text`` message item plus one ``function_call`` item per tool
    call, or, when the message carries ``provider_items`` produced by this very model, those items are
    replayed verbatim (that is how encrypted ``reasoning`` items travel across tool calls)
  * ``tool`` messages → ``function_call_output`` items
  * tool schemas → flat ``{"type": "function", "name", "description", "parameters"}``

Requests are stateless (``store: false``); scoot replays history itself.
"""

from __future__ import annotations

import json
from typing import Callable, List, Optional, Tuple

from ..errors import ApiError
from .base import (
    BaseProvider,
    ChatRequest,
    ChatResult,
    decode_json,
    finish_stream,
    iter_sse_json,
    normalize_usage,
    raise_for_status,
)

# Models that accept the ``reasoning`` parameter (others reject it with a 400).
_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4", "codex")


def is_reasoning_model(model: str) -> bool:
    m = (model or "").lower()
    return any(m.startswith(p) or p in m for p in _REASONING_PREFIXES)


# ── request translation ─────────────────────────────────────────────────────────
def _content_parts(content, role: str) -> object:
    """Neutral content (str or OpenAI-chat parts) → Responses content for ``role``."""
    if isinstance(content, str) or content is None:
        return content or ""
    text_type = "output_text" if role == "assistant" else "input_text"
    parts = []
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            parts.append({"type": text_type, "text": part.get("text", "")})
        elif ptype == "image_url":
            img = part.get("image_url") or {}
            url = img.get("url") if isinstance(img, dict) else img
            item = {"type": "input_image", "image_url": url}
            if isinstance(img, dict) and img.get("detail"):
                item["detail"] = img["detail"]
            parts.append(item)
        elif ptype in ("input_text", "output_text", "input_image"):
            parts.append(part)
    return parts


def _arguments(raw) -> str:
    if isinstance(raw, str):
        return raw
    return json.dumps(raw or {})


def translate_messages(messages: List[dict], qualified_model: str) -> Tuple[str, List[dict]]:
    """Return ``(instructions, input_items)`` for the Responses request."""
    instructions: List[str] = []
    items: List[dict] = []
    for m in messages:
        role = m.get("role")
        if role in ("system", "developer"):
            text = m.get("content")
            if isinstance(text, list):
                text = "\n".join(p.get("text", "") for p in text if isinstance(p, dict))
            if text:
                instructions.append(str(text))
        elif role == "user":
            items.append({"role": "user", "content": _content_parts(m.get("content"), "user")})
        elif role == "assistant":
            replay = m.get("provider_items")
            if isinstance(replay, dict) and replay.get("model") == qualified_model and replay.get("items"):
                items.extend(replay["items"])
                continue
            text = m.get("content")
            if isinstance(text, list):
                items.append({"role": "assistant", "content": _content_parts(text, "assistant")})
            elif text:
                items.append({"role": "assistant",
                              "content": [{"type": "output_text", "text": str(text)}]})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                items.append({
                    "type": "function_call",
                    "call_id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "arguments": _arguments(fn.get("arguments")),
                })
        elif role == "tool":
            content = m.get("content")
            if not isinstance(content, str):
                content = json.dumps(content) if content is not None else ""
            items.append({"type": "function_call_output", "call_id": m.get("tool_call_id", ""),
                          "output": content})
    return "\n\n".join(instructions), items


def translate_tool(tool: dict) -> dict:
    """Neutral ``{"type": "function", "function": {...}}`` → flat Responses tool definition."""
    fn = tool.get("function") if isinstance(tool, dict) else None
    if not isinstance(fn, dict):
        return tool
    return {
        "type": "function",
        "name": fn.get("name", ""),
        "description": fn.get("description", ""),
        "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
    }


# ── response parsing ────────────────────────────────────────────────────────────
def _text_of(item: dict) -> str:
    parts = item.get("content") or []
    if isinstance(parts, str):
        return parts
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "output_text")


def _tool_call_of(item: dict) -> dict:
    return {
        "id": item.get("call_id") or item.get("id") or "",
        "type": "function",
        "function": {"name": item.get("name", ""), "arguments": item.get("arguments") or "{}"},
    }


def build_result(items: List[dict], model: str, usage: Optional[dict], status: str,
                 streamed_text: str = "") -> ChatResult:
    """Fold output items into a :class:`ChatResult`; ``provider_items`` keeps them for replay."""
    texts = [_text_of(it) for it in items if it.get("type") == "message"]
    tool_calls = [_tool_call_of(it) for it in items if it.get("type") == "function_call"]
    content = streamed_text or "".join(texts)
    if tool_calls:
        finish = "tool_calls"
    elif status == "incomplete":
        finish = "length"
    else:
        finish = "stop"
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    if items:
        message["provider_items"] = {"model": model, "items": items}
    return ChatResult(content=content, model=model, tool_calls=tool_calls, finish_reason=finish,
                      usage=normalize_usage(usage), raw_message=message)


class _StreamState:
    """Accumulates Responses SSE events into output items + live text."""

    def __init__(self) -> None:
        self.items: List[dict] = []
        self.text: List[str] = []
        self.usage: dict = {}
        self.model = ""
        self.status = ""
        self.error: Optional[dict] = None

    def add(self, obj: dict) -> str:
        etype = obj.get("type", "")
        if etype == "response.output_text.delta":
            delta = obj.get("delta") or ""
            if delta:
                self.text.append(delta)
            return delta
        if etype == "response.output_item.done":
            item = obj.get("item")
            if isinstance(item, dict):
                self.items.append(item)
        elif etype in ("response.completed", "response.incomplete", "response.failed"):
            resp = obj.get("response") or {}
            self.usage = resp.get("usage") or self.usage
            self.model = resp.get("model") or self.model
            self.status = resp.get("status") or etype.rsplit(".", 1)[-1]
            if resp.get("error"):
                self.error = resp["error"]
            if not self.items and resp.get("output"):
                self.items = list(resp["output"])
        elif etype == "error":
            self.error = {"message": obj.get("message") or obj.get("error") or "stream error",
                          "code": obj.get("code", "")}
        return ""


class OpenAIResponsesProvider(BaseProvider):
    wire = "openai_responses"

    def _payload(self, req: ChatRequest, stream: bool) -> dict:
        instructions, items = translate_messages(req.messages, self.qualified(req.model))
        payload: dict = {"model": req.model, "input": items, "store": False, "stream": stream}
        if instructions:
            payload["instructions"] = instructions
        if req.tools:
            payload["tools"] = [translate_tool(t) for t in req.tools]
            payload["tool_choice"] = req.tool_choice or "auto"
        if req.max_tokens:
            payload["max_output_tokens"] = req.max_tokens
        if req.temperature is not None and self.spec.has("temperature"):
            payload["temperature"] = req.temperature
        effort = getattr(self.config, "effort", "") or ""
        if self.spec.has("reasoning") and is_reasoning_model(req.model) and effort:
            payload["reasoning"] = {"effort": effort}
        return payload

    def _complete(self, req: ChatRequest) -> ChatResult:
        payload = self._payload(req, stream=False)
        status, body = self._http("POST", "/responses", payload, req.cancel_event)
        data = decode_json(status, body)
        if data.get("error"):
            raise_for_status(400, json.dumps({"error": data["error"]}), self.name)
        return build_result(data.get("output") or [], self.qualified(data.get("model") or req.model),
                            data.get("usage"), data.get("status", ""))

    def _stream(self, req: ChatRequest, on_delta: Callable[[str], None]) -> ChatResult:
        payload = self._payload(req, stream=True)
        state = _StreamState()
        holder: dict = {}
        leftovers: List[str] = []
        for obj in iter_sse_json(self._http_stream("/responses", payload, req.cancel_event), holder,
                                 leftovers):
            text = state.add(obj)
            if text:
                on_delta(text)
        finish_stream(holder, leftovers, self.name)
        if state.error:
            raise_for_status(400, json.dumps({"error": state.error}), self.name)
            raise ApiError(str(state.error))
        return build_result(state.items, self.qualified(state.model or req.model), state.usage,
                            state.status, streamed_text="".join(state.text))
