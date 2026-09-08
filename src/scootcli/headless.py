"""Headless mode: ``scoot --headless`` speaks line-delimited JSON on stdin and stdout.

Every line on stdout is one JSON object and nothing else is ever written there; diagnostics go to
stderr. It is the multi-turn form of ``--json``: sessions, compaction, tools, approvals, and hooks work
as in the REPL, but every interaction is a message. Protocol version 1; fields are only added within
a major version. The tables of message types live in ``docs/headless-protocol.md``.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import uuid
from contextlib import contextmanager, redirect_stdout
from typing import Optional

from . import __version__
from .approvals import Approval, Decision
from .tools.base import ToolResult

PROTOCOL = 1
_DECISIONS = {"allow": Decision.APPROVE, "allow_tool": Decision.APPROVE_TOOL,
              "allow_session": Decision.APPROVE_SESSION, "deny": Decision.SKIP, "abort": Decision.ABORT}
_OPTIONS = ["allow", "allow_tool", "allow_session", "deny", "abort"]


def approval_timeout() -> float:
    try:
        return float(os.environ.get("SCOOT_APPROVAL_TIMEOUT", "120"))
    except ValueError:
        return 120.0


class Writer:
    """Thread-safe JSON-lines writer bound to the real stdout."""

    def __init__(self, stream):
        self.stream = stream
        self.lock = threading.Lock()

    def emit(self, type_: str, **fields) -> None:
        obj = {"type": type_}
        obj.update(fields)
        line = json.dumps(obj, ensure_ascii=False, default=str)
        with self.lock:
            self.stream.write(line + "\n")
            self.stream.flush()


class _Streamer:
    """What ``Agent`` expects from ``ui.stream(...)``: ``.delta``, ``.started``, ``.close``."""

    def __init__(self, writer: Writer):
        self._w = writer
        self.started = False
        self._parts = []

    def delta(self, text: str) -> None:
        if not text:
            return
        self.started = True
        self._parts.append(text)
        self._w.emit("text_delta", text=text)

    def close(self) -> None:
        if self.started:
            self._w.emit("assistant", text="".join(self._parts), final=True)


class HeadlessUI:
    """The UI the agent drives; every call becomes an event, approvals wait on a queue."""

    def __init__(self, writer: Writer, answers: "queue.Queue[dict]", timeout: Optional[float] = None):
        self.writer = writer
        self.answers = answers
        self.timeout = approval_timeout() if timeout is None else timeout
        self.cancel_event: Optional[threading.Event] = None

    @contextmanager
    def activity(self, message: str, cancel_event: threading.Event):
        self.writer.emit("activity", message=message)
        yield

    @contextmanager
    def stream(self, cancel_event: threading.Event):
        yield _Streamer(self.writer)

    def begin_turn(self) -> None:
        pass

    def assistant(self, text: str) -> None:
        self.writer.emit("assistant", text=text, final=False)

    def auto_approved(self, tool, args) -> None:
        from .hooks import tool_kind

        self.writer.emit("tool_call", id=self._call_id(), name=tool.name, args=args, kind=tool_kind(tool),
                         auto_approved=True)

    def approve(self, tool, args, ctx) -> Approval:
        from .hooks import tool_kind

        req = self._call_id()
        preview = ""
        try:
            preview = tool.preview(args, ctx) or ""
        except Exception:
            pass
        self.writer.emit("approval_request", id=req, name=tool.name, args=args, kind=tool_kind(tool),
                         preview=str(preview)[:4000], options=_OPTIONS, timeout_s=self.timeout)
        deadline = time.time() + self.timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                self.writer.emit("notice", message=f"approval {req} timed out after {self.timeout:g}s; denied")
                return Approval(Decision.SKIP, args)
            if self.cancel_event is not None and self.cancel_event.is_set():
                return Approval(Decision.ABORT, args)
            try:
                msg = self.answers.get(timeout=min(0.2, remaining))
            except queue.Empty:
                continue
            if msg.get("id") != req:  # an unbound answer must never approve whatever comes next
                self.writer.emit("error", message=f"approval answer must carry id {req!r}, got {msg.get('id')!r}",
                                 kind="protocol")
                continue
            decision = _DECISIONS.get(str(msg.get("decision", "")).lower())
            if decision is None:
                self.writer.emit("error", message=f"unknown decision {msg.get('decision')!r}; expected one of {_OPTIONS}", kind="protocol")
                continue
            new_args = msg.get("args") if isinstance(msg.get("args"), dict) else args
            self.writer.emit("tool_call", id=req, name=tool.name, args=new_args, kind=tool_kind(tool),
                             auto_approved=False, decision=str(msg.get("decision")).lower())
            return Approval(decision, new_args)

    def approve_scope(self, tool, path, ctx) -> str:
        req = self._call_id()
        self.writer.emit("scope_request", id=req, name=tool.name, path=str(path),
                         options=["allow_once", "allow_dir", "allow_all", "deny", "abort"], timeout_s=self.timeout)
        mapping = {"allow_once": "once", "allow_dir": "dir", "allow_all": "all", "deny": "deny", "abort": "abort"}
        deadline = time.time() + self.timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                self.writer.emit("notice", message=f"scope request {req} timed out after {self.timeout:g}s; denied")
                return "deny"
            if self.cancel_event is not None and self.cancel_event.is_set():
                return "abort"
            try:
                msg = self.answers.get(timeout=min(0.2, remaining))
            except queue.Empty:
                continue
            if msg.get("id") != req:
                self.writer.emit("error", message=f"scope answer must carry id {req!r}, got {msg.get('id')!r}",
                                 kind="protocol")
                continue
            verdict = mapping.get(str(msg.get("decision", "")).lower())
            if verdict is None:
                self.writer.emit("error", message=f"unknown scope decision {msg.get('decision')!r}; expected one of {list(mapping)}", kind="protocol")
                continue
            return verdict

    def plan(self, plan) -> None:
        self.writer.emit("plan", steps=plan)

    def tool_result(self, name: str, result: ToolResult) -> None:
        self.writer.emit("tool_result", name=name, ok=result.ok, summary=result.summary or "",
                         error=result.error or "", content=(result.content or "")[:8000])

    @staticmethod
    def _call_id() -> str:
        return "call_" + uuid.uuid4().hex[:10]


class Headless:
    """The main loop: reads messages, runs turns, answers with events."""

    def __init__(self, session, agent, stdin=None, stdout=None, approval_timeout_s: Optional[float] = None,
                 heartbeat_s: float = 10.0):
        self.session = session
        self.agent = agent
        self.stdin = stdin or sys.stdin
        self.writer = Writer(stdout or sys.__stdout__)
        self.inbox: "queue.Queue[dict]" = queue.Queue()
        self.answers: "queue.Queue[dict]" = queue.Queue()
        self.ui = HeadlessUI(self.writer, self.answers, approval_timeout_s)
        self.heartbeat_s = heartbeat_s
        self.busy = False
        self.cancel: Optional[threading.Event] = None
        self._stop = threading.Event()
        self._turn = 0

    # ── threads ──────────────────────────────────────────────────────────────────
    def _reader(self) -> None:
        for line in self.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                if not isinstance(msg, dict) or "type" not in msg:
                    raise ValueError("expected a JSON object with a 'type'")
            except (json.JSONDecodeError, ValueError) as exc:
                self.writer.emit("error", message=f"bad input line: {exc}", kind="protocol")
                continue
            t = msg["type"]
            if t == "approve":
                self.answers.put(msg)
            elif t == "interrupt":
                if self.cancel is not None:
                    self.cancel.set()
            elif t == "note":
                notes = getattr(self.session, "pending_notes", None)
                if notes is None:
                    notes = []
                    self.session.pending_notes = notes
                notes.append(str(msg.get("text", "")))
                self.writer.emit("notice", message="note queued for the next model call")
            else:
                self.inbox.put(msg)
        self.inbox.put({"type": "shutdown", "reason": "stdin closed"})

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.heartbeat_s):
            self.writer.emit("heartbeat", busy=self.busy, turn=self._turn)

    # ── main loop ────────────────────────────────────────────────────────────────
    def run(self) -> int:
        from . import tools
        from .hooks import session_event, startup_notices

        tools.load_builtins()
        threading.Thread(target=self._reader, daemon=True).start()
        if self.heartbeat_s > 0:
            threading.Thread(target=self._heartbeat, daemon=True).start()
        for note in startup_notices(self.session):
            self.writer.emit("notice", message=note)
        session_event(self.session, "SessionStart", source="resume" if getattr(self.session, "resumed", False) else "startup")
        self.writer.emit("ready", protocol=PROTOCOL, version=__version__, session_id=self.session.id,
                         model=self.session.active_model, cwd=str(self.session.config.root),
                         tools=sorted(tools.all_tools()), resumed=bool(getattr(self.session, "resumed", False)))
        code = 0
        try:
            while True:
                msg = self.inbox.get()
                t = msg.get("type")
                if t == "shutdown":
                    self.session.autosave()
                    self.writer.emit("bye", reason=msg.get("reason", "shutdown"))
                    return 0
                if t == "prompt":
                    self._turn_from(msg)
                elif t == "command":
                    self._command(msg)
                else:
                    self.writer.emit("error", message=f"unknown message type {t!r}", kind="protocol")
        except KeyboardInterrupt:
            code = 130
            return code
        finally:
            self._stop.set()
            session_event(self.session, "SessionEnd", reason="quit" if code == 0 else f"exit {code}")

    def _turn_from(self, msg: dict) -> None:
        from .errors import Interrupted, ScootError
        from .hooks import submit_prompt
        from .vision import fold_images_into_text

        text = str(msg.get("text", "")).strip()
        if not text:
            self.writer.emit("error", message="prompt has no text", kind="protocol")
            return
        self._turn += 1
        self.writer.emit("turn_start", turn=self._turn, prompt=text)
        submitted = submit_prompt(self.session, text)
        if submitted is None:
            reason = getattr(self.session, "hook_block_reason", "") or "blocked by a UserPromptSubmit hook"
            self.writer.emit("turn_end", turn=self._turn, status="blocked", steps=0, model=self.session.active_model,
                             usage={}, content="", error=reason)
            return
        text = submitted
        for path in msg.get("images") or []:
            text += f" {path}"
        self.busy = True
        self.cancel = threading.Event()
        self.ui.cancel_event = self.cancel
        self.session.last_error = ""
        before = self._session_cost()
        try:
            with redirect_stdout(sys.stderr):  # any stray print from a tool or command stays off the protocol
                try:
                    text = fold_images_into_text(text, self.session.config, self.session.provider, ui=self.ui,
                                                 cancel_event=self.cancel)
                except Interrupted:
                    self.writer.emit("turn_end", turn=self._turn, status="interrupted", steps=0,
                                     model=self.session.active_model, usage={}, content="")
                    return
                self.session.messages.append({"role": "user", "content": text})
                outcome = self.agent.run_turn(self.session, self.ui, self.cancel)
            self.session.autosave()
            if outcome.status == "max_steps":
                self.writer.emit("notice", message=f"reached the step limit ({self.session.config.max_steps}); send another prompt to continue")
            if outcome.status == "error":
                self.writer.emit("error", message=outcome.error, kind="turn")
            if outcome.status == "incomplete":
                self.writer.emit("notice", message=f"reply cut off: {outcome.error}; the content is partial")
            self.writer.emit("turn_end", turn=self._turn, status=outcome.status, steps=outcome.steps,
                             model=self.session.active_model, usage=self.session.last_usage,
                             cost=self._turn_cost(before), cost_session=self._session_cost(),
                             content=outcome.content, error=outcome.error)
        except ScootError as exc:
            self.writer.emit("error", message=str(exc), hint=getattr(exc, "hint", ""), kind="turn")
            self.writer.emit("turn_end", turn=self._turn, status="error", steps=0, model=self.session.active_model,
                             usage={}, content="", error=str(exc))
        finally:
            self.busy = False
            self.cancel = None
            self.ui.cancel_event = None

    def _session_cost(self):
        fn = getattr(self.session, "session_cost", None)
        try:
            return fn() if callable(fn) else None
        except Exception:
            return None

    def _turn_cost(self, before):
        after = self._session_cost()
        if after is None or before is None:
            return None
        return round(after - before, 6)

    def _command(self, msg: dict) -> None:
        from . import commands
        import io

        commands.load_builtins()
        name = str(msg.get("name", "")).lstrip("/")
        cmd = commands.get(name)
        if cmd is None:
            self.writer.emit("error", message=f"unknown command {name!r}", kind="protocol")
            return
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                result = cmd.handler(self.session, str(msg.get("args", "")))
            except Exception as exc:  # a command must never take the process down
                self.writer.emit("error", message=f"/{name} failed: {exc}", kind="command")
                return
        self.writer.emit("command_output", name=name, output=buf.getvalue())
        if result == commands.QUIT:
            self.inbox.put({"type": "shutdown", "reason": f"/{name}"})


def run_headless(config, pool, resume=None) -> int:
    """Entry point for ``scoot --headless``."""
    from .agent import Agent
    from .hooks import Hooks
    from .providers.registry import readiness
    from .repl import ReplSession

    writer = Writer(sys.__stdout__)
    ready, message = readiness(config)
    if not ready:
        writer.emit("error", message=message, kind="setup")
        return 1
    session = ReplSession(config, pool)
    if resume is not None:
        session.apply_record(resume)
    session.hooks = Hooks(config.root)
    agent = Agent(config.override(stream=True), pool)
    return Headless(session, agent).run()
