"""/yolo — shortcut to switch the session into yolo approval mode (auto-approve all)."""

from __future__ import annotations

from ..rendering import color
from . import register
from .base import SlashCommand


def _run(session, args: str):
    session.approval_mode = "yolo"
    print(color("⚡ yolo mode ON — tool calls run without asking.", "yellow"))
    print(color("  (destructive shell commands are still confirmed; /approve always to revert)", "gray"))
    if args.strip().lower() in ("worktree", "wt", "--worktree"):
        from . import get
        get("worktree").handler(session, "start")


register(SlashCommand("yolo", "auto-approve all tool calls this session", _run, usage="[worktree]"))

