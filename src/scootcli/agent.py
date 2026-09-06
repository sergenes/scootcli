"""The agentic loop (PLAN §7).

Each turn: build messages → call the model with the tool schemas → if it returns ``tool_calls``,
approve + execute each and loop; otherwise the plain-text reply is the completion (DONE sentinel
stripped). Bounded by ``max_steps``. ESC cancellation is honored around every blocking call.

The loop runs on the *calling* thread and delegates all terminal concerns (spinner, ESC listener,
approval prompts) to an injected ``ui`` object, so it stays decoupled and unit-testable (see
:class:`HeadlessUI`).
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import List, Optional

from . import tools
from .approvals import Approval, Decision, needs_prompt
from .config import Config
from .errors import ScootError, ContextLengthError, Interrupted, ModelUnavailableError
from .prompts import build_agent_system_prompt
from .tools.base import ToolContext, ToolResult


@dataclass
class AgentOutcome:
    status: str  # "done" | "interrupted" | "aborted" | "max_steps" | "error"
    content: str = ""
    error: str = ""
    steps: int = 0
    streamed: bool = False  # True if the final content was already printed live (streaming)


def _strip_done(text: str) -> str:
    """Remove a trailing ``DONE`` sentinel line from the model's final message."""
    lines = (text or "").rstrip().splitlines()
    while lines and lines[-1].strip() in ("DONE", ""):
        if lines[-1].strip() == "DONE":
            lines.pop()
            break
        lines.pop()
    return "\n".join(lines).rstrip()


def _fmt_error(exc: Exception) -> str:
    """Format an error with its actionable hint, if any."""
    hint = getattr(exc, "hint", "")
    return f"{exc}" + (f" — {hint}" if hint else "")


class HeadlessUI:
    """A non-interactive UI for programmatic/testing use (no spinner; fixed approval decision)."""

    def __init__(self, decision: Decision = Decision.APPROVE):
        self.decision = decision
        self.events: List[tuple] = []

    @contextmanager
    def activity(self, message: str, cancel_event: threading.Event):
        self.events.append(("activity", message))
        yield

    def assistant(self, text: str) -> None:
        self.events.append(("assistant", text))

    def approve(self, tool, args, ctx) -> Approval:
        self.events.append(("approve", tool.name, args))
        return Approval(self.decision, args)

    def auto_approved(self, tool, args) -> None:
        self.events.append(("auto", tool.name, args))

    def plan(self, plan) -> None:
        self.events.append(("plan", plan))

    def tool_result(self, name: str, result: ToolResult) -> None:
        self.events.append(("tool_result", name, result.ok, result.summary))


