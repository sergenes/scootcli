"""Provider registry and the pool the application holds.

Adding a provider is one :func:`register` call with a :class:`ProviderSpec`; the wire adapters are
selected by ``spec.wire``. Base URLs can be overridden per provider with ``SCOOT_<NAME>_BASE_URL``.
"""

from __future__ import annotations

import os
import socket
from dataclasses import replace
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from ..errors import ConfigError, ScootError
from .base import BaseProvider, ChatResult, ModelInfo, ProviderSpec, qualify, split_model_id

_SPECS: Dict[str, ProviderSpec] = {}
_WIRES: Dict[str, type] = {}


def register(spec: ProviderSpec) -> None:
    _SPECS[spec.name] = spec


def get(name: str) -> Optional[ProviderSpec]:
    load_builtins()
    return _SPECS.get(name)


def names() -> List[str]:
    load_builtins()
    return list(_SPECS)


def all_specs() -> List[ProviderSpec]:
    load_builtins()
    return list(_SPECS.values())


_loaded = False


def load_builtins() -> None:
    """Register the built-in providers and wire adapters (idempotent)."""
    global _loaded
    if _loaded:
        return
    from .anthropic import AnthropicProvider
    from .openai_chat import OpenAIChatProvider
    from .openai_responses import OpenAIResponsesProvider

    _WIRES["openai_responses"] = OpenAIResponsesProvider
    _WIRES["openai_chat"] = OpenAIChatProvider
    _WIRES["anthropic"] = AnthropicProvider
    register(ProviderSpec(
        name="openai",
        base_url="https://api.openai.com/v1",
        wire="openai_responses",
        key_env=("OPENAI_API_KEY",),
        key_required=True,
        preferred_models=("gpt-5.3-codex", "gpt-5.5", "gpt-5.4", "gpt-5", "gpt-4.1"),
        vision_models=("gpt-5.3-codex", "gpt-5", "gpt-4.1", "gpt-4o"),
        capabilities=frozenset({"tools", "streaming", "vision", "reasoning"}),
    ))
    register(ProviderSpec(
        name="ollama",
        base_url="http://localhost:11434/v1",
        wire="openai_responses",
        key_env=("OLLAMA_API_KEY",),
        key_required=False,
        preferred_models=("llama3.2", "qwen3", "llama3.1", "mistral"),
        vision_models=("qwen2.5vl", "llama3.2-vision", "llava", "gemma3", "minicpm-v"),
        capabilities=frozenset({"tools", "streaming", "vision", "temperature"}),
    ))
    register(ProviderSpec(
        name="anthropic",
        base_url="https://api.anthropic.com/v1",
        wire="anthropic",
        key_env=("ANTHROPIC_API_KEY",),
        key_required=True,
        preferred_models=("claude-opus-5", "claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5"),
        vision_models=("claude",),
        capabilities=frozenset({"tools", "streaming", "vision", "reasoning"}),
    ))
    _loaded = True


def base_url_for(spec: ProviderSpec) -> str:
    override = os.environ.get(f"SCOOT_{spec.name.upper()}_BASE_URL", "").strip()
    return override or spec.base_url


def is_local_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or host.endswith(".localhost")


