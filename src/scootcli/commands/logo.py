"""/logo — show the mascot, or turn it on/off (persisted across launches).

``off`` hides the launch banner art and the face in the status bar; the transcript label is unaffected
(see ``--no-emoji`` / ``SCOOT_EMOJI`` for that).
"""

from __future__ import annotations

from .. import logo, preferences
from ..rendering import color, eprint
from . import register
from .base import SlashCommand

_ON = ("on", "true", "1", "yes", "show")
_OFF = ("off", "false", "0", "no", "hide")


def _run(session, args: str):
    want = (args or "").strip().lower()
    cfg = session.config
    if not want:
        for row in logo.mascot("idle", "lean"):
            print(color(row, "cyan"))
        state = "on" if getattr(cfg, "logo", True) else "off"
        print(color(f"logo: {state}  (usage: /logo on|off · also --no-logo / SCOOT_LOGO=0)", "gray"))
        return
    if want in _ON:
        on = True
    elif want in _OFF:
        on = False
    else:
        eprint(color(f"unknown option '{want}'. use: /logo on|off", "yellow"))
        return
    preferences.set_logo(on)
    try:
        session.config = cfg.override(logo=on)  # Config is frozen: swap in a copy for the live session
    except Exception:
        pass
    bar = getattr(session, "status_bar", None)
    if bar is not None:
        from ..auth import account_label, auth_source
        from ..panel import build_status_text

        try:
            bar.render(build_status_text(session, account_label() or auth_source()))
        except Exception:
            pass
    print(color(f"logo {'on' if on else 'off'} (saved; the banner follows on the next launch).", "gray"))


register(SlashCommand("logo", "show the mascot, or /logo on|off to toggle it", _run, usage="[on|off]"))
