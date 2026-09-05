"""Minimal stdlib rendering helpers: ANSI colors + secret redaction. No third-party deps."""

from __future__ import annotations

import difflib
import os
import re
import sys

_COLOR_ENABLED = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

_CODES = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
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
    for line in lines:
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