def reachable(url: str, timeout: float = 0.3) -> bool:
    """One TCP connect to the URL's host and port. Cheap enough to run at startup for a local server."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


SETUP_HELP = (
    "No model provider is ready yet. Set one up:\n"
    "  scoot auth set openai        hosted, needs an OpenAI API key\n"
    "  scoot auth set anthropic     hosted, needs an Anthropic API key\n"
    "  ollama pull llama3.2         local and free (install from https://ollama.com), then run scoot again"
)


SETUP_HINT = ("set up a provider: `scoot auth set openai` or `scoot auth set anthropic` (hosted), "
              "or `ollama pull llama3.2` after installing https://ollama.com (local)")


def any_hosted_configured() -> bool:
    from ..auth import is_configured

    return any(s.key_required and is_configured(s) for s in all_specs())


def readiness(config, model: Optional[str] = None) -> Tuple[bool, str]:
    """Whether the provider that will serve ``model`` (or the default one) can take a request now.

    Hosted providers are ready when a key is present (no network call). A keyless local provider is
    ready when its server answers a TCP connect; when nothing is configured at all, the message lists
    every way to get started.
    """
    from ..auth import is_configured

    head, _ = split_model_id(model) if model else (None, "")
    name = head if head and get(head) is not None else default_provider_name(config)
    spec = get(name)
    if spec is None:
        return False, SETUP_HELP
    if spec.key_required:
        if is_configured(spec):
            return True, ""
        return False, SETUP_HELP
    url = base_url_for(spec)
    if reachable(url):
        return True, ""
    if any_hosted_configured():
        return False, f"{name} is not running at {url} (start it with `ollama serve`, or pick another provider with --provider)"
    return False, SETUP_HELP


def make_provider(name: str, config, transport=None) -> BaseProvider:
    """Instantiate the adapter for ``name`` with any base-URL override applied."""
    spec = get(name)
    if spec is None:
        raise ConfigError(f"unknown provider '{name}'", hint=f"known providers: {', '.join(names())}")
    cls = _WIRES.get(spec.wire)
    if cls is None:
        raise ConfigError(f"provider '{name}' uses unknown wire '{spec.wire}'")
    return cls(replace(spec, base_url=base_url_for(spec)), config, transport=transport)


def default_provider_name(config) -> str:
    """``SCOOT_PROVIDER`` if set, else the first provider with a key configured, else ``ollama``."""
    from ..auth import is_configured

    wanted = (getattr(config, "provider", "") or "").strip().lower()
    if wanted:
        if get(wanted) is None:
            raise ConfigError(f"unknown provider '{wanted}'", hint=f"known providers: {', '.join(names())}")
        return wanted
    for spec in all_specs():
        if spec.key_required and is_configured(spec):
            return spec.name
    return "ollama"


def fallback_model(config) -> str:
    """The qualified model ``auto`` resolves to before any live model list is known."""
    name = default_provider_name(config)
    spec = get(name)
    head = spec.preferred_models[0] if spec and spec.preferred_models else "default"
    return qualify(name, head)


class ProviderPool:
    """Holds one adapter per provider, built lazily, and dispatches by the ``provider/`` prefix.

    Implements the same ``chat`` / ``chat_stream`` / ``list_models`` surface as a single provider, so
    consumers never care whether one or many providers are in play. This is also where a routing
    provider would plug in later: a router is just another entry the pool can dispatch to.
    """

    def __init__(self, config, transport=None):
        self.config = config
        self._transport = transport
        self._providers: Dict[str, BaseProvider] = {}
        self.list_errors: Dict[str, str] = {}

    @property
    def default_name(self) -> str:
        return default_provider_name(self.config)

    @property
    def spec(self) -> ProviderSpec:
        return self.provider(self.default_name).spec

    def provider(self, name: Optional[str] = None) -> BaseProvider:
        name = name or self.default_name
        if name not in self._providers:
            self._providers[name] = make_provider(name, self.config, transport=self._transport)
        return self._providers[name]

    def for_model(self, model: Optional[str]) -> "tuple[BaseProvider, str]":
        """Resolve ``provider/model`` (or a bare name) to ``(adapter, bare_model)``."""
        head, bare = split_model_id(model)
        if head and get(head) is None:
            raise ConfigError(f"unknown provider '{head}' in model '{model}'",
                              hint=f"known providers: {', '.join(names())}")
        return self.provider(head), bare

    def chat(self, messages, model=None, **kwargs) -> ChatResult:
        p, bare = self.for_model(model or self.config.resolve_model())
        return p.chat(messages, model=bare, **kwargs)

    def chat_stream(self, messages, model=None, **kwargs) -> ChatResult:
        p, bare = self.for_model(model or self.config.resolve_model())
        return p.chat_stream(messages, model=bare, **kwargs)

    def list_models(self, provider: Optional[str] = None, cancel_event=None) -> List[ModelInfo]:
        """Models from one provider, or from every configured provider (failures are recorded, not raised)."""
        from ..auth import is_configured

        if provider:
            return self.provider(provider).list_models(cancel_event=cancel_event)
        out: List[ModelInfo] = []
        self.list_errors = {}
        for spec in all_specs():
            if not is_configured(spec):
                continue
            url = base_url_for(spec)
            if not spec.key_required and is_local_url(url) and not reachable(url):
                self.list_errors[spec.name] = f"not running at {url} (start it with `ollama serve`)"
                continue
            try:
                out.extend(self.provider(spec.name).list_models(cancel_event=cancel_event))
            except ScootError as exc:
                self.list_errors[spec.name] = str(exc) + (f" ({exc.hint})" if getattr(exc, "hint", "") else "")
        return out
