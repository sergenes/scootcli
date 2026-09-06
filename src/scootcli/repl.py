"""Persistent REPL with live status and ESC-to-interrupt (PLAN §6).

Lifecycle: banner → wait for a prompt → run the turn (spinner + ESC-interrupt) → print result →
repeat. Slash-commands (``/help``, ``/status``, …) are handled locally by the ``commands`` registry.

M2 runs a single chat turn per prompt (no tools yet — the agentic loop arrives in M4), but the
worker-thread + cancel-event machinery is exactly what the tool loop will reuse.
"""

from __future__ import annotations

import re
import shutil
import sys
import threading
import time
from contextlib import contextmanager
from typing import List

from . import commands
from .agent import Agent, AgentOutcome
from .approvals import MODES, request_approval
from .providers import ProviderPool
from .config import Config
from .keys import InterruptibleSection
from .lineeditor import LineEditor
from . import logo
from .panel import StatusBar, build_status_text
from .prompts import CHAT_SYSTEM_PROMPT
from .rendering import color, eprint, redact
from .status import Status
from .tools.base import ToolResult


_CODES_RESET = "\033[0m" if sys.stdout.isatty() else ""


def _assistant_label(emoji: bool = True) -> str:
    """Lead-in gutter marking the start of the assistant's final answer (🛴 scoot, or ⏺ scoot)."""
    return color(logo.label(emoji), "cyan", "bold")


# A slash-command is "/" + a command-ish name (letters/digits/-/_), then a space or end-of-line.
# This deliberately does NOT match a dropped file path like "/Users/me/shot.png" (which has more "/").
_COMMAND_RE = re.compile(r"^/[A-Za-z][A-Za-z0-9_-]*(?:\s|$)")


def _is_slash_command(line: str) -> bool:
    return bool(_COMMAND_RE.match(line))



def _user_echo(text: str) -> str:
    """Bolded, colour-guttered echo of a submitted user prompt (dock mode only)."""
    return color("❯ ", "green", "bold") + color(text, "bold")



