"""Provider protocol, neutral types, and the plumbing every wire adapter shares.

A wire adapter subclasses :class:`BaseProvider` and implements three things: ``_complete`` (one
buffered chat call), ``_stream`` (one streamed call, invoking ``on_delta``), and optionally
``_parse_models``. Everything else, API-key lookup, retries with capped backoff, HTTP status to typed
error mapping, and ``provider/model`` id handling, lives here so adapters stay small.
"""

from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, FrozenSet, Iterator, List, Optional, Protocol, Tuple

from ..errors import (
    ApiError,
    AuthError,
    ContextLengthError,
    Interrupted,
    ModelUnavailableError,
    NetworkError,
    QuotaError,
    RETRIABLE,
    RateLimitError,
    ServerError,
)
from ..transport import _STATUS_MARKER, make_transport

_RETRY_ATTEMPTS = 3
_RETRY_BASE = 0.5
_RETRY_CAP = 8.0

STATUS_LINE_PREFIX = _STATUS_MARKER.strip()  # "HTTP_STATUS:"


# ── neutral types ──────────────────────────────────────────────────────────────
@dataclass
class ChatResult:
    """Parsed result of one model call (provider-independent)."""

    content: str
    model: str
    tool_calls: List[dict] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)
    raw_message: dict = field(default_factory=dict)  # the assistant message to append to history


@dataclass
class ChatRequest:
    """One model call. ``hints`` is free-form metadata a router may read (task text, has_images…)."""

    messages: List[dict]
    model: str
    tools: Optional[List[dict]] = None
    tool_choice: str = "auto"
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    cancel_event: Optional[threading.Event] = None
    hints: Dict = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderSpec:
    """A registry row: how to reach one provider. Pure data, so new providers are configuration."""

    name: str
    base_url: str
    wire: str = "openai_responses"  # openai_responses | openai_chat
    key_env: Tuple[str, ...] = ()  # env var names that may hold the API key, in order
    key_required: bool = True
    preferred_models: Tuple[str, ...] = ()  # substrings, best first; used by ``auto`` and as fallback
    vision_models: Tuple[str, ...] = ()  # substrings of models that accept image input
    capabilities: FrozenSet[str] = frozenset({"tools", "streaming"})  # + vision reasoning temperature
    extra_headers: Tuple[Tuple[str, str], ...] = ()

    def has(self, capability: str) -> bool:
        return capability in self.capabilities


@dataclass
class ModelInfo:
    """One model as listed by a provider; ``id`` is the qualified ``provider/name``."""

    id: str
    provider: str
    name: str
    raw: dict = field(default_factory=dict)


def split_model_id(model: Optional[str]) -> Tuple[Optional[str], str]:
    """``"openai/gpt-5.3-codex"`` → ``("openai", "gpt-5.3-codex")``; a bare name → ``(None, name)``."""
    if model and "/" in model:
        head, _, tail = model.partition("/")
        if head and tail and " " not in head and "." not in head:
            return head, tail
    return None, model or ""


def qualify(provider: str, model: str) -> str:
    """Prefix ``model`` with ``provider/`` unless it already carries a provider."""
    return model if split_model_id(model)[0] else f"{provider}/{model}"


class Provider(Protocol):
    """What consumers depend on. :class:`~scootcli.providers.registry.ProviderPool` implements it too."""

    def chat(self, messages: List[dict], model: Optional[str] = None, tools=None, tool_choice="auto",
             temperature=None, max_tokens=None, cancel_event=None, hints=None) -> ChatResult: ...

    def chat_stream(self, messages: List[dict], model: Optional[str] = None, tools=None,
                    tool_choice="auto", temperature=None, max_tokens=None, cancel_event=None,
                    on_delta: Optional[Callable[[str], None]] = None, hints=None) -> ChatResult: ...

    def list_models(self, cancel_event=None) -> List[ModelInfo]: ...


# ── error mapping ──────────────────────────────────────────────────────────────
def _error_fields(body: str) -> Tuple[str, str]:
    """Pull ``(message, code)`` out of an OpenAI/Anthropic-style ``{"error": ...}`` body."""
    if not body:
        return "", ""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return body[:200], ""
    if not isinstance(data, dict):
        return "", ""
    err = data.get("error")
    if isinstance(err, dict):
        return (err.get("message") or ""), (err.get("code") or err.get("type") or "")
    if isinstance(err, str):
        return err, ""
    return "", ""


