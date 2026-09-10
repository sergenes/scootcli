"""Session persistence: auto-save each turn, resume with ``--continue`` / ``--resume`` (PLAN §14).

Stdlib-only and offline-friendly. Each interactive session is auto-saved after every turn to
``~/.local/state/scoot/sessions/<id>.json`` (owner-only, ``0600``), keyed by workspace root so
``--continue`` can find the most recent conversation for the directory you're in. Token-like strings
are redacted before writing (defense in depth); only the most recent :data:`SESSION_RETENTION`
sessions are kept.

The state directory can be overridden with ``SCOOT_STATE_DIR`` (used by tests).
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .config import SESSION_RETENTION, STATE_DIR
from .rendering import redact_value

_VERSION = 1
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")  # a basename, never a path


def valid_id(session_id) -> bool:
    """Session ids are file basenames: letters, digits, ``-``, ``_``; no separators, dots, or paths.

    Every persistence entry point checks this, so ``/forget ../x`` or a crafted record id cannot
    reach a file outside the sessions directory.
    """
    return isinstance(session_id, str) and bool(_ID_RE.match(session_id))


def _sessions_dir() -> Path:
    override = os.environ.get("SCOOT_STATE_DIR")
    base = Path(override).expanduser() if override else STATE_DIR
    return base / "sessions"


def new_session_id() -> str:
    """A sortable, unique id: ``YYYYmmdd-HHMMSS-<rand>``."""
    return time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()


def complete_tool_results(messages: List[dict]) -> List[dict]:
    """Return ``messages`` with a result present for every assistant tool call.

    A turn interrupted mid-batch used to leave calls without results; providers reject such a
    history on the next request. Missing results are filled with a note, in place, right after the
    results that do exist, so the conversation replays.
    """
    out: List[dict] = []
    i = 0
    while i < len(messages):
        m = messages[i]
        out.append(m)
        i += 1
        calls = m.get("tool_calls") if isinstance(m, dict) and m.get("role") == "assistant" else None
        if not calls:
            continue
        answered = set()
        while i < len(messages) and isinstance(messages[i], dict) and messages[i].get("role") == "tool":
            answered.add(messages[i].get("tool_call_id"))
            out.append(messages[i])
            i += 1
        for tc in calls:
            tc_id = tc.get("id", "") if isinstance(tc, dict) else ""
            if tc_id not in answered:
                out.append({"role": "tool", "tool_call_id": tc_id,
                            "content": "no result recorded: the turn was interrupted before this call finished"})
    return out


def _redact_messages(messages: List[dict]) -> List[dict]:
    """Copy messages with token-like substrings masked everywhere text is stored: ``content`` (string
    or parts), tool-call arguments, and the text and tool-input copies inside ``provider_items``.
    Encrypted or signed replay fields are kept as received (see ``rendering.redact_value``)."""
    return [redact_value(msg) if isinstance(msg, dict) else msg for msg in messages]


@dataclass
class SessionRecord:
    """A persisted conversation + the session settings needed to resume it."""

    id: str
    root: str
    created: float
    updated: float
    model: str
    active_model: str
    approval_mode: str
    total_prompt: int = 0
    total_completion: int = 0
    messages: List[dict] = field(default_factory=list)

    @property
    def turns(self) -> int:
        return sum(1 for m in self.messages if m.get("role") == "user")

    def first_prompt(self) -> str:
        for m in self.messages:
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                text = m["content"].strip().replace("\n", " ")
                return (text[:57] + "…") if len(text) > 58 else text
        return "(no prompt yet)"

    def to_dict(self) -> dict:
        return {
            "version": _VERSION,
            "id": self.id,
            "root": self.root,
            "created": self.created,
            "updated": self.updated,
            "model": self.model,
            "active_model": self.active_model,
            "approval_mode": self.approval_mode,
            "total_prompt": self.total_prompt,
            "total_completion": self.total_completion,
            "messages": self.messages,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionRecord":
        """Build a record from saved JSON, checking the shape; raises ``ValueError`` on a bad one."""
        if not isinstance(data, dict) or not valid_id(data.get("id")):
            raise ValueError("session record without a valid id")
        if int(data.get("version", _VERSION) or 0) > _VERSION:
            raise ValueError("session record from a newer scoot")
        messages = data.get("messages", [])
        if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
            raise ValueError("session record with malformed messages")

        def num(key, default=0.0):
            v = data.get(key, default)
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default

        return cls(
            id=data["id"],
            root=str(data.get("root", "") or ""),
            created=num("created"),
            updated=num("updated"),
            model=str(data.get("model", "auto") or "auto"),
            active_model=str(data.get("active_model", "") or ""),
            approval_mode=str(data.get("approval_mode", "always") or "always"),
            total_prompt=int(num("total_prompt", 0)),
            total_completion=int(num("total_completion", 0)),
            messages=messages,
        )


def save(record: SessionRecord) -> Optional[Path]:
    """Persist ``record`` (owner-only), prune old sessions, and return the path (best-effort)."""
    if not valid_id(record.id):
        return None
    record.updated = time.time()
    payload = record.to_dict()
    payload["messages"] = _redact_messages(record.messages)

    directory = _sessions_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory.parent, 0o700)
            os.chmod(directory, 0o700)
        except OSError:
            pass
        from .store import write_json_atomic

        path = write_json_atomic(directory / f"{record.id}.json", payload)
        _prune()
        return path
    except OSError:
        return None  # persistence must never break the REPL


def load(session_id: str) -> Optional[SessionRecord]:
    if not valid_id(session_id):
        return None
    try:
        data = json.loads((_sessions_dir() / f"{session_id}.json").read_text())
        return SessionRecord.from_dict(data)
    except (OSError, ValueError, TypeError):  # JSONDecodeError is a ValueError
        return None


def list_sessions(root: Optional[str] = None) -> List[SessionRecord]:
    """Return saved sessions (optionally filtered to ``root``), newest first."""
    directory = _sessions_dir()
    records: List[SessionRecord] = []
    try:
        files = list(directory.glob("*.json"))
    except OSError:
        return []
    for f in files:
        try:
            rec = SessionRecord.from_dict(json.loads(f.read_text()))
        except (OSError, ValueError, TypeError):
            continue  # one malformed record must not stop the others from loading
        if root is None or rec.root == str(root):
            records.append(rec)
    records.sort(key=lambda r: r.updated, reverse=True)
    return records


def latest_for_root(root: str) -> Optional[SessionRecord]:
    sessions = list_sessions(str(root))
    return sessions[0] if sessions else None


def delete(session_id: str) -> bool:
    if not valid_id(session_id):
        return False
    try:
        (_sessions_dir() / f"{session_id}.json").unlink()
        return True
    except (FileNotFoundError, OSError):
        return False


def delete_all(root: Optional[str] = None) -> int:
    count = 0
    for rec in list_sessions(root):
        if delete(rec.id):
            count += 1
    return count


def _prune() -> None:
    """Keep only the most recent :data:`SESSION_RETENTION` sessions (across all roots)."""
    everything = list_sessions()
    for rec in everything[SESSION_RETENTION:]:
        delete(rec.id)

