"""Network-free tests for the agent loop, using a scripted fake client.

Run: PYTHONPATH=src python3 tests/test_agent.py
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from scootcli.agent import Agent, HeadlessUI, _strip_done
from scootcli.approvals import Decision
from scootcli.providers import ChatResult
from scootcli.config import Config


class FakeClient:
    """Returns scripted ChatResults; repeats the last one if the script is exhausted."""

    def __init__(self, results):
        self.results = results
        self.i = 0

    def chat(self, *args, **kwargs):
        r = self.results[min(self.i, len(self.results) - 1)]
        self.i += 1
        return r


class FakeSession:
    def __init__(self):
        self.messages = []
        self.model = "gpt-4o"
        self.active_model = "gpt-4o"

    def resolved_model(self):
        return "gpt-4o"

    def available_models(self):
        return ["gpt-4o"]

    def account(self, usage):
        pass


def _toolcall(name, args, call_id="tc1"):
    return ChatResult(
        content="",
        model="m",
        tool_calls=[{"id": call_id, "type": "function",
                     "function": {"name": name, "arguments": json.dumps(args)}}],
    )


def _agent(root, results):
    cfg = Config().override(root=str(root))
    return Agent(cfg, FakeClient(results)), FakeSession()


def test_strip_done():
    assert _strip_done("answer\nDONE") == "answer"
    assert _strip_done("answer") == "answer"
    assert _strip_done("line1\nline2\nDONE\n") == "line1\nline2"


def test_tool_call_then_done_creates_file():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        agent, session = _agent(root, [
            _toolcall("write_file", {"path": "a.txt", "content": "hi"}),
            ChatResult(content="Created a.txt.\nDONE", model="m"),
        ])
        outcome = agent.run_turn(session, HeadlessUI(Decision.APPROVE))
        assert outcome.status == "done"
        assert outcome.content == "Created a.txt."
        assert (root / "a.txt").read_text() == "hi"
        # A tool result message was fed back to the model.
        assert any(m.get("role") == "tool" for m in session.messages)


def test_skip_does_not_execute():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        agent, session = _agent(root, [
            _toolcall("write_file", {"path": "b.txt", "content": "nope"}),
            ChatResult(content="ok, skipped.\nDONE", model="m"),
        ])
        outcome = agent.run_turn(session, HeadlessUI(Decision.SKIP))
        assert outcome.status == "done"
        assert not (root / "b.txt").exists()
        assert any("declined" in m.get("content", "") for m in session.messages if m["role"] == "tool")


def test_abort_stops_turn():
    with tempfile.TemporaryDirectory() as d:
        agent, session = _agent(Path(d), [
            _toolcall("write_file", {"path": "c.txt", "content": "x"}),
        ])
        outcome = agent.run_turn(session, HeadlessUI(Decision.ABORT))
        assert outcome.status == "aborted"
        assert not (Path(d) / "c.txt").exists()


def test_max_steps_guard():
    with tempfile.TemporaryDirectory() as d:
        cfg = Config().override(root=d, max_steps=3)
        # Always returns a (harmless) read tool call -> never finishes.
        client = FakeClient([_toolcall("list_dir", {"path": "."})])
        agent = Agent(cfg, client)
        session = FakeSession()
        outcome = agent.run_turn(session, HeadlessUI(Decision.APPROVE))
        assert outcome.status == "max_steps"


def test_unknown_tool_is_reported_not_fatal():
    with tempfile.TemporaryDirectory() as d:
        agent, session = _agent(Path(d), [
            _toolcall("nonexistent_tool", {}),
            ChatResult(content="handled.\nDONE", model="m"),
        ])
        outcome = agent.run_turn(session, HeadlessUI(Decision.APPROVE))
        assert outcome.status == "done"
        assert any("unknown tool" in m.get("content", "") for m in session.messages if m["role"] == "tool")


def test_update_plan_flows_to_session_and_ui():
    with tempfile.TemporaryDirectory() as d:
        plan = [
            {"step": "read config", "status": "completed"},
            {"step": "add flag", "status": "in_progress"},
            {"step": "test", "status": "pending"},
        ]
        agent, session = _agent(Path(d), [
            _toolcall("update_plan", {"plan": plan}),
            ChatResult(content="done.\nDONE", model="m"),
        ])
        ui = HeadlessUI(Decision.APPROVE)  # would prompt, but update_plan is auto-approved
        outcome = agent.run_turn(session, ui)
        assert outcome.status == "done"
        # The plan is stored on the session and rendered via ui.plan (not the generic tool line).
        assert getattr(session, "plan", None) == plan
        assert any(e[0] == "plan" for e in ui.events)
        assert not any(e[0] == "approve" and e[1] == "update_plan" for e in ui.events)


if __name__ == "__main__":
    import types

    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            fn()
            print(f"ok  {name}")
            passed += 1
    print(f"\n{passed} passed")



def test_failed_tool_output_reaches_the_model():
    """R09: a failing command's diagnostics are the payload, not just ``ERROR: exit 1``."""
    with tempfile.TemporaryDirectory() as d:
        agent, session = _agent(Path(d), [
            _toolcall("run_shell", {"command": "echo 'useful diagnostic' >&2; exit 1"}),
            ChatResult(content="I see the diagnostic.\nDONE", model="m"),
        ])
        outcome = agent.run_turn(session, HeadlessUI(Decision.APPROVE))
        assert outcome.status == "done"
        payload = [m["content"] for m in session.messages if m.get("role") == "tool"][0]
        assert payload.startswith("ERROR: exit 1")
        assert "useful diagnostic" in payload
        assert payload.count("exit 1") == 1  # the exit line is not repeated
