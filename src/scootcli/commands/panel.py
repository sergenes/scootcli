"""/panel — toggle the persistent bottom status bar on/off."""

from __future__ import annotations

from ..rendering import color, eprint
from . import register
from .base import SlashCommand


def _run(session, args: str):
    bar = getattr(session, "status_bar", None)
    if bar is None:
        eprint(color("status bar is not available in this context.", "yellow"))
        return
    on = bar.toggle()
    if on:
        from ..auth import account_label, auth_source
        from ..panel import build_status_text

        try:
            bar.render(build_status_text(session, account_label() or auth_source()))
        except Exception:
            pass
    print(color(f"panel {'on' if on else 'off'}.", "gray"))


register(SlashCommand("panel", "toggle the bottom status bar", _run))

