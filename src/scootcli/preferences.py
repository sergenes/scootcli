"""Persisted user preferences (the chosen model, the mascot toggle), surviving across launches.

Unlike sessions (per-directory conversations), this is a single *global* preference file at
``~/.config/scoot/preferences.json``. Writing is best-effort — a failure never breaks the CLI.
``SCOOT_CONFIG_DIR`` overrides the location (used by tests).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .config import SCOOT_CONFIG_DIR


def _config_dir() -> Path:
    override = os.environ.get("SCOOT_CONFIG_DIR")
    return Path(override).expanduser() if override else SCOOT_CONFIG_DIR


def _prefs_file() -> Path:
    return _config_dir() / "preferences.json"


def load_preferences() -> dict:
    try:
        data = json.loads(_prefs_file().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _write(prefs: dict) -> None:
    directory = _config_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        path = _prefs_file()
        tmp = directory / ".preferences.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(prefs, fh)
        os.replace(tmp, path)  # atomic
    except OSError:
        pass  # preferences are best-effort


def save_preference(key: str, value) -> None:
    prefs = load_preferences()
    if value is None:
        prefs.pop(key, None)
    else:
        prefs[key] = value
    _write(prefs)


# ── model preference ────────────────────────────────────────────────────────────
def get_model() -> Optional[str]:
    model = load_preferences().get("model")
    return model or None


def set_model(name: str) -> None:
    """Persist the chosen model (including ``auto``) so the next launch reuses it."""
    save_preference("model", name)


def clear_model() -> None:
    save_preference("model", None)


# ── mascot preference ───────────────────────────────────────────────────────────
def get_logo() -> Optional[bool]:
    """``True``/``False`` if the user chose via ``/logo on|off``; ``None`` when unset (default on)."""
    value = load_preferences().get("logo")
    return value if isinstance(value, bool) else None


def set_logo(on: Optional[bool]) -> None:
    """Persist the mascot toggle (``None`` forgets it → back to the default)."""
    save_preference("logo", on)

