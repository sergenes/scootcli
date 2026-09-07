"""Persisted user preferences (the chosen model, the mascot toggle), surviving across launches.

One file at ``~/.config/scoot/preferences.json`` (``SCOOT_CONFIG_DIR`` overrides the location, used by
tests). The model preference has two layers: ``models`` maps a workspace root to the model chosen
there (``/model`` or ``--model`` in that folder), and ``model`` is the choice for every folder without
one (``/model X everywhere``). Writing is best-effort: a failure never breaks the CLI.
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
FOLDER = "folder"        # the model saved for this workspace root
EVERYWHERE = "everywhere"  # the model saved for every folder without its own


def _root_key(root) -> str:
    return str(Path(root).expanduser().resolve())


def _folder_models(prefs: dict) -> dict:
    models = prefs.get("models")
    return models if isinstance(models, dict) else {}


def saved_models(root=None):
    """``(folder_model, global_model)``: what is saved for ``root`` (``None`` without a root) and for
    everywhere; either may be ``None``."""
    prefs = load_preferences()
    folder = _folder_models(prefs).get(_root_key(root)) if root is not None else None
    return (folder or None), (prefs.get("model") or None)


def get_model(root=None) -> Optional[str]:
    """The saved model that applies in ``root``: the folder's own choice, else the global one."""
    folder, everywhere = saved_models(root)
    return folder or everywhere


def model_source(root=None) -> Optional[str]:
    """Which layer :func:`get_model` answered from: ``"folder"``, ``"everywhere"``, or ``None``."""
    folder, everywhere = saved_models(root)
    return FOLDER if folder else (EVERYWHERE if everywhere else None)


def set_model(name: str, root=None) -> None:
    """Persist the chosen model (including ``auto`` / ``default``) so the next launch reuses it: for
    the folder ``root`` when given, otherwise for every folder (which also drops the folder's own
    entry when ``root`` is passed as ``everywhere=True`` via :func:`set_model_everywhere`)."""
    if root is None:
        save_preference("model", name)
        return
    prefs = load_preferences()
    models = dict(_folder_models(prefs))
    models[_root_key(root)] = name
    prefs["models"] = models
    _write(prefs)


def set_model_everywhere(name: str, root=None) -> None:
    """Make ``name`` the model for every folder, and forget ``root``'s own choice so it applies there too."""
    prefs = load_preferences()
    prefs["model"] = name
    if root is not None:
        models = dict(_folder_models(prefs))
        models.pop(_root_key(root), None)
        prefs["models"] = models
    _write(prefs)


def clear_model(root=None) -> None:
    """Forget the saved model: the folder's own entry when ``root`` is given, else the global one."""
    if root is None:
        save_preference("model", None)
        return
    prefs = load_preferences()
    models = dict(_folder_models(prefs))
    if models.pop(_root_key(root), None) is not None:
        prefs["models"] = models
        _write(prefs)


# ── mascot preference ───────────────────────────────────────────────────────────
def get_logo() -> Optional[bool]:
    """``True``/``False`` if the user chose via ``/logo on|off``; ``None`` when unset (default on)."""
    value = load_preferences().get("logo")
    return value if isinstance(value, bool) else None


def set_logo(on: Optional[bool]) -> None:
    """Persist the mascot toggle (``None`` forgets it → back to the default)."""
    save_preference("logo", on)

