"""Hooks: shell commands scoot runs at lifecycle events, with a JSON payload on stdin.

Events: ``SessionStart``, ``UserPromptSubmit``, ``PreToolUse``, ``PostToolUse``, ``Stop``,
``Notification``, ``SessionEnd``. Configuration lives in ``hooks.json`` in scoot's config dir
(global) and in ``.scoot/hooks.json`` under the workspace (project); both are merged, project first.
The file shape and the payloads follow the de-facto standard set by Claude Code, so a hook script
written for one works with the other:

    {"PreToolUse": [{"matcher": "run_shell|write_file", "hooks": [{"type": "command", "command": "...", "timeout": 60}]}]}

A hook answers with its exit code or with JSON on stdout:
  * exit 0, empty stdout: no decision
  * exit 0, JSON stdout: ``{"decision": "block", "reason": ...}`` or, for PreToolUse,
    ``{"permissionDecision": "allow" | "deny" | "ask", "reason": ...}``; plain text on stdout is
    extra context (used by UserPromptSubmit)
  * exit 2: block or deny, stderr is the reason
  * anything else, or a timeout: logged and ignored
Hooks run sequentially in configuration order; the first blocking decision wins.
``SCOOT_HOOKS=0`` disables everything. Hooks never see API keys.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "Notification",
          "SessionEnd")
DEFAULT_TIMEOUT = 60
_MAX_CAPTURE = 16_000
_TOOL_KINDS = {"read": "read", "write": "write", "shell": "shell"}
# Claude Code's names for the same jobs, so a hooks.json written for it ("matcher": "Bash|Write|Edit")
# fires for scoot's tools too, and a shared script can switch on ``tool_alias``.
TOOL_ALIASES = {
    "read_file": "Read", "list_dir": "LS", "search": "Grep", "write_file": "Write", "edit_file": "Edit",
    "run_shell": "Bash", "open_editor": "Open", "update_plan": "TodoWrite",
}


@dataclass
class Decision:
    action: str = ""      # "" | allow | deny | ask | block
    reason: str = ""
    context: str = ""     # extra text a hook printed (UserPromptSubmit adds it to the prompt)

    @property
    def blocks(self) -> bool:
        return self.action in ("deny", "block")


@dataclass
class HookResult:
    event: str
    command: str
    outcome: str  # ok | allow | deny | ask | block | timeout | error | bad-json
    detail: str = ""
    seconds: float = 0.0


def enabled() -> bool:
    return os.environ.get("SCOOT_HOOKS", "1").strip().lower() not in ("0", "false", "no", "off")


def global_path() -> Path:
    override = os.environ.get("SCOOT_CONFIG_DIR")
    if override:
        return Path(override).expanduser() / "hooks.json"
    from .config import _config_home

    return _config_home() / "hooks.json"


def project_path(root) -> Path:
    return Path(root) / ".scoot" / "hooks.json"


def _load_file(path: Path) -> Dict[str, list]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else data  # Claude Code nests under "hooks"
    out: Dict[str, list] = {}
    for event, entries in hooks.items():
        if event in EVENTS and isinstance(entries, list):
            out[event] = [e for e in entries if isinstance(e, dict) and isinstance(e.get("hooks"), list)]
    return out


def tool_kind(tool) -> str:
    if getattr(tool, "auto_approve", False):
        return "meta"
    return _TOOL_KINDS.get(getattr(tool, "risk", ""), "read")


def tool_alias(name: str) -> str:
    return TOOL_ALIASES.get(name, name)


def matcher_hits(matcher: str, tool_name: str) -> bool:
    """A matcher regex matches scoot's tool name or its Claude Code alias."""
    try:
        rx = re.compile(str(matcher))
    except re.error:
        return False
    return bool(rx.search(tool_name) or rx.search(tool_alias(tool_name)))


