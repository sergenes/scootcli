"""/worktree — isolate autonomous work in a throwaway git worktree, then review & merge (M5.1b)."""

from __future__ import annotations

from ..rendering import color, eprint
from .. import worktree as wt
from . import register
from .base import SlashCommand

_ACTIONS = ("merge", "keep", "discard")


def _start(session):
    if session.worktree is not None:
        print(color(f"already in worktree {session.worktree.branch}.", "yellow"))
        return
    root = session.config.root
    if not wt.is_git_repo(root):
        print(color("not a git repo — worktree isolation unavailable here.", "yellow"))
        print(color("  tip: use /yolo in place, or `git init` first.", "gray"))
        return
    try:
        w = wt.start(root)
    except Exception as exc:  # git failure
        eprint(color(f"could not create worktree: {exc}", "red"))
        return
    session.worktree = w
    session.switch_root(w.path)
    print(color(f"⚙ isolated in worktree {w.branch}", "cyan"))
    print(color(f"  root → {w.path}", "gray"))
    print(color("  work freely (try /yolo); then /worktree merge|keep|discard", "gray"))


def _finish(session, action):
    w = session.worktree
    if w is None:
        print(color("not in a worktree.", "yellow"))
        return
    result = wt.finish(w, action)
    print(color(result.message, "gray" if result.ok else "yellow"))
    if result.retained:
        # The work is still in the worktree; the session stays there so nothing is lost.
        print(color(f"  still in worktree {w.branch} at {w.path}", "gray"))
        print(color(f"  fix it and run /worktree {action} again, or /worktree discard to drop it", "gray"))
        return
    session.switch_root(w.original_root)
    session.worktree = None
    print(color(f"  root → {w.original_root}", "gray"))


def _run(session, args: str):
    sub = (args.strip() or "start").split()[0].lower()
    if sub in ("start", "on"):
        _start(session)
    elif sub == "diff":
        if session.worktree is None:
            print(color("not in a worktree.", "yellow"))
            return
        d = wt.diff(session.worktree)
        print(d or color("(no changes yet)", "gray"))
    elif sub in _ACTIONS:
        _finish(session, sub)
    else:
        print(color(f"usage: /worktree start|diff|{'|'.join(_ACTIONS)}", "gray"))


register(SlashCommand("worktree", "isolate work in a git worktree, then merge/keep/discard",
                      _run, usage="start|diff|merge|keep|discard"))

