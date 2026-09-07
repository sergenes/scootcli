"""Stdlib raw-mode keyboard handling for ESC-to-interrupt (PLAN §6).

During a working turn we put the terminal in cbreak mode and run a small listener thread that watches
stdin for the ESC key. On ESC we set a shared ``cancel_event`` which the transport polls to terminate
the in-flight request (and, later, tool) immediately.

Line editing for typing prompts happens in normal cooked mode (plain ``input()``); raw mode is only
active *while the agent works*, so we never interfere with the user typing.

While a section is active it is the only reader of stdin, so it also relays the terminal's answer to
a cursor-position request (``ESC[6n`` → ``ESC[row;colR``); :func:`query_cursor` is how the REPL finds
out where output is after a resize that happened mid-turn.
"""

from __future__ import annotations

import os
import re
import sys
import threading
from typing import Optional, Tuple

try:
    import termios
    import tty
    import select

    _HAVE_TERMIOS = True
except ImportError:  # pragma: no cover - non-POSIX
    _HAVE_TERMIOS = False

ESC = "\x1b"
NOTE_KEY = b"\x0e"  # Ctrl-N: ask for a note at the next model call
_CURSOR_REPORT = re.compile(rb"\x1b\[(\d+);(\d+)R")  # the terminal's answer to ESC[6n

_active_section: "Optional[InterruptibleSection]" = None  # the section currently reading stdin


def parse_cursor_report(data: bytes) -> Optional[Tuple[int, int]]:
    """``(row, col)`` from a byte stream holding ``ESC[row;colR``, or ``None``."""
    m = _CURSOR_REPORT.search(data)
    return (int(m.group(1)), int(m.group(2))) if m else None


def query_cursor(timeout: float = 0.2) -> Optional[Tuple[int, int]]:
    """Ask the terminal where the cursor is, while a section's listener can read the answer.

    Returns ``(row, col)`` (1-based) or ``None`` when no section is active (so nothing would read the
    reply and it would leak into the next prompt) or the terminal did not answer in time.
    """
    section = _active_section
    if section is None or not section.enabled:
        return None
    from .resize import CURSOR_REPORT

    section.cursor_event.clear()
    section.cursor_report = None
    sys.stdout.write(CURSOR_REPORT)
    sys.stdout.flush()
    if section.cursor_event.wait(timeout):
        return section.cursor_report
    return None


def read_key() -> str:
    """Read a single keypress (lowercased). Falls back to a line read on non-TTY (piped) input."""
    if not (_HAVE_TERMIOS and sys.stdin.isatty()):
        line = sys.stdin.readline()
        return (line.strip()[:1].lower() if line.strip() else "q")
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch.lower()


class InterruptibleSection:
    """Context manager: enable ESC-interrupt for the duration of a working turn.

    Usage::

        cancel = threading.Event()
        with InterruptibleSection(cancel):
            ...run work, poll cancel...

    If stdin is not a TTY (piped input) or termios is unavailable, this is a no-op and ESC-interrupt
    is simply unavailable — the work still runs normally.
    """

    def __init__(self, cancel_event: threading.Event, note_event: "threading.Event | None" = None):
        self.cancel_event = cancel_event
        self.note_event = note_event
        self.enabled = _HAVE_TERMIOS and sys.stdin.isatty()
        self._fd = sys.stdin.fileno() if self.enabled else -1
        self._old_attrs = None
        self._thread = None
        self._stop = threading.Event()
        self.cursor_report: Optional[Tuple[int, int]] = None  # last ESC[row;colR seen (see query_cursor)
        self.cursor_event = threading.Event()

    def __enter__(self) -> "InterruptibleSection":
        global _active_section
        if not self.enabled:
            return self
        try:
            self._old_attrs = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)  # keeps ISIG so Ctrl-C still quits
        except (termios.error, ValueError):
            self.enabled = False
            self._old_attrs = None
            return self
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()
        _active_section = self
        return self

    def _listen(self) -> None:
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([sys.stdin], [], [], 0.1)
            except (OSError, ValueError):
                break
            if not ready:
                continue
            try:
                ch = os.read(self._fd, 1)
            except (OSError, ValueError):
                break
            if ch == NOTE_KEY and self.note_event is not None:
                self.note_event.set()
                continue
            if ch == b"\x1b":
                # Distinguish a bare ESC (a real interrupt) from an escape sequence — arrow keys,
                # mouse reports, or bracketed-paste markers (\x1b[201~) left in the buffer after a
                # drag-and-drop. If more bytes follow immediately, it's a sequence: drain + ignore.
                try:
                    more, _, _ = select.select([sys.stdin], [], [], 0.05)
                except (OSError, ValueError):
                    more = None
                if more:
                    seq = bytearray(ch)
                    try:
                        while select.select([sys.stdin], [], [], 0)[0]:
                            byte = os.read(self._fd, 1)
                            if not byte:
                                break
                            seq += byte
                    except (OSError, ValueError):
                        pass
                    report = parse_cursor_report(bytes(seq))
                    if report is not None:  # the terminal answering query_cursor
                        self.cursor_report = report
                        self.cursor_event.set()
                    continue
                self.cancel_event.set()
                break

    def __exit__(self, *exc) -> None:
        global _active_section
        if _active_section is self:
            _active_section = None
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.3)
        if self._old_attrs is not None:
            try:
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_attrs)
            except (termios.error, ValueError):
                pass

