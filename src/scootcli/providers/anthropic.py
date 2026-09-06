"""Anthropic Messages API adapter (``POST {base}/messages``).

Anthropic has one chat endpoint and it is the same kind of API as OpenAI's Responses: stateless, a
turn is a list of typed content blocks, opaque reasoning (``thinking`` blocks) is echoed back to the
producing model. Translation from scoot's neutral format:

  * ``system`` messages → the top-level ``system`` string
  * ``user`` messages → ``text`` / ``image`` (base64) blocks
  * ``assistant`` messages → ``text`` + ``tool_use`` blocks, or the verbatim ``provider_items`` blocks
    (thinking included) when this very model produced them
  * consecutive ``tool`` messages → one ``user`` message of ``tool_result`` blocks, never split
  * tool schemas → ``{"name", "description", "input_schema"}``

Model-specific request rules (from the Claude API reference, 2026-06):
  * ``max_tokens`` is required; we default it generously because we stream.
  * Thinking: on by default on Claude Opus 5, Sonnet 5, and Fable, so no parameter is sent there;
    Opus 4.6/4.7/4.8 and Sonnet 4.6 need ``{"type": "adaptive"}`` explicitly; Haiku gets nothing.
  * ``output_config.effort`` carries ``SCOOT_EFFORT`` (``xhigh`` is downgraded to ``high`` on 4.6).
  * Sampling parameters (``temperature``) are rejected on 4.6+, so none are sent.
  * ``cache_control: {"type": "ephemeral"}`` at the top level caches the stable prefix (system prompt
    and tool list) automatically.
  * On Claude Opus 5 and Fable the server-side refusal fallback is requested by default
    (``fallbacks: "default"`` with its beta header); ``SCOOT_ANTHROPIC_FALLBACKS=0`` turns it off.
  * ``stop_reason: "refusal"`` becomes a clear error instead of an empty answer.
"""

from __future__ import annotations

import json
import os
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

API_VERSION = "2023-06-01"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
DEFAULT_MAX_TOKENS = 32000

# Models where omitting ``thinking`` means *no* thinking: send adaptive explicitly.
_EXPLICIT_ADAPTIVE = ("claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8", "claude-sonnet-4-6")
# Models that get the server-side refusal fallback by default.
_FALLBACK_MODELS = ("claude-opus-5", "claude-fable", "claude-mythos")
# 4.6-generation models cap effort at ``high``.
_EFFORT_HIGH_MAX = ("claude-opus-4-6", "claude-sonnet-4-6")


def thinking_param(model: str) -> Optional[dict]:
    m = (model or "").lower()
    if any(m.startswith(p) for p in _EXPLICIT_ADAPTIVE):
        return {"type": "adaptive"}
    return None


def effort_param(model: str, effort: str) -> Optional[str]:
    m = (model or "").lower()
    e = (effort or "").strip().lower()
    if not e or "haiku" in m:
        return None
    if e == "xhigh" and any(m.startswith(p) for p in _EFFORT_HIGH_MAX):
        return "high"
    return e if e in ("low", "medium", "high", "xhigh", "max") else None


def fallbacks_enabled(model: str) -> bool:
    m = (model or "").lower()
    if not any(m.startswith(p) for p in _FALLBACK_MODELS):
        return False
    return (os.environ.get("SCOOT_ANTHROPIC_FALLBACKS", "1").strip().lower()
            not in ("0", "false", "no", "off"))


# ── request translation ─────────────────────────────────────────────────────────
def _image_block(url: str) -> dict:
    if url.startswith("data:"):
        head, _, data = url.partition(",")
        media = head[len("data:"):].split(";")[0] or "image/png"
        return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}
    return {"type": "image", "source": {"type": "url", "url": url}}


def _user_blocks(content) -> object:
    if isinstance(content, str) or content is None:
        return content or ""
    blocks = []
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            blocks.append({"type": "text", "text": part.get("text", "")})
        elif ptype == "image_url":
            img = part.get("image_url") or {}
            url = img.get("url") if isinstance(img, dict) else img
            if url:
                blocks.append(_image_block(url))
        elif ptype in ("image", "document", "tool_result"):
            blocks.append(part)
    return blocks


