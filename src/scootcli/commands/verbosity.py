"""/verbosity — regulate how much of a turn's "thinking" shows in the feed.

Levels (see :class:`scootcli.repl.ReplUI`):
  * ``full``    — reasoning narration shown; every tool-in-use line kept in the feed (a run log).
  * ``compact`` — reasoning narration shown; tool-in-use is one transient line above the spinner,
                  removed once the tool finishes (nothing accumulates).
  * ``quiet``   — reasoning narration suppressed; tools render like ``compact``.
"""

from __future__ import annotations

from ..rendering import color, eprint
from . import register
from .base import SlashCommand

_LEVELS = ("full", "compact", "quiet")
_ALIASES = {"default": "full", "collapse": "compact", "collapsed": "compact",
            "off": "quiet", "min": "quiet", "minimal": "quiet"}
_HELP = {
    "full": "show reasoning + keep every tool call in the feed",
    "compact": "show reasoning; collapse tool calls to one transient line above Thinking…",
    "quiet": "hide reasoning; collapse tool calls",
}


def _run(session, args: str):
    ui = getattr(session, "ui", None)
    if ui is None:
        eprint(color("verbosity is not available in this context.", "yellow"))
        return

    want = (args or "").strip().lower()
    if not want:
        cur = getattr(ui, "verbosity", "full")
        print(color(f"feed verbosity: {cur}", "bold"))
        for lvl in _LEVELS:
            mark = color("●", "cyan") if lvl == cur else color("○", "gray")
            print(f"  {mark} {color(lvl, 'bold')}  {color('— ' + _HELP[lvl], 'gray')}")
        print(color("  usage: /verbosity full|compact|quiet", "gray"))
        return

    level = _ALIASES.get(want, want)
    if level not in _LEVELS:
        eprint(color(f"unknown level '{want}'. use: full | compact | quiet", "yellow"))
        return
    ui.verbosity = level
    try:
        session.config.verbosity = level  # keep config in sync (surfaced in /status)
    except Exception:
        pass
    print(color(f"feed verbosity → {level}  ({_HELP[level]})", "gray"))


register(SlashCommand(
    "verbosity", "regulate feed detail (full|compact|quiet)", _run,
    usage="[full|compact|quiet]",
))