class Agent:
    """Runs the agentic loop for one user turn."""

    def __init__(self, config: Config, provider):
        self.config = config
        self.provider = provider  # a Provider (usually the ProviderPool); dispatches by provider/model
        self._streaming = getattr(config, "stream", True)
        tools.load_builtins()

    # ── public API ───────────────────────────────────────────────────────────────
    def run_turn(self, session, ui, cancel_event: Optional[threading.Event] = None) -> AgentOutcome:
        cancel_event = cancel_event or threading.Event()
        cfg = getattr(session, "config", None) or self.config
        session.active_model = self._pick_model(session)
        self._refresh_workspace(session, cfg)
        steps = 0
        compacted = False
        while True:
            if cancel_event.is_set():
                return AgentOutcome("interrupted", steps=steps)
            steps += 1
            if steps > cfg.max_steps:
                return AgentOutcome("max_steps", steps=steps - 1)

            try:
                result, streamed = self._model_call(session, cfg, ui, cancel_event, steps)
            except Interrupted:
                return AgentOutcome("interrupted", steps=steps)
            except ModelUnavailableError as exc:
                if self._fallback_model(session):
                    ui.assistant(f"model unavailable — switching to {session.active_model}.")
                    steps -= 1  # don't count the failed attempt
                    continue
                return AgentOutcome("error", error=_fmt_error(exc), steps=steps)
            except ContextLengthError as exc:
                if not compacted and session.messages:
                    compacted = True
                    try:
                        from .context import compact
                        with ui.activity("context too long — compacting…", cancel_event):
                            compact(session, cancel_event)
                        steps -= 1
                        continue
                    except ScootError:
                        pass
                return AgentOutcome("error", error=_fmt_error(exc), steps=steps)
            except ScootError as exc:
                return AgentOutcome("error", error=_fmt_error(exc), steps=steps)

            try:
                session.account(result.usage, model=result.model or session.active_model)
            except TypeError:  # older/fake sessions with a one-argument account()
                session.account(result.usage)
            session.messages.append(self._assistant_message(result))

            if result.tool_calls:
                if (result.content or "").strip() and not streamed:
                    ui.assistant(result.content.strip())
                signal = self._run_tools(session, result.tool_calls, ui, cancel_event, cfg)
                if signal in ("abort", "interrupted"):
                    status = "aborted" if signal == "abort" else "interrupted"
                    return AgentOutcome(status, steps=steps)
                continue

            # No tool calls -> the model is done.
            return AgentOutcome(
                "done", content=_strip_done(result.content), steps=steps, streamed=streamed
            )

    # ── model call (streaming or buffered) ───────────────────────────────────────
    def _model_call(self, session, cfg, ui, cancel_event, steps):
        """Call the model, streaming tokens live when the UI supports it.

        Returns ``(result, streamed)`` where ``streamed`` is True if content was already printed.
        """
        messages = self._messages(session, cfg)
        hints = {"task_text": self._last_user_text(session), "needs_tools": True, "step": steps}
        if self._streaming and hasattr(ui, "stream") and hasattr(self.provider, "chat_stream"):
            with ui.stream(cancel_event) as streamer:
                result = self.provider.chat_stream(
                    messages,
                    model=session.active_model,
                    tools=tools.schemas(),
                    tool_choice="auto",
                    cancel_event=cancel_event,
                    on_delta=streamer.delta,
                    hints=hints,
                )
            return result, bool(getattr(streamer, "started", False))

        with ui.activity(f"thinking… (step {steps})", cancel_event):
            result = self.provider.chat(
                messages,
                model=session.active_model,
                tools=tools.schemas(),
                tool_choice="auto",
                cancel_event=cancel_event,
                hints=hints,
            )
        return result, False

    @staticmethod
    def _last_user_text(session) -> str:
        for m in reversed(getattr(session, "messages", []) or []):
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                return m["content"]
        return ""

    # ── message construction ─────────────────────────────────────────────────────
    def _pick_model(self, session) -> str:
        """Resolve the model for this turn. ``auto`` routes once per turn (see ``providers.router``)."""
        if session.model.lower() != "auto":
            return session.resolved_model()
        from .providers.router import Router, compute_hints

        router = getattr(session, "router", None)
        if router is None:
            router = Router()
            try:
                session.router = router
            except Exception:
                pass
        try:
            available = session.available_models()
        except Exception:
            available = []
        bad = getattr(session, "bad_models", None) or set()
        available = [m for m in available if m not in bad] or available
        hints = compute_hints(session)
        return router.choose(session, hints, available, session.resolved_model(),
                             classify=self._classify_with_provider).model

    def _classify_with_provider(self, model: str, prompt: str) -> str:
        """One small, non-streaming call used by a configured router classifier."""
        result = self.provider.chat([{"role": "user", "content": prompt}], model=model, max_tokens=5,
                                    temperature=0, hints={"purpose": "route"})
        return result.content or ""

    def _fallback_model(self, session) -> bool:
        """After a model-unavailable error, blacklist it and switch models. Returns False if stuck."""
        bad = session.active_model
        fallback = session.resolved_model()
        if bad == fallback and session.model.lower() != "auto":
            return False
        if hasattr(session, "bad_models"):
            session.bad_models.add(bad)
        new = self._pick_model(session)
        if new == bad:
            if bad == fallback:
                return False
            new = fallback
        session.active_model = new
        return True

    def _messages(self, session, cfg=None) -> List[dict]:
        cfg = cfg or getattr(session, "config", None) or self.config
        system = build_agent_system_prompt(
            root=cfg.root,
            model=session.active_model,
            tool_list=tools.tool_list_text(),
            workspace=getattr(session, "workspace_ctx", ""),
        )
        return [{"role": "system", "content": system}, *session.messages]

    def _refresh_workspace(self, session, cfg) -> None:
        """Recompute the workspace map once per turn (best-effort; stored on the session)."""
        ctx = ""
        if getattr(cfg, "workspace_context", True):
            try:
                from .workspace import workspace_context

                ctx = workspace_context(cfg.root)
            except Exception:
                ctx = ""
        try:
            session.workspace_ctx = ctx
        except Exception:
            pass

    @staticmethod
    def _assistant_message(result) -> dict:
        """The history entry for a model reply. ``provider_items`` (opaque items such as encrypted
        reasoning) ride along so the adapter that produced them can replay them next call."""
        msg = {"role": "assistant", "content": result.content or ""}
        if result.tool_calls:
            msg["tool_calls"] = result.tool_calls
        raw = result.raw_message if isinstance(result.raw_message, dict) else {}
        if raw.get("provider_items"):
            msg["provider_items"] = raw["provider_items"]
        return msg

    @staticmethod
    def _append_tool(session, tool_call_id: str, content: str) -> None:
        session.messages.append(
            {"role": "tool", "tool_call_id": tool_call_id, "content": content}
        )

    @staticmethod
    def _parse_args(raw) -> dict:
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    # ── tool execution ───────────────────────────────────────────────────────────
    def _run_tools(self, session, tool_calls, ui, cancel_event, cfg=None) -> Optional[str]:
        cfg = cfg or getattr(session, "config", None) or self.config
        ctx = ToolContext(root=cfg.root, config=cfg, cancel_event=cancel_event)
        mode = getattr(session, "approval_mode", "always")
        # Session trust-list: tools the user chose to auto-approve for the rest of the session.
        trusted = getattr(session, "trusted_tools", None)
        if trusted is None:
            trusted = set()
            try:
                session.trusted_tools = trusted
            except Exception:
                pass
        for tc in tool_calls:
            if cancel_event.is_set():
                return "interrupted"
            tc_id = tc.get("id", "")
            fn = tc.get("function", {}) or {}
            name = fn.get("name", "")
            args = self._parse_args(fn.get("arguments"))

            tool = tools.get(name)
            if tool is None:
                self._append_tool(session, tc_id, f"error: unknown tool '{name}'")
                continue

            # Approval policy: auto-approve when the mode/trust allows it, else prompt.
            if needs_prompt(mode, tool, args, trusted) is None:
                if not getattr(tool, "auto_approve", False):
                    ui.auto_approved(tool, args)  # meta tools render their own output
            else:
                approval = ui.approve(tool, args, ctx)
                if approval.decision == Decision.ABORT:
                    self._append_tool(session, tc_id, "user aborted the operation")
                    return "abort"
                if approval.decision == Decision.SKIP:
                    self._append_tool(
                        session, tc_id, "user declined this action; consider another approach"
                    )
                    ui.tool_result(name, ToolResult(ok=False, summary="skipped"))
                    continue
                if approval.decision == Decision.APPROVE_TOOL:
                    trusted.add(name)
                    ui.assistant(f"trusting {name} for the rest of this session.")
                elif approval.decision == Decision.APPROVE_SESSION:
                    session.approval_mode = mode = "yolo"
                    ui.assistant("auto-approving all tool calls for this session (yolo).")
                args = approval.args

            try:
                with ui.activity(f"running {name}…", cancel_event):
                    result = tool.run(args, ctx)
            except Interrupted:
                return "interrupted"
            except Exception as exc:  # a tool must never take down the loop
                result = ToolResult.fail(f"tool crashed: {exc}")

            self._handle_result(session, name, result, ui)
            payload = result.content if result.ok else f"ERROR: {result.error}"
            self._append_tool(session, tc_id, payload or "(no output)")
        return None

    @staticmethod
    def _handle_result(session, name: str, result: ToolResult, ui) -> None:
        """Surface a tool result: render a plan update specially, else the generic result line."""
        plan = result.meta.get("plan") if (result.ok and isinstance(result.meta, dict)) else None
        if plan is not None:
            try:
                session.plan = plan
            except Exception:
                pass
            if hasattr(ui, "plan"):
                ui.plan(plan)
                return
        ui.tool_result(name, result)