def _arguments_dict(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def translate_messages(messages: List[dict], qualified_model: str) -> Tuple[str, List[dict]]:
    """Return ``(system, messages)`` for the Messages request."""
    system: List[str] = []
    out: List[dict] = []
    pending_results: List[dict] = []

    def flush_results() -> None:
        if pending_results:
            out.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for m in messages:
        role = m.get("role")
        if role in ("system", "developer"):
            text = m.get("content")
            if isinstance(text, list):
                text = "\n".join(p.get("text", "") for p in text if isinstance(p, dict))
            if text:
                system.append(str(text))
            continue
        if role == "tool":
            content = m.get("content")
            if not isinstance(content, str):
                content = json.dumps(content) if content is not None else ""
            pending_results.append({"type": "tool_result", "tool_use_id": m.get("tool_call_id", ""),
                                    "content": content})
            continue
        flush_results()
        if role == "user":
            out.append({"role": "user", "content": _user_blocks(m.get("content"))})
        elif role == "assistant":
            replay = m.get("provider_items")
            if isinstance(replay, dict) and replay.get("model") == qualified_model and replay.get("items"):
                out.append({"role": "assistant", "content": list(replay["items"])})
                continue
            blocks: List[dict] = []
            text = m.get("content")
            if isinstance(text, list):
                blocks.extend(b for b in _user_blocks(text) if b.get("type") == "text")
            elif text:
                blocks.append({"type": "text", "text": str(text)})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                blocks.append({"type": "tool_use", "id": tc.get("id", ""), "name": fn.get("name", ""),
                               "input": _arguments_dict(fn.get("arguments"))})
            if blocks:
                out.append({"role": "assistant", "content": blocks})
    flush_results()
    return "\n\n".join(system), out


def translate_tool(tool: dict) -> dict:
    fn = tool.get("function") if isinstance(tool, dict) else None
    if not isinstance(fn, dict):
        return tool
    return {
        "name": fn.get("name", ""),
        "description": fn.get("description", ""),
        "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
    }


# ── response parsing ────────────────────────────────────────────────────────────
_STOP = {"end_turn": "stop", "tool_use": "tool_calls", "max_tokens": "length", "stop_sequence": "stop",
         "pause_turn": "stop", "refusal": "refusal"}


def build_result(blocks: List[dict], model: str, usage: Optional[dict], stop_reason: str,
                 stop_details: Optional[dict] = None, streamed_text: str = "") -> ChatResult:
    if stop_reason == "refusal":
        detail = (stop_details or {}).get("explanation") or (stop_details or {}).get("category") or ""
        raise ApiError("the model declined this request" + (f": {detail}" if detail else ""),
                       code="refusal", hint="rephrase the request or switch models with /model")
    texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
    tool_calls = [{"id": b.get("id", ""), "type": "function",
                   "function": {"name": b.get("name", ""), "arguments": json.dumps(b.get("input") or {})}}
                  for b in blocks if b.get("type") == "tool_use"]
    content = streamed_text or "".join(texts)
    finish = "tool_calls" if tool_calls else _STOP.get(stop_reason, "stop")
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    if blocks:
        message["provider_items"] = {"model": model, "items": blocks}
    return ChatResult(content=content, model=model, tool_calls=tool_calls, finish_reason=finish,
                      usage=normalize_usage(usage), raw_message=message)


class _StreamState:
    """Accumulates Messages SSE events into content blocks + live text."""

    def __init__(self) -> None:
        self.blocks: dict = {}          # index -> block dict
        self._json: dict = {}           # index -> partial tool input JSON
        self.text: List[str] = []
        self.usage: dict = {}
        self.model = ""
        self.stop_reason = ""
        self.stop_details: Optional[dict] = None
        self.error: Optional[dict] = None

    def add(self, obj: dict) -> str:
        etype = obj.get("type", "")
        if etype == "message_start":
            msg = obj.get("message") or {}
            self.model = msg.get("model") or self.model
            self.usage.update(msg.get("usage") or {})
        elif etype == "content_block_start":
            idx = obj.get("index", 0)
            block = dict(obj.get("content_block") or {})
            if block.get("type") == "tool_use":
                self._json[idx] = ""
                block.setdefault("input", {})
            self.blocks[idx] = block
        elif etype == "content_block_delta":
            idx = obj.get("index", 0)
            block = self.blocks.setdefault(idx, {"type": "text", "text": ""})
            delta = obj.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                text = delta.get("text") or ""
                block["text"] = block.get("text", "") + text
                self.text.append(text)
                return text
            if dtype == "input_json_delta":
                self._json[idx] = self._json.get(idx, "") + (delta.get("partial_json") or "")
            elif dtype == "thinking_delta":
                block["thinking"] = block.get("thinking", "") + (delta.get("thinking") or "")
            elif dtype == "signature_delta":
                block["signature"] = delta.get("signature") or block.get("signature", "")
        elif etype == "content_block_stop":
            idx = obj.get("index", 0)
            if idx in self._json:
                raw = self._json.pop(idx)
                try:
                    self.blocks[idx]["input"] = json.loads(raw) if raw.strip() else {}
                except json.JSONDecodeError:
                    self.blocks[idx]["input"] = {}
        elif etype == "message_delta":
            delta = obj.get("delta") or {}
            self.stop_reason = delta.get("stop_reason") or self.stop_reason
            if delta.get("stop_details"):
                self.stop_details = delta["stop_details"]
            self.usage.update(obj.get("usage") or {})
        elif etype == "error":
            self.error = obj.get("error") or {"message": "stream error"}
        return ""

    def ordered_blocks(self) -> List[dict]:
        return [self.blocks[i] for i in sorted(self.blocks)]


class AnthropicProvider(BaseProvider):
    wire = "anthropic"

    # ── auth + headers ────────────────────────────────────────────────────────────
    def _auth_token(self) -> str:
        self._auth()  # raises the same "no API key" error as the other providers
        return ""  # the key travels in x-api-key, not Authorization

    def _headers_for(self, payload: Optional[dict]) -> List[str]:
        headers = [f"x-api-key: {self._auth()}", f"anthropic-version: {API_VERSION}"]
        if payload and payload.get("fallbacks"):
            headers.append(f"anthropic-beta: {FALLBACK_BETA}")
        return headers + self._extra_headers()

    # ── request ───────────────────────────────────────────────────────────────────
    def _payload(self, req: ChatRequest, stream: bool) -> dict:
        system, messages = translate_messages(req.messages, self.qualified(req.model))
        payload: dict = {
            "model": req.model,
            "max_tokens": req.max_tokens or DEFAULT_MAX_TOKENS,
            "messages": messages,
            "cache_control": {"type": "ephemeral"},
        }
        if system:
            payload["system"] = system
        if stream:
            payload["stream"] = True
        if req.tools:
            payload["tools"] = [translate_tool(t) for t in req.tools]
            payload["tool_choice"] = {"type": "auto"}
        thinking = thinking_param(req.model)
        if thinking:
            payload["thinking"] = thinking
        effort = effort_param(req.model, getattr(self.config, "effort", ""))
        if effort:
            payload["output_config"] = {"effort": effort}
        if fallbacks_enabled(req.model):
            payload["fallbacks"] = "default"
        return payload

    def _complete(self, req: ChatRequest) -> ChatResult:
        payload = self._payload(req, stream=False)
        status, body = self._http("POST", "/messages", payload, req.cancel_event)
        data = decode_json(status, body)
        if data.get("type") == "error" or data.get("error"):
            raise_for_status(400, json.dumps({"error": data.get("error")}), self.name)
        return build_result(data.get("content") or [], self.qualified(data.get("model") or req.model),
                            data.get("usage"), data.get("stop_reason") or "", data.get("stop_details"))

    def _stream(self, req: ChatRequest, on_delta: Callable[[str], None]) -> ChatResult:
        payload = self._payload(req, stream=True)
        state = _StreamState()
        holder: dict = {}
        leftovers: List[str] = []
        for obj in iter_sse_json(self._http_stream("/messages", payload, req.cancel_event), holder,
                                 leftovers):
            text = state.add(obj)
            if text:
                on_delta(text)
        finish_stream(holder, leftovers, self.name)
        if state.error:
            raise_for_status(400, json.dumps({"error": state.error}), self.name)
            raise ApiError(str(state.error))
        return build_result(state.ordered_blocks(), self.qualified(state.model or req.model), state.usage,
                            state.stop_reason, state.stop_details, streamed_text="".join(state.text))
