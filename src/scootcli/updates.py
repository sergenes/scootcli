"""Opt-in check for a newer release on PyPI. Never runs unless asked (``--check-update`` or
``SCOOT_UPDATE_CHECK=1``), and never blocks: one request with a short timeout, failures are silent."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Optional

from . import __version__

PYPI_JSON = "https://pypi.org/pypi/scootcli/json"


def enabled() -> bool:
    return os.environ.get("SCOOT_UPDATE_CHECK", "").strip().lower() in ("1", "true", "yes", "on")


def _key(version: str):
    return tuple(int(x) for x in re.findall(r"\d+", version)[:3]) or (0,)


def is_newer(latest: Optional[str], current: str = __version__) -> bool:
    return bool(latest) and _key(latest) > _key(current)


def latest_version(timeout: float = 2.0, opener=None) -> Optional[str]:
    """The newest release on PyPI, or ``None`` when it cannot be determined."""
    try:
        req = urllib.request.Request(PYPI_JSON, headers={"User-Agent": f"scoot/{__version__}"})
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as resp:
            data = json.load(resp)
        return str(data["info"]["version"])
    except Exception:
        return None


def check() -> "tuple[str, Optional[str]]":
    """``(current, latest_or_None)``."""
    return __version__, latest_version()


def upgrade_hint() -> str:
    return "pipx upgrade scootcli  (or pip install --upgrade scootcli, or rerun the curl installer)"
