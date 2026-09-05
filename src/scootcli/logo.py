"""scoot's mascot: one square-head robot on a kick scooter, drawn once and reused everywhere.

Every variant below is the same character in the same pose (one arm on the handlebar, standing on
the deck, two wheels) so the mascot stays recognisable across the README, the launch banner, the
status bar and ``/help``. The eyes are the only moving part: they track what the agent is doing.

Variants:
  * ``banner``  - box-drawing, 5 rows: the REPL launch banner.
  * ``lean``    - leaning forward with speed lines: ``/logo`` and the README on unicode terminals.
  * ``micro``   - 3 rows: ``/help`` header and other compact spots.
  * ``ascii``   - plain ASCII: renders in every font (README fallback).

Nothing here prints or colours; callers decide where the art goes and how it is styled.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

NAME = "scoot"
LABEL_EMOJI = "🛴"  # U+1F6F4 kick scooter
LABEL_PLAIN = "⏺"

STATES = ("idle", "thinking", "stopped")

# Eye pairs per state, for the wide (3-cell) face used by banner / lean.
EYES: Dict[str, str] = {"idle": "o o", "thinking": "> >", "stopped": "- -"}

_ART: Dict[str, Tuple[str, ...]] = {
    "banner": (
        "       ╭───╮",
        "       │{eyes}│",
        "    T──┤───┤",
        "    │  ╰┬─┬╯",
        "  (o)═══╧═╧═(o)",
    ),
    "lean": (
        "          ╭───╮",
        "        ─ │{eyes}│",
        "       ─T─┤───┤",
        "        │ ╰┬─┬╯",
        "      (o)══╧═╧══(o)",
    ),
    "micro": (
        "       ╭{eyes}╮",
        "     T─┤   │",
        " (o)═╧═══════(o)",
    ),
    "ascii": (
        "       .---.",
        "       |{eyes}|",
        "    T__|---|",
        "    |  |_|_|",
        "  (o)=======(o)",
    ),
}

VARIANTS = tuple(_ART)


def eyes(state: str = "idle", variant: str = "banner") -> str:
    """The eye glyphs for ``state``; ``micro``/``ascii`` join them with ``_`` (``o_o``)."""
    pair = EYES.get(state, EYES["idle"])
    return pair.replace(" ", "_") if variant in ("micro", "ascii") else pair


def mascot(state: str = "idle", variant: str = "banner") -> List[str]:
    """Rows of the mascot, right-padded to a common width so they compose side by side."""
    rows = [row.format(eyes=eyes(state, variant)) for row in _ART[variant]]
    width = max(len(r) for r in rows)
    return [r.ljust(width) for r in rows]


def face(state: str = "idle") -> str:
    """One-cell-high face for the status bar: ``╭o o╮`` idle, ``╭> >╮`` thinking, ``╭- -╮`` stopped."""
    return f"╭{eyes(state)}╮"


def label(emoji: bool = True) -> str:
    """The assistant's transcript lead-in: ``🛴 scoot``, or ``⏺ scoot`` where emoji are unwanted."""
    return f"{LABEL_EMOJI if emoji else LABEL_PLAIN} {NAME}"


def tilde(path) -> str:
    """Show the home directory as ``~`` (banner and status text stay short)."""
    import os

    p = str(path)
    home = os.path.expanduser("~")
    if home and (p == home or p.startswith(home + os.sep)):
        return "~" + p[len(home):]
    return p


def fit(text: str, width: int) -> str:
    """Shorten ``text`` to ``width`` cells with a middle ellipsis so both ends stay readable."""
    if width <= 0 or len(text) <= width:
        return text
    if width < 5:
        return text[:width]
    head = (width - 1) // 2
    tail = width - 1 - head
    return text[:head] + "…" + text[-tail:]


def compose(art: Iterable[str], text: Iterable[str], gap: int = 3) -> List[str]:
    """Place ``text`` rows to the right of ``art`` rows (both top-aligned; the shorter side is padded)."""
    art, text = list(art), list(text)
    width = max((len(r) for r in art), default=0)
    blank = " " * width
    rows = []
    for i in range(max(len(art), len(text))):
        left = art[i] if i < len(art) else blank
        right = text[i] if i < len(text) else ""
        rows.append((left + " " * gap + right).rstrip())
    return rows
