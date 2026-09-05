"""/reset — clear the conversation history and context (keeps any loaded project context)."""

from __future__ import annotations

from ..rendering import color
from . import register
from .base import SlashCommand


def _run(session, args: str):
    session.reset()
    redraw = getattr(session, "redraw_home", None)
    if callable(redraw):
        redraw("fresh session — conversation + context cleared.")
    else:
        print(color("conversation + context cleared — fresh session.", "gray"))


register(SlashCommand("reset", "clear the conversation + context", _run))

