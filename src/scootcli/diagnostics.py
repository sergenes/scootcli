"""The latest error of a session, kept outside the conversation (``/error``).

The status bar shows only a short label for a failed turn, and that label is gone at the next
prompt. This module keeps one small record per session, next to the saved sessions, so the failure
can still be read after compaction or a restart.

The record is deliberately poor in content. The stack is metadata only: the exception class, an HTTP
status or ``errno`` when there is one, and the file, line, and function of each frame. It never holds
local variables, argv, credentials, or request and response payloads, and it holds the message only
for scoot's own typed errors (the text already printed to the terminal, redacted and capped). An
unexpected exception's text is not stored at all, because nothing bounds what it may quote.
"""

from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path
from typing import Optional

from .errors import ScootError

VERSION = 1
MAX_BYTES = 16 * 1024   # the whole record on disk
_MAX_FRAMES = 30        # innermost frames kept
_MAX_MESSAGE = 600
_MAX_HINT = 300
_MAX_CAUSES = 8
_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def _where(filename: str) -> str:
    """A frame's file without anything personal: package-relative for scoot's own code, the bare
    file name for everything else (a full path would carry the home directory)."""
    path = os.path.abspath(filename or "")
    if path.startswith(_PACKAGE_DIR + os.sep):
        return "scootcli/" + os.path.relpath(path, _PACKAGE_DIR).replace(os.sep, "/")
    return os.path.basename(path) or "?"


def _chain(exc: BaseException) -> "list[BaseException]":
    """``exc`` and what it was raised from or while handling, outermost first, without cycles."""
    out, seen = [], set()
    while exc is not None and id(exc) not in seen and len(out) <= _MAX_CAUSES:
        seen.add(id(exc))
        out.append(exc)
        exc = exc.__cause__ or exc.__context__
    return out


def describe(exc: BaseException) -> dict:
    """Metadata about ``exc``: class, status/code/errno, cause classes, and stack locations."""
    chain = _chain(exc)
    frames = []
    for link in reversed(chain):  # the original failure first, so the innermost frames come last
        for fs in traceback.extract_tb(link.__traceback__):
            frames.append({"file": _where(fs.filename), "line": int(fs.lineno or 0), "func": str(fs.name)})
    errno = next((e.errno for e in chain if isinstance(e, OSError) and isinstance(e.errno, int)), None)
    status = getattr(exc, "status", 0)
    code = getattr(exc, "code", "")
    return {
        "kind": exc.__class__.__name__,
        "status": status if isinstance(status, int) and not isinstance(status, bool) else 0,
        "code": code[:80] if isinstance(code, str) else "",
        "errno": errno,
        "causes": [e.__class__.__name__ for e in chain[1:]],
        "frames": frames[-_MAX_FRAMES:],
    }


def build(exc: Optional[BaseException] = None, *, message: str = "", detail: Optional[dict] = None,
          model: str = "", version: str = "") -> dict:
    """One record. ``detail`` is a :func:`describe` result made where the exception was caught (the
    agent loop turns errors into outcomes); ``message`` is text already shown to the user."""
    from .rendering import redact

    info = dict(detail) if isinstance(detail, dict) else (describe(exc) if exc is not None else {})
    hint = ""
    if isinstance(exc, ScootError):
        message = message or str(exc)
        hint = getattr(exc, "hint", "") or ""
    elif exc is not None:
        message = ""  # an unexpected exception's text is never stored
    record = {
        "version": VERSION,
        "time": time.time(),
        "kind": str(info.get("kind") or "Error")[:80],
        "message": redact(str(message or ""))[:_MAX_MESSAGE],
        "hint": redact(str(hint))[:_MAX_HINT],
        "status": info.get("status") or 0,
        "code": info.get("code") or "",
        "errno": info.get("errno"),
        "causes": list(info.get("causes") or [])[:_MAX_CAUSES],
        "model": str(model or "")[:120],
        "scoot": str(version or ""),
        "frames": list(info.get("frames") or [])[-_MAX_FRAMES:],
    }
    while len(json.dumps(record)) > MAX_BYTES and record["frames"]:
        record["frames"] = record["frames"][len(record["frames"]) // 2 + 1:]
    return record


def _errors_dir() -> Path:
    from .sessions import _sessions_dir

    return _sessions_dir().parent / "errors"


def path_for(session_id: str) -> Path:
    return _errors_dir() / f"{session_id}.json"


def save(session_id: str, record: dict) -> bool:
    """Replace the session's record (atomic, owner-only). Best-effort: never raises."""
    from .sessions import valid_id
    from .store import write_json_atomic

    if not valid_id(session_id):
        return False
    try:
        write_json_atomic(path_for(session_id), record)
        return True
    except (OSError, TypeError, ValueError):
        return False


def load(session_id: str) -> Optional[dict]:
    from .sessions import valid_id

    if not valid_id(session_id):
        return None
    try:
        path = path_for(session_id)
        if path.stat().st_size > MAX_BYTES:
            return None
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def delete(session_id: str) -> bool:
    from .sessions import valid_id

    if not valid_id(session_id):
        return False
    try:
        path_for(session_id).unlink()
        return True
    except OSError:
        return False
