"""Headless mode: the JSON-lines protocol end to end with a scripted provider."""

from __future__ import annotations

import io
import json
import queue
import threading
import time

from scootcli.agent import Agent
from scootcli.config import Config
from scootcli.headless import Headless
from scootcli.providers import ChatResult


class _Stdin:
    """A blocking line source the test feeds while the loop runs."""

    def __init__(self):
        self.q: "queue.Queue[str | None]" = queue.Queue()

    def send(self, obj) -> None:
        self.q.put(json.dumps(obj) if not isinstance(obj, str) else obj)

    def close(self) -> None:
        self.q.put(None)

    def __iter__(self):
        while True:
            line = self.q.get()
            if line is None:
                return
            yield line + "\n"


class _Out:
    def __init__(self):
        self.buf = io.StringIO()
        self.lock = threading.Lock()

    def write(self, s):
        with self.lock:
            self.buf.write(s)

    def flush(self):
        pass

    def events(self):
        with self.lock:
            text = self.buf.getvalue()
        return [json.loads(l) for l in text.splitlines() if l.strip()]

    def wait_for(self, type_, timeout=5.0, **match):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for e in self.events():
                if e["type"] == type_ and all(e.get(k) == v for k, v in match.items()):
                    return e
            time.sleep(0.02)
        raise AssertionError(f"no {type_} {match} in {[e['type'] for e in self.events()]}")


