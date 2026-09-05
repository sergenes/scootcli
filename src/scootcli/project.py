"""Project learning for /init: scan the workspace and generate an AGENTS.md via the model.

The scan is intentionally cheap and read-only: a bounded file tree plus the contents of common
manifest/entry files. The model turns that digest into a concise AGENTS.md that is then auto-loaded
into the system prompt on every run (PLAN §6, §15).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import List, Optional

_SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", "dist", "build", ".idea", ".vscode", ".cache",
}
_MANIFESTS = [
    "README.md", "README.rst", "README", "pyproject.toml", "setup.cfg", "setup.py",
    "requirements.txt", "package.json", "tsconfig.json", "pom.xml", "build.gradle",
    "build.gradle.kts", "go.mod", "Cargo.toml", "Gemfile", "Makefile", "Dockerfile",
]
_MAX_TREE_ENTRIES = 200
_MAX_MANIFEST_BYTES = 4000

_INIT_SYSTEM = (
    "You are documenting a software project for an AI coding assistant. Given a file tree and key "
    "files, write a concise AGENTS.md in Markdown with these sections: Overview (what the project is), "
    "Layout (key directories/files), Build & Test (exact commands if discoverable), Conventions, and "
    "Notes. Be specific and terse. Output only the Markdown."
)


def _iter_tree(root: Path) -> List[str]:
    entries: List[str] = []
    for path in sorted(root.rglob("*")):
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        rel = path.relative_to(root).as_posix()
        entries.append(rel + ("/" if path.is_dir() else ""))
        if len(entries) >= _MAX_TREE_ENTRIES:
            entries.append("… [tree truncated]")
            break
    return entries


def _collect_manifests(root: Path) -> List[str]:
    chunks: List[str] = []
    for name in _MANIFESTS:
        path = root / name
        if path.exists() and path.is_file():
            try:
                text = path.read_text("utf-8", "replace")[:_MAX_MANIFEST_BYTES]
            except OSError:
                continue
            chunks.append(f"### {name}\n```\n{text}\n```")
    return chunks


def scan(root: Path) -> str:
    """Return a text digest of the project (tree + manifests) for the model."""
    tree = "\n".join(_iter_tree(root))
    manifests = "\n\n".join(_collect_manifests(root)) or "(no common manifest files found)"
    return f"# File tree\n{tree}\n\n# Key files\n{manifests}"


def _strip_code_fence(text: str) -> str:
    """If the whole response is wrapped in a ``` fence, unwrap it."""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        lines = lines[1:]  # drop opening ```lang
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]  # drop closing ```
        return "\n".join(lines).strip()
    return t


def generate_agents_md(session, cancel_event: Optional[threading.Event] = None) -> str:
    """Scan the workspace root and ask the model to produce AGENTS.md content."""
    root = session.config.root
    digest = scan(root)
    result = session.provider.chat(
        [
            {"role": "system", "content": _INIT_SYSTEM},
            {"role": "user", "content": digest},
        ],
        model=session.active_model,
        max_tokens=1200,
        cancel_event=cancel_event,
    )
    session.account(result.usage)
    return _strip_code_fence((result.content or "").strip())

