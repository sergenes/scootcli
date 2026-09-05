"""Tool foundation: interfaces, sandboxing, truncation, and an interruptible subprocess helper.

Each tool is a small object exposing a JSON schema (for the API ``tools`` param) plus a ``run``
handler. Tools are sandboxed to the workspace root and return a structured :class:`ToolResult` so the
agent loop can feed errors back to the model instead of crashing (PLAN §8).
"""

from __future__ import annotations

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
def safe_path(root: Path, raw: str) -> Path:
    """Resolve ``raw`` against ``root`` and ensure it stays inside it. Raises PathEscapeError."""
    if raw is None:
        raise ToolError("missing 'path'")
    root = root.resolve()
    p = Path(raw)
    if not p.is_absolute():
        p = root / p
    p = p.resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise PathEscapeError(f"path escapes workspace root: {raw}")
    return p


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
) -> "tuple[int, str, str]":
    """Run ``cmd`` returning ``(returncode, stdout, stderr)``.

    Honors ``cancel_event`` (ESC) by terminating the process, and enforces ``timeout``.
    """
    import time

    proc = subprocess.Popen(
        cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
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

