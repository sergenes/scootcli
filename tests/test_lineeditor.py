"""Tests for the fixed-bottom line editor state machine (PLAN §19 / M14a).

All TTY-free: we exercise the pure `apply_key` transition and the `LineEditor` fallback flag.

Run: PYTHONPATH=src python3 tests/test_lineeditor.py
"""

from __future__ import annotations


def _feed(keys, state=None):
    from scootcli.lineeditor import EditorState, apply_key

    s = state or EditorState()
    for k in keys:
        s = apply_key(s, k)
    return s


def test_insert_printable_builds_buffer():
    s = _feed(list("hello"))
    assert s.buffer == "hello"
    assert s.cursor == 5
    assert not s.done


def test_backspace_deletes_before_cursor():
    s = _feed(list("abc") + ["\x7f"])
    assert s.buffer == "ab"
    assert s.cursor == 2


def test_backspace_at_start_is_noop():
    s = _feed(["\x7f"])
    assert s.buffer == ""
    assert s.cursor == 0


def test_enter_submits_and_keeps_buffer():
    s = _feed(list("run tests") + ["\r"])
    assert s.submit is True
    assert s.done is True
    assert s.buffer == "run tests"


def test_newline_also_submits():
    s = _feed(list("x") + ["\n"])
    assert s.submit is True


def test_ctrl_d_on_empty_is_eof():
    s = _feed(["\x04"])
    assert s.eof is True
    assert s.done is True


def test_ctrl_d_with_text_is_ignored():
    s = _feed(list("keep") + ["\x04"])
    assert s.eof is False
    assert s.buffer == "keep"


def test_ctrl_c_interrupts():
    s = _feed(list("abc") + ["\x03"])
    assert s.interrupt is True
    assert s.done is True


def test_control_chars_are_ignored_as_input():
    # A stray tab (\t) is a control char in M14a and should not enter the buffer.
    s = _feed(["\t", "a"])
    assert s.buffer == "a"


# ── M14b: cursor movement, kill ops, forward-delete, history ──────────────────────


def test_left_right_move_cursor_and_insert_midline():
    from scootcli.lineeditor import _LEFT, _RIGHT

    s = _feed(list("abc") + [_LEFT, _LEFT])  # cursor between 'a' and 'b'
    assert s.cursor == 1
    s = _feed([_RIGHT], s)                   # cursor between 'b' and 'c'
    assert s.cursor == 2
    s = _feed(["X"], s)                      # insert at cursor
    assert s.buffer == "abXc"
    assert s.cursor == 3


def test_left_right_clamp_at_bounds():
    from scootcli.lineeditor import _LEFT, _RIGHT

    s = _feed(list("ab") + [_LEFT, _LEFT, _LEFT])
    assert s.cursor == 0
    s = _feed([_RIGHT, _RIGHT, _RIGHT], s)
    assert s.cursor == 2


def test_home_end_and_ctrl_a_ctrl_e():
    from scootcli.lineeditor import _END, _HOME

    s = _feed(list("hello") + [_HOME])
    assert s.cursor == 0
    s = _feed([_END], s)
    assert s.cursor == 5
    # Raw Ctrl-A / Ctrl-E map to HOME / END.
    s = _feed(list("hello") + ["\x01"])
    assert s.cursor == 0
    s = _feed(["\x05"], s)
    assert s.cursor == 5


def test_kill_to_start_and_end():
    from scootcli.lineeditor import _HOME, _KILL_TO_END, _KILL_TO_START, _RIGHT

    # Ctrl-U kills from start to cursor.
    s = _feed(list("hello world") + [_HOME, _RIGHT, _RIGHT])  # cursor at index 2
    s = _feed([_KILL_TO_START], s)
    assert s.buffer == "llo world"
    assert s.cursor == 0
    # Ctrl-K kills from cursor to end.
    s = _feed(list("hello world") + [_HOME, _RIGHT, _RIGHT, _RIGHT, _RIGHT, _RIGHT])  # cursor at 5
    s = _feed([_KILL_TO_END], s)
    assert s.buffer == "hello"


