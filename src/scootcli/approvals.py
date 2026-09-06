"""Human-in-the-loop approvals (PLAN §9).

Default policy is ``always``: every tool call is shown and must be approved. The prompt renders the
tool name + arguments and, for writes/shell, a rich preview (diff or command). Inline single-key
controls:

    [a] approve once   [t] trust this tool for the session   [A] approve all this session (yolo)
    [e] edit arguments   [s] skip (tell the model no)   [q] abort the agent

``[t]`` and ``[A]`` fight approval fatigue on read-heavy tasks; the shell denylist still re-confirms
catastrophic commands even after ``[A]``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum

from .keys import read_key
from .rendering import color, eprint
from .tools.base import Tool, ToolContext


class Decision(Enum):
    APPROVE = "approve"
    APPROVE_TOOL = "approve_tool"        # trust this tool for the rest of the session
    APPROVE_SESSION = "approve_session"  # approve everything for the rest of the session (yolo)
    SKIP = "skip"
    ABORT = "abort"


@dataclass
class Approval:
    decision: Decision
    args: dict  # possibly edited by the user


# ── Approval modes (increasing autonomy) ───────────────────────────────────────
#   always     — prompt for every tool call
#   auto-read  — auto-approve read-only tools; prompt writes & shell
#   auto-edits — auto-approve reads AND file writes/edits; prompt shell only
#   yolo       — auto-approve everything (denylisted shell commands still confirmed)
MODES = ("always", "auto-read", "auto-edits", "yolo")

# Catastrophic shell commands that are ALWAYS confirmed, even in yolo mode.
_DENYLIST = [
    (re.compile(r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r", re.I), "recursive force delete"),
    (re.compile(r":\(\)\s*\{.*\|.*&\s*\}\s*;", re.S), "fork bomb"),
    (re.compile(r"\bgit\s+push\b", re.I), "git push"),
    (re.compile(r"\bsudo\b", re.I), "sudo"),
    (re.compile(r"\b(shutdown|reboot|halt|poweroff)\b", re.I), "power/shutdown"),
    (re.compile(r"\bmkfs\b|\bdd\s+.*of=/dev/", re.I), "disk/format command"),
    (re.compile(r"\b(curl|wget|nc|ncat|scp|sftp)\b", re.I), "outbound network command"),
    (re.compile(r"\bchmod\s+-[a-z]*r|\bchown\s+-[a-z]*r", re.I), "recursive permission change"),
    (re.compile(r">\s*/dev/(sd|nvme|disk)", re.I), "write to raw disk"),
]


def denylisted_reason(command: str):
    """Return a reason string if the shell command matches the always-confirm denylist, else None."""
    for pattern, reason in _DENYLIST:
        if pattern.search(command or ""):
            return reason
    return None


def needs_prompt(mode: str, tool: Tool, args: dict, trusted=()) -> "str | None":
    """Decide whether a tool call must be prompted.

    Returns ``None`` if it may be auto-approved, or a short reason string if a prompt is required.
    ``trusted`` is the set of tool names the user chose to auto-approve for this session.
    Denylisted shell commands are ALWAYS confirmed, regardless of mode or trust.
    """
    if tool.name == "run_shell":
        reason = denylisted_reason(args.get("command", ""))
        if reason:
            return f"blocked command ({reason})"
    if getattr(tool, "auto_approve", False):
        return None  # side-effect-free meta tools (e.g. update_plan) never prompt
    if mode == "yolo":
        return None
    if tool.name in (trusted or ()):
        return None
    if mode == "auto-read":
        return None if tool.risk == "read" else "write/shell action"
    if mode == "auto-edits":
        return None if tool.risk in ("read", "write") else "shell action"
    return "approval required"  # always


_RISK_COLOR = {"read": "cyan", "write": "yellow", "shell": "red"}


def _render(tool: Tool, args: dict, ctx: ToolContext) -> None:
    dot = color("●", _RISK_COLOR.get(tool.risk, "cyan"))
    print(f"{dot} {color(tool.name, 'bold')} {color(json.dumps(args, ensure_ascii=False), 'gray')}")
    try:
        preview = tool.preview(args, ctx)
    except Exception as exc:  # a preview must never crash the loop
        preview = color(f"(preview unavailable: {exc})", "gray")
    if preview:
        for line in preview.splitlines():
            print("  " + line)


def _edit_args(args: dict) -> dict:
    """Let the user type replacement JSON for the tool arguments."""
    print(color("  current args: ", "gray") + json.dumps(args, ensure_ascii=False))
    print(color("  enter new JSON (blank to keep): ", "gray"), end="", flush=True)
    try:
        raw = input()
    except (EOFError, KeyboardInterrupt):
        return args
    raw = raw.strip()
    if not raw:
        return args
    try:
        new = json.loads(raw)
        if isinstance(new, dict):
            return new
        eprint(color("  (ignored: not a JSON object)", "yellow"))
    except json.JSONDecodeError as exc:
        eprint(color(f"  (ignored: invalid JSON: {exc})", "yellow"))
    return args


SCOPE_CHOICES = ("once", "dir", "all", "deny", "abort")


def request_scope(tool: Tool, path, ctx: ToolContext) -> str:
    """Ask once about a path outside the workspace. Returns one of ``SCOPE_CHOICES``."""
    from pathlib import Path as _P

    p = _P(str(path))
    where = p if p.is_dir() else p.parent
    print(color(f"  ⌂ {tool.name} wants {p}", "yellow") + color("  (outside the workspace)", "gray"))
    while True:
        print(
            "  "
            + color("allow this path", "green") + " [a]  "
            + color(f"allow {where} this session", "green") + " [d]  "
            + color("allow anywhere this session", "green") + " [A]  "
            + color("skip", "yellow") + " [s]  "
            + color("quit", "red") + " [q] › ",
            end="", flush=True,
        )
        key = read_key()
        print(key)
        if key in ("a", "y", "\r", "\n", ""):
            return "once"
        if key == "d":
            return "dir"
        if key == "A":
            return "all"
        if key == "s":
            return "deny"
        if key == "q":
            return "abort"


def request_approval(tool: Tool, args: dict, ctx: ToolContext) -> Approval:
    """Show the pending tool call and read the user's decision (with optional arg editing)."""
    while True:
        _render(tool, args, ctx)
        print(
            "  "
            + color("approve", "green")
            + " [a]  "
            + color(f"trust {tool.name}", "green")
            + " [t]  "
            + color("all-session", "green")
            + " [A]  "
            + color("edit", "cyan")
            + " [e]  "
            + color("skip", "yellow")
            + " [s]  "
            + color("quit", "red")
            + " [q] › ",
            end="",
            flush=True,
        )
        key = read_key()
        print(key)  # echo the choice for a clean transcript

        if key in ("a", "y", "\r", "\n", ""):
            return Approval(Decision.APPROVE, args)
        if key == "t":
            return Approval(Decision.APPROVE_TOOL, args)
        if key == "A":
            return Approval(Decision.APPROVE_SESSION, args)
        if key == "e":
            args = _edit_args(args)
            continue
        if key == "s":
            return Approval(Decision.SKIP, args)
        if key in ("q", "\x1b"):
            return Approval(Decision.ABORT, args)
        # Unknown key: re-prompt.