class ReplSession:
    """Holds conversation + token accounting for one interactive session."""

    def __init__(self, config: Config, provider: Optional[ProviderPool]):
        self.config = config
        self.provider = provider  # the ProviderPool (or None in tests)
        self.model = config.model  # user preference: "auto" or a concrete id
        self.active_model = self.resolved_model()  # last concrete model actually used
        self.approval_mode = config.approval if config.approval in MODES else "always"
        self.messages: List[dict] = []
        self.last_usage: dict = {}
        self.total_prompt = 0
        self.total_completion = 0
        self._available: List[str] = None
        self.bad_models: set = set()  # models that returned "unavailable" this session
        self.worktree = None  # active git worktree (M5.1b), if any
        self.trusted_tools: set = set()  # tools the user auto-approved for this session ([t])
        self.plan: List[dict] = []  # latest update_plan steps ({step, status}); shown in the UI
        self.last_error = ""  # short label of the most recent turn failure (shown in the bar until next turn)
        self.last_output = ""  # plain-text of the last assistant answer (for /c + Ctrl-S copy)
        self.mascot_state = "idle"  # drives the mascot's eyes in the status bar: idle | thinking | stopped
        self.usage_by_model: dict = {}  # qualified model -> {"prompt": n, "completion": n, "calls": n}
        self.route_reason = ""  # why the router picked the active model (auto only)
        # Persistence (auto-save each turn; resume via --continue/--resume).
        from . import sessions

        self.id = sessions.new_session_id()
        self.created = time.time()
        self.resumed = False

    def resolved_model(self) -> str:
        """Qualified ``provider/model`` for the current preference (``auto`` → the default fallback)."""
        from .providers.base import qualify
        from .providers.registry import default_provider_name, fallback_model

        from .config import is_model_alias

        if is_model_alias(self.model):
            return fallback_model(self.config)
        return qualify(default_provider_name(self.config), self.model)

    def to_record(self):
        """Snapshot the session as a persistable record."""
        from .sessions import SessionRecord

        return SessionRecord(
            id=self.id,
            root=str(self.config.root),
            created=self.created,
            updated=time.time(),
            model=self.model,
            active_model=self.active_model,
            approval_mode=self.approval_mode,
            total_prompt=self.total_prompt,
            total_completion=self.total_completion,
            messages=self.messages,
        )

    def apply_record(self, record) -> None:
        """Load a saved conversation + settings into this live session.

        The saved model is adopted only when this run set nothing explicit (the preference is still
        the ``default`` alias) and the saved model's provider is configured; an explicit ``--model`` /
        ``SCOOT_MODEL`` / saved preference for this run always wins over what the session recorded.
        """
        self.id = record.id
        self.created = record.created or self.created
        if self._may_adopt_saved_model(record.model, record.active_model):
            self.model = record.model or self.model
            self.active_model = record.active_model or self.active_model
        if record.approval_mode in MODES:
            self.approval_mode = record.approval_mode
        self.total_prompt = record.total_prompt
        self.total_completion = record.total_completion
        self.messages = list(record.messages)
        self.resumed = True

    def _may_adopt_saved_model(self, model: str, active_model: str) -> bool:
        from .config import DEFAULT_MODEL_ALIAS, is_model_alias
        from .providers.base import split_model_id

        if (self.model or "").strip().lower() != DEFAULT_MODEL_ALIAS:
            return False  # this run chose a model (flag, env, or saved preference): keep it
        candidate = active_model if is_model_alias(model) else model
        head, _ = split_model_id(candidate)
        if not head:
            return True
        try:
            from .auth import is_configured
            from .providers import registry

            spec = registry.get(head)
            return spec is not None and is_configured(spec)
        except Exception:
            return True

    def autosave(self) -> None:
        """Persist the session after a turn (best-effort; never raises).

        A conversation with no assistant reply yet (the first turn failed before the model answered)
        is not worth resuming, so it is not written.
        """
        if not any(m.get("role") == "assistant" for m in self.messages):
            return
        try:
            from . import sessions

            sessions.save(self.to_record())
        except Exception:
            pass

    def available_models(self) -> List[str]:
        """Chat-capable, qualified model ids from the live lists (cached; safe fallback on failure)."""
        if self._available is None:
            from .models import is_chat_model

            try:
                data = self.provider.list_models()
                self._available = [m.id for m in data if is_chat_model(m.raw)] or [self.resolved_model()]
            except Exception:
                self._available = [self.resolved_model()]
        return self._available

    def reset(self) -> None:
        from . import sessions

        self.messages.clear()
        self.last_usage = {}
        self.plan = []  # drop the progress checklist so the bar's ◇ segment clears too
        # Start a brand-new session id so a fresh conversation gets its own saved file.
        self.id = sessions.new_session_id()
        self.created = time.time()
        self.resumed = False

    def account(self, usage: dict, model: str = "") -> None:
        self.last_usage = usage or {}
        prompt = int((usage or {}).get("prompt_tokens", 0) or 0)
        completion = int((usage or {}).get("completion_tokens", 0) or 0)
        self.total_prompt += prompt
        self.total_completion += completion
        key = model or getattr(self, "active_model", "") or "?"
        slot = self.usage_by_model.setdefault(key, {"prompt": 0, "completion": 0, "calls": 0})
        slot["prompt"] += prompt
        slot["completion"] += completion
        slot["calls"] += 1

    def full_messages(self) -> List[dict]:
        return [{"role": "system", "content": CHAT_SYSTEM_PROMPT}, *self.messages]


