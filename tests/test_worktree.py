"""Tests for git-worktree isolation (M5.1b). Skips gracefully if git is unavailable.

Run: PYTHONPATH=src python3 tests/test_worktree.py
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from scootcli import worktree as wt


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)


def _make_repo() -> Path:
    d = Path(tempfile.mkdtemp())
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "test@example.com")
    _git(d, "config", "user.name", "Test")
    (d / "seed.txt").write_text("seed\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "init")
    return d


def test_is_git_repo():
    if shutil.which("git") is None:
        print("(skipped: git not available)")
        return
    repo = _make_repo()
    non = Path(tempfile.mkdtemp())
    try:
        assert wt.is_git_repo(repo)
        assert not wt.is_git_repo(non)
    finally:
        shutil.rmtree(repo, ignore_errors=True)
        shutil.rmtree(non, ignore_errors=True)


def test_start_diff_merge_roundtrip():
    if shutil.which("git") is None:
        print("(skipped: git not available)")
        return
    repo = _make_repo()
    try:
        w = wt.start(repo)
        assert w.path.exists() and w.branch.startswith("scoot/")
        # Simulate the agent creating a file in the isolated worktree.
        (w.path / "new.txt").write_text("hello from worktree\n")
        d = wt.diff(w)
        assert "new.txt" in d
        assert wt.has_changes(w)
        # Merge back into the base branch.
        res = wt.finish(w, "merge")
        assert res.ok and "merged" in res.message
        assert (repo / "new.txt").read_text() == "hello from worktree\n"
        assert not w.path.exists()  # worktree cleaned up
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_discard_leaves_base_untouched():
    if shutil.which("git") is None:
        print("(skipped: git not available)")
        return
    repo = _make_repo()
    try:
        w = wt.start(repo)
        (w.path / "temp.txt").write_text("scratch\n")
        res = wt.finish(w, "discard")
        assert res.ok and "discarded" in res.message
        assert not (repo / "temp.txt").exists()
    finally:
        shutil.rmtree(repo, ignore_errors=True)


if __name__ == "__main__":
    import types

    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            fn()
            print(f"ok  {name}")
            passed += 1
    print(f"\n{passed} passed")



# ── 0.9.0: a worktree never loses work (review R05, R06) ─────────────────────────
def _failing_commit_hook(repo: Path) -> None:
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'rejected by hook' >&2\nexit 1\n")
    hook.chmod(0o755)


def test_failed_commit_keeps_worktree_and_files():
    if shutil.which("git") is None:
        return
    repo = _make_repo()
    try:
        w = wt.start(repo)
        (w.path / "work.txt").write_text("hours of work\n")
        _failing_commit_hook(repo)
        for action in ("keep", "merge"):
            res = wt.finish(w, action)
            assert not res.ok and res.retained, res
            assert "commit failed" in res.message and "rejected by hook" in res.message
            assert (w.path / "work.txt").read_text() == "hours of work\n"
            assert w.path.exists()
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_committed_branch_still_merges_with_clean_index():
    if shutil.which("git") is None:
        return
    repo = _make_repo()
    try:
        w = wt.start(repo)
        (w.path / "done.txt").write_text("committed by the agent\n")
        _git(w.path, "add", "-A")
        _git(w.path, "commit", "-qm", "agent commit")
        assert not wt.has_changes(w) and wt.has_commits(w)
        res = wt.finish(w, "merge")
        assert res.ok and "merged" in res.message, res
        assert (repo / "done.txt").read_text() == "committed by the agent\n"
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_keep_with_nothing_removes_the_empty_branch_and_merge_conflict_retains():
    if shutil.which("git") is None:
        return
    repo = _make_repo()
    try:
        w = wt.start(repo)
        res = wt.finish(w, "keep")
        assert res.ok and not res.retained and "nothing to keep" in res.message
        assert not w.path.exists()
        branches = subprocess.run(["git", "-C", str(repo), "branch", "--list", w.branch],
                                  capture_output=True, text=True).stdout
        assert branches.strip() == ""
        # A conflicting merge keeps the worktree and says so.
        w = wt.start(repo)
        (w.path / "seed.txt").write_text("worktree version\n")
        (repo / "seed.txt").write_text("main version\n")
        _git(repo, "commit", "-qam", "diverge")
        res = wt.finish(w, "merge")
        assert not res.ok and res.retained and "merge failed" in res.message
        assert (w.path / "seed.txt").exists()
        _git(repo, "merge", "--abort")
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_entering_a_worktree_moves_scope_and_leaving_after_failure_does_not():
    """R06: the session's scope follows the root; R05: a retained worktree keeps the session in it."""
    import io
    from contextlib import redirect_stdout

    from scootcli.commands import worktree as cmd
    from scootcli.config import Config
    from scootcli.hooks import Hooks
    from scootcli.repl import ReplSession

    if shutil.which("git") is None:
        return
    repo = _make_repo()
    try:
        session = ReplSession(Config().override(root=str(repo)), None)
        session.hooks = Hooks(repo)
        session.scope.grant(repo.parent / "elsewhere.txt")
        with redirect_stdout(io.StringIO()):
            cmd._start(session)
        w = session.worktree
        assert w is not None
        assert session.config.root == w.path.resolve()
        assert session.scope.root == w.path.resolve()
        assert not session.scope.allows(repo / "seed.txt"), "the original checkout stayed in scope"
        assert session.scope.granted == [], "grants for the old root were carried over"
        assert session.hooks.root == w.path
        (w.path / "work.txt").write_text("keep me\n")
        _failing_commit_hook(repo)
        out = io.StringIO()
        with redirect_stdout(out):
            cmd._finish(session, "keep")
        assert "commit failed" in out.getvalue()
        assert session.worktree is w and session.config.root == w.path.resolve()
        assert (w.path / "work.txt").exists()
    finally:
        shutil.rmtree(repo, ignore_errors=True)