def _toolcall(name, args, cid):
    return ChatResult(content="", model="m", tool_calls=[{"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}])


class _Provider:
    def __init__(self, results):
        self.results, self.i, self.seen = results, 0, []

    def chat(self, messages, **kw):
        self.seen.append([m for m in messages])
        r = self.results[min(self.i, len(self.results) - 1)]
        self.i += 1
        return r

    def list_models(self, cancel_event=None):
        return []


class _Session:
    def __init__(self, root, provider, approval="always"):
        self.id = "hl-1"
        self.config = Config().override(root=str(root), stream=False, workspace_context=False, approval=approval)
        self.provider = provider
        self.model = "m"
        self.active_model = "m"
        self.approval_mode = approval
        self.messages = []
        self.last_usage = {}
        self.bad_models = set()
        self.trusted_tools = set()
        self.resumed = False
        self.saved = 0

    def resolved_model(self):
        return "m"

    def available_models(self):
        return ["m"]

    def account(self, usage, model=""):
        self.last_usage = usage or {}

    def autosave(self):
        self.saved += 1


def _start(root, provider, approval="always", timeout=5.0):
    session = _Session(root, provider, approval)
    stdin, out = _Stdin(), _Out()
    h = Headless(session, Agent(session.config, provider), stdin=stdin, stdout=out, approval_timeout_s=timeout,
                 heartbeat_s=0)
    t = threading.Thread(target=h.run, daemon=True)
    t.start()
    return h, session, stdin, out, t


def test_full_turn_with_approval_round_trip(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    provider = _Provider([_toolcall("read_file", {"path": "a.txt"}, "c1"), ChatResult(content="It says hello.", model="m")])
    h, session, stdin, out, t = _start(tmp_path, provider)
    ready = out.wait_for("ready")
    assert ready["protocol"] == 1 and "read_file" in ready["tools"] and ready["session_id"] == "hl-1"
    stdin.send({"type": "prompt", "text": "what is in a.txt?"})
    req = out.wait_for("approval_request")
    assert req["name"] == "read_file" and req["kind"] == "read" and "allow" in req["options"]
    stdin.send({"type": "approve", "id": req["id"], "decision": "allow"})
    end = out.wait_for("turn_end")
    assert end["status"] == "done" and end["content"] == "It says hello." and end["turn"] == 1
    assert "cost" in end and "cost_session" in end  # None here (model "m" has no price), present always
    types = [e["type"] for e in out.events()]
    assert types.index("tool_call") < types.index("tool_result") < types.index("turn_end")
    assert out.wait_for("tool_result")["ok"] is True and "hello" in out.wait_for("tool_result")["content"]
    assert session.saved == 1
    stdin.send({"type": "shutdown"})
    out.wait_for("bye")
    t.join(2)
    assert all(isinstance(e, dict) for e in out.events())  # stdout carried only JSON objects


def test_deny_and_abort_answers(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    provider = _Provider([_toolcall("read_file", {"path": "a.txt"}, "c1"), ChatResult(content="ok", model="m"),
                          _toolcall("read_file", {"path": "a.txt"}, "c2"), ChatResult(content="ok", model="m")])
    h, session, stdin, out, t = _start(tmp_path, provider)
    out.wait_for("ready")
    stdin.send({"type": "prompt", "text": "read it"})
    req = out.wait_for("approval_request")
    stdin.send({"type": "approve", "id": req["id"], "decision": "deny"})
    out.wait_for("turn_end", turn=1)
    assert any(m.get("role") == "tool" and "declined" in m["content"] for m in session.messages)
    stdin.send({"type": "prompt", "text": "again"})
    out.wait_for("turn_start", turn=2)
    deadline = time.time() + 5
    while time.time() < deadline:
        reqs = [e for e in out.events() if e["type"] == "approval_request"]
        if len(reqs) == 2:
            break
        time.sleep(0.02)
    stdin.send({"type": "approve", "id": reqs[-1]["id"], "decision": "abort"})
    assert out.wait_for("turn_end", turn=2)["status"] == "aborted"
    stdin.close()
    t.join(2)


def test_approval_timeout_denies(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    provider = _Provider([_toolcall("read_file", {"path": "a.txt"}, "c1"), ChatResult(content="ok", model="m")])
    h, session, stdin, out, t = _start(tmp_path, provider, timeout=0.3)
    out.wait_for("ready")
    stdin.send({"type": "prompt", "text": "read it"})
    out.wait_for("approval_request")
    notice = out.wait_for("notice")
    assert "timed out" in notice["message"]
    assert out.wait_for("turn_end")["status"] == "done"
    stdin.close()
    t.join(2)


def test_yolo_auto_approves_and_note_is_delivered(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    provider = _Provider([_toolcall("read_file", {"path": "a.txt"}, "c1"), ChatResult(content="ok", model="m")])
    h, session, stdin, out, t = _start(tmp_path, provider, approval="yolo")
    out.wait_for("ready")
    stdin.send({"type": "note", "text": "prefer short answers"})
    out.wait_for("notice")
    stdin.send({"type": "prompt", "text": "read it"})
    end = out.wait_for("turn_end")
    assert end["status"] == "done"
    assert out.wait_for("tool_call")["auto_approved"] is True
    assert not any(e["type"] == "approval_request" for e in out.events())
    # the note became a user message before the first model call
    first_call = provider.seen[0]
    assert any(m.get("role") == "user" and "prefer short answers" in m.get("content", "") for m in first_call)
    stdin.close()
    t.join(2)


def test_interrupt_cancels_a_running_turn(tmp_path):
    started = threading.Event()

    class _Slow(_Provider):
        def chat(self, messages, cancel_event=None, **kw):
            started.set()
            for _ in range(200):
                if cancel_event is not None and cancel_event.is_set():
                    from scootcli.errors import Interrupted

                    raise Interrupted("cancelled")
                time.sleep(0.02)
            return ChatResult(content="late", model="m")

    h, session, stdin, out, t = _start(tmp_path, _Slow([]), approval="yolo")
    out.wait_for("ready")
    stdin.send({"type": "prompt", "text": "slow"})
    assert started.wait(3)
    stdin.send({"type": "interrupt"})
    assert out.wait_for("turn_end")["status"] == "interrupted"
    stdin.close()
    t.join(2)


def test_commands_and_bad_input(tmp_path):
    provider = _Provider([ChatResult(content="ok", model="m")])
    h, session, stdin, out, t = _start(tmp_path, provider, approval="yolo")
    out.wait_for("ready")
    stdin.send("this is not json")
    assert "bad input line" in out.wait_for("error")["message"]
    def wait_error(text):
        deadline = time.time() + 5
        while time.time() < deadline:
            if any(e["type"] == "error" and text in e["message"] for e in out.events()):
                return
            time.sleep(0.02)
        raise AssertionError(f"no error containing {text!r}")

    stdin.send({"type": "dance"})
    wait_error("unknown message type")
    stdin.send({"type": "prompt"})
    wait_error("no text")
    stdin.send({"type": "command", "name": "help"})
    co = out.wait_for("command_output")
    assert co["name"] == "help" and "slash-commands" in co["output"]
    stdin.send({"type": "command", "name": "exit"})
    out.wait_for("bye")
    t.join(2)


# ── 0.10.0: an answer must name its request (review R20) ────────────────────────
def test_approval_answer_without_id_is_ignored():
    import queue
    import threading
    from scootcli import tools
    from scootcli.approvals import Decision
    from scootcli.headless import HeadlessUI
    from scootcli.tools.base import ToolContext

    tools.load_builtins()
    out = []

    class _W:
        def emit(self, type, **fields):
            out.append({"type": type, **fields})

    ui = HeadlessUI(_W(), queue.Queue(), timeout=1.0)
    ui.answers.put({"type": "approve", "decision": "allow"})              # no id
    ui.answers.put({"type": "approve", "id": "", "decision": "allow"})    # empty id
    ui.answers.put({"type": "approve", "id": "call_other", "decision": "allow"})
    threading.Timer(0.3, lambda: ui.answers.put({"type": "approve", "id": next(
        e["id"] for e in out if e["type"] == "approval_request"), "decision": "deny"})).start()
    approval = ui.approve(tools.get("write_file"), {"path": "x", "content": "y"}, ToolContext(root="."))
    assert approval.decision == Decision.SKIP
    errors = [e for e in out if e["type"] == "error"]
    assert len(errors) == 3 and all("must carry id" in e["message"] for e in errors)
