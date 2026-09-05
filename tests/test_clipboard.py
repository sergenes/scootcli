"""Network-/TTY-free tests for clipboard copy (PLAN §3 / M21).

We monkeypatch ``shutil.which`` + ``subprocess.run`` to assert the right backend is chosen, and the
OSC-52 fallback when no binary is available.

Run: PYTHONPATH=src python3 tests/test_clipboard.py
"""

from __future__ import annotations

import base64

import scootcli.clipboard as clip


def _patch(monkeypatch, available, run_recorder):
    """Make only the binaries in ``available`` resolvable, and record subprocess.run calls."""
    monkeypatch.setattr(clip.shutil, "which",
                        lambda name: f"/usr/bin/{name}" if name in available else None)

    def _run(cmd, **kwargs):
        run_recorder.append((cmd, kwargs))

    monkeypatch.setattr(clip.subprocess, "run", _run)


def test_prefers_pbcopy_when_present(monkeypatch):
    calls = []
    _patch(monkeypatch, {"pbcopy", "xclip"}, calls)
    assert clip.copy_to_clipboard("hello") is True
    assert calls and calls[0][0][0] == "/usr/bin/pbcopy"
    assert calls[0][1]["input"] == b"hello"


def test_falls_through_to_xclip_with_selection_args(monkeypatch):
    calls = []
    _patch(monkeypatch, {"xclip"}, calls)
    assert clip.copy_to_clipboard("data") is True
    cmd = calls[0][0]
    assert cmd[0] == "/usr/bin/xclip"
    assert cmd[1:] == ["-selection", "clipboard"]


def test_binary_failure_advances_to_next(monkeypatch):
    calls = []
    monkeypatch.setattr(clip.shutil, "which",
                        lambda name: f"/usr/bin/{name}" if name in {"wl-copy", "xsel"} else None)

    def _run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0].endswith("wl-copy"):
            raise clip.subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(clip.subprocess, "run", _run)
    assert clip.copy_to_clipboard("x") is True
    assert calls[0][0].endswith("wl-copy")
    assert calls[1][0].endswith("xsel")


def test_osc52_fallback_when_no_binary(monkeypatch):
    import io

    monkeypatch.setattr(clip.shutil, "which", lambda name: None)  # no binaries
    fake = io.StringIO()
    fake.isatty = lambda: True  # type: ignore[attr-defined]
    monkeypatch.setattr(clip.sys, "stdout", fake)
    assert clip.copy_to_clipboard("hi") is True
    payload = base64.b64encode(b"hi").decode("ascii")
    assert fake.getvalue() == f"\033]52;c;{payload}\a"


def test_osc52_skipped_when_not_a_tty(monkeypatch):
    import io

    monkeypatch.setattr(clip.shutil, "which", lambda name: None)
    fake = io.StringIO()
    fake.isatty = lambda: False  # type: ignore[attr-defined]
    monkeypatch.setattr(clip.sys, "stdout", fake)
    assert clip.copy_to_clipboard("hi") is False
    assert fake.getvalue() == ""


def test_empty_text_is_noop(monkeypatch):
    monkeypatch.setattr(clip.shutil, "which", lambda name: "/usr/bin/pbcopy")
    assert clip.copy_to_clipboard("") is False


def test_copy_session_output_messages(monkeypatch):
    monkeypatch.setattr(clip, "copy_to_clipboard", lambda text: True)

    class _S:
        last_output = "  the answer  "

    ok, msg = clip.copy_session_output(_S())
    assert ok is True
    assert "10 chars" in msg  # "the answer" after trimming surrounding whitespace

    class _Empty:
        last_output = ""

    ok, msg = clip.copy_session_output(_Empty())
    assert ok is False
    assert "nothing to copy" in msg


def test_copy_session_output_reports_failure(monkeypatch):
    monkeypatch.setattr(clip, "copy_to_clipboard", lambda text: False)

    class _S:
        last_output = "answer"

    ok, msg = clip.copy_session_output(_S())
    assert ok is False
    assert "couldn't reach a clipboard" in msg


if __name__ == "__main__":
    import types

    class _MP:
        """Minimal monkeypatch with revert (osc52 tests replace the shared sys.stdout)."""

        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, old in reversed(self._undo):
                setattr(obj, name, old)
            self._undo.clear()

    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            mp = _MP()
            try:
                if "monkeypatch" in fn.__code__.co_varnames:
                    fn(mp)
                else:
                    fn()
            finally:
                mp.undo()
            print(f"ok  {name}")
            passed += 1
    print(f"\n{passed} passed")

