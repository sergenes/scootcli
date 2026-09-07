"""Network-free tests for terminal-resize handling: the re-anchor geometry, the input re-wrap math,
cursor-report parsing in both stdin readers, and the SIGWINCH watcher's wake-up pipe.

Run: PYTHONPATH=src python3 -m pytest -q tests/test_resize.py
"""

from __future__ import annotations

import os
import select
import signal

from scootcli import keys
from scootcli.lineeditor import LineEditor, parse_cursor_report
from scootcli.resize import ResizeWatcher, input_rewrap_rows, plan_reanchor


# ── plan_reanchor: where output resumes after the terminal moved everything ────────────────────


def test_reanchor_shrink_moves_anchor_with_the_caret():
    # 40 rows → 24: tmux dropped the rows below the caret, then scrolled 13 into history (caret 38 → 25).
    plan = plan_reanchor(anchor_row=36, caret_drawn=38, caret_now=25, region_bottom=20)
    assert plan.anchor_row == 20         # 36 - 13 = 23, then scrolled up 3 to fit above the dock
    assert plan.scroll == 3
    assert plan.clear_from == 21         # everything below the anchor belongs to the dock
    assert plan.cursor_row == 20


def test_reanchor_no_scroll_when_it_fits():
    plan = plan_reanchor(anchor_row=6, caret_drawn=37, caret_now=37, region_bottom=41)  # grow, no history
    assert (plan.anchor_row, plan.scroll, plan.clear_from) == (6, 0, 7)


def test_reanchor_keeps_a_virtual_row_above_the_screen():
    # A sparse transcript: the anchor (row 6) scrolled into history when the caret moved 37 → 24.
    plan = plan_reanchor(anchor_row=6, caret_drawn=37, caret_now=24, region_bottom=20)
    assert plan.anchor_row == -7          # kept as is: a later grow pulls the history back
    assert plan.cursor_row == 1           # but the save slot is clamped onto the screen
    assert plan.clear_from == 1
    assert plan.scroll == 0
    # ...and the grow brings it back exactly (tmux restored 13 lines; caret 22 → 35).
    back = plan_reanchor(anchor_row=-7, caret_drawn=22, caret_now=35, region_bottom=41)
    assert back.anchor_row == 6


# ── input_rewrap_rows: our own full-width rows split when the width shrinks ───────────────────


def test_rewrap_zero_when_width_did_not_shrink():
    assert input_rewrap_rows(lines_above=2, caret_col=10, old_cols=80, new_cols=80) == 0
    assert input_rewrap_rows(lines_above=2, caret_col=10, old_cols=80, new_cols=120) == 0
    assert input_rewrap_rows(lines_above=2, caret_col=10, old_cols=80, new_cols=0) == 0


def test_rewrap_counts_split_rows_above_the_caret():
    # Two full 100-col rows above the caret each become two 80-col rows: two extra rows.
    assert input_rewrap_rows(lines_above=2, caret_col=10, old_cols=100, new_cols=80) == 2
    # A 200-col row becomes three 80-col rows.
    assert input_rewrap_rows(lines_above=1, caret_col=0, old_cols=200, new_cols=80) == 2


def test_rewrap_caret_moves_onto_a_later_part_of_its_own_row():
    # Caret at column 90 (0-based) of a 100-col row → second 80-col part, reported column 11.
    assert input_rewrap_rows(lines_above=0, caret_col=90, old_cols=100, new_cols=80, reported_col=11) == 1


def test_rewrap_detects_a_truncating_terminal():
    # Same caret, but the terminal clamped it to the last column instead: nothing re-wrapped.
    assert input_rewrap_rows(lines_above=1, caret_col=90, old_cols=100, new_cols=80, reported_col=80) == 0
    # Ambiguous (a re-wrapped caret would also land on the last column): assume re-wrap, so the row
    # above splits in two (+1) and the caret sits on the second part of its own row (+1).
    assert input_rewrap_rows(lines_above=1, caret_col=159, old_cols=160, new_cols=80, reported_col=80) == 2


# ── cursor reports (ESC[row;colR) in both stdin readers ───────────────────────────────────────


def test_parse_cursor_report_body():
    assert parse_cursor_report("24;13") == ("cursor", 24, 13)
    assert parse_cursor_report("24") is None
    assert parse_cursor_report("a;b") is None


def test_editor_reads_a_cursor_report_from_the_fd():
    r, w = os.pipe()
    try:
        os.write(w, b"[24;13R")  # what follows the ESC the reader already consumed
        editor = LineEditor(enabled=False)
        assert editor._read_escape(r) == ("cursor", 24, 13)
        os.write(w, b"[3~")  # a regular Delete still parses
        assert editor._read_escape(r) == "DELETE"
    finally:
        os.close(r)
        os.close(w)


def test_keys_parse_cursor_report_bytes():
    assert keys.parse_cursor_report(b"\x1b[20;44R") == (20, 44)
    assert keys.parse_cursor_report(b"\x1b[201~") is None
    assert keys.parse_cursor_report(b"") is None


def test_query_cursor_is_none_without_a_listener():
    assert keys._active_section is None
    assert keys.query_cursor(timeout=0.01) is None  # nothing would read the reply: don't ask


# ── ResizeWatcher: SIGWINCH wakes a select on the pipe ────────────────────────────────────────


def test_watcher_wakes_select_and_reports_once():
    watcher = ResizeWatcher()
    assert watcher.fd == -1 and watcher.take() is False
    assert watcher.install() is True
    try:
        assert watcher.fd >= 0
        ready, _, _ = select.select([watcher.fd], [], [], 0)
        assert not ready
        os.kill(os.getpid(), signal.SIGWINCH)
        ready, _, _ = select.select([watcher.fd], [], [], 1.0)
        assert ready == [watcher.fd]
        assert watcher.take() is True
        assert watcher.take() is False  # drained
    finally:
        watcher.uninstall()
    assert watcher.fd == -1
    assert signal.getsignal(signal.SIGWINCH) in (signal.SIG_DFL, signal.SIG_IGN, None)


def test_watcher_calls_the_hook_from_the_handler():
    seen = []
    watcher = ResizeWatcher()
    assert watcher.install(lambda: seen.append(True))
    try:
        os.kill(os.getpid(), signal.SIGWINCH)
        select.select([watcher.fd], [], [], 1.0)  # lets the handler run
        assert seen == [True]
        assert watcher.take() is True
    finally:
        watcher.uninstall()
