"""/resume [<id>] — reload a saved conversation (defaults to the latest for this directory)."""

from __future__ import annotations

from ..rendering import color, eprint
from . import register
from .base import SlashCommand


def _run(session, args: str):
    from .. import sessions

    ident = args.strip()
    if ident:
        record = sessions.load(ident)
        if record is None:
            eprint(color(f"no session with id '{ident}' (try /sessions).", "yellow"))
            return
    else:
        record = sessions.latest_for_root(str(session.config.root))
        if record is None:
            print(color("no saved sessions for this directory.", "gray"))
            return

    session.apply_record(record)
    print(color(f"resumed session {record.id} · {len(session.messages)} messages "
                f"· model {session.active_model}.", "gray"))


register(SlashCommand("resume", "resume a saved session", _run, usage="[id]"))

