"""Terminal resize (SIGWINCH) plumbing for the REPL dock (stdlib-only, POSIX).

The dock pins a band of rows at the bottom of the terminal with a DECSTBM scroll region. A resize (a
window drag, or a font zoom in tmux, iTerm, Terminal.app) invalidates all of it at once: terminals
reset the scroll region, drop or add rows, and scroll content into history to keep the cursor on
screen (tmux even pulls history back when the window grows). Nothing tells a program about it except
``SIGWINCH``, so this module turns that signal into things the REPL can act on at a safe moment:

* :class:`ResizeWatcher` installs the handler and a self-pipe (``signal.set_wakeup_fd``) so a blocking
  ``select`` on stdin wakes up when the terminal changes and the line editor repaints at once, instead
  of at the next keystroke. The handler itself only sets a flag; all drawing happens on the main
  thread's normal path.
* :func:`plan_reanchor` is the pure geometry. After a resize the terminal has moved the cursor and the
  content around it together (up on a shrink, down when history is pulled back). Given where the
  editor last drew its caret, where the terminal now reports it (``ESC[6n``), and the row where the
  transcript ended (the "anchor" where output resumes after the prompt), it works out the anchor's new
  row, how many rows to scroll so it fits above the dock, and which rows to blank.
"""

from __future__ import annotations

import os
import signal
import threading
from dataclasses import dataclass
from typing import Callable, Optional

CURSOR_REPORT = "\033[6n"  # DSR: ask the terminal where the cursor is; it answers ESC[row;colR


@dataclass(frozen=True)
class Reanchor:
    """Where output resumes after a resize, and what to do to the screen to make that true."""

    anchor_row: int  # the transcript's end (may be < 1 when it scrolled into history)
    scroll: int      # rows to scroll the whole screen up first (transcript spilled into the dock)
    clear_from: int  # first row to blank: everything below the anchor belongs to the dock

    @property
    def cursor_row(self) -> int:
        """The on-screen row to save as the output anchor (a virtual row above the top clamps to 1)."""
        return max(1, self.anchor_row)


def input_rewrap_rows(lines_above: int, caret_col: int, old_cols: int, new_cols: int,
                      reported_col: Optional[int] = None) -> int:
    """How many extra rows the editor's own input rows take after a width change.

    The rows between the transcript's end and the caret are blank pad rows (which never re-wrap) and
    the input's own rows. When the width shrinks, terminals that re-wrap on resize (tmux, iTerm2,
    kitty, VTE, xterm.js) split every full-width input row above the caret's into
    ``ceil(old_cols / new_cols)`` rows, and move the caret onto the ``caret_col // new_cols``-th part
    of its own row. That is the amount by which the caret's reported row overstates the transcript's
    shift. ``caret_col`` is 0-based.

    A terminal that truncates instead (classic xterm) is recognised when the caret sat beyond the new
    width: it reports the caret clamped to the last column, where a re-wrapping terminal reports the
    column within the split part. Without that evidence, re-wrapping is assumed (the common case).
    """
    if new_cols <= 0 or old_cols <= new_cols:
        return 0
    if reported_col is not None and caret_col >= new_cols:
        rewrapped_col = caret_col % new_cols + 1
        if reported_col == new_cols and rewrapped_col != new_cols:
            return 0  # truncated, not re-wrapped: nothing moved
    per_row = -(-old_cols // new_cols)
    return lines_above * (per_row - 1) + caret_col // new_cols


def plan_reanchor(anchor_row: int, caret_drawn: int, caret_now: int, region_bottom: int) -> Reanchor:
    """Geometry for repainting the dock after a resize.

    ``anchor_row`` is the transcript's end before the resize, ``caret_drawn`` the row the editor last
    put its caret on, ``caret_now`` the row the terminal reports for it after the resize (the content
    moved with it), ``region_bottom`` the last row of the new scroll region. Rows are 1-based.
    """
    shift = caret_now - caret_drawn
    anchor = anchor_row + shift
    scroll = max(0, anchor - region_bottom)
    anchor -= scroll
    return Reanchor(anchor_row=anchor, scroll=scroll, clear_from=max(1, anchor + 1))


class ResizeWatcher:
    """A ``SIGWINCH`` handler with a self-pipe, so a ``select`` on stdin can wake up on a resize.

    ``fd`` is the pipe's read end to add to a ``select`` set; :meth:`take` drains it and says whether a
    resize happened since the last call. ``on_resize`` (optional) is called from the signal handler,
    on the main thread, for code paths that are not sitting in the editor's read loop (a running turn).
    No-op when the platform has no ``SIGWINCH`` or when installed off the main thread.
    """

    def __init__(self) -> None:
        self.pending = False
        self._read_fd = -1
        self._write_fd = -1
        self._installed = False
        self._prev_handler = None
        self._prev_wakeup = -1
        self.on_resize: Optional[Callable[[], None]] = None

    @property
    def fd(self) -> int:
        return self._read_fd

    def install(self, on_resize: Optional[Callable[[], None]] = None) -> bool:
        if self._installed:
            return True
        if not hasattr(signal, "SIGWINCH") or threading.current_thread() is not threading.main_thread():
            return False
        try:
            r, w = os.pipe()
            os.set_blocking(r, False)
            os.set_blocking(w, False)
            self._prev_wakeup = signal.set_wakeup_fd(w, warn_on_full_buffer=False)
            self._prev_handler = signal.signal(signal.SIGWINCH, self._handle)
        except (OSError, ValueError):
            return False
        self._read_fd, self._write_fd = r, w
        self.on_resize = on_resize
        self._installed = True
        return True

    def uninstall(self) -> None:
        if not self._installed:
            return
        try:
            signal.signal(signal.SIGWINCH, self._prev_handler or signal.SIG_DFL)
            signal.set_wakeup_fd(self._prev_wakeup)
        except (OSError, ValueError):
            pass
        for fd in (self._read_fd, self._write_fd):
            try:
                os.close(fd)
            except OSError:
                pass
        self._read_fd = self._write_fd = -1
        self._installed = False

    def _handle(self, signum, frame) -> None:
        self.pending = True
        if self.on_resize is not None:
            try:
                self.on_resize()
            except Exception:
                pass  # a repaint must never break the REPL

    def take(self) -> bool:
        """Drain the wake-up pipe; return whether a resize is pending (and clear it)."""
        seen = self.pending
        if self._read_fd >= 0:
            while True:
                try:
                    chunk = os.read(self._read_fd, 64)
                except BlockingIOError:
                    break
                except OSError:
                    break
                if not chunk:
                    break
                if bytes([signal.SIGWINCH]) in chunk:
                    seen = True
        self.pending = False
        return seen
