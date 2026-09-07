"""Fixed-bottom input editor (stdlib raw-mode) for the REPL dock (PLAN §19 / M14).

The editor renders the prompt near the bottom of the terminal (just above the status bar) and returns
the typed line, while all agent output scrolls in the region above it. The core is a **pure state
machine** (:func:`apply_key`) so it is unit-testable with scripted keys and no TTY; the rendering/
reading is a thin layer on top.

M14a shipped: printable insert, Backspace, Enter (submit), Ctrl-C (interrupt), Ctrl-D (EOF on empty).
M14b adds full line editing: ←/→ + Ctrl-B/F, Home/End + Ctrl-A/E, Ctrl-U/K/W kill ops, forward Delete,
↑/↓ (and Ctrl-P/N) history recall. M14c adds bracketed paste: a multi-line paste arrives as one payload
(ESC[200~ … ESC[201~) and is inserted via :func:`insert_text` with newlines flattened to spaces, so it
never submits mid-paste. **Tab** completes/cycles a leading ``/command`` name (:func:`cycle_completion`,
fed by the command registry via a ``completer`` callback).

Rendering has two modes. Without a :class:`~scootcli.panel.DockLayout` the input is a single row that
scrolls horizontally (legacy). With a layout, a long line **wraps onto extra rows** and the dock grows
(and shrinks) with it: :func:`wrap_layout` computes the visual geometry purely (unit-tested), and the
renderer updates the shared layout + asks the status bar to re-establish the scroll region and redraw
the frame (``on_reflow``) so growing input never overwrites the bar. The input is framed by blank pad
rows (not drawn rules), which are resize-proof and leave no artifacts.

**Resize.** A terminal resize (a window drag, a font zoom) resets the scroll region and moves the
content: the bar is gone, the input is on the wrong row. With a :class:`~scootcli.resize.ResizeWatcher`
the read loop wakes up on ``SIGWINCH`` and repaints at once. To put things back where they belong it
needs one fact the terminal keeps to itself: where the transcript ended. So ``readline`` asks for the
cursor position (``ESC[6n``) right after saving that spot (the "anchor" the answer resumes at), and
again for the caret after a resize; the difference says how far the content moved, and
:func:`~scootcli.resize.plan_reanchor` turns that into the scroll + clear + redraw that keeps the
transcript, the input, and the bar consistent at the new size.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from collections import deque
from dataclasses import dataclass, replace

from .rendering import color

try:
    import select
    import termios
    import tty

    _HAVE_TERMIOS = True
except ImportError:  # pragma: no cover - non-POSIX
    _HAVE_TERMIOS = False

# Raw control keys.
_ENTER = ("\r", "\n")
_BACKSPACE = ("\x7f", "\b")
_CTRL_C = "\x03"
_CTRL_D = "\x04"
_CTRL_S = "\x13"  # copy the last answer to the clipboard ("save"); deliverable because raw mode is on
_TAB = "\t"  # slash-command completion (cycles through matches)

# Logical key tokens (multi-char so they never collide with a printable insert). The reader translates
# raw ESC sequences and editing control bytes into these before calling apply_key.
_LEFT = "LEFT"
_RIGHT = "RIGHT"
_HOME = "HOME"
_END = "END"
_UP = "UP"
_DOWN = "DOWN"
_DELETE = "DELETE"           # forward delete (Delete key / ESC[3~)
_KILL_TO_START = "KILL_BOL"  # Ctrl-U
_KILL_TO_END = "KILL_EOL"    # Ctrl-K
_KILL_WORD = "KILL_WORD"     # Ctrl-W
_PASTE_START = "PASTE_START"  # ESC[200~ — begin a bracketed-paste payload
_RESIZE = "RESIZE"           # the terminal changed size (ResizeWatcher wake-up)
_CURSOR = "cursor"           # ("cursor", row, col): the terminal's answer to ESC[6n
_PASTE = "paste"             # ("paste", text): a bracketed-paste payload

# Terminal control: enable/disable bracketed-paste mode so a multi-line paste arrives as one payload
# (wrapped in ESC[200~ … ESC[201~) instead of a stream of Enter keys that would each submit.
_PASTE_ON = "\033[?2004h"
_PASTE_OFF = "\033[?2004l"

# Editing control bytes → logical tokens (readline-style emacs bindings).
_CONTROL_KEYS = {
    "\x01": _HOME,           # Ctrl-A
    "\x05": _END,            # Ctrl-E
    "\x02": _LEFT,           # Ctrl-B
    "\x06": _RIGHT,          # Ctrl-F
    "\x10": _UP,             # Ctrl-P
    "\x0e": _DOWN,           # Ctrl-N
    "\x15": _KILL_TO_START,  # Ctrl-U
    "\x0b": _KILL_TO_END,    # Ctrl-K
    "\x17": _KILL_WORD,      # Ctrl-W
}


@dataclass(frozen=True)
class EditorState:
    """Immutable editor state; :func:`apply_key` returns the next one.

    History recall is modelled purely: ``history`` is the (immutable) list of prior lines and
    ``hist_pos`` indexes into it, where ``hist_pos == len(history)`` means "the live line". ``stash``
    remembers the in-progress line while browsing so ↓ past the newest entry restores it.
    """

    buffer: str = ""
    cursor: int = 0
    submit: bool = False     # Enter pressed → return the buffer
    eof: bool = False        # Ctrl-D on an empty buffer → raise EOFError (like input())
    interrupt: bool = False  # Ctrl-C → raise KeyboardInterrupt (quit app)
    copy: bool = False       # Ctrl-S → copy last answer (non-terminating; readline resets it)
    history: tuple = ()
    hist_pos: int = 0
    stash: str = ""
    cleared: bool = False    # ↓ on the live line stashed the draft into ``stash`` and blanked it; ↑ restores
    comp_prefix: object = None  # base prefix captured on the first Tab of a completion cycle (None = idle)
    comp_index: int = 0         # which match the last Tab landed on (cycles with repeated Tab)

    @property
    def done(self) -> bool:
        return self.submit or self.eof or self.interrupt


def _kill_word(state: EditorState) -> EditorState:
    """Delete the whitespace-delimited word immediately before the cursor (Ctrl-W)."""
    b, i = state.buffer, state.cursor
    j = i
    while j > 0 and b[j - 1] == " ":
        j -= 1
    while j > 0 and b[j - 1] != " ":
        j -= 1
    return replace(state, buffer=b[:j] + b[i:], cursor=j)


def _history_prev(state: EditorState) -> EditorState:
    """↑ / Ctrl-P: move to an older history entry (stashing the live line on first step up).

    If the live line was just cleared with ↓ (``cleared``), ↑ first restores that stashed draft
    instead of stepping into history.
    """
    if state.cleared:
        return replace(state, buffer=state.stash, cursor=len(state.stash), cleared=False)
    if state.hist_pos <= 0:
        return state
    stash = state.buffer if state.hist_pos == len(state.history) else state.stash
    pos = state.hist_pos - 1
    buf = state.history[pos]
    return replace(state, buffer=buf, cursor=len(buf), hist_pos=pos, stash=stash)


def _history_next(state: EditorState) -> EditorState:
    """↓ / Ctrl-N: move to a newer entry, restoring the stashed live line past the newest.

    At the live line (``hist_pos == len(history)``) with text present, ↓ instead stashes the current
    draft and blanks the input (a quick "clear but keep it" — ↑ brings the draft back).
    """
    if state.hist_pos >= len(state.history):
        if state.buffer and not state.cleared:
            return replace(state, buffer="", cursor=0, stash=state.buffer, cleared=True)
        return state
    pos = state.hist_pos + 1
    buf = state.history[pos] if pos < len(state.history) else state.stash
    return replace(state, buffer=buf, cursor=len(buf), hist_pos=pos)


def apply_key(state: EditorState, key: str) -> EditorState:
    """Pure transition: given the current state and a key (raw char or logical token), return next."""
    # Any key other than Tab breaks an in-progress completion cycle (Tab is handled by the reader,
    # which calls cycle_completion directly, so it never reaches here).
    if state.comp_prefix is not None:
        state = replace(state, comp_prefix=None)
    # Terminating keys.
    if key == _CTRL_C:
        return replace(state, interrupt=True)
    if key == _CTRL_D:
        # EOF only on an empty buffer (mirrors builtin input()); otherwise ignored.
        return replace(state, eof=True) if not state.buffer else state
    if key in _ENTER:
        return replace(state, submit=True)
    if key == _CTRL_S:
        # Copy the last answer to the clipboard. Non-terminating: readline handles it, resets the
        # flag, and keeps the in-progress buffer intact.
        return replace(state, copy=True)

    # Deletion.
    if key in _BACKSPACE:
        if state.cursor > 0:
            b = state.buffer
            return replace(state, buffer=b[: state.cursor - 1] + b[state.cursor:], cursor=state.cursor - 1)
        return state
    if key == _DELETE:
        if state.cursor < len(state.buffer):
            b = state.buffer
            return replace(state, buffer=b[: state.cursor] + b[state.cursor + 1:])
        return state
    if key == _KILL_TO_START:
        return replace(state, buffer=state.buffer[state.cursor:], cursor=0)
    if key == _KILL_TO_END:
        return replace(state, buffer=state.buffer[: state.cursor])
    if key == _KILL_WORD:
        return _kill_word(state)

    # Cursor movement.
    if key == _LEFT:
        return replace(state, cursor=max(0, state.cursor - 1))
    if key == _RIGHT:
        return replace(state, cursor=min(len(state.buffer), state.cursor + 1))
    if key == _HOME:
        return replace(state, cursor=0)
    if key == _END:
        return replace(state, cursor=len(state.buffer))

    # History recall.
    if key == _UP:
        return _history_prev(state)
    if key == _DOWN:
        return _history_next(state)

    # Editing control bytes mapped to the tokens above (so callers can feed raw Ctrl-* too).
    token = _CONTROL_KEYS.get(key)
    if token is not None:
        return apply_key(state, token)

    # Printable insert (ignore any other control chars).
    if len(key) == 1 and key >= " " and key != "\x7f":
        b = state.buffer
        return replace(state, buffer=b[: state.cursor] + key + b[state.cursor:],
                       cursor=state.cursor + 1, cleared=False)
    return state


def insert_text(state: EditorState, text: str) -> EditorState:
    """Insert a chunk of text at the cursor (bracketed paste). Pure and unit-testable.

    Single-line editor: newlines/tabs collapse to spaces and other control chars are dropped, so a
    multi-line paste becomes one clean line instead of submitting on every embedded newline.
    """
    out = []
    for c in text:
        if c in "\r\n\t":
            out.append(" ")
        elif c >= " " and c != "\x7f":
            out.append(c)
        # else: drop stray control chars
    cleaned = "".join(out)
    if not cleaned:
        return state
    b = state.buffer
    return replace(
        state,
        buffer=b[: state.cursor] + cleaned + b[state.cursor:],
        cursor=state.cursor + len(cleaned),
        cleared=False,
    )


def cycle_completion(state: EditorState, candidates) -> EditorState:
    """Tab: complete/cycle a leading slash-command name. Pure and unit-testable.

    Only acts when the buffer is a bare ``/word`` token (a slash followed by zero or more
    non-space chars, cursor at end). On the first Tab it captures that prefix, finds the matching
    command names in alphabetical order, and shows the first match; each further Tab (with the cycle
    still live) advances to the next match, wrapping around. ``candidates`` are command names
    *without* the leading slash. Any non-Tab key clears the cycle (see :func:`apply_key`).
    """
    buf = state.buffer
    # Must be a single leading slash token with the cursor at the end (no mid-line completion).
    if not buf.startswith("/") or " " in buf or state.cursor != len(buf):
        return replace(state, comp_prefix=None)

    # The prefix to match against is captured on the first Tab of a cycle; on later Tabs we keep the
    # original prefix so cycling stays anchored even though the buffer now holds a full command name.
    prefix = state.comp_prefix if state.comp_prefix is not None else buf[1:]
    matches = sorted(n for n in candidates if n.startswith(prefix))
    if not matches:
        return replace(state, comp_prefix=None)

    if state.comp_prefix is None:
        index = 0  # first Tab → first match
    else:
        index = (state.comp_index + 1) % len(matches)  # subsequent Tab → next match (wraps)
    completed = "/" + matches[index]
    return replace(
        state,
        buffer=completed,
        cursor=len(completed),
        comp_prefix=prefix,
        comp_index=index,
        cleared=False,
    )


# ── Pure wrapping geometry (unit-testable, no TTY) ─────────────────────────────
def wrap_layout(prompt_len: int, buffer: str, cursor: int, cols: int, max_rows: int):
    """Compute how a ``prompt + buffer`` line wraps across rows of width ``cols``.

    Returns ``(window_top, height, caret_line, caret_col)`` where:
      * ``window_top`` — index of the first *visual* line shown (vertical scroll when the line is
        taller than ``max_rows``);
      * ``height`` — number of visual rows to draw (``1..max_rows``);
      * ``caret_line`` — the visual line (absolute) the caret sits on;
      * ``caret_col`` — the 0-based column of the caret within its line.

    Pure: the renderer turns these into absolute cursor moves; kept separate so it is testable.
    """
    cols = max(1, cols)
    max_rows = max(1, max_rows)
    stream_len = prompt_len + len(buffer)
    caret_pos = prompt_len + cursor
    caret_line = caret_pos // cols
    caret_col = caret_pos % cols
    total_lines = max(1, -(-stream_len // cols)) if stream_len else 1
    # If the caret sits exactly at a wrap boundary (end of line == start of the next), show the extra
    # empty line so the caret has somewhere to land.
    total_lines = max(total_lines, caret_line + 1)
    height = min(total_lines, max_rows)
    window_top = 0
    if caret_line >= max_rows:
        window_top = caret_line - max_rows + 1
    window_top = min(window_top, max(0, total_lines - max_rows))
    return window_top, height, caret_line, caret_col



def parse_cursor_report(body: str):
    """``"row;col"`` (the inside of ``ESC[row;colR``) → ``("cursor", row, col)``, or ``None`` if malformed."""
    row, sep, col = body.partition(";")
    if not sep or not row.isdigit() or not col.isdigit():
        return None
    return (_CURSOR, int(row), int(col))


class LineEditor:
    """Reads a line on a fixed bottom row; falls back to builtin ``input()`` when disabled/non-TTY."""

    # How long to wait for the terminal's cursor report before carrying on without it. Local
    # terminals answer within a millisecond; a slow SSH hop is the only thing that gets near this.
    CURSOR_REPORT_TIMEOUT = 0.15

    def __init__(self, enabled: bool = True, on_copy=None, layout=None, on_reflow=None, completer=None,
                 resize=None):
        self.enabled = bool(enabled) and _HAVE_TERMIOS and sys.stdin.isatty() and sys.stdout.isatty()
        self.history: list = []  # submitted lines, oldest→newest (↑/↓ recall)
        self.on_copy = on_copy   # zero-arg callback for Ctrl-S (copies + prints its own feedback)
        self.layout = layout     # shared DockLayout for multi-row growth (None = legacy single row)
        self.on_reflow = on_reflow  # called after layout.input_rows changes (re-establish region+frame)
        self.completer = completer  # zero-arg callable → iterable of slash-command names (no leading /)
        self.resize = resize     # ResizeWatcher: wakes the read loop on SIGWINCH (None = poll on keys)
        self.active = False      # True while readline owns the terminal (the Repl's resize hook checks)
        self._drawn_rows = 1     # visual input-height currently on screen (drives clear-on-shrink)
        self._caret_row = 0      # terminal row the caret was last drawn on (1-based)
        self._caret_col = 0      # 0-based column of the caret as last drawn
        self._caret_offset = 0   # input rows drawn above the caret's row
        self._drawn_cols = 0     # terminal width at the last draw (re-wrap math on a width change)
        self._input_top = 0      # terminal row of the first input row as last drawn (1-based)
        self._anchor = None      # (row, col) where output resumes after the prompt; row may be < 1
        self._pending = deque()  # keys read ahead while waiting for a cursor report
        self._reports = deque()  # what each outstanding ESC[6n was for, oldest first: "anchor"/"caret"


    def readline(self, prompt: str = "› ") -> str:
        """Read one line. Raises ``EOFError`` / ``KeyboardInterrupt`` exactly like ``input()``.

        When the dock is disabled or stdin/stdout is not a TTY, defers to builtin ``input()`` so
        piping and the test suite behave unchanged.
        """
        if not self.enabled:
            return input(color(prompt, "green"))

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        hist = tuple(self.history)
        state = EditorState(history=hist, hist_pos=len(hist))
        if self.layout is not None:
            self.layout.input_rows = 1  # start each line at a single row; it grows as the line wraps
            self._drawn_rows = 1
        self._anchor = None
        self._pending.clear()
        self._reports.clear()
        try:
            tty.setraw(fd)  # raw: deliver Ctrl-C/Ctrl-D as bytes (we translate them ourselves)
            self.active = True
            sys.stdout.write(_PASTE_ON)  # ask the terminal to bracket pasted text
            sys.stdout.write("\0337")  # DECSC: save the in-region cursor to restore afterwards
            # Ask where that anchor is, and wait briefly for the answer before the first paint: if the
            # last turn ended with the cursor inside the dock (a resize while it ran), the transcript's
            # tail must be scrolled up before the input rows are drawn over it.
            self._request_cursor("anchor")
            if self._await_cursor("anchor") and self._anchor_below_region():
                self._repaint(prompt, state)
            else:
                self._render(prompt, state)
            while not state.done:
                key = self._next_key()
                if key is None:  # stream closed → treat as EOF
                    state = replace(state, eof=True)
                    break
                if key == _RESIZE:  # the terminal changed size: repaint everything at the new geometry
                    self._repaint(prompt, state)
                    continue
                if isinstance(key, tuple) and key[0] == _CURSOR:  # a late cursor report
                    if self._note_cursor(key) == "anchor" and self._anchor_below_region():
                        self._repaint(prompt, state)
                    continue
                if isinstance(key, tuple):  # ("paste", text) → bulk insert, never submits
                    state = insert_text(state, key[1])
                elif key == "\x1b":  # lone ESC (no recognised sequence) → ignore
                    continue
                elif key == _TAB:  # slash-command completion (cycle through matches)
                    state = cycle_completion(state, self._candidates())
                else:
                    state = apply_key(state, key)
                if state.copy:  # Ctrl-S: copy the last answer, then carry on editing
                    self._handle_copy()
                    state = replace(state, copy=False)
                if not state.done:
                    self._render(prompt, state)
        finally:
            # Clear the transient input band so the submitted text doesn't linger on the fixed input
            # line(s) while the turn runs (the prompt is echoed into the scroll region separately), and
            # collapse the dock back to a single input row so the turn output regains that space. We
            # clear the WHOLE grown band (pad + input rows + spacer + bar) — not just the input rows —
            # so a grown dock never leaves a stale row behind that would scroll up into the transcript.
            rows = shutil.get_terminal_size((80, 24)).lines
            if self.layout is not None:
                band_top = self.layout.top_pad_row(rows)  # top of the current (possibly grown) band
                for r in range(band_top, rows + 1):
                    sys.stdout.write(f"\033[{max(1, r)};1H\033[2K")
                self.layout.input_rows = 1
                self._drawn_rows = 1
                if self.on_reflow is not None:
                    self.on_reflow()  # re-establish the region + redraw the frame for the 1-row dock
            else:
                inrow = max(1, rows - 1)
                sys.stdout.write(f"\033[{inrow};1H\033[2K")
            sys.stdout.write("\0338")  # DECRC: restore the cursor back into the output region
            sys.stdout.write(_PASTE_OFF)  # stop bracketing paste once we hand control back
            sys.stdout.flush()
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            self.active = False


        if state.interrupt:
            raise KeyboardInterrupt
        if state.eof:
            raise EOFError
        line = state.buffer
        if line and (not self.history or self.history[-1] != line):
            self.history.append(line)  # de-duplicate consecutive repeats
        return line

    # ── input ────────────────────────────────────────────────────────────────────
    def _candidates(self):
        """Return the slash-command names offered for Tab completion (empty when no completer)."""
        if self.completer is None:
            return ()
        try:
            return tuple(self.completer())
        except Exception:
            return ()

    def _next_key(self):
        """The next key: one read ahead while waiting for a cursor report, else a fresh read."""
        if self._pending:
            return self._pending.popleft()
        return self._read_key()

    def _read_key(self):
        """Read one logical key: a decoded char, an ESC-sequence token, a ``("paste", text)`` or
        ``("cursor", row, col)`` event, ``RESIZE`` when the terminal changed size, or ``None`` on EOF.

        Reads straight from the raw fd via ``os.read`` (not ``sys.stdin``) so escape sequences aren't
        stranded in Python's stream buffer where ``select`` can't see them. With a resize watcher the
        read waits on its wake-up pipe too, so a resize repaints at once rather than at the next key.
        """
        fd = sys.stdin.fileno()
        wake = self.resize.fd if self.resize is not None else -1
        while wake >= 0:
            ready, _, _ = select.select([fd, wake], [], [])
            if wake in ready and self.resize.take():
                return _RESIZE
            if fd in ready:
                break
        b = os.read(fd, 1)
        if not b:
            return None
        if b == b"\x1b":
            token = self._read_escape(fd)
            if token == _PASTE_START:
                return (_PASTE, self._read_paste(fd))
            return token or "\x1b"
        return self._decode(fd, b)

    @staticmethod
    def _decode(fd: int, first: bytes) -> str:
        """Decode one (possibly multi-byte UTF-8) character starting with ``first``."""
        lead = first[0]
        if lead < 0x80 or lead < 0xC0:
            extra = 0            # ASCII, or a stray continuation byte (decode leniently)
        elif lead < 0xE0:
            extra = 1
        elif lead < 0xF0:
            extra = 2
        else:
            extra = 3
        buf = bytearray(first)
        for _ in range(extra):
            more = os.read(fd, 1)
            if not more:
                break
            buf += more
        return bytes(buf).decode("utf-8", "replace")

    def _read_escape(self, fd: int):
        """Translate a CSI/SS3 escape sequence (already past ESC) into a logical key token, or None."""

        def more(timeout: float = 0.03) -> str:
            r, _, _ = select.select([fd], [], [], timeout)
            if not r:
                return ""
            b = os.read(fd, 1)
            return b.decode("latin-1") if b else ""

        intro = more()
        if intro not in ("[", "O"):  # not a CSI (ESC[) or SS3 (ESCO) sequence
            return None
        code = more()
        simple = {"A": _UP, "B": _DOWN, "C": _RIGHT, "D": _LEFT, "H": _HOME, "F": _END}
        if code in simple:
            return simple[code]
        if code.isdigit():  # extended form like ESC[3~ (Delete), ESC[1~ (Home), ESC[200~ (paste)
            num = code
            while True:
                nxt = more()
                if nxt in ("", "~", "R"):
                    break
                num += nxt
            if nxt == "R":  # cursor position report: ESC[row;colR (the answer to ESC[6n)
                return parse_cursor_report(num)
            if num == "200":
                return _PASTE_START
            return {"1": _HOME, "7": _HOME, "4": _END, "8": _END, "3": _DELETE}.get(num)
        return None

    # ── cursor reports (where the transcript ends, where the caret went after a resize) ──────────
    def _request_cursor(self, kind: str) -> None:
        """Ask the terminal where the cursor is; the reply is matched to ``kind`` in arrival order."""
        from .resize import CURSOR_REPORT

        self._reports.append(kind)
        sys.stdout.write(CURSOR_REPORT)
        sys.stdout.flush()

    def _note_cursor(self, report) -> str:
        """Record a ``("cursor", row, col)`` reply against the oldest outstanding request; return its kind."""
        kind = self._reports.popleft() if self._reports else "anchor"
        if kind == "anchor":
            self._anchor = (report[1], report[2])
        return kind

    def _await_cursor(self, kind: str, timeout: float = None):
        """Wait (briefly) for the reply to the outstanding ``kind`` request; keys typed meanwhile are
        queued for the main loop. Returns ``(row, col)`` or ``None`` when the terminal did not answer
        in time, in which case all outstanding requests are forgotten, so a late reply can never be
        matched to the wrong question."""
        if timeout is None:
            timeout = self.CURSOR_REPORT_TIMEOUT
        fd = sys.stdin.fileno()
        deadline = time.monotonic() + timeout
        while kind in self._reports:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                break
            key = self._read_key()
            if key is None:
                self._pending.append(None)  # EOF: let the main loop see it after the repaint
                break
            if isinstance(key, tuple) and key[0] == _CURSOR:
                if self._note_cursor(key) == kind:
                    return key[1], key[2]
                continue
            self._pending.append(key)
        self._reports.clear()
        return None

    def _anchor_below_region(self) -> bool:
        """Whether the output anchor sits inside the dock's band (the last turn ended after a resize)."""
        if self._anchor is None or self.layout is None:
            return False
        rows = shutil.get_terminal_size((80, 24)).lines
        return self._anchor[0] > self.layout.region_bottom(rows)

    @staticmethod
    def _read_paste(fd: int) -> str:
        """Collect a bracketed-paste payload up to the ESC[201~ terminator (bounded by a stall)."""
        term = b"\x1b[201~"
        buf = bytearray()
        while True:
            r, _, _ = select.select([fd], [], [], 0.5)
            if not r:
                break  # safety: stop if the terminator never arrives
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            buf += chunk
            idx = buf.find(term)
            if idx != -1:
                del buf[idx:]
                break
        return bytes(buf).decode("utf-8", "replace")

    # ── rendering ────────────────────────────────────────────────────────────────
    def _handle_copy(self) -> None:
        """Run the Ctrl-S copy callback, printing its feedback into the scroll region.

        The input row is transient, so drop back to the saved in-region cursor (DECRC), let the
        callback print a normal line there, then re-save (DECSC) so the next output flows below it.
        The caller re-renders the input row afterwards.
        """
        if self.on_copy is None:
            return
        sys.stdout.write("\0338")  # DECRC: restore the in-region cursor
        try:
            self.on_copy()
        finally:
            sys.stdout.write("\0337")  # DECSC: re-save for the next scroll-region write
            self._request_cursor("anchor")  # the feedback moved the anchor: learn its new row

    def _render(self, prompt: str, state: EditorState) -> None:
        """Draw the prompt + buffer on the fixed input row(s) and place the caret at the edit point."""
        if self.layout is not None:
            self._render_multi(prompt, state)
        else:
            self._render_single(prompt, state)

    def _repaint(self, prompt: str, state: EditorState) -> None:
        """Rebuild the dock after a resize (or when the anchor turned up inside the dock's band).

        The terminal moved the content and the cursor together, so the caret's new row (asked for
        with ``ESC[6n``) says how far the transcript's end moved. From that, :func:`plan_reanchor`
        gives the row where output resumes; if it spilled into the band the whole screen is scrolled
        up first (the region is reset to full height for that), everything below it is blanked, the
        region and the frame are re-established, the anchor is re-saved (DECSC) where the answer will
        print, and the input is drawn at its new place. Emitted as one write so nothing interleaves.
        """
        if self.layout is None:
            self._render(prompt, state)
            return
        from .resize import input_rewrap_rows, plan_reanchor

        size = shutil.get_terminal_size((80, 24))
        rows, cols = size.lines, size.columns
        self._request_cursor("caret")
        caret_now = self._await_cursor("caret")
        caret_row = caret_now[0] if caret_now else min(self._caret_row or rows, rows)
        # The caret's row overstates the transcript's shift by the re-wrap of our own input rows.
        caret_row -= input_rewrap_rows(self._caret_offset, self._caret_col, self._drawn_cols or cols, cols,
                                       caret_now[1] if caret_now else None)
        if self._anchor is not None:
            anchor_row, anchor_col = self._anchor
        else:
            # Unknown anchor: assume the transcript ran right up to the dock (never overwrites output).
            anchor_row, anchor_col = max(1, (self._input_top or rows) - 2), 1
        _, height, _, _ = wrap_layout(len(prompt), state.buffer, state.cursor, cols, self.layout.MAX_INPUT_ROWS)
        self.layout.input_rows = height
        plan = plan_reanchor(anchor_row, self._caret_row or caret_row, caret_row, self.layout.region_bottom(rows))
        out = ["\033[r"]  # full-height region so the scroll below moves the whole screen
        if plan.scroll:
            out.append(f"\033[{rows};1H" + "\n" * plan.scroll)
        out.extend(f"\033[{r};1H\033[2K" for r in range(plan.clear_from, rows + 1))
        sys.stdout.write("".join(out))
        self._drawn_rows = height
        if self.on_reflow is not None:
            self.on_reflow()  # re-establish the region + redraw pad/spacer/bar for the new geometry
        col = anchor_col if plan.anchor_row >= 1 else 1
        sys.stdout.write(f"\033[{plan.cursor_row};{col}H\0337")  # re-save where output resumes
        self._anchor = (plan.anchor_row, col)  # keep the virtual row: a later grow may bring it back
        self._render(prompt, state)

    def _render_single(self, prompt: str, state: EditorState) -> None:
        """Legacy one-row renderer: long lines scroll horizontally to keep the caret visible."""
        size = shutil.get_terminal_size((80, 24))
        rows, cols = size.lines, size.columns
        row = max(1, rows - 1)  # input row sits just above the bottom status bar
        pw = len(prompt)
        avail = max(1, cols - pw - 1)
        # Horizontal scroll window: keep the cursor inside [start, start+avail).
        start = 0 if state.cursor < avail else state.cursor - avail + 1
        visible = state.buffer[start:start + avail]
        curcol = pw + (state.cursor - start) + 1
        sys.stdout.write(
            f"\033[{row};1H\033[2K"        # move to the input row and clear it
            + color(prompt, "green") + visible
            + f"\033[{row};{curcol}H"      # position the caret at the edit column
        )
        self._caret_row = self._input_top = row
        self._caret_col, self._caret_offset, self._drawn_cols = curcol - 1, 0, cols
        sys.stdout.flush()

    def _render_multi(self, prompt: str, state: EditorState) -> None:
        """Multi-row renderer: the input wraps onto new lines and the dock grows/shrinks with it.

        When the visual height changes we update the shared ``layout`` and ask the status bar to
        re-establish the scroll region + redraw the frame (``on_reflow``) before drawing the input
        rows, so growing input never overwrites the bar and shrinking input frees the space again.
        """
        size = shutil.get_terminal_size((80, 24))
        rows, cols = size.lines, size.columns
        pw = len(prompt)
        window_top, height, caret_line, caret_col = wrap_layout(
            pw, state.buffer, state.cursor, cols, self.layout.MAX_INPUT_ROWS
        )

        # If the height changed, clear the old (possibly taller) band, resize the dock, and reflow the
        # frame before we draw. Clearing the union band avoids leaving stale text behind on shrink.
        if height != self._drawn_rows:
            union = max(height, self._drawn_rows)
            band_top = max(1, rows - (union + 3) + 1)  # rule + union + spacer + bar
            for r in range(band_top, rows + 1):
                sys.stdout.write(f"\033[{r};1H\033[K")
            self.layout.input_rows = height
            if self.on_reflow is not None:
                self.on_reflow()  # re-establish region + redraw rule/spacer/bar for the new height
            self._drawn_rows = height

        # Build the wrapped stream (uncolored) and slice the visible window of lines.
        stream = prompt + state.buffer
        top = self.layout.input_top(rows)
        for i in range(height):
            line_idx = window_top + i
            seg = stream[line_idx * cols:(line_idx + 1) * cols]
            if line_idx == 0:
                # First visual line begins with the prompt — colorize just that prefix.
                seg = color(prompt, "green") + seg[pw:]
            sys.stdout.write(f"\033[{top + i};1H\033[2K" + seg)
        caret_row = top + (caret_line - window_top)
        sys.stdout.write(f"\033[{caret_row};{caret_col + 1}H")  # position the caret at the edit point
        self._caret_row, self._input_top = caret_row, top
        self._caret_col, self._caret_offset, self._drawn_cols = caret_col, caret_line - window_top, cols
        sys.stdout.flush()





