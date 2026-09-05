"""SlashCommand interface for the drop-in command registry (PLAN §6, §2 design principles).

Adding a new slash-command = drop a module in this package that builds a :class:`SlashCommand` and
calls :func:`scootcli.commands.register`. No core edits required.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:  # avoid a circular import at runtime
    from ..repl import ReplSession

# Control-flow signal a command may return to the REPL.
QUIT = "quit"


@dataclass
class SlashCommand:
    """A REPL slash-command.

    ``handler(session, args)`` runs the command. It may return :data:`QUIT` to end the session,
    otherwise ``None``. ``args`` is the raw text after the command name.
    """

    name: str
    summary: str
    handler: "Callable[[ReplSession, str], Optional[str]]"
    usage: str = ""

