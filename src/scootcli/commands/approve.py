"""/approve — view or set the session approval mode (always | auto-read | auto-edits | yolo)."""

from __future__ import annotations

from ..approvals import MODES
from ..rendering import color
from . import register
from .base import SlashCommand

_DESCRIPTIONS = {
    "always": "prompt for every tool call (safest)",
    "auto-read": "auto-approve read-only tools; prompt writes & shell",
    "auto-edits": "auto-approve reads & file edits; prompt shell only",
    "yolo": "auto-approve everything (denylisted shell commands still confirmed)",
}


def _run(session, args: str):
    arg = args.strip().lower()
    if not arg:
        print(color(f"approval mode: {session.approval_mode}", "bold"))
        for mode in MODES:
            mark = color(" ← current", "green") if mode == session.approval_mode else ""
            print(f"  {color(mode, 'cyan')}{mark}  {color('— ' + _DESCRIPTIONS[mode], 'gray')}")
        print(color("  use: /approve <mode>   (or /yolo)", "gray"))
        return
    if arg not in MODES:
        print(color(f"unknown mode '{arg}'. choose: {', '.join(MODES)}", "yellow"))
        return
    session.approval_mode = arg
    print(color(f"approval mode set to {arg}.", "gray"))
    if arg == "yolo":
        print(color("  ⚠ yolo: tool calls run without asking (destructive shell commands still confirmed).", "yellow"))


register(SlashCommand("approve", "view/set approval mode", _run,
                      usage="[always|auto-read|auto-edits|yolo]"))

