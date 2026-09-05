"""/compact — summarize the conversation, clear it, and continue from the summary."""

from __future__ import annotations

from ..activity import activity
from ..context import compact, estimate_context_tokens, estimate_messages_tokens
from ..errors import ScootError, Interrupted
from ..rendering import color, eprint
from . import register
from .base import SlashCommand


def _run(session, args: str):
    if not session.messages:
        print(color("nothing to compact.", "gray"))
        return
    before = estimate_context_tokens(session)
    try:
        with activity("compacting context…") as cancel:
            summary = compact(session, cancel)
    except Interrupted:
        print(color("⏹ interrupted", "yellow"))
        return
    except ScootError as exc:
        eprint(color(f"compact failed: {exc}", "red"))
        return
    if not summary:
        print(color("nothing to compact.", "gray"))
        return
    after = estimate_messages_tokens(session.messages)
    note = f"context compacted: ~{before} → ~{after} prompt tokens. Continuing from summary."
    redraw = getattr(session, "redraw_home", None)
    if callable(redraw):
        redraw(note)
    else:
        print(color(note, "gray"))


register(SlashCommand("compact", "summarize & shrink the conversation", _run))

