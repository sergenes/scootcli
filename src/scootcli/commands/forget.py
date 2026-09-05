"""/forget [<id>|all] — delete saved session(s) for this workspace root."""

from __future__ import annotations

from ..rendering import color, eprint
from . import register
from .base import SlashCommand


def _run(session, args: str):
    from .. import sessions

    arg = args.strip()
    if arg == "all":
        n = sessions.delete_all(str(session.config.root))
        print(color(f"forgot {n} saved session(s) for this directory.", "gray"))
        return
    if not arg:
        eprint(color("usage: /forget <id>|all (see /sessions).", "yellow"))
        return
    if sessions.delete(arg):
        print(color(f"forgot session {arg}.", "gray"))
    else:
        eprint(color(f"no session with id '{arg}'.", "yellow"))


register(SlashCommand("forget", "delete saved session(s)", _run, usage="<id>|all"))

