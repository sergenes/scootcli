"""/sessions — list saved sessions for this workspace root (resume with /resume <id>)."""

from __future__ import annotations

import time

from ..rendering import color
from . import register
from .base import SlashCommand


def _ago(ts: float) -> str:
    if not ts:
        return "?"
    secs = max(0, int(time.time() - ts))
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _run(session, args: str):
    from .. import sessions

    records = sessions.list_sessions(str(session.config.root))
    if not records:
        print(color("no saved sessions for this directory.", "gray"))
        return
    print(color("saved sessions (this directory):", "bold"))
    for rec in records:
        marker = color(" ← current", "green") if rec.id == session.id else ""
        print(f"  {color(rec.id, 'cyan')}  {color(_ago(rec.updated), 'gray'):<20} "
              f"{rec.turns} turns · {rec.active_model or rec.model}{marker}")
        print(f"      {color(rec.first_prompt(), 'gray')}")
    print(color("resume with: /resume <id>", "gray"))


register(SlashCommand("sessions", "list saved sessions", _run))