def test_kill_word_before_cursor():
    from scootcli.lineeditor import _KILL_WORD

    s = _feed(list("foo bar baz") + [_KILL_WORD])
    assert s.buffer == "foo bar "
    # Trailing spaces are consumed with the word.
    s = _feed(list("foo bar   ") + [_KILL_WORD])
    assert s.buffer == "foo "


def test_forward_delete():
    from scootcli.lineeditor import _DELETE, _LEFT

    s = _feed(list("abc") + [_LEFT, _LEFT, _DELETE])  # cursor at index 1, delete 'b'
    assert s.buffer == "ac"
    assert s.cursor == 1
    # Delete at end of line is a no-op.
    s = _feed(list("ac") + [_DELETE])
    assert s.buffer == "ac"


def test_history_up_down_recall_and_stash():
    from scootcli.lineeditor import EditorState, _DOWN, _UP, apply_key

    hist = ("first", "second")
    s = EditorState(history=hist, hist_pos=len(hist))
    s = apply_key(s, "d")           # start typing a live line
    assert s.buffer == "d"
    s = apply_key(s, _UP)           # recall newest
    assert s.buffer == "second"
    assert s.cursor == len("second")
    s = apply_key(s, _UP)           # recall older
    assert s.buffer == "first"
    s = apply_key(s, _UP)           # clamp at oldest
    assert s.buffer == "first"
    s = apply_key(s, _DOWN)         # forward
    assert s.buffer == "second"
    s = apply_key(s, _DOWN)         # past newest → restore stashed live line
    assert s.buffer == "d"


def test_history_down_at_live_is_noop():
    from scootcli.lineeditor import EditorState, _DOWN, apply_key

    s = EditorState(history=("a",), hist_pos=1)  # already live, empty buffer
    s = apply_key(s, _DOWN)
    assert s.buffer == ""
    assert s.hist_pos == 1


def test_down_clears_draft_and_up_restores_it():
    from scootcli.lineeditor import EditorState, _DOWN, _UP, apply_key

    s = EditorState()
    for c in "draft":
        s = apply_key(s, c)
    assert s.buffer == "draft"
    s = apply_key(s, _DOWN)          # ↓ on the live line stashes + clears
    assert s.buffer == "" and s.cleared and s.stash == "draft"
    s = apply_key(s, _UP)            # ↑ restores the stashed draft
    assert s.buffer == "draft" and not s.cleared
    assert s.cursor == len("draft")


def test_typing_after_clear_is_not_clobbered_by_up():
    from scootcli.lineeditor import EditorState, _DOWN, _UP, apply_key

    s = EditorState(history=("old",), hist_pos=1)
    for c in "draft":
        s = apply_key(s, c)
    s = apply_key(s, _DOWN)          # clear (keeping "draft")
    s = apply_key(s, "x")            # start a new line → cleared flag drops
    assert s.buffer == "x" and not s.cleared
    s = apply_key(s, _UP)            # ↑ now steps into real history, not the stash
    assert s.buffer == "old"


def test_ctrl_p_ctrl_n_are_history():
    from scootcli.lineeditor import EditorState, apply_key

    s = EditorState(history=("only",), hist_pos=1)
    s = apply_key(s, "\x10")  # Ctrl-P → up
    assert s.buffer == "only"
    s = apply_key(s, "\x0e")  # Ctrl-N → down (back to live/stash)
    assert s.buffer == ""


# ── M14c: bracketed paste ─────────────────────────────────────────────────────────


def test_insert_text_flattens_newlines_and_tabs():
    from scootcli.lineeditor import EditorState, insert_text

    s = insert_text(EditorState(), "a\nb\tc\r\nd")
    assert s.buffer == "a b c  d"
    assert s.cursor == len(s.buffer)


def test_insert_text_at_cursor_between_chars():
    from scootcli.lineeditor import _HOME, _RIGHT, insert_text
    from scootcli.lineeditor import EditorState, apply_key

    s = _feed(list("XY") + [_HOME, _RIGHT])  # cursor between X and Y
    s = insert_text(s, "ab")
    assert s.buffer == "XabY"
    assert s.cursor == 3


