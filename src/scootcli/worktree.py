"""Git-worktree isolation for autonomous (YOLO) runs (PLAN §16, M5.1b).

Creates a throwaway worktree on a fresh ``scoot/<ts>`` branch so the agent can work freely; the
human then reviews the diff and chooses merge / keep / discard. Falls back gracefully outside git.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Worktree:
    original_root: Path
    path: Path
    branch: str
    base_ref: str


def _git(root: Path, *args: str, check: bool = True) -> "subprocess.CompletedProcess":
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=check,
    )


def is_git_repo(root: Path) -> bool:
    try:
        r = _git(root, "rev-parse", "--is-inside-work-tree", check=False)
        return r.returncode == 0 and r.stdout.strip() == "true"
    except (OSError, FileNotFoundError):
        return False


def current_ref(root: Path) -> str:
    r = _git(root, "rev-parse", "--abbrev-ref", "HEAD", check=False)
    ref = r.stdout.strip()
    return ref or "HEAD"


def start(root: Path) -> Worktree:
    """Create an isolated worktree + branch from the current HEAD."""
    ts = time.strftime("%Y%m%d-%H%M%S")
    branch = f"scoot/{ts}"
    path = root / ".scoot" / f"work-{ts}"
    path.parent.mkdir(parents=True, exist_ok=True)
    base = current_ref(root)
    _git(root, "worktree", "add", "-b", branch, str(path), "HEAD")
    return Worktree(original_root=root, path=path, branch=branch, base_ref=base)


def diff(wt: Worktree) -> str:
    """Stage all changes in the worktree and return a stat + unified diff vs the base."""
    _git(wt.path, "add", "-A", check=False)
    stat = _git(wt.path, "diff", "--cached", "--stat", check=False).stdout
    full = _git(wt.path, "diff", "--cached", check=False).stdout
    return (stat + "\n" + full).strip()


def has_changes(wt: Worktree) -> bool:
    _git(wt.path, "add", "-A", check=False)
    r = _git(wt.path, "diff", "--cached", "--quiet", check=False)
    return r.returncode != 0  # non-zero = there are staged changes


def _commit_all(wt: Worktree) -> bool:
    if not has_changes(wt):
        return False
    _git(wt.path, "add", "-A", check=False)
    r = _git(wt.path, "commit", "-m", f"scoot: {wt.branch}", check=False)
    return r.returncode == 0


def finish(wt: Worktree, action: str) -> str:
    """Complete the session. ``action`` ∈ {merge, keep, discard}. Returns a status message."""
    if action == "discard":
        _git(wt.original_root, "worktree", "remove", "--force", str(wt.path), check=False)
        _git(wt.original_root, "branch", "-D", wt.branch, check=False)
        return f"discarded worktree and branch {wt.branch}."

    committed = _commit_all(wt)

    if action == "keep":
        _cleanup_dir(wt)
        return (f"kept branch {wt.branch} for manual review (git checkout {wt.branch})."
                if committed else "no changes; nothing to keep.")

    # action == "merge"
    if not committed:
        _cleanup_dir(wt)
        return "no changes to merge."
    r = _git(wt.original_root, "merge", "--no-edit", wt.branch, check=False)
    if r.returncode != 0:
        return (f"merge conflict — resolve manually: `git merge {wt.branch}` "
                f"(worktree kept). {r.stderr.strip()[:200]}")
    _cleanup_dir(wt)
    _git(wt.original_root, "branch", "-d", wt.branch, check=False)
    return f"merged {wt.branch} into {wt.base_ref}."


def _cleanup_dir(wt: Worktree) -> None:
    _git(wt.original_root, "worktree", "remove", "--force", str(wt.path), check=False)


def cleanup(wt: Optional[Worktree]) -> None:
    """Best-effort removal of the worktree dir (branch retained). For crash/exit safety."""
    if wt is None:
        return
    _cleanup_dir(wt)

