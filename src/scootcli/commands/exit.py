"""/exit — quit the REPL."""

from __future__ import annotations

from . import QUIT, register
from .base import SlashCommand


def _run(session, args: str):
    return QUIT


register(SlashCommand("exit", "quit scoot", _run))