def test_insert_text_drops_control_chars_and_empty_is_noop():
    from scootcli.lineeditor import EditorState, insert_text

    s = insert_text(EditorState(), "a\x00b\x07c")  # NUL and BEL dropped
    assert s.buffer == "abc"
    # A paste that reduces to nothing leaves the state unchanged.
    s2 = insert_text(EditorState(buffer="keep", cursor=4), "\x00\x01")
    assert s2.buffer == "keep"
    assert s2.cursor == 4


def test_editor_disabled_when_not_tty(monkeypatch):
    from scootcli.lineeditor import LineEditor

    ed = LineEditor(enabled=False)
    assert ed.enabled is False


# ── M21: Ctrl-S copy signal ────────────────────────────────────────────────────────


def test_ctrl_s_sets_copy_flag_without_submitting():
    # Ctrl-S ("\x13") is a non-terminating signal: it flags copy but keeps the buffer editable.
    s = _feed(list("keep me") + ["\x13"])
    assert s.copy is True
    assert s.submit is False
    assert s.done is False
    assert s.buffer == "keep me"
    assert s.cursor == len("keep me")


def test_editor_stores_on_copy_callback():
    from scootcli.lineeditor import LineEditor

    marker = []
    ed = LineEditor(enabled=False, on_copy=lambda: marker.append(1))
    assert ed.on_copy is not None
    ed.on_copy()
    assert marker == [1]


def test_copy_feedback_returns_to_the_first_column(capsys):
    # The terminal is in raw mode while the editor reads, so the feedback's "\\n" alone would leave
    # the next output starting mid-row. The carriage return comes before the cursor is re-saved.
    from scootcli.lineeditor import LineEditor

    ed = LineEditor(enabled=False, on_copy=lambda: print("copied"))
    ed._handle_copy()
    out = capsys.readouterr().out
    assert "copied\n\r\0337" in out


def test_completion_listing_ends_each_row_with_cr_lf(capsys):
    from scootcli.lineeditor import LineEditor

    LineEditor(enabled=False)._show_listing(["agent.py", "auth.py"])
    out = capsys.readouterr().out
    assert out.startswith("\0338") and "auth.py" in out and "\r\n\0337" in out


# ── Multi-row wrapping geometry (pure, TTY-free) ───────────────────────────────
def test_wrap_layout_single_row_when_short():
    from scootcli.lineeditor import wrap_layout

    # prompt "› " (len 2) + "hello" fits well within 80 cols → one row, caret at end.
    top, height, cline, ccol = wrap_layout(2, "hello", 5, cols=80, max_rows=6)
    assert (top, height, cline) == (0, 1, 0)
    assert ccol == 2 + 5


def test_wrap_layout_grows_to_second_row():
    from scootcli.lineeditor import wrap_layout

    # cols=10, prompt len 2 → 8 buffer chars fill row 0; the 9th wraps to row 1.
    top, height, cline, ccol = wrap_layout(2, "x" * 9, 9, cols=10, max_rows=6)
    assert top == 0
    assert height == 2           # two visual rows now
    assert cline == 1            # caret is on the second visual line
    assert ccol == 1             # (2 + 9) % 10 == 1


def test_wrap_layout_caret_at_wrap_boundary_adds_empty_row():
    from scootcli.lineeditor import wrap_layout

    # Exactly fills row 0 (2 + 8 == 10); caret at end should land on a fresh empty row 1.
    top, height, cline, ccol = wrap_layout(2, "x" * 8, 8, cols=10, max_rows=6)
    assert height == 2
    assert cline == 1
    assert ccol == 0


def test_wrap_layout_vertical_scroll_beyond_max_rows():
    from scootcli.lineeditor import wrap_layout

    # A very long line past the cap scrolls so the caret line stays visible in the window.
    top, height, cline, ccol = wrap_layout(0, "x" * 100, 100, cols=10, max_rows=3)
    assert height == 3                     # capped
    assert cline == 10                     # caret line (100 // 10)
    assert top == cline - 3 + 1 == 8       # window scrolled to keep the caret visible


