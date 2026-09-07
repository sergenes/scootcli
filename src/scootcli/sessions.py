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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .config import SESSION_RETENTION, STATE_DIR
from .rendering import redact

_VERSION = 1


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
    """Copy messages, masking token-like substrings in string content fields."""
    cleaned: List[dict] = []
    for msg in messages:
        m = dict(msg)
        content = m.get("content")
        if isinstance(content, str):
            m["content"] = redact(content)
        cleaned.append(m)
    return cleaned


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
        return cls(
            id=data["id"],
            root=data.get("root", ""),
            created=data.get("created", 0.0),
            updated=data.get("updated", 0.0),
            model=data.get("model", "auto"),
            active_model=data.get("active_model", ""),
            approval_mode=data.get("approval_mode", "always"),
            total_prompt=int(data.get("total_prompt", 0) or 0),
            total_completion=int(data.get("total_completion", 0) or 0),
            messages=data.get("messages", []),
        )


def save(record: SessionRecord) -> Optional[Path]:
    """Persist ``record`` (owner-only), prune old sessions, and return the path (best-effort)."""
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
        path = directory / f"{record.id}.json"
        tmp = directory / f".{record.id}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)  # atomic
        _prune()
        return path
    except OSError:
        return None  # persistence must never break the REPL


def load(session_id: str) -> Optional[SessionRecord]:
    try:
        data = json.loads((_sessions_dir() / f"{session_id}.json").read_text())
        return SessionRecord.from_dict(data)
    except (OSError, json.JSONDecodeError, KeyError):
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
        except (OSError, json.JSONDecodeError, KeyError):
            continue
        if root is None or rec.root == str(root):
            records.append(rec)
    records.sort(key=lambda r: r.updated, reverse=True)
    return records


def latest_for_root(root: str) -> Optional[SessionRecord]:
    sessions = list_sessions(str(root))
    return sessions[0] if sessions else None


def delete(session_id: str) -> bool:
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

