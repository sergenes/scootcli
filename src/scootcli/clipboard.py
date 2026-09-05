"""Copy text to the system clipboard using stdlib only (PLAN §3 / M21).

Strategy (first that works wins):
  1. A platform clipboard binary via ``subprocess`` — ``pbcopy`` (macOS), ``wl-copy`` / ``xclip`` /
     ``xsel`` (Linux), ``clip.exe`` (WSL/Windows).
  2. An **OSC 52** terminal escape written to the TTY (``\\033]52;c;<base64>\\a``) — works over SSH with
     no helper binary, when the terminal supports it.

Everything degrades gracefully: :func:`copy_to_clipboard` returns ``False`` when no path works, so the
caller can show a friendly note instead of raising.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import sys

# (binary name, extra args) in priority order.
_BINARIES = (
    ("pbcopy", ()),                              # macOS
    ("wl-copy", ()),                             # Wayland
    ("xclip", ("-selection", "clipboard")),      # X11
    ("xsel", ("--clipboard", "--input")),        # X11
    ("clip.exe", ()),                            # WSL / Windows
)

# Many terminals cap the OSC 52 payload; bail on very large content rather than corrupt the clipboard.
_OSC52_MAX_B64 = 100_000


def _via_binary(text: str) -> bool:
    """Pipe ``text`` into the first available clipboard binary on ``PATH``."""
    for name, args in _BINARIES:
        path = shutil.which(name)
        if not path:
            continue
        try:
            subprocess.run(
                [path, *args],
                input=text.encode("utf-8"),
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            continue  # try the next backend
    return False


def _via_osc52(text: str) -> bool:
    """Set the clipboard via an OSC 52 escape (SSH-friendly; needs terminal support)."""
    if not sys.stdout.isatty():
        return False
    try:
        b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    except Exception:
        return False
    if len(b64) > _OSC52_MAX_B64:
        return False
    try:
        sys.stdout.write(f"\033]52;c;{b64}\a")
        sys.stdout.flush()
    except Exception:
        return False
    return True


def copy_to_clipboard(text: str) -> bool:
    """Copy ``text`` to the clipboard. Returns ``True`` on success, ``False`` if no method worked."""
    if not text:
        return False
    return _via_binary(text) or _via_osc52(text)


def copy_session_output(session) -> "tuple[bool, str]":
    """Copy the session's last assistant answer. Returns ``(ok, human_message)``.

    Shared by the ``/c`` slash-command and the in-editor Ctrl-S hotkey so both behave identically.
    """
    text = (getattr(session, "last_output", "") or "").strip()
    if not text:
        return False, "nothing to copy yet — ask me something first."
    if copy_to_clipboard(text):
        return True, f"copied last answer ({len(text)} chars) to the clipboard."
    return False, "couldn't reach a clipboard (no pbcopy/xclip/OSC-52 support)."

