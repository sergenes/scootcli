"""/c — copy the most recent assistant answer to the system clipboard (PLAN §3 / M21).

The same action is available in the input dock via Ctrl-S. ⌘C can't be used: terminal emulators keep
the ⌘ (Super) modifier for themselves and never forward it to the program.
"""

from __future__ import annotations

from ..clipboard import copy_session_output
from ..rendering import color, eprint
from . import register
from .base import SlashCommand


def _run(session, args: str):
    ok, message = copy_session_output(session)
    (print if ok else eprint)(color(message, "gray" if ok else "yellow"))


register(SlashCommand("c", "copy the last answer to the clipboard (also Ctrl-S)", _run))
# Discoverable alias for people who reach for the long form.
register(SlashCommand("copy", "copy the last answer to the clipboard (alias of /c)", _run))

