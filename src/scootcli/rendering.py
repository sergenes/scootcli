"""Minimal stdlib rendering helpers: ANSI colors + secret redaction. No third-party deps."""

from __future__ import annotations

import difflib
import os
import re
import sys
from typing import List

_COLOR_ENABLED = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

_CODES = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "reverse": "\033[7m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "gray": "\033[90m",
}


def color(text: str, *styles: str) -> str:
    """Wrap ``text`` in the given ANSI styles (no-op when color is disabled)."""
    if not _COLOR_ENABLED or not styles:
        return text
    prefix = "".join(_CODES.get(s, "") for s in styles)
    return f"{prefix}{text}{_CODES['reset']}"


# Redact anything that looks like a token/secret before printing (defense in depth): provider API
# keys (OpenAI ``sk-…``, Anthropic ``sk-ant-…``), GitHub tokens, Bearer values, and JWTs. Bearer values
# may contain separators, so the whole non-whitespace run is masked.
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{12,}|gh[opsu]_[A-Za-z0-9]{8,}|Bearer\s+\S+|tid=\S+|eyJ[A-Za-z0-9._\-]{20,})"
)


def redact(text: str) -> str:
    """Mask token-like substrings so secrets never reach the terminal/logs."""
    return _SECRET_RE.sub("<redacted>", text)


# Terminal control sequences and C0 control bytes in text that came from a file, a tool, a hook, or
# the model. Left intact, they could recolour the screen, move the cursor, hide an approval preview,
# or trigger a terminal escape feature. Complete ANSI/OSC sequences are removed first; then every
# remaining control byte, a lone or split ESC included, so a sequence split across stream chunks is
# neutralised too. Tab and newline are kept.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[@-Z\\-_]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # C0 except \t (09) and \n (0a); drops CR and ESC


def strip_controls(text: str) -> str:
    """Neutralise terminal control sequences in untrusted text, keeping tabs and newlines.

    Use it on anything that originates outside scoot's own UI (file diffs, tool output, model text,
    replayed transcript) before it reaches the terminal, so the text cannot drive the terminal.
    """
    if not text:
        return text
    return _CTRL_RE.sub("", _ANSI_RE.sub("", text))


_PROMPT_GUTTER = "❯ "  # ❯


def user_band(text: str, width: int) -> str:
    """Render a submitted user prompt as a full-width reverse-video band, so it reads as clearly
    distinct from the assistant's answer below it.

    Reverse video (SGR 7) swaps the terminal's own foreground and background, so the band fits any
    theme, light or dark, with no hard-coded colour. ``width`` is passed in by the caller (the current
    terminal width), so a resize is reflected on the next prompt; text already in the scrollback keeps
    its old width, as terminal history cannot reflow. Each physical line is padded with spaces to the
    full width so the band spans the screen; a long or multi-line prompt becomes several stacked bands.

    Falls back to a plain guttered line when colour is off (not a TTY, or ``NO_COLOR``). Width is
    measured in characters, which is exact for the usual ASCII prompt; a wide glyph (CJK, emoji) may
    make one line's band a cell short or long, which is cosmetic only.
    """
    if not _COLOR_ENABLED:
        return _PROMPT_GUTTER + text
    on, off = _CODES["reverse"], _CODES["reset"]
    width = max(int(width) or 0, len(_PROMPT_GUTTER) + 1)
    lines: List[str] = []
    for i, para in enumerate(text.split("\n")):
        body = (_PROMPT_GUTTER if i == 0 else "  ") + para
        chunks = [body[j:j + width] for j in range(0, len(body), width)] or [body]
        for chunk in chunks:
            lines.append(f"{on}{chunk}{' ' * (width - len(chunk))}{off}")
    return "\n".join(lines)


# Replay-item fields that must be stored exactly as received: encrypted or signed by the provider,
# and rejected on the next request if a single byte changes. Never redacted.
_OPAQUE_KEYS = frozenset({"signature", "encrypted_content", "thinking", "summary", "data"})


def redact_value(value, _key: str = ""):
    """``redact`` applied through dicts and lists: every string leaf is masked, except under the
    opaque keys above. A message's ``content``, its ``tool_calls[*].function.arguments``, and the text
    or tool-input copies inside ``provider_items`` all pass through here before a session is saved."""
    if _key in _OPAQUE_KEYS:
        return value
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    return value


def eprint(*args, **kwargs) -> None:
    """Print to stderr."""
    print(*args, file=sys.stderr, **kwargs)


def diff_stats(old: str, new: str) -> "tuple[int, int]":
    """Return ``(added, removed)`` line counts between two texts."""
    added = removed = 0
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm=""):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def unified_diff(old: str, new: str, path: str = "", context: int = 3) -> str:
    """Return a colorized unified diff between ``old`` and ``new``."""
    from_name = f"a/{path}" if path else "old"
    to_name = f"b/{path}" if path else "new"
    lines = difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile=from_name, tofile=to_name, lineterm="", n=context,
    )
    out = []
    for raw in lines:
        line = strip_controls(raw)  # the +/-/@@ markers are scoot's; the line content is from a file
        if line.startswith("+++") or line.startswith("---"):
            out.append(color(line, "bold"))
        elif line.startswith("@@"):
            out.append(color(line, "cyan"))
        elif line.startswith("+"):
            out.append(color(line, "green"))
        elif line.startswith("-"):
            out.append(color(line, "red"))
        else:
            out.append(color(line, "gray"))
    return "\n".join(out)



