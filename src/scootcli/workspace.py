"""Workspace context: a compact, deterministic map of the repo injected into the agent prompt.

Giving the model an at-a-glance view of the project (git branch + a bounded directory tree) means it
can orient without spending its first step(s) on ``list_dir``. It is intentionally cheap, read-only,
and tightly bounded so it costs few tokens; it is recomputed once per user turn (see
:meth:`scootcli.agent.Agent.run_turn`). Toggle with ``SCOOT_WORKSPACE_CONTEXT`` / ``--no-workspace``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List

_SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", "dist", "build", ".idea", ".vscode", ".cache",
    ".egg-info",
}
_MAX_DEPTH = 2
_MAX_ENTRIES = 120
_MAX_FILES_PER_DIR = 12
_GIT_TIMEOUT = 2


def _git_summary(root: Path) -> str:
    """Best-effort ``branch, N uncommitted change(s)`` for a git repo, or '' if not one/available."""
    def _git(*args) -> "subprocess.CompletedProcess | None":
        try:
            return subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True, text=True, timeout=_GIT_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    head = _git("rev-parse", "--abbrev-ref", "HEAD")
    if head is None or head.returncode != 0:
        return ""
    branch = head.stdout.strip() or "(detached)"
    status = _git("status", "--porcelain")
    dirty = 0
    if status is not None and status.returncode == 0:
        dirty = sum(1 for line in status.stdout.splitlines() if line.strip())
    return f"branch {branch}, {dirty} uncommitted change(s)"


def _is_hidden(name: str) -> bool:
    return name.startswith(".") and name not in (".github", ".env.example")


def render_tree(root: Path, max_depth: int = _MAX_DEPTH, max_entries: int = _MAX_ENTRIES) -> str:
    """Return a compact indented tree (dirs first), depth- and entry-bounded."""
    lines: List[str] = []
    budget = max_entries

    def walk(directory: Path, depth: int) -> None:
        nonlocal budget
        if depth > max_depth or budget <= 0:
            return
        try:
            children = sorted(directory.iterdir(), key=lambda c: (c.is_file(), c.name.lower()))
        except OSError:
            return
        dirs = [c for c in children if c.is_dir()
                and c.name not in _SKIP_DIRS and not c.name.endswith(".egg-info")
                and not _is_hidden(c.name)]
        files = [c for c in children if c.is_file() and not _is_hidden(c.name)]
        indent = "  " * depth
        for child in dirs:
            if budget <= 0:
                break
            budget -= 1
            lines.append(f"{indent}{child.name}/")
            walk(child, depth + 1)
        shown = files[:_MAX_FILES_PER_DIR]
        for child in shown:
            if budget <= 0:
                break
            budget -= 1
            lines.append(f"{indent}{child.name}")
        if len(files) > len(shown) and budget > 0:
            lines.append(f"{indent}… (+{len(files) - len(shown)} more files)")

    walk(Path(root), 0)
    if budget <= 0:
        lines.append("… [truncated]")
    return "\n".join(lines)


def workspace_context(root, max_depth: int = _MAX_DEPTH, max_entries: int = _MAX_ENTRIES) -> str:
    """Compose the workspace map (git summary + tree). Returns '' if nothing useful is available."""
    root = Path(root)
    if not root.is_dir():
        return ""
    parts: List[str] = []
    git = _git_summary(root)
    if git:
        parts.append(f"- git: {git}")
    tree = render_tree(root, max_depth, max_entries)
    if tree:
        indented = "\n".join("  " + line for line in tree.splitlines())
        parts.append("- files:\n" + indented)
    return "\n".join(parts)

