"""Tool foundation: interfaces, sandboxing, truncation, and an interruptible subprocess helper.

Each tool is a small object exposing a JSON schema (for the API ``tools`` param) plus a ``run``
handler. Tools are sandboxed to the workspace root and return a structured :class:`ToolResult` so the
agent loop can feed errors back to the model instead of crashing (PLAN §8).
"""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..errors import ScootError, Interrupted

# ── Limits (keep tool output and reads bounded) ────────────────────────────────
MAX_READ_BYTES = 200 * 1024        # 200 KB per read_file
MAX_OUTPUT_CHARS = 16_000          # truncate tool output fed back to the model
MAX_OUTPUT_LINES = 400
DEFAULT_SHELL_TIMEOUT = 60

# Env overrides for non-interactive shell tools: stop child commands (git, pip, …) from
# blocking on a pager or an interactive prompt that can never be answered here.
_NONINTERACTIVE_ENV = {
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
    "GH_PROMPT_DISABLED": "1",
    "PYTHONUNBUFFERED": "1",
    "DEBIAN_FRONTEND": "noninteractive",
}


def noninteractive_env(base: Optional[dict] = None) -> dict:
    """Return a copy of ``base`` (default ``os.environ``) with pager/prompt blockers set."""
    env = dict(os.environ if base is None else base)
    env.update(_NONINTERACTIVE_ENV)
    return env


class ToolError(ScootError):
    """A tool failed in a way worth surfacing (returned as ToolResult, not raised to the user)."""


class PathEscapeError(ToolError):
    """A path resolved outside the workspace root."""


@dataclass
class ToolContext:
    """Ambient info passed to every tool invocation."""

    root: Path
    config: Any = None
    cancel_event: Optional[threading.Event] = None
    scope: "Optional[Scope]" = None  # where file tools may go beyond the workspace root


class Scope:
    """Where file tools may operate: the workspace root plus what the user granted this session.

    The workspace is always allowed. The first access elsewhere asks the user once, who can allow
    that path, its directory for the session, or anywhere for the session (``everything``).
    """

    def __init__(self, root, everything: bool = False):
        self.root = Path(root).resolve()
        self.granted: List[Path] = []
        self.everything = everything

    def allows(self, path) -> bool:
        p = Path(path).expanduser().resolve()
        if self.everything or _inside(p, self.root):
            return True
        return any(_inside(p, g) for g in self.granted)

    def grant(self, path) -> Path:
        p = Path(path).expanduser().resolve()
        if p not in self.granted:
            self.granted.append(p)
        return p

    def grant_dir(self, path) -> Path:
        p = Path(path).expanduser().resolve()
        return self.grant(p if p.is_dir() else p.parent)

    def grant_all(self) -> None:
        self.everything = True

    def describe(self) -> str:
        if self.everything:
            return "anywhere on this machine (for this session)"
        extra = f" + {len(self.granted)} granted" if self.granted else ""
        return f"workspace {self.root}{extra}"


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


@dataclass
class ToolResult:
    """Structured result of a tool call."""

    ok: bool
    content: str = ""          # text fed back to the model
    error: str = ""            # populated when ok is False
    summary: str = ""          # short human line for the status region, e.g. "42 lines"
    meta: dict = field(default_factory=dict)

    @classmethod
    def fail(cls, error: str) -> "ToolResult":
        return cls(ok=False, error=error, summary=error)


class Tool:
    """Base class for tools. Subclasses set ``name``/``description``/``parameters`` and impl ``run``."""

    name: str = ""
    description: str = ""
    parameters: dict = {"type": "object", "properties": {}}
    risk: str = "read"  # "read" | "write" | "shell" — drives the approval UI emphasis
    auto_approve: bool = False  # meta tools with no side effects (e.g. update_plan) skip approval

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def preview(self, args: dict, ctx: ToolContext) -> Optional[str]:
        """Optional rich preview shown at approval time (e.g. a diff). ``None`` = args-only."""
        return None

    def paths(self, args: dict) -> "list[str]":
        """The filesystem paths a call touches, for the scope check before it runs.

        Default: the ``path`` and ``cwd`` arguments. A tool with other path arguments overrides this.
        """
        return [str(args[k]) for k in ("path", "cwd") if isinstance(args.get(k), str) and args.get(k)]

    def schema(self) -> dict:
        """OpenAI-style function schema for the ``tools`` request param."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ── Sandboxing ─────────────────────────────────────────────────────────────────
def resolve_path(root: Path, raw: str) -> Path:
    """``raw`` as an absolute path: ``~`` expanded, relative paths taken from ``root``."""
    if raw is None:
        raise ToolError("missing 'path'")
    p = Path(str(raw)).expanduser()
    if not p.is_absolute():
        p = Path(root).resolve() / p
    return p.resolve()


def safe_path(root: Path, raw: str, scope: "Optional[Scope]" = None) -> Path:
    """Resolve ``raw`` and check it is inside the workspace or granted by ``scope``.

    Raises :class:`PathEscapeError` (carrying ``.path``) otherwise; the agent turns that into a
    question to the user rather than a refusal.
    """
    p = resolve_path(root, raw)
    if scope is not None:
        if scope.allows(p):
            return p
    elif _inside(p, Path(root).resolve()):
        return p
    exc = PathEscapeError(f"outside the workspace: {p} (the user can allow it)")
    exc.path = p
    raise exc


# ── Truncation ─────────────────────────────────────────────────────────────────
def truncate(text: str, max_chars: int = MAX_OUTPUT_CHARS, max_lines: int = MAX_OUTPUT_LINES) -> str:
    """Bound text by lines and chars, appending a clear truncation notice when cut."""
    lines = text.splitlines()
    cut = False
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        cut = True
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars]
        cut = True
    if cut:
        out += "\n… [output truncated]"
    return out


# ── Interruptible subprocess (shared by run_shell + ripgrep search) ────────────
def run_subprocess(
    cmd: list,
    cwd: Path,
    cancel_event: Optional[threading.Event] = None,
    timeout: int = DEFAULT_SHELL_TIMEOUT,
    env: Optional[dict] = None,
) -> "tuple[int, str, str]":
    """Run ``cmd`` returning ``(returncode, stdout, stderr)``.

    Honors ``cancel_event`` (ESC) by terminating the process, and enforces ``timeout``.
    stdin is closed (``DEVNULL``) so a child can't wedge waiting for interactive input.
    """
    import time

    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    deadline = time.time() + timeout
    while True:
        try:
            stdout, stderr = proc.communicate(timeout=0.1)
            return proc.returncode, stdout, stderr
        except subprocess.TimeoutExpired:
            if cancel_event is not None and cancel_event.is_set():
                proc.terminate()
                try:
                    proc.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise Interrupted("tool cancelled by user")
            if time.time() > deadline:
                proc.kill()
                proc.communicate()
                raise ToolError(f"command timed out after {timeout}s")

