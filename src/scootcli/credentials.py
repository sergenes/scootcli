"""Secure-at-rest storage for provider API keys.

Stdlib-only and offline-friendly (no OS-keychain dependency, so the zipapp stays self-contained).
Keys live in ``~/.config/scoot/credentials.json`` as ``{"keys": {"openai": "sk-..."}}`` with owner-only
permissions (directory ``0700``, file ``0600``). Keys are never echoed; ``rendering.redact()`` masks them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional

from . import config as _config


def _file() -> Path:
    """Where credentials are written: the unified config dir."""
    return _config.config_dir() / "credentials.json"


def _read_path() -> Path:
    """Where to read credentials from: the config dir, or the legacy ``~/.config/scoot`` when the
    config dir came from ``XDG_CONFIG_HOME`` (not an explicit ``SCOOT_CONFIG_DIR``) and has no file
    yet, so a user who set XDG keeps their pre-0.13.0 keys without re-authenticating."""
    primary = _file()
    if not primary.exists() and not os.environ.get("SCOOT_CONFIG_DIR"):
        legacy = _config.legacy_config_dir() / "credentials.json"
        if legacy != primary and legacy.exists():
            return legacy
    return primary


def _load() -> Dict[str, str]:
    try:
        data = json.loads(_read_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    keys = data.get("keys") if isinstance(data, dict) else None
    return {k: v for k, v in keys.items() if isinstance(v, str) and v} if isinstance(keys, dict) else {}


def _write(keys: Dict[str, str]) -> Path:
    from .store import write_json_atomic

    return write_json_atomic(_file(), {"keys": keys})


def load_key(provider: str) -> Optional[str]:
    """The saved API key for ``provider``, or ``None``."""
    return _load().get(provider) or None


def save_key(provider: str, key: str) -> Path:
    """Persist ``key`` for ``provider`` (owner-only) and return the file path."""
    keys = _load()
    keys[provider] = key.strip()
    return _write(keys)


def delete_key(provider: str) -> bool:
    """Forget the saved key for ``provider``. Return ``True`` if one was removed."""
    keys = _load()
    if provider not in keys:
        return False
    del keys[provider]
    _write(keys)
    return True


def saved_providers() -> list:
    return sorted(_load())


def credentials_path() -> Path:
    return _file()
