"""Persistent bottom status bar for the REPL (stdlib-only, TTY-only).

Uses a DECSTBM scroll region so the last terminal row stays pinned while normal output scrolls above
it. The bar shows the signed-in user, session/folder, model, approval mode, and tokens spent. It is a
no-op when stdout is not a TTY or when disabled (``SCOOT_PANEL=false`` / ``/panel``).
"""

from __future__ import annotations

import shutil
import sys
from typing import Optional


def _fmt_tokens(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


class DockLayout:
    """Shared bottom-dock geometry between the :class:`StatusBar` and the ``LineEditor``.

    The dock reserves a band of rows at the bottom of the terminal, from the top down:

        top pad (1, blank)  ·  input block (``input_rows``, grows as the line wraps)  ·  spacer (1)  ·  bar (1)

    ``input_rows`` is owned by the line editor (it grows/shrinks as the typed line wraps) and read by
    the status bar when it (re)establishes the DECSTBM scroll region, so the two never disagree. The
    separation above/below the input is provided by **blank pad rows** (not drawn rules): blanks are
    immune to terminal-resize width changes and can never leave a smeared artifact in the transcript.
    All row math lives here so both components compute identical positions.
    """

    MAX_INPUT_ROWS = 6  # cap the growth; beyond this the input scrolls vertically within the band

    def __init__(self) -> None:
        self.input_rows = 1

    def reserved(self) -> int:
        # top pad + input block + spacer + bar
        return 1 + self.input_rows + 1 + 1

    def region_bottom(self, rows: int) -> int:
        return max(1, rows - self.reserved())

    def top_pad_row(self, rows: int) -> int:
        return self.region_bottom(rows) + 1

    def input_top(self, rows: int) -> int:
        return self.top_pad_row(rows) + 1

    def input_bottom(self, rows: int) -> int:
        return self.input_top(rows) + self.input_rows - 1

    def spacer_row(self, rows: int) -> int:
        return self.input_bottom(rows) + 1

    def bar_row(self, rows: int) -> int:
        return rows


def build_status_text(session, user: Optional[str] = None) -> str:
    """Compose the status-bar text from the live session (pure; unit-testable)."""
    import os

    folder = os.path.basename(str(session.config.root).rstrip("/")) or str(session.config.root)
    short_id = session.id.split("-")[-1] if getattr(session, "id", "") else "?"
    model = session.active_model or session.resolved_model()
    if session.model.lower() == "auto":
        model += " (auto)"
    if not getattr(session, "provider_ready", True):
        model = "none · run /auth"
        user = "not set up"
    spent = session.total_prompt + session.total_completion
    ctx = int((getattr(session, "last_usage", None) or {}).get("prompt_tokens", 0) or 0)
    if not ctx:
        try:
            from .context import estimate_messages_tokens

            ctx = estimate_messages_tokens(getattr(session, "messages", None))
        except Exception:
            ctx = 0
    # Context as a share of the auto-compact threshold — a heads-up that a /compact is coming. Warn
    # (△) once we cross 80% so the user can /compact on their own terms first.
    limit = int(getattr(getattr(session, "config", None), "compact_at", 0) or 0)
    ctx_seg = f"▤ ctx {_fmt_tokens(ctx)}"
    if limit > 0:
        pct = min(999, int(round(ctx * 100 / limit)))
        ctx_seg = f"{'△' if pct >= 80 else '▤'} ctx {_fmt_tokens(ctx)} ({pct}%)"
    ctx_seg += f" · Σ {_fmt_tokens(spent)} tok"
    segments = [
        f"⏻ {user}" if user else "⏻ signed in",
        f"⌂ {folder}·{short_id}",
        f"⚙ {model}",
        f"◈ {session.approval_mode}",
        ctx_seg,
    ]
    msgs = getattr(session, "messages", None)
    if msgs:
        segments.append(f"✉ {len(msgs)}")
    if getattr(session, "worktree", None) is not None:
        segments.append(f"⑂ {session.worktree.branch}")
    plan = getattr(session, "plan", None)
    if plan:
        done = sum(1 for s in plan if s.get("status") == "completed")
        segments.append(f"◇ {done}/{len(plan)}")
    err = getattr(session, "last_error", None)
    if err:
        segments.append(f"⚠ {err}")
    newer = getattr(session, "update_available", "")
    if newer:
        segments.append(f"⬆ {newer}")
    # The mascot's face leads the bar; its eyes follow the turn (idle / thinking / stopped).
    if getattr(getattr(session, "config", None), "logo", True):
        from .logo import face

        segments.insert(0, face(getattr(session, "mascot_state", "idle")))
    return "  │  ".join(segments)


class StatusBar:
    """A pinned bottom bar. Safe no-op when not a TTY or disabled.

    When ``reserve_input`` is set (the REPL "dock", PLAN §19), rows are reserved just above the bar for
    a fixed input line owned by :class:`~scootcli.lineeditor.LineEditor`. With a :class:`DockLayout` the
    input can span **multiple rows** (a wrapping line) framed by blank pad rows above and below; the bar
    reads ``layout.input_rows`` when it (re)establishes the scroll region, and blanks the pad/spacer and
    draws the bar (the input rows themselves are owned by the editor). :meth:`reflow` lets the editor
    grow/shrink that region mid-edit without disturbing its own saved cursor.
    """

    def __init__(self, enabled: bool = True, reserve_input: bool = False, layout=None):
        self.enabled = enabled and sys.stdout.isatty()
        self.reserve_input = reserve_input
        self.layout = layout  # DockLayout when the multi-row dock is active; None = legacy single row
        self._installed = False
        self._rows = 0
        self._reserved_cache = -1
        self._text = ""

    # ── lifecycle ────────────────────────────────────────────────────────────────
    def install(self) -> None:
        if not self.enabled or self._installed:
            return
        self._rows = self._term_rows()
        # Reserve the bottom rows: a blank spacer + [optional input row] + the bar. The scroll region
        # is 1..region_bottom, so output never butts up against (or overwrites) the reserved rows. Home
        # the cursor so whatever prints next (the banner) starts at the top and flows down.
        sys.stdout.write(f"\033[1;{self._region_bottom(self._rows)}r")
        sys.stdout.write("\033[1;1H")
        sys.stdout.flush()
        self._installed = True

    def render(self, text: Optional[str] = None) -> None:
        """Redraw the bar (recomputes size so it self-heals after a resize)."""
        if not self.enabled:
            return
        if text is not None:
            self._text = text
        rows = self._term_rows()
        reserved = self._reserved()
        sys.stdout.write("\0337")  # save cursor + attrs (DECSC)
        if not self._installed or rows != self._rows or reserved != self._reserved_cache:
            self._rows = rows
            self._reserved_cache = reserved
            sys.stdout.write(f"\033[1;{self._region_bottom(rows)}r")  # (re)establish scroll region
            self._installed = True
        self._draw_frame(rows)
        sys.stdout.write("\0338")  # restore cursor (DECRC)
        sys.stdout.flush()

    def reflow(self) -> None:
        """Re-establish the scroll region + redraw the frame for the current ``layout.input_rows``.

        Unlike :meth:`render`, this does **not** use the DECSC/DECRC save slot — it is called by the
        line editor *while editing*, which owns that slot for its own final cursor restore. The caller
        repositions the caret afterwards, so we may leave the cursor anywhere.
        """
        if not self.enabled:
            return
        rows = self._term_rows()
        self._rows = rows
        self._reserved_cache = self._reserved()
        self._installed = True
        sys.stdout.write(f"\033[1;{self._region_bottom(rows)}r")  # (re)establish for the new height
        self._draw_frame(rows)
        # no flush — the editor flushes after drawing the input rows + caret

    def set_text(self, text: str) -> None:
        """Update the cached bar text without emitting anything (used before :meth:`reflow`)."""
        self._text = text


    def _draw_frame(self, rows: int) -> None:
        """Draw the non-input reserved rows: the top pad (if docked), the spacer, and the bar.

        The pad + spacer are kept **blank** (just erased) rather than drawn with rule glyphs, so they
        are immune to resize width changes and never leave an artifact when the region scrolls.
        """
        cols = self._term_cols()
        bar = self._text[: cols - 2]
        if self.layout is not None:
            sys.stdout.write(f"\033[{self.layout.top_pad_row(rows)};1H\033[K")  # blank pad above input
        # Keep the spacer row blank (the input row(s), if reserved, are owned by the editor — never
        # touched here), then draw the bar on the fixed bottom row in reverse video.
        sys.stdout.write(f"\033[{self._spacer_row(rows)};1H\033[K")
        sys.stdout.write(f"\033[{rows};1H\033[K\033[7m {bar} \033[0m")

    def remove(self) -> None:
        if not self.enabled or not self._installed:
            return
        rows = self._term_rows()
        sys.stdout.write("\033[r")  # reset scroll region to full screen
        # Clear the whole reserved band (pad + input rows + spacer + bar) so nothing lingers.
        if self.layout is not None:
            top = self.layout.top_pad_row(rows)
        elif self.reserve_input:
            top = rows - 2  # legacy: spacer, input, bar
        else:
            top = rows - 1  # spacer, bar
        for r in range(top, rows + 1):
            sys.stdout.write(f"\033[{r};1H\033[K")
        sys.stdout.flush()
        self._installed = False


    def toggle(self) -> bool:
        """Flip enabled state (install/remove accordingly). Returns the new state."""
        if self._installed:
            self.remove()
            self.enabled = False
        else:
            self.enabled = sys.stdout.isatty()
            # render() re-establishes the scroll region inside a cursor save/restore, so re-enabling
            # mid-session doesn't jump the cursor the way install() (which homes it) would.
            self.render(self._text)
        return self.enabled

    # ── internals ────────────────────────────────────────────────────────────────
    def _reserved(self) -> int:
        # Rows kept out of the scroll region. With a DockLayout the input block grows, so defer to it
        # (rule + input_rows + spacer + bar). Legacy paths: spacer + bar, plus one input row if docked.
        if self.layout is not None:
            return self.layout.reserved()
        return 3 if self.reserve_input else 2

    def _region_bottom(self, rows: int) -> int:
        # Bottom row of the scroll region (guard tiny terminals).
        if self.layout is not None:
            return self.layout.region_bottom(rows)
        return max(1, rows - self._reserved())

    def _spacer_row(self, rows: int) -> int:
        # The blank separator sits just below the input block (or the scroll region when not docked).
        if self.layout is not None:
            return self.layout.spacer_row(rows)
        return self._region_bottom(rows) + 1

    @staticmethod
    def _term_rows() -> int:
        return shutil.get_terminal_size((80, 24)).lines

    @staticmethod
    def _term_cols() -> int:
        return shutil.get_terminal_size((80, 24)).columns



