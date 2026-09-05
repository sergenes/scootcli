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
        msg = wt.finish(w, "merge")
        assert "merged" in msg
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
        msg = wt.finish(w, "discard")
        assert "discarded" in msg
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