class _StreamPrinter:
    """Prints streamed tokens live, holding back a small tail to suppress a trailing ``DONE``.

    Content streams through a short buffer so a final ``DONE`` sentinel (emitted by the model per the
    agent system prompt) never flashes on screen. The holdback is a few characters, so latency is
    imperceptible.
    """

    _HOLDBACK = 8

    def __init__(self, status: Status, labels: bool = True, on_label=None):
        self._status = status
        self._labels = labels
        self._on_label = on_label  # called to emit the (once-per-turn) ⏺ scoot lead-in
        self.started = False
        self._buf = ""

    def delta(self, text: str) -> None:
        if not text:
            return
        if not self.started:
            self._status.stop()  # first token: clear the spinner line
            if self._labels and sys.stdout.isatty():
                if self._on_label is not None:
                    self._on_label()  # print ⏺ scoot once per turn (no-op if already shown)
                else:
                    print(_assistant_label())  # mark the start of the answer (kept out of piped output)
            self.started = True
        self._buf += text
        if len(self._buf) > self._HOLDBACK:
            emit, self._buf = self._buf[: -self._HOLDBACK], self._buf[-self._HOLDBACK:]
            self._write(emit)

    def close(self) -> None:
        if not self.started:
            return
        tail = self._buf.rstrip()
        if tail.endswith("DONE"):
            tail = tail[:-4].rstrip()
        self._write(tail)
        self._write("\n")

    @staticmethod
    def _write(text: str) -> None:
        if text:
            sys.stdout.write(text)
            sys.stdout.flush()


