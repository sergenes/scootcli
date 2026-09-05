"""/help — list available slash-commands."""

from __future__ import annotations

from ..rendering import color
from . import all_commands, register
from .base import SlashCommand


def _run(session, args: str):
    if getattr(getattr(session, "config", None), "logo", True):
        from ..logo import mascot

        for row in mascot("idle", "micro"):
            print(color(row, "cyan"))
    print(color("slash-commands:", "bold"))
    for name, cmd in all_commands().items():
        usage = f" {cmd.usage}" if cmd.usage else ""
        print(f"  {color('/' + name, 'cyan')}{usage}  {color('— ' + cmd.summary, 'gray')}")
    print(color("  (or just type a prompt · esc to interrupt · ctrl-c to quit)", "gray"))
    print(color("  keys: ↑/↓ recall history · ↓ on a draft clears it (↑ brings it back)", "gray"))
    print(color("  feed detail: /verbosity full|compact|quiet (collapse tool calls / hide reasoning)",
                "gray"))
    print(color("  copy the last reply with /c or Ctrl-S (⌘C can't be captured by a terminal app)",
                "gray"))


register(SlashCommand("help", "show this help", _run))