def raise_for_status(status: int, body: str, provider: str = "") -> None:
    """Classify an HTTP status + error body into a typed error; return silently on success."""
    msg, code = _error_fields(body)
    who = provider or "the provider"
    if status == 401:
        raise AuthError(msg or "unauthorized (401)",
                        hint=f"API key for {who} is missing or invalid; run `scoot auth`")
    if status in (402, 403):
        raise QuotaError(msg or f"access denied ({status})", status=status,
                         hint=f"check billing, quota, and permissions for {who}")
    if status == 429:
        raise RateLimitError(msg or "rate limited (429)", status=status,
                             hint="too many requests; backing off and retrying")
    if 500 <= status <= 599:
        raise ServerError(msg or f"server error ({status})", status=status,
                          hint=f"{who} issue; backing off and retrying")
    if status >= 400 or msg:
        low = (code + " " + msg).lower()
        if ("prompt is too long" in low or "too many tokens" in low
                or ("context" in low and any(w in low for w in ("length", "exceed", "maximum", "token")))):
            raise ContextLengthError(msg or "context length exceeded", status=status, code=code,
                                     hint="run /compact to shrink context, then retry")
        if "model" in low and any(w in low for w in
                                  ("not supported", "not accessible", "not found", "not_found", "unsupported",
                                   "does not exist", "not exist")):
            raise ModelUnavailableError(msg or "model unavailable", status=status, code=code,
                                        hint="switching to a supported model")
        if msg:
            raise ApiError(msg, status=status, code=code)
        raise ApiError(f"request failed (HTTP {status})", status=status)


def decode_json(status: int, body: str) -> dict:
    if not body:
        raise ApiError("empty response from API", status=status)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ApiError(f"non-JSON response (HTTP {status}): {body[:200]}", status=status) from exc


def normalize_usage(usage: Optional[dict]) -> dict:
    """Return usage with the neutral ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens`` keys."""
    if not isinstance(usage, dict):
        return {}
    out = dict(usage)
    if "prompt_tokens" not in out and "input_tokens" in out:
        out["prompt_tokens"] = out.get("input_tokens") or 0
    if "completion_tokens" not in out and "output_tokens" in out:
        out["completion_tokens"] = out.get("output_tokens") or 0
    if "total_tokens" not in out:
        out["total_tokens"] = int(out.get("prompt_tokens") or 0) + int(out.get("completion_tokens") or 0)
    return out


