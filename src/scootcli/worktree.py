"""Git-worktree isolation for autonomous (YOLO) runs (PLAN §16, M5.1b).

Creates a throwaway worktree on a fresh ``scoot/<ts>`` branch so the agent can work freely; the
human then reviews the diff and chooses merge / keep / discard. Falls back gracefully outside git.

Nothing here removes a worktree that still holds work: a commit that fails, or a merge that
conflicts, leaves the worktree and the branch in place and says so. Forced removal is reserved for
an explicit ``discard``.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Worktree:
    original_root: Path
    path: Path
    branch: str
    base_ref: str
    base_commit: str = ""  # the commit the branch started from; what "has changes" is measured against


@dataclass
class FinishResult:
    """What ``finish`` did. ``retained`` means the worktree is still there (and the session should
    stay in it) because something went wrong that the user has to look at."""

    ok: bool
    message: str
    retained: bool = False


def _git(root: Path, *args: str, check: bool = True) -> "subprocess.CompletedProcess":
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=check,
    )


def _err(r: "subprocess.CompletedProcess") -> str:
    return (r.stderr or r.stdout or "").strip()[:200]


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


def _head_commit(root: Path) -> str:
    r = _git(root, "rev-parse", "HEAD", check=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def start(root: Path) -> Worktree:
    """Create an isolated worktree + branch from the current HEAD."""
    ts = time.strftime("%Y%m%d-%H%M%S")
    branch = f"scoot/{ts}"
    path = root / ".scoot" / f"work-{ts}"
    path.parent.mkdir(parents=True, exist_ok=True)
    base = current_ref(root)
    base_commit = _head_commit(root)
    _git(root, "worktree", "add", "-b", branch, str(path), "HEAD")
    return Worktree(original_root=root, path=path, branch=branch, base_ref=base, base_commit=base_commit)


def diff(wt: Worktree) -> str:
    """Stage all changes in the worktree and return a stat + unified diff vs the base."""
    _git(wt.path, "add", "-A", check=False)
    stat = _git(wt.path, "diff", "--cached", "--stat", check=False).stdout
    full = _git(wt.path, "diff", "--cached", check=False).stdout
    return (stat + "\n" + full).strip()


def has_changes(wt: Worktree) -> bool:
    """Uncommitted changes (staged after ``add -A``) in the worktree."""
    _git(wt.path, "add", "-A", check=False)
    r = _git(wt.path, "diff", "--cached", "--quiet", check=False)
    return r.returncode != 0  # non-zero = there are staged changes


def has_commits(wt: Worktree) -> bool:
    """Commits on the branch beyond the base: work the agent (or a hook) already committed."""
    head = _head_commit(wt.path)
    return bool(head) and head != wt.base_commit


def _commit_all(wt: Worktree) -> "tuple[str, str]":
    """Commit everything in the worktree. Returns ``("clean" | "committed" | "failed", detail)``.

    The three outcomes are kept apart on purpose: ``finish`` used to read a failed commit as "no
    changes" and then remove the worktree, taking the uncommitted files with it.
    """
    if not has_changes(wt):
        return "clean", ""
    r = _git(wt.path, "commit", "-m", f"scoot: {wt.branch}", check=False)
    if r.returncode != 0:
        return "failed", _err(r) or f"git commit exited {r.returncode}"
    return "committed", ""


def finish(wt: Worktree, action: str) -> FinishResult:
    """Complete the session. ``action`` ∈ {merge, keep, discard}.

    On any Git failure the worktree is retained and the result says so; nothing is force-removed
    except by ``discard``.
    """
    if action == "discard":
        r = _git(wt.original_root, "worktree", "remove", "--force", str(wt.path), check=False)
        if r.returncode != 0:
            return FinishResult(False, f"could not remove worktree {wt.path}: {_err(r)}", retained=True)
        _git(wt.original_root, "branch", "-D", wt.branch, check=False)
        return FinishResult(True, f"discarded worktree and branch {wt.branch}.")

    state, detail = _commit_all(wt)
    if state == "failed":
        return FinishResult(False, f"commit failed, worktree kept at {wt.path}: {detail}", retained=True)
    ahead = has_commits(wt)

    if action == "keep":
        if not ahead:
            return _remove_empty(wt, "no changes; nothing to keep.")
        r = _git(wt.original_root, "worktree", "remove", str(wt.path), check=False)
        if r.returncode != 0:
            return FinishResult(False, f"branch {wt.branch} is committed but the worktree could not be "
                                       f"removed: {_err(r)}", retained=True)
        return FinishResult(True, f"kept branch {wt.branch} for manual review (git checkout {wt.branch}).")

    # action == "merge"
    if not ahead:
        return _remove_empty(wt, "no changes to merge.")
    r = _git(wt.original_root, "merge", "--no-edit", wt.branch, check=False)
    if r.returncode != 0:
        return FinishResult(False, f"merge failed, worktree kept: resolve with `git merge {wt.branch}` "
                                   f"in {wt.original_root}. {_err(r)}", retained=True)
    r = _git(wt.original_root, "worktree", "remove", str(wt.path), check=False)
    if r.returncode != 0:
        return FinishResult(True, f"merged {wt.branch} into {wt.base_ref}, but the worktree at {wt.path} "
                                  f"could not be removed: {_err(r)}")
    _git(wt.original_root, "branch", "-d", wt.branch, check=False)
    return FinishResult(True, f"merged {wt.branch} into {wt.base_ref}.")


def _remove_empty(wt: Worktree, message: str) -> FinishResult:
    """Drop a worktree and branch that hold nothing beyond the base commit."""
    r = _git(wt.original_root, "worktree", "remove", str(wt.path), check=False)
    if r.returncode != 0:
        return FinishResult(False, f"could not remove worktree {wt.path}: {_err(r)}", retained=True)
    _git(wt.original_root, "branch", "-d", wt.branch, check=False)
    return FinishResult(True, message)