def test_dock_layout_geometry_grows_reserved_rows():
    from scootcli.panel import DockLayout

    lay = DockLayout()
    # 1 input row: pad + input + spacer + bar == 4 reserved.
    assert lay.reserved() == 4
    assert lay.region_bottom(24) == 20
    assert lay.top_pad_row(24) == 21
    assert lay.input_top(24) == 22
    assert lay.spacer_row(24) == 23
    assert lay.bar_row(24) == 24

    # Grow to 3 input rows: reserved climbs to 6 and the region shrinks accordingly.
    lay.input_rows = 3
    assert lay.reserved() == 6
    assert lay.region_bottom(24) == 18
    assert lay.top_pad_row(24) == 19
    assert lay.input_top(24) == 20
    assert lay.input_bottom(24) == 22
    assert lay.spacer_row(24) == 23


def test_statusbar_with_layout_reserves_dynamically():
    from scootcli.panel import DockLayout, StatusBar

    lay = DockLayout()
    bar = StatusBar(enabled=True, reserve_input=True, layout=lay)  # not a TTY in tests → no output
    assert bar._reserved() == 4      # rule + 1 input + spacer + bar
    lay.input_rows = 2
    assert bar._reserved() == 5
    assert bar._region_bottom(24) == 19


_CMDS = ["help", "verbosity", "version", "reset", "resume", "status"]


def _tab(state, entries=None):
    from scootcli.lineeditor import complete

    return complete(state, _CMDS, entries)


def test_tab_completes_unique_prefix():
    s, listing = _tab(_feed(list("/he")))
    assert s.buffer == "/help"
    assert s.cursor == len("/help")
    assert listing == ()


def test_first_tab_lists_ambiguous_commands_then_cycles():
    s, listing = _tab(_feed(list("/ve")))
    assert listing == ("/verbosity", "/version")   # every match, on the first Tab
    assert s.buffer == "/ver"                       # extended to the common prefix
    s, listing = _tab(s)
    assert s.buffer == "/verbosity" and listing == ()  # alphabetically before /version
    s, _ = _tab(s)
    assert s.buffer == "/version"
    s, _ = _tab(s)  # wraps back to the first match
    assert s.buffer == "/verbosity"


def test_first_tab_extends_commands_to_their_common_prefix():
    s, listing = _tab(_feed(list("/r")))
    assert s.buffer == "/res" and listing == ("/reset", "/resume")
    s, _ = _tab(s)
    assert s.buffer == "/reset"


def test_tab_on_bare_slash_lists_all():
    s, listing = _tab(_feed(["/"]))
    assert s.buffer == "/" and len(listing) == len(_CMDS)
    s, _ = _tab(s)
    assert s.buffer == "/help"  # first alphabetically


def test_tab_no_match_leaves_buffer():
    s, listing = _tab(_feed(list("/zzz")))
    assert s.buffer == "/zzz"
    assert s.comp_prefix is None and listing == ()


def test_tab_without_a_path_lister_is_noop():
    s, listing = _tab(_feed(list("hello")))
    assert s.buffer == "hello" and listing == ()


def test_typing_breaks_completion_cycle():
    from scootcli.lineeditor import apply_key

    s, _ = _tab(_feed(list("/ve")))
    s, _ = _tab(s)                   # → /verbosity, cycle live
    s = apply_key(s, "x")            # any key ends the cycle and inserts normally
    assert s.comp_prefix is None
    assert s.buffer == "/verbosityx"


# ── 0.17.0: Tab completes file paths ────────────────────────────────────────────
_TREE = {
    "": [("src", True), ("setup.py", False), ("setup.cfg", False), ("README.md", False),
         (".github", True), (".gitignore", False)],
    "src/": [("scootcli", True)],
    "src/scootcli/": [("agent.py", False), ("approvals.py", False), ("auth.py", False)],
    "~/": [("notes", True)],
    "/": [("usr", True), ("var", True)],
}


def _entries(directory):
    return _TREE[directory]


def test_tab_inserts_a_unique_file_with_a_space_and_a_directory_with_a_slash():
    s, listing = _tab(_feed(list("read REA")), _entries)
    assert s.buffer == "read README.md " and listing == ()
    s, _ = _tab(_feed(list("look in sr")), _entries)
    assert s.buffer == "look in src/"
    s, _ = _tab(s, _entries)                       # the only entry of src/
    assert s.buffer == "look in src/scootcli/"