# ── shared plumbing ────────────────────────────────────────────────────────────
class BaseProvider:
    """Auth, retries, and HTTP for one provider; wire adapters implement the (de)serialisation."""

    wire = "abstract"

    def __init__(self, spec: ProviderSpec, config, transport=None, api_key: Optional[str] = None):
        self.spec = spec
        self.config = config
        self.transport = transport or make_transport(config)
        self._api_key = api_key

    # ── identity ────────────────────────────────────────────────────────────────
    @property
    def name(self) -> str:
        return self.spec.name

    def default_model(self) -> Optional[str]:
        """The provider's first preferred model (bare), or ``None`` when it has no opinion."""
        return self.spec.preferred_models[0] if self.spec.preferred_models else None

    def bare_model(self, model: Optional[str]) -> str:
        """Strip our own ``provider/`` prefix; a bare name (or another provider's) passes through."""
        head, tail = split_model_id(model)
        return tail if head == self.name else (model or "")

    def qualified(self, model: str) -> str:
        return qualify(self.name, self.bare_model(model))

    # ── public API (the Provider protocol) ───────────────────────────────────────
    def chat(self, messages, model=None, tools=None, tool_choice="auto", temperature=None,
             max_tokens=None, cancel_event=None, hints=None) -> ChatResult:
        req = self._request(messages, model, tools, tool_choice, temperature, max_tokens,
                            cancel_event, hints)
        return self._with_retry(lambda: self._complete(req), req.cancel_event)

    def chat_stream(self, messages, model=None, tools=None, tool_choice="auto", temperature=None,
                    max_tokens=None, cancel_event=None, on_delta=None, hints=None) -> ChatResult:
        req = self._request(messages, model, tools, tool_choice, temperature, max_tokens,
                            cancel_event, hints)
        emitted = [False]
        sink = on_delta or (lambda _t: None)

        def _on_delta(text: str) -> None:
            emitted[0] = True
            sink(text)

        return self._with_retry(lambda: self._stream(req, _on_delta), req.cancel_event, emitted)

    def list_models(self, cancel_event=None) -> List[ModelInfo]:
        status, body = self._http("GET", "/models", None, cancel_event)
        return self._parse_models(decode_json(status, body))

    # ── adapter hooks ────────────────────────────────────────────────────────────
    def _complete(self, req: ChatRequest) -> ChatResult:
        raise NotImplementedError

    def _stream(self, req: ChatRequest, on_delta: Callable[[str], None]) -> ChatResult:
        raise NotImplementedError

    def _parse_models(self, data) -> List[ModelInfo]:
        """OpenAI-style ``{"data": [{"id": ...}]}`` (also what Ollama's ``/v1/models`` returns)."""
        rows = data.get("data", []) if isinstance(data, dict) else data
        out = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            mid = row.get("id") or row.get("name")
            if mid:
                out.append(ModelInfo(id=qualify(self.name, mid), provider=self.name, name=mid, raw=row))
        return out

    # ── request assembly ─────────────────────────────────────────────────────────
    def _request(self, messages, model, tools, tool_choice, temperature, max_tokens, cancel_event,
                 hints) -> ChatRequest:
        bare = self.bare_model(model) or self.default_model() or ""
        if not bare:
            raise ApiError(f"no model given for provider '{self.name}'",
                           hint=f"pass --model {self.name}/<name> or set SCOOT_MODEL")
        return ChatRequest(messages=list(messages), model=bare, tools=tools, tool_choice=tool_choice,
                           temperature=temperature, max_tokens=max_tokens, cancel_event=cancel_event,
                           hints=dict(hints or {}))

    # ── HTTP ─────────────────────────────────────────────────────────────────────
    def api_key(self) -> Optional[str]:
        if self._api_key is None:
            from ..auth import api_key_for

            self._api_key = api_key_for(self.spec) or ""
        return self._api_key or None

    def _auth(self) -> str:
        key = self.api_key()
        if not key and self.spec.key_required:
            env = self.spec.key_env[0] if self.spec.key_env else f"{self.name.upper()}_API_KEY"
            raise AuthError(f"no API key for {self.name}",
                            hint=f"set {env} or run `scoot auth set {self.name}`")
        return key or ""

    def _url(self, path: str) -> str:
        return self.spec.base_url.rstrip("/") + path

    def _extra_headers(self) -> List[str]:
        return [f"{k}: {v}" for k, v in self.spec.extra_headers]

    # Two hooks let an adapter change how the key travels: by default it is a Bearer token in
    # ``Authorization``; Anthropic overrides both to send ``x-api-key`` plus its version header.
    def _auth_token(self) -> str:
        return self._auth()

    def _headers_for(self, payload: Optional[dict]) -> List[str]:
        return self._extra_headers()

    def _http(self, method: str, path: str, payload: Optional[dict], cancel_event) -> Tuple[int, str]:
        """One request, status-checked. Callers wrap it in :meth:`_with_retry` when appropriate."""
        status, body = self.transport.request(
            method, self._url(path), self._auth_token(), extra_headers=self._headers_for(payload),
            body=payload, cancel_event=cancel_event,
        )
        raise_for_status(status, body, self.name)
        return status, body

    def _http_stream(self, path: str, payload: dict, cancel_event) -> Iterator[str]:
        return self.transport.stream_request(
            "POST", self._url(path), self._auth_token(), extra_headers=self._headers_for(payload),
            body=payload, cancel_event=cancel_event,
        )

    # ── retry policy ─────────────────────────────────────────────────────────────
    def _with_retry(self, fn: Callable[[], ChatResult], cancel_event, emitted=None) -> ChatResult:
        """Retry transient failures with capped, jittered backoff; never after tokens were printed.

        A connection failure to a *local* server is not transient (nothing is listening), so it fails
        at once with a hint about starting the server instead of burning the retry budget.
        """
        from .registry import is_local_url

        attempt = 0
        while True:
            attempt += 1
            try:
                return fn()
            except NetworkError as exc:
                if is_local_url(self.spec.base_url):
                    from .registry import any_hosted_configured

                    start = f"{self.name} is not running at {self.spec.base_url}: start it (`ollama serve`; install from https://ollama.com)"
                    other = ("or pick a configured hosted provider with /model or --provider"
                             if any_hosted_configured() else
                             "or set up a hosted provider: `scoot auth set openai` · `scoot auth set anthropic`")
                    exc.hint = f"{start}, {other}"
                    raise
                if attempt >= _RETRY_ATTEMPTS or (emitted is not None and emitted[0]):
                    raise
                self._backoff(attempt, cancel_event)
            except RETRIABLE:
                if attempt >= _RETRY_ATTEMPTS or (emitted is not None and emitted[0]):
                    raise
                self._backoff(attempt, cancel_event)

    @staticmethod
    def _backoff(attempt: int, cancel_event) -> None:
        delay = min(_RETRY_CAP, _RETRY_BASE * (2 ** (attempt - 1)))
        delay += random.uniform(0, delay * 0.25)  # jitter
        end = time.time() + delay
        while time.time() < end:
            if cancel_event is not None and cancel_event.is_set():
                raise Interrupted("cancelled during backoff")
            time.sleep(0.05)


def iter_sse_json(lines: Iterator[str], status_holder: dict, leftovers: List[str]) -> Iterator[dict]:
    """Walk raw SSE lines: yield each ``data:`` JSON object, record the status sentinel, collect
    anything that is not SSE (an error body) into ``leftovers``."""
    for line in lines:
        if line.startswith(STATUS_LINE_PREFIX):
            try:
                status_holder["status"] = int(line.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                pass
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("event:") or stripped.startswith(":"):
            continue
        if stripped.startswith("data:"):
            status_holder["saw_data"] = True
            data = stripped[len("data:"):].strip()
            if data == "[DONE]":
                continue
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                continue
        else:
            leftovers.append(line)


def finish_stream(status_holder: dict, leftovers: List[str], provider: str) -> None:
    """After a stream ends: raise the typed error for a non-200 status or a plain error body."""
    status = status_holder.get("status", 200)
    if status != 200 or (not status_holder.get("saw_data") and leftovers):
        body = "".join(leftovers).strip()
        raise_for_status(status if status != 200 else 400, body, provider)
        if body:
            raise ApiError(body[:200], status=status)