class ReplUI:
    """Supplies terminal affordances (spinner, ESC, approvals) to the agent loop.

    Step-by-step narration, tool calls, and tool results are rendered as **transient** single-line
    status (each overwriting the previous, like the spinner) so the scrollback stays clean; only
    permanent output — the progress plan and the final answer — is committed with a newline. On a
    non-TTY (piped/`--json`) these ephemeral lines are dropped entirely, keeping stdout for the
    answer alone.

    ``verbosity`` regulates how much of a turn's "thinking" shows in the feed (set by ``/verbosity``):

      * ``"full"`` (default) — reasoning narration is shown and each tool-in-use line is kept in the
        feed as a log of what ran.
      * ``"compact"`` — reasoning narration is shown; the tool-in-use line is a single transient row
        above the spinner that is removed once the tool finishes (nothing accumulates).
      * ``"quiet"`` — reasoning narration is suppressed entirely; tools render like ``compact``.
    """

    LEVELS = ("full", "compact", "quiet")

    def __init__(self, labels: bool = True, verbosity: str = "full", emoji: bool = True):
        self.labels = labels
        self.emoji = emoji  # 🛴 label, or ⏺ when the terminal has no emoji font
        self.verbosity = verbosity if verbosity in self.LEVELS else "full"
        self._tty = sys.stdout.isatty()
        self._transient = False  # a transient line is currently on screen (no trailing newline)
        self._labelled = False   # the ⏺ scoot lead-in has been printed for the current turn
        self._tool_line = None   # pending tool-in-use line, pinned above the spinner by activity()

    def begin_turn(self) -> None:
        """Reset per-turn state so the ⏺ scoot label prints once at the next assistant output."""
        self._labelled = False

    def label_once(self) -> None:
        """Print the ⏺ scoot lead-in the first time the assistant speaks in a turn (TTY only).

        Subsequent streamed messages / narration in the same turn print underneath without repeating
        the gutter, so a multi-step turn reads as one labelled block.
        """
        if self._labelled or not self.labels or not self._tty:
            return
        print(_assistant_label(self.emoji))
        self._labelled = True

    # ── transient single-line region ─────────────────────────────────────────────
    def _ephemeral(self, text: str) -> None:
        """Draw ``text`` on the current line, overwriting whatever was there (no newline)."""
        if not self._tty:
            return
        width = max(1, shutil.get_terminal_size((80, 24)).columns - 1)
        clipped = self._clip(text, width)
        sys.stdout.write(f"\r\033[K{clipped}")
        sys.stdout.flush()
        self._transient = True

    def _commit_line(self) -> None:
        """Clear any transient line so the next permanent print starts on a clean row."""
        if self._transient and self._tty:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()
        self._transient = False

    @staticmethod
    def _clip(text: str, width: int) -> str:
        """Truncate to ``width`` *visible* columns, ignoring ANSI escapes, keeping styling intact."""
        visible = 0
        out = []
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\033":  # copy an escape sequence verbatim (doesn't consume width)
                j = text.find("m", i)
                if j == -1:
                    break
                out.append(text[i:j + 1])
                i = j + 1
                continue
            if visible >= width:
                out.append("…")
                break
            out.append(ch)
            visible += 1
            i += 1
        out.append(_CODES_RESET)
        return "".join(out)

    @contextmanager
    def activity(self, message: str, cancel_event: threading.Event):
        # If a tool line is pending (from auto_approved / approval), pin it on its own row and run the
        # spinner underneath, so the tool-in-use stays visible during the call. We keep the tool row on
        # screen after the call (it scrolls into the feed as a log of what ran) — only the spinner row
        # is cleared — so fast tools no longer flash past before you can read them.
        tool_line = self._tool_line
        self._tool_line = None
        if tool_line and self._tty:
            self._commit_line()  # ensure a clean row for the pinned tool line
            width = max(1, shutil.get_terminal_size((80, 24)).columns - 1)
            sys.stdout.write("\r\033[K" + self._clip(tool_line, width) + "\n")
            sys.stdout.flush()
        else:
            self._commit_line()  # spinner owns the line next; drop any leftover transient
        status = Status()
        status.start(message)
        try:
            with InterruptibleSection(cancel_event):
                yield
        finally:
            status.stop()  # clears the spinner row; the tool row above stays on screen
            # In compact/quiet the tool row is a single transient line above the spinner: erase it once
            # the tool finishes so nothing accumulates. In full we keep it in the feed as a run log.
            if tool_line and self._tty and self.verbosity != "full":
                sys.stdout.write("\033[1A\r\033[K")  # step up onto the tool row and erase it
                sys.stdout.flush()

    @contextmanager
    def stream(self, cancel_event: threading.Event):
        """Yield a printer that renders streamed tokens live (spinner until the first token)."""
        self._commit_line()
        status = Status()
        status.start("thinking…")
        printer = _StreamPrinter(status, labels=self.labels, on_label=self.label_once)
        try:
            with InterruptibleSection(cancel_event):
                yield printer
        finally:
            if not printer.started:
                status.stop()
            printer.close()

    def assistant(self, text: str) -> None:
        # Intermediate narration ("I'll read X, then edit Y") — transient on a TTY (overwritten by
        # the next step); on a non-TTY it's diagnostic, so drop it (keep stdout clean for --json).
        if self.verbosity == "quiet":
            return  # reasoning narration suppressed at this level
        if self._tty:
            self.label_once()  # print the ⏺ scoot lead-in once, then narrate underneath it
            self._ephemeral(color(text, "gray"))
        else:
            eprint(color(text, "gray"))

    def approve(self, tool, args, ctx):
        self._commit_line()  # an approval prompt is interactive; don't let it clobber a transient
        return request_approval(tool, args, ctx)

    def auto_approved(self, tool, args) -> None:
        import json as _json

        dot = color("●", {"read": "cyan", "write": "yellow", "shell": "red"}.get(tool.risk, "cyan"))
        # Stash the tool line; ``activity`` pins it above the spinner for the duration of the call and
        # removes it afterwards, so the tool-in-use is visible without cluttering the transcript.
        self._tool_line = (
            f"{dot} {color(tool.name, 'bold')} "
            f"{color(_json.dumps(args, ensure_ascii=False), 'gray')} {color('(auto)', 'gray')}")

    def plan(self, plan) -> None:
        """Render the current plan as a checkbox list (✔ done · ▶ in progress · ○ pending)."""
        self._commit_line()  # the plan is permanent output
        marks = {"completed": color("✔", "green"), "in_progress": color("▶", "cyan")}
        done = sum(1 for s in plan if s.get("status") == "completed")
        print(color(f"◇ plan · {done}/{len(plan)}", "bold"))
        for step in plan:
            status = step.get("status", "pending")
            mark = marks.get(status, color("○", "gray"))
            text = step.get("step", "")
            body = color(text, "gray") if status == "completed" else text
            print(f"  {mark} {body}")

    def tool_result(self, name: str, result: ToolResult) -> None:
        icon = color("✔", "green") if result.ok else color("✗", "yellow")
        detail = result.summary or (result.error if not result.ok else "")
        self._ephemeral(f"  {icon} {color(name, 'bold')} {color(detail, 'gray')}")