def test_tab_extends_to_the_common_prefix_then_lists_on_the_second_tab():
    s, listing = _tab(_feed(list("open se")), _entries)
    assert s.buffer == "open setup." and listing == ()
    s, listing = _tab(s, _entries)
    assert s.buffer == "open setup." and listing == ("setup.cfg", "setup.py")


def test_tab_with_nothing_to_extend_lists_on_the_second_tab():
    s, listing = _tab(_feed(list("src/scootcli/a")), _entries)
    assert listing == () and s.buffer == "src/scootcli/a"
    s, listing = _tab(s, _entries)
    assert listing == ("agent.py", "approvals.py", "auth.py")


def test_tab_offers_dot_entries_only_when_a_dot_is_typed():
    s, listing = _tab(_feed(list("see s")), _entries)   # .github does not get in the way of s…
    s, listing = _tab(s, _entries)
    assert ".github/" not in listing and ".gitignore" not in listing
    s, _ = _tab(_feed(list("see .gith")), _entries)
    assert s.buffer == "see .github/"
    s, _ = _tab(_feed(list("see .")), _entries)
    assert s.buffer == "see .git"                        # common prefix of .github and .gitignore


def test_tab_completes_home_and_absolute_paths_and_mid_line_words():
    from scootcli.lineeditor import EditorState

    s, _ = _tab(_feed(list("cat ~/no")), _entries)
    assert s.buffer == "cat ~/notes/"
    s, _ = _tab(_feed(list("/us")), _entries)            # no command starts with "us": a path
    assert s.buffer == "/usr/"
    s, _ = _tab(EditorState(buffer="diff REA and x", cursor=len("diff REA")), _entries)
    assert s.buffer == "diff README.md and x" and s.cursor == len("diff README.md")
    s, _ = _tab(EditorState(buffer="diff README", cursor=4), _entries)  # caret inside a word: no-op
    assert s.buffer == "diff README"


def test_tab_survives_a_lister_that_fails_and_an_empty_word():
    def boom(_directory):
        raise OSError("gone")

    s, listing = _tab(_feed(list("cat nope/x")), boom)
    assert s.buffer == "cat nope/x" and listing == ()
    s, listing = _tab(_feed(list("cat ")), _entries)
    assert s.buffer == "cat " and listing == ()


def test_format_columns_fits_the_width_and_caps_the_rows():
    from scootcli.lineeditor import format_columns

    lines = format_columns(["agent.py", "approvals.py", "auth.py"], cols=60)
    assert lines == ["agent.py      approvals.py  auth.py"]
    assert all(len(line) < 20 for line in format_columns(["a" * 50, "b"], cols=20))
    many = format_columns([f"file{i:03d}.txt" for i in range(200)], cols=40, max_rows=4)
    assert len(many) == 4 and many[-1].startswith("… +") and many[-1].endswith("more")
    shown = sum(len(line.split()) for line in many[:-1])
    assert shown + int(many[-1].split("+")[1].split()[0]) == 200
    assert format_columns([], 80) == []


def test_repl_path_entries_follow_the_root_and_skip_noise(tmp_path):
    from types import SimpleNamespace

    from scootcli.repl import Repl

    (tmp_path / "src").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / ".github").mkdir()
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / ".DS_Store").write_text("x")
    fake = SimpleNamespace(session=SimpleNamespace(config=SimpleNamespace(root=tmp_path)))
    found = dict(Repl._path_entries(fake, ""))
    assert found == {"src": True, ".github": True, "a.txt": False}
    assert dict(Repl._path_entries(fake, str(tmp_path) + "/")) == found   # absolute works too


if __name__ == "__main__":
    import types

    class _MP:
        def setenv(self, k, v):
            import os

            os.environ[k] = v

    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            fn(_MP()) if "monkeypatch" in fn.__code__.co_varnames else fn()
            print(f"ok  {name}")
            passed += 1
    print(f"\n{passed} passed")