class Hooks:
    """The merged hook configuration for one workspace, plus a record of what ran."""

    def __init__(self, root, config: Optional[Dict[str, list]] = None):
        self.root = Path(root)
        self.sources: List[str] = []
        self.config: Dict[str, list] = {}
        self.history: List[HookResult] = []
        if config is not None:
            self.config = config
            self.sources = ["injected"]
        else:
            self.reload()

    def reload(self) -> None:
        self.config = {}
        self.sources = []
        for path in (project_path(self.root), global_path()):
            if path.is_file():
                loaded = _load_file(path)
                self.sources.append(str(path))
                for event, entries in loaded.items():
                    self.config.setdefault(event, []).extend(entries)

    def has(self, event: str) -> bool:
        return enabled() and bool(self.config.get(event))

    # ── payloads ─────────────────────────────────────────────────────────────────
    @staticmethod
    def payload(session, event: str, **fields) -> dict:
        from . import sessions

        sid = getattr(session, "id", "")
        cfg = getattr(session, "config", None)
        body = {
            "session_id": sid,
            "cwd": str(getattr(cfg, "root", "") or ""),
            "hook_event_name": event,
            "model": getattr(session, "active_model", ""),
            "transcript_path": str(sessions._sessions_dir() / f"{sid}.json") if sid else "",
        }
        body.update(fields)
        return body

    # ── running ──────────────────────────────────────────────────────────────────
    def run(self, event: str, payload: dict, cancel_event: Optional[threading.Event] = None) -> Decision:
        if not self.has(event):
            return Decision()
        tool_name = str(payload.get("tool_name", ""))
        context: List[str] = []
        for entry in self.config.get(event, []):
            matcher = entry.get("matcher")
            if matcher and event in ("PreToolUse", "PostToolUse"):
                if not matcher_hits(matcher, tool_name):
                    continue
            for hook in entry.get("hooks", []):
                if not isinstance(hook, dict) or hook.get("type", "command") != "command":
                    continue
                command = str(hook.get("command", "")).strip()
                if not command:
                    continue
                timeout = int(hook.get("timeout") or DEFAULT_TIMEOUT)
                decision = self._run_command(event, command, payload, timeout, cancel_event)
                if decision.context:
                    context.append(decision.context)
                if decision.action:
                    decision.context = "\n".join(context)
                    return decision
        return Decision(context="\n".join(context))

    def _run_command(self, event: str, command: str, payload: dict, timeout: int, cancel_event) -> Decision:
        env = dict(os.environ)
        env["SCOOT_SESSION_ID"] = str(payload.get("session_id", ""))
        env["SCOOT_HOOK_EVENT"] = event
        started = time.time()
        try:
            proc = subprocess.run(
                command, shell=True, cwd=str(self.root), env=env, input=json.dumps(payload),
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            self._record(event, command, "timeout", f"after {timeout}s", started)
            return Decision()
        except OSError as exc:
            self._record(event, command, "error", str(exc), started)
            return Decision()
        out = (proc.stdout or "")[:_MAX_CAPTURE].strip()
        err = (proc.stderr or "")[:_MAX_CAPTURE].strip()
        if proc.returncode == 2:
            action = "deny" if event == "PreToolUse" else "block"
            self._record(event, command, action, err, started)
            return Decision(action=action, reason=err or "blocked by hook")
        if proc.returncode != 0:
            self._record(event, command, "error", f"exit {proc.returncode}: {err[:200]}", started)
            return Decision()
        if not out:
            self._record(event, command, "ok", "", started)
            return Decision()
        if out.startswith("{"):
            try:
                data = json.loads(out)
            except json.JSONDecodeError:
                self._record(event, command, "bad-json", out[:200], started)
                return Decision()
            decision = self._interpret(event, data)
            self._record(event, command, decision.action or "ok", decision.reason, started)
            return decision
        self._record(event, command, "ok", "context", started)
        return Decision(context=out)

    @staticmethod
    def _interpret(event: str, data: dict) -> Decision:
        reason = str(data.get("reason") or "")
        # Claude Code nests a hook's decision under "hookSpecificOutput" (with the reason as
        # "permissionDecisionReason" and extra context as "additionalContext"); scoot's own flat
        # shape puts the same keys at the top level. Read both, nested first.
        hso = data.get("hookSpecificOutput")
        if isinstance(hso, dict):
            if not reason:
                reason = str(hso.get("permissionDecisionReason") or hso.get("reason") or "")
            perm_src = hso.get("permissionDecision")
            context_src = hso.get("additionalContext")
        else:
            perm_src = data.get("permissionDecision")
            context_src = None
        perm = str(perm_src or "").lower()
        if perm == "approve":
            perm = "allow"  # Claude Code's older synonym
        if event == "PreToolUse" and perm in ("allow", "deny", "ask"):
            return Decision(action=perm, reason=reason)
        raw = str(data.get("decision") or "").lower()
        if raw == "block":
            return Decision(action="deny" if event == "PreToolUse" else "block", reason=reason)
        if raw == "approve" and event == "PreToolUse":
            return Decision(action="allow", reason=reason)
        context = context_src or data.get("additionalContext") or data.get("context") or ""
        return Decision(context=str(context) if context else "")

    def _record(self, event: str, command: str, outcome: str, detail: str, started: float) -> None:
        self.history.append(HookResult(event, command, outcome, detail, round(time.time() - started, 3)))
        del self.history[:-50]


# ── helpers used by the REPL, one-shot mode, and headless mode ─────────────────
def for_session(session) -> Optional[Hooks]:
    return getattr(session, "hooks", None) if enabled() else None


def submit_prompt(session, text: str, cancel_event=None) -> Optional[str]:
    """Fire ``UserPromptSubmit``. Returns the (possibly extended) prompt, or ``None`` when blocked."""
    hooks = for_session(session)
    if hooks is None or not hooks.has("UserPromptSubmit"):
        return text
    decision = hooks.run("UserPromptSubmit", hooks.payload(session, "UserPromptSubmit", prompt=text),
                         cancel_event)
    if decision.blocks:
        session.last_error = "blocked by hook"
        try:
            session.hook_block_reason = decision.reason
        except Exception:
            pass
        return None
    if decision.context:
        return f"{text}\n\n[context from hook]\n{decision.context}"
    return text


def notify(session, kind: str, message: str) -> None:
    hooks = for_session(session)
    if hooks is not None and hooks.has("Notification"):
        hooks.run("Notification", hooks.payload(session, "Notification", kind=kind, message=message))


def session_event(session, event: str, **fields) -> None:
    hooks = for_session(session)
    if hooks is not None and hooks.has(event):
        hooks.run(event, hooks.payload(session, event, **fields))
