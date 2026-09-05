"""Where API keys come from, per provider.

Order: the provider's environment variables (the vendor's standard names, ``OPENAI_API_KEY`` and so
on, which a ``.env`` may have supplied), then scoot's own saved credentials (``scoot auth set``).
Providers that need no key (a local Ollama) are always considered configured.
"""

from __future__ import annotations

import os
from typing import List, Optional

from . import credentials
from .providers.base import ProviderSpec


def api_key_for(spec: ProviderSpec) -> Optional[str]:
    for env in spec.key_env:
        value = (os.environ.get(env) or "").strip()
        if value:
            return value
    return credentials.load_key(spec.name)


def key_source(spec: ProviderSpec) -> Optional[str]:
    """``"env:OPENAI_API_KEY"``, ``"saved"``, or ``None`` when no key is available."""
    for env in spec.key_env:
        if (os.environ.get(env) or "").strip():
            return f"env:{env}"
    if credentials.load_key(spec.name):
        return "saved"
    return None


def is_configured(spec: ProviderSpec) -> bool:
    return (not spec.key_required) or api_key_for(spec) is not None


def status_rows(config) -> List[dict]:
    """One row per registered provider: name, key source, whether a key is required, default flag."""
    from .providers import registry

    default = registry.default_provider_name(config)
    rows = []
    for spec in registry.all_specs():
        rows.append({
            "name": spec.name,
            "source": key_source(spec),
            "required": spec.key_required,
            "configured": is_configured(spec),
            "default": spec.name == default,
            "base_url": registry.base_url_for(spec),
        })
    return rows


def missing_key_hint(spec: ProviderSpec) -> str:
    env = spec.key_env[0] if spec.key_env else f"{spec.name.upper()}_API_KEY"
    return f"set {env} (in the environment or ~/.config/scoot/.env) or run `scoot auth set {spec.name}`"