class Repl:
    """The interactive loop."""

    def __init__(self, config: Config, provider: Optional[ProviderPool], resume=None):
        self.session = ReplSession(config, provider)
        if resume is not None:
            self.session.apply_record(resume)
        self.agent = Agent(config, provider)
        self.labels = getattr(config, "labels", True)
        self.ui = ReplUI(labels=self.labels, verbosity=getattr(config, "verbosity", "full"),
                         emoji=getattr(config, "emoji", True))
        # The "dock" pins the prompt to a fixed bottom row; it needs the bar's reserved region and a
        # real TTY. When off (flag/env/non-TTY), we fall back to a plain inline input().
        panel_on = getattr(config, "panel", True)
        self.dock = (getattr(config, "dock", True) and panel_on
                     and sys.stdout.isatty() and sys.stdin.isatty())
        self.bar = StatusBar(panel_on, reserve_input=self.dock)
        # The multi-row input dock shares geometry with the bar via a DockLayout so a wrapping line can
        # grow the input area (and shrink it back) without the two disagreeing on row positions.
        self._dock_layout = None
        if self.dock:
            from .panel import DockLayout

            self._dock_layout = DockLayout()
            self.bar.layout = self._dock_layout
        self.editor = LineEditor(
            enabled=self.dock, on_copy=self._copy_last,
            layout=self._dock_layout, on_reflow=self._reflow_dock,
            completer=self._command_names,
        )
        self.session.status_bar = self.bar  # let the /panel command reach it
        from .hooks import Hooks

        self.session.hooks = Hooks(config.root)
        self.session.ui = self.ui  # let the /verbosity command reach the feed renderer
        self.session.dock = self.dock  # surfaced in /status
        self.session.redraw_home = self._redraw_home  # let commands clear+reprint the header (e.g. /reset)
        self._user = None  # cached signed-in user label for the status bar
        self._seed_history()  # recover ↑-key recall from a resumed conversation

    def _command_names(self):
        """Slash-command names (no leading '/') for Tab completion, alphabetically."""
        commands.load_builtins()
        return list(commands.all_commands().keys())

    def _seed_history(self) -> None:
        """Prime the line editor's ↑/↓ recall with the user prompts from a resumed session."""
        try:
            for m in self.session.messages:
                if m.get("role") != "user":
                    continue
                text = m.get("content")
                if not isinstance(text, str):
                    continue
                line = text.strip()
                if line and (not self.editor.history or self.editor.history[-1] != line):
                    self.editor.history.append(line)
        except Exception:
            pass  # history recall is a nicety; never block startup on it

    def _replay_transcript(self) -> None:
        """Re-print the restored conversation so a resumed session shows its full history on screen."""
        try:
            printed = False
            for m in self.session.messages:
                role = m.get("role")
                content = m.get("content")
                if not isinstance(content, str) or not content.strip():
                    continue
                text = content.strip()
                if role == "user":
                    print(_user_echo(text) if self.labels else (color("› ", "green") + text))
                    printed = True
                elif role == "assistant":
                    if self.labels and sys.stdout.isatty():
                        print(_assistant_label(self.ui.emoji))
                    print(text)
                    printed = True
            if printed:
                print(color("─" * 8 + " end of resumed history " + "─" * 8, "gray"))
        except Exception:
            pass  # transcript replay is cosmetic; never block startup on it

    def run(self) -> int:
        commands.load_builtins()
        self._user = self._auth_user()
        if sys.stdout.isatty():
            # Start on a clean screen so the banner/bar don't overlap prior terminal content.
            sys.stdout.write("\033[2J\033[3J\033[H")  # clear screen + scrollback, cursor home
            sys.stdout.flush()
        self.bar.install()
        self._banner()
        if self.session.resumed:
            self._replay_transcript()
        from .hooks import session_event

        session_event(self.session, "SessionStart", source="resume" if self.session.resumed else "startup")
        code = 1
        try:
            code = self._loop()
            return code
        finally:
            self.bar.remove()
            session_event(self.session, "SessionEnd", reason="quit" if code == 0 else f"exit {code}")

    def _loop(self) -> int:
        while True:
            self._refresh_bar()
            try:
                raw = self.editor.readline("› ")
            except EOFError:
                print()
                return 0
            except KeyboardInterrupt:  # Ctrl-C at the prompt quits the app
                print()
                return 0

            line = raw.strip()
            if not line:
                continue
            # In dock mode the input row is transient, so echo the submitted prompt into the scrolling
            # region to keep it in history (inline input() already echoes what the user typed). Long
            # image paths are shown as compact [Image N] badges; the real path is still processed.
            if self.dock:
                echo = self._badge_paths(raw)
                print(_user_echo(echo) if self.labels else (color("› ", "green") + echo))
            if _is_slash_command(line):
                if self._handle_command(line) == commands.QUIT:
                    return 0
                continue
            try:
                self._run_turn(line)
            except KeyboardInterrupt:  # Ctrl-C during work quits the app
                print()
                return 130
            except Exception as exc:  # never let one bad turn kill the REPL — surface it and continue
                self._report_turn_error(exc)

    def _report_turn_error(self, exc: BaseException) -> None:
        """Surface an unexpected turn failure to the user without crashing the REPL."""
        from .errors import ScootError

        self.ui._commit_line()  # clear any leftover transient step/tool line
        kind = exc.__class__.__name__
        msg = redact(str(exc)) or kind
        self.session.last_error = kind if isinstance(exc, ScootError) else f"{kind}"
        self._refresh_bar()  # surface the failure in the status bar (cleared when the next turn starts)
        from .hooks import notify

        notify(self.session, "error", f"{kind}: {msg}"[:300])
        if isinstance(exc, ScootError):
            print(color(f"⚠ {msg}", "red"))
        else:
            print(color(f"⚠ unexpected error ({kind}): {msg}", "red"))
            if self.session.config.verbose:
                import traceback

                eprint(color(traceback.format_exc(), "gray"))
            else:
                print(color("  (run with --verbose or set SCOOT_VERBOSE=1 for a traceback)", "gray"))

    def _auth_user(self):
        """Status-bar identity: the default provider, flagged when it still lacks a key."""
        try:
            from .auth import is_configured

            pool = self.session.provider
            name = pool.default_name
            return name if is_configured(pool.spec) else f"{name} (no key)"
        except Exception:
            return None

    def _copy_last(self) -> None:
        """Ctrl-S handler: copy the last answer to the clipboard and print a one-line result."""
        from .clipboard import copy_session_output

        ok, message = copy_session_output(self.session)
        print(color(message, "gray" if ok else "yellow"))

    def _badge_paths(self, raw: str) -> str:
        """Compact image paths in the echoed prompt to ``[Image N]`` badges (best-effort)."""
        cfg = self.session.config
        if not getattr(cfg, "images", True):
            return raw
        try:
            from .images import badge_text

            return badge_text(raw, root=getattr(cfg, "root", None))
        except Exception:
            return raw

    def _maybe_fold_images(self, user_text: str) -> str:
        """If the prompt references dropped image files, describe them and fold that into the text.

        Propagates ``Interrupted`` (so a real ESC aborts the turn); any other problem returns the
        original prompt unchanged (never breaks a turn).
        """
        from .errors import Interrupted

        try:
            from .vision import fold_images_into_text

            return fold_images_into_text(
                user_text, self.session.config, self.session.provider, ui=self.ui,
            )
        except Interrupted:
            raise
        except Exception:
            return user_text

    def _refresh_bar(self) -> None:
        try:
            self.bar.render(build_status_text(self.session, self._user))
        except Exception:
            pass  # the bar must never break the REPL

    def _reflow_dock(self) -> None:
        """Called by the editor when the wrapping input grows/shrinks: re-establish the region and
        redraw the frame for the new height (without touching the editor's cursor save slot)."""
        try:
            self.bar.set_text(build_status_text(self.session, self._user))  # refresh text (no output)
            self.bar.reflow()  # re-establish region + redraw frame; no DECSC/DECRC (editor owns it)
        except Exception:
            pass  # never let a redraw break the input loop

    # ── UI ─────────────────────────────────────────────────────────────────────
    def _redraw_home(self, note: str = None) -> None:
        """Clear the screen + scrollback and reprint the launch header (used by /reset, /compact).

        Re-establishes the status bar afterwards so the fresh screen looks like a new launch. No-op
        off a TTY. An optional ``note`` line replaces the resume/hint line in the banner.
        """
        if not sys.stdout.isatty():
            return
        sys.stdout.write("\033[2J\033[3J\033[H")
        sys.stdout.flush()
        self._banner(note=note)
        try:
            self.bar.render(build_status_text(self.session, self._user))
        except Exception:
            pass

    def _banner_lines(self, note: str = None) -> "list[str]":
        """The banner's information lines (uncoloured): what/where, resume state, key hints."""
        cfg = self.session.config
        lines = [f"{self.session.active_model} · root: {logo.tilde(cfg.root)}"]
        if note:
            lines.append(note)
        elif self.session.resumed:
            lines.append(f"resumed session {self.session.id} · {len(self.session.messages)} messages "
                         f"· /reset to start fresh")
        else:
            hint = self._resume_hint()
            if hint:
                lines.append(hint)
        lines.append("type a prompt · /help · esc to interrupt · ctrl-c to quit")
        lines.append("⌃S or /c copies the last reply to your clipboard")
        return lines

    def _banner(self, note: str = None) -> None:
        """Launch banner: the mascot with the info lines beside it, or a plain box with ``--no-logo``."""
        lines = self._banner_lines(note)
        if getattr(self.session.config, "logo", True):
            from . import __version__

            art_rows = logo.mascot("idle", "banner")
            cols = shutil.get_terminal_size((100, 24)).columns
            avail = max(20, cols - len(art_rows[0]) - 3)
            head = f"{logo.NAME} {__version__} · "
            lines = [logo.fit(lines[0], avail - len(head))] + [logo.fit(l, avail) for l in lines[1:]]
            art = [color(row, "cyan") for row in art_rows]
            text = [color(logo.NAME, "cyan", "bold") + color(f" {__version__} · " + lines[0], "gray")]
            text += [color(line, "gray") for line in lines[1:]]
            for row in logo.compose(art, text):
                print(row)
            return
        from . import __version__

        print(color(f"╭─ scoot {__version__} ", "cyan"))
        for line in lines:
            print(color("│ ", "cyan") + color(line, "gray"))
        print(color("╰─", "cyan"))

    def _resume_hint(self) -> "str | None":
        """The most recent saved session for this directory, so resume is discoverable (or ``None``)."""
        if getattr(self.session.config, "resume", "hint") == "off":
            return None
        try:
            from . import sessions

            last = sessions.latest_for_root(str(self.session.config.root))
        except Exception:
            last = None
        if not last or not last.messages:
            return None
        prompt = logo.fit(last.first_prompt(), 30)
        turns = f"{last.turns} turn" + ("" if last.turns == 1 else "s")
        return f"↩ {last.id} · {turns} · \"{prompt}\" · /resume or -c to continue"

    def _handle_command(self, line: str):
        name, _, args = line[1:].partition(" ")
        cmd = commands.get(name)
        if cmd is None:
            eprint(color(f"unknown command: /{name} (try /help)", "yellow"))
            return None
        return cmd.handler(self.session, args.strip())

    # ── One agentic turn ─────────────────────────────────────────────────────────
    def _run_turn(self, user_text: str) -> None:
        session = self.session
        from .errors import Interrupted

        session.last_error = ""  # a fresh turn clears any prior failure shown in the bar
        self.ui.begin_turn()  # reset the once-per-turn ⏺ scoot label
        session.mascot_state = "thinking"  # eyes: > >
        self._refresh_bar()
        try:
            from .hooks import submit_prompt

            submitted = submit_prompt(session, user_text)
            if submitted is None:
                session.mascot_state = "stopped"
                reason = getattr(session, "hook_block_reason", "") or "a UserPromptSubmit hook blocked it"
                print(color(f"⏹ prompt not sent: {reason}", "yellow"))
                return
            user_text = submitted
            try:
                user_text = self._maybe_fold_images(user_text)
            except Interrupted:
                session.mascot_state = "stopped"
                print(color("⏹ interrupted", "yellow"))
                return  # don't append a half-processed prompt to the conversation
            session.messages.append({"role": "user", "content": user_text})
            while True:
                cancel = threading.Event()
                outcome = self.agent.run_turn(session, self.ui, cancel)
                self._render_outcome(outcome)
                if outcome.status == "max_steps" and self._ask_continue():
                    continue  # keep going on the same conversation for another batch of steps
                break
            session.mascot_state = "idle" if outcome.status in ("done", "max_steps") else "stopped"
            self._maybe_autocompact()
            session.autosave()
        except BaseException:
            session.mascot_state = "stopped"  # eyes: - -  (error / ctrl-c); the loop refreshes the bar
            raise

    def _ask_continue(self) -> bool:
        """After hitting the step limit, ask whether to keep going (interactive only)."""
        limit = self.session.config.max_steps
        from .hooks import notify

        notify(self.session, "max_steps", f"reached the step limit ({limit}); waiting for the user")
        try:
            ans = input(color(
                f"reached step limit ({limit}). continue for another {limit} steps? [y/N] ",
                "yellow"))
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if ans.strip().lower() in ("y", "yes"):
            return True
        print(color("stopped. type a new prompt to redirect, or ask it to continue.", "gray"))
        return False

    def _maybe_autocompact(self) -> None:
        """Summarize automatically when the context grows past the configured threshold."""
        from .activity import activity
        from .context import compact, estimate_context_tokens
        from .errors import ScootError, Interrupted

        session = self.session
        tokens = estimate_context_tokens(session)
        if not tokens or tokens <= session.config.compact_at:
            return
        print(color(f"context ~{tokens} tokens > {session.config.compact_at}; auto-compacting…", "gray"))
        try:
            with activity("auto-compacting…") as cancel:
                compact(session, cancel)
        except (Interrupted, ScootError):
            pass  # best-effort; keep going even if summarization fails

    def _render_outcome(self, outcome: AgentOutcome) -> None:
        session = self.session
        self.ui._commit_line()  # clear any leftover transient step/tool line before permanent output
        if outcome.status == "done":
            answer = outcome.content.strip()
            session.last_output = answer  # remember for /c + Ctrl-S copy
            if answer and not outcome.streamed:
                if self.labels and sys.stdout.isatty():
                    print(_assistant_label(self.ui.emoji))
                print(answer)
            if session.config.verbose:
                u = session.last_usage
                eprint(color(
                    f"[{session.resolved_model()}] steps={outcome.steps} "
                    f"prompt={u.get('prompt_tokens', '?')} "
                    f"completion={u.get('completion_tokens', '?')}", "gray"))
        elif outcome.status == "interrupted":
            print(color("⏹ interrupted", "yellow"))
        elif outcome.status == "aborted":
            print(color("✋ aborted", "yellow"))
        elif outcome.status == "max_steps":
            # The continue-prompt is handled by _run_turn / _ask_continue.
            pass
        elif outcome.status == "error":
            eprint(color(f"⚠ {redact(outcome.error)}", "red"))

