"""Provider registry and the pool the application holds.

Adding a provider is one :func:`register` call with a :class:`ProviderSpec`; the wire adapters are
selected by ``spec.wire``. Base URLs can be overridden per provider with ``SCOOT_<NAME>_BASE_URL``.
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Dict, List, Optional

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
    from .openai_chat import OpenAIChatProvider
    from .openai_responses import OpenAIResponsesProvider

    _WIRES["openai_responses"] = OpenAIResponsesProvider
    _WIRES["openai_chat"] = OpenAIChatProvider
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
    _loaded = True


def base_url_for(spec: ProviderSpec) -> str:
    override = os.environ.get(f"SCOOT_{spec.name.upper()}_BASE_URL", "").strip()
    return override or spec.base_url


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
            try:
                out.extend(self.provider(spec.name).list_models(cancel_event=cancel_event))
            except ScootError as exc:
                self.list_errors[spec.name] = str(exc)
        return out
