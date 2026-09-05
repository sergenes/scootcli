"""Live status region: a single-line spinner + activity message (PLAN §6, §10).

Stdlib only — uses carriage-return redraws and ANSI. Runs in its own thread so the message keeps
animating while the model call / tool runs. Auto-disables when stdout is not a TTY.
"""

from __future__ import annotations

import sys
import threading
import time

from .rendering import color

_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_CLEAR_LINE = "\r\033[K"


class Status:
    """A threaded spinner with an updatable message and a persistent step hint."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled and sys.stdout.isatty()
        self._message = ""
        self._hint = ""
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def start(self, message: str = "thinking…", hint: str = "esc to stop") -> None:
        self._message = message
        self._hint = hint
        if not self.enabled:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def update(self, message: str, hint: str = None) -> None:
        with self._lock:
            self._message = message
            if hint is not None:
                self._hint = hint

    def _spin(self) -> None:
        i = 0
        while not self._stop.is_set():
            with self._lock:
                message, hint = self._message, self._hint
            frame = color(_FRAMES[i % len(_FRAMES)], "cyan")
            tail = color(f" · {hint}", "gray") if hint else ""
            sys.stdout.write(f"{_CLEAR_LINE}  {frame} {message}{tail}")
            sys.stdout.flush()
            i += 1
            time.sleep(0.08)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.3)
            self._thread = None
        if self.enabled:
            sys.stdout.write(_CLEAR_LINE)
            sys.stdout.flush()

    def line(self, text: str) -> None:
        """Print a permanent line, clearing the spinner first so it doesn't get mangled."""
        if self.enabled:
            sys.stdout.write(_CLEAR_LINE)
        print(text)

