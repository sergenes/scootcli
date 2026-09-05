"""Network-free tests for REPL turn flow: continue-on-max-steps prompt.

Run: PYTHONPATH=src python3 tests/test_repl.py
"""

from __future__ import annotations

import builtins
import os
import tempfile

from scootcli.agent import AgentOutcome
from scootcli.config import Config
from scootcli.repl import Repl


class _FakeAgent:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = 0

    def run_turn(self, session, ui, cancel):
        o = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        return o


def _repl():
    os.environ["SCOOT_STATE_DIR"] = tempfile.mkdtemp(prefix="scoot-repl-")
    cfg = Config().override(root="/tmp", panel=False)
    return Repl(cfg, provider=None)


def _with_input(answer, fn):
    orig = builtins.input
    builtins.input = lambda *a, **k: answer
    try:
        fn()
    finally:
        builtins.input = orig


def test_continue_yes_reruns_the_turn():
    repl = _repl()
    repl.agent = _FakeAgent([
        AgentOutcome("max_steps", steps=50),
        AgentOutcome("done", content="finished", steps=4),
    ])
    _with_input("y", lambda: repl._run_turn("big task"))
    assert repl.agent.calls == 2  # asked to continue -> ran again


def test_continue_no_stops():
    repl = _repl()
    repl.agent = _FakeAgent([AgentOutcome("max_steps", steps=50)])
    _with_input("n", lambda: repl._run_turn("big task"))
    assert repl.agent.calls == 1  # declined -> stopped


def test_report_turn_error_does_not_raise():
    repl = _repl()
    # Both a known ScootError and an unexpected exception should be surfaced, not re-raised.
    from scootcli.errors import ScootError

    repl._report_turn_error(ScootError("token expired"))
    repl._report_turn_error(RuntimeError("boom"))


def test_ui_quiet_suppresses_reasoning_narration():
    from scootcli.repl import ReplUI

    ui = ReplUI(verbosity="quiet")
    ui._tty = False  # force the non-TTY branch (would eprint otherwise)
    # In quiet mode assistant() must be a no-op (no reasoning in the feed); just assert it doesn't raise.
    ui.assistant("I'll read cli.py, then edit it")
    assert ui.verbosity == "quiet"

    ui.verbosity = "bogus-level"  # unknown levels are tolerated; default stays sane elsewhere
    assert ReplUI(verbosity="bogus").verbosity == "full"


def test_done_does_not_prompt():
    repl = _repl()
    repl.agent = _FakeAgent([AgentOutcome("done", content="ok", steps=2)])

    def _boom(*a, **k):
        raise AssertionError("should not prompt on a completed turn")

    orig = builtins.input
    builtins.input = _boom
    try:
        repl._run_turn("small task")
    finally:
        builtins.input = orig
    assert repl.agent.calls == 1


def test_resume_hint_shows_last_session_for_root():
    import io
    from contextlib import redirect_stdout

    from scootcli import sessions

    repl = _repl()  # fresh SCOOT_STATE_DIR, root=/tmp
    root = str(repl.session.config.root)  # resolved (e.g. /private/tmp on macOS)
    # Save a prior session for this root so the banner can surface it.
    sessions.save(sessions.SessionRecord(
        id="20260831-090000-aa11", root=root, created=1.0, updated=2.0,
        model="auto", active_model="gpt-4o", approval_mode="always",
        messages=[{"role": "user", "content": "fix the parser"}],
    ))
    hint = repl._resume_hint()
    assert hint and "20260831-090000-aa11" in hint
    buf = io.StringIO()
    with redirect_stdout(buf):
        repl._banner()  # the banner surfaces the hint beside the mascot
    out = buf.getvalue()
    assert "20260831-090000-aa11" in out
    assert "fix the parser" in out
    assert "/resume" in out


def test_resume_hint_silent_without_saved_session():
    import io
    from contextlib import redirect_stdout

    repl = _repl()  # fresh empty state dir
    buf = io.StringIO()
    with redirect_stdout(buf):
        repl._resume_hint()
    assert buf.getvalue() == ""


