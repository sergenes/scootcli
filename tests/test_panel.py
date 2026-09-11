"""Network-free tests for the bottom status bar (text builder + safe no-op when not a TTY).

Run: PYTHONPATH=src python3 tests/test_panel.py
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

from scootcli.config import Config
from scootcli.panel import StatusBar, _fmt_tokens, build_status_text


class _Session:
    def __init__(self, root="/proj/widgets", model="auto", active="gpt-5-mini",
                 mode="always", p=900, c=300, worktree=None):
        self.config = Config().override(root=root)
        self.id = "20260831-120000-ab12"
        self.model = model
        self.active_model = active
        self.approval_mode = mode
        self.total_prompt = p
        self.total_completion = c
        self.worktree = worktree

    def resolved_model(self):
        return "gpt-4o" if self.model == "auto" else self.model


def test_fmt_tokens():
    assert _fmt_tokens(0) == "0"
    assert _fmt_tokens(999) == "999"
    assert _fmt_tokens(1000) == "1.0k"
    assert _fmt_tokens(1234) == "1.2k"


def test_build_status_text_segments():
    text = build_status_text(_Session(), user="jdoe")
    assert "jdoe" in text
    assert "widgets" in text            # folder basename
    assert "ab12" in text               # short session id
    assert "gpt-5-mini (auto)" in text  # active model + auto marker
    assert "always" in text             # approval mode
    assert "1.2k tok" in text           # 900 + 300 = 1200


def test_build_status_text_without_user_and_with_worktree():
    class _WT:
        branch = "scoot/20260831"

    text = build_status_text(_Session(mode="yolo", worktree=_WT()), user=None)
    assert "signed in" in text
    assert "yolo" in text
    assert "scoot/20260831" in text


def test_build_status_text_shows_plan_progress():
    session = _Session()
    session.plan = [
        {"step": "a", "status": "completed"},
        {"step": "b", "status": "in_progress"},
        {"step": "c", "status": "pending"},
    ]
    text = build_status_text(session, user="jdoe")
    assert "◇ 1/3" in text  # 1 of 3 steps completed


def test_build_status_text_shows_last_error():
    session = _Session()
    session.last_error = "AuthError"
    text = build_status_text(session, user="jdoe")
    assert "⚠ AuthError" in text


def test_statusbar_is_noop_without_tty():
    # With stdout redirected to a StringIO (isatty()==False), the bar must do nothing / never crash.
    buf = io.StringIO()
    with redirect_stdout(buf):
        bar = StatusBar(enabled=True)
        noop = bar.enabled is False
        bar.install()
        bar.render("hello")
        bar.remove()
    assert noop is True
    assert buf.getvalue() == ""


def test_max_steps_default_is_raised():
    assert Config().max_steps == 50


def test_reserved_geometry_for_dock():
    # Without the input dock: reserve 2 rows (spacer + bar). With it: 3 (spacer + input + bar).
    plain = StatusBar(enabled=True)
    assert plain._reserved() == 2
    assert plain._region_bottom(24) == 22   # scroll region 1..22
    assert plain._spacer_row(24) == 23      # spacer just below the region

    docked = StatusBar(enabled=True, reserve_input=True)
    assert docked._reserved() == 3
    assert docked._region_bottom(24) == 21  # scroll region 1..21
    assert docked._spacer_row(24) == 22     # spacer at 22, input at 23, bar at 24


if __name__ == "__main__":
    import types

    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            fn()
            print(f"ok  {name}")
            passed += 1
    print(f"\n{passed} passed")




# ── 0.13.0: live per-turn tool-call segment (bar refresh) ───────────────────────
def test_build_status_text_shows_turn_tool_calls():
    session = _Session()
    session.messages = [{"role": "user", "content": "hi"}]
    assert "⚒" not in build_status_text(session, user="jdoe")  # nothing until a tool runs
    session.turn_tool_calls = 3
    assert "⚒ 3" in build_status_text(session, user="jdoe")