def test_resume_hint_suppressed_when_policy_off():
    import io
    from contextlib import redirect_stdout

    from scootcli import sessions

    os.environ["SCOOT_STATE_DIR"] = tempfile.mkdtemp(prefix="scoot-repl-")
    cfg = Config().override(root="/tmp", panel=False, resume="off")
    repl = Repl(cfg, provider=None)
    sessions.save(sessions.SessionRecord(
        id="20260831-100000-bb22", root=str(cfg.root), created=1.0, updated=2.0,
        model="auto", active_model="gpt-4o", approval_mode="always",
        messages=[{"role": "user", "content": "hello"}],
    ))
    buf = io.StringIO()
    with redirect_stdout(buf):
        repl._resume_hint()
    assert buf.getvalue() == ""  # policy=off → no hint even with a saved session


def test_auto_resume_loads_latest_for_root():
    from scootcli import cli, sessions

    os.environ["SCOOT_STATE_DIR"] = tempfile.mkdtemp(prefix="scoot-auto-")
    cfg = Config().override(root="/tmp", resume="auto")
    root = str(cfg.root)
    sessions.save(sessions.SessionRecord(
        id="20260831-110000-cc33", root=root, created=1.0, updated=2.0,
        model="auto", active_model="gpt-4o", approval_mode="always",
        messages=[{"role": "user", "content": "earlier work"}],
    ))

    captured = {}

    class _FakePool:
        def __init__(self):
            self.config = cfg
            self.spec = registry.get("ollama")  # needs no key, so no onboarding hint interferes

    class _FakeRepl:
        def __init__(self, config, provider, resume=None):
            captured["resume"] = resume

        def run(self):
            return 0

    # Patch Repl so _interactive runs offline.
    import scootcli.repl as repl_mod
    from scootcli.providers import registry
    orig_repl = repl_mod.Repl
    repl_mod.Repl = _FakeRepl
    try:
        cli._interactive(_FakePool(), resume=None)
    finally:
        repl_mod.Repl = orig_repl

    assert captured["resume"] is not None
    assert captured["resume"].id == "20260831-110000-cc33"


# ── M21: copy the last answer ──────────────────────────────────────────────────────


def test_render_outcome_captures_last_output():
    repl = _repl()
    repl._render_outcome(AgentOutcome("done", content="  the final answer  ", steps=1))
    assert repl.session.last_output == "the final answer"


def test_render_outcome_non_done_leaves_last_output_untouched():
    repl = _repl()
    repl.session.last_output = "previous"
    repl._render_outcome(AgentOutcome("error", error="boom", steps=1))
    assert repl.session.last_output == "previous"


def test_copy_last_uses_clipboard_and_reports(monkeypatch):
    import scootcli.clipboard as clip

    repl = _repl()
    repl.session.last_output = "answer text"
    seen = {}
    monkeypatch.setattr(clip, "copy_to_clipboard", lambda text: seen.setdefault("text", text) or True)
    repl._copy_last()
    assert seen["text"] == "answer text"


# ── M22 fix: a dropped file path must not be parsed as a slash-command ───────────────


def test_slash_command_detection_vs_dropped_path():
    from scootcli.repl import _is_slash_command

    # Real slash-commands.
    assert _is_slash_command("/help")
    assert _is_slash_command("/model gpt-4o")
    assert _is_slash_command("/c")
    assert _is_slash_command("/unknown")  # still a command (→ "unknown command" message)
    # Dropped absolute image paths must NOT look like commands.
    assert not _is_slash_command("/Users/me/Desktop/Screenshot.png describe this")
    assert not _is_slash_command("/Users/me/a b/shot.png")
    assert not _is_slash_command("/")


if __name__ == "__main__":
    import types

    class _MP:
        def setattr(self, obj, name, value):
            setattr(obj, name, value)

    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            if "monkeypatch" in fn.__code__.co_varnames:
                fn(_MP())
            else:
                fn()
            print(f"ok  {name}")
            passed += 1
    print(f"\n{passed} passed")

