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


def test_aborting_a_batch_answers_every_tool_call():
    """R11: an aborted batch leaves no tool call without a result."""
    with tempfile.TemporaryDirectory() as d:
        agent, session = _agent(Path(d), [ChatResult(
            content="", model="m",
            tool_calls=[{"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": json.dumps({"path": "a", "content": "1"})}},
                        {"id": "c2", "type": "function", "function": {"name": "write_file", "arguments": json.dumps({"path": "b", "content": "2"})}}],
        )])
        outcome = agent.run_turn(session, HeadlessUI(Decision.ABORT))
        assert outcome.status == "aborted"
        answered = [m["tool_call_id"] for m in session.messages if m.get("role") == "tool"]
        assert answered == ["c1", "c2"]
        assert not (Path(d) / "a").exists() and not (Path(d) / "b").exists()


def test_malformed_arguments_do_not_run_the_tool():
    with tempfile.TemporaryDirectory() as d:
        agent, session = _agent(Path(d), [
            ChatResult(content="", model="m", tool_calls=[{"id": "c1", "type": "function", "function": {
                "name": "write_file", "arguments": '{"path": "cut.txt", "content": "half'}}]),
            ChatResult(content="retried.\nDONE", model="m"),
        ])
        outcome = agent.run_turn(session, HeadlessUI(Decision.APPROVE))
        assert outcome.status == "done"
        assert not (Path(d) / "cut.txt").exists()
        tool_msgs = [m["content"] for m in session.messages if m.get("role") == "tool"]
        assert tool_msgs and "not a JSON object" in tool_msgs[0]


def test_length_finish_is_incomplete_not_done():
    """R12: a reply cut at the output limit is reported as incomplete, with the partial text."""
    with tempfile.TemporaryDirectory() as d:
        agent, session = _agent(Path(d), [ChatResult(content="first half of", model="m", finish_reason="length")])
        outcome = agent.run_turn(session, HeadlessUI(Decision.APPROVE))
        assert outcome.status == "incomplete"
        assert outcome.content == "first half of"
        assert "output limit" in outcome.error


def test_cut_off_tool_calls_never_run_and_retries_are_bounded():
    with tempfile.TemporaryDirectory() as d:
        cut = ChatResult(content="", model="m", finish_reason="length", tool_calls=[
            {"id": "c1", "type": "function", "function": {"name": "write_file",
                                                          "arguments": json.dumps({"path": "x.txt", "content": "looks complete"})}}])
        agent, session = _agent(Path(d), [cut, cut, cut])
        outcome = agent.run_turn(session, HeadlessUI(Decision.APPROVE))
        assert outcome.status == "incomplete"
        assert not (Path(d) / "x.txt").exists()
        assert outcome.steps == 2
        tool_msgs = [m for m in session.messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 2 and all("cut off" in m["content"] for m in tool_msgs)


def test_fallback_never_alternates_between_two_dead_models(monkeypatch):
    """R16: even a router that keeps offering an already-failed model makes fallback give up."""
    with tempfile.TemporaryDirectory() as d:
        agent, session = _agent(Path(d), [ChatResult(content="x", model="m")])
        session.bad_models = set()
        session.active_model = "A"
        picks = iter(["B", "A", "B"])  # a picker that would loop A <-> B forever
        monkeypatch.setattr(agent, "_pick_model", lambda s: next(picks))
        assert agent._fallback_model(session) is True and session.active_model == "B"
        assert agent._fallback_model(session) is False  # A is already bad -> stop, no cycle


def test_oneshot_json_ui_keeps_stdout_clean_and_declines_prompts():
    """R18: the one-shot JSON UI writes nothing to stdout; the plan and declines go to stderr."""
    import io
    from contextlib import redirect_stderr, redirect_stdout

    from scootcli.repl import OneShotJsonUI

    with tempfile.TemporaryDirectory() as d:
        agent, session = _agent(Path(d), [
            _toolcall("update_plan", {"plan": [{"step": "a", "status": "completed"},
                                               {"step": "b", "status": "pending"}]}),
            _toolcall("write_file", {"path": "x.txt", "content": "no"}),  # a write prompts in 'always'
            ChatResult(content="done.\nDONE", model="m"),
        ])
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            outcome = agent.run_turn(session, OneShotJsonUI())
        assert outcome.status == "done"
        assert out.getvalue() == "", "the JSON one-shot UI must never touch stdout"
        assert not (Path(d) / "x.txt").exists(), "an approval that cannot be answered is declined"
        assert "plan" in err.getvalue() and "declined" in err.getvalue()


def test_turn_usage_sums_the_whole_turn_not_the_last_call():
    """Item 2: usage reflects every model call in the turn, not just the final one."""
    from scootcli.config import Config
    from scootcli.repl import ReplSession

    with tempfile.TemporaryDirectory() as d:
        cfg = Config().override(root=d, workspace_context=False, stream=False)
        # Two model calls: a tool call (usage A), then the final answer (usage B).
        r1 = _toolcall("list_dir", {"path": "."}, "c1"); r1.usage = {"prompt_tokens": 100, "completion_tokens": 20}
        r2 = ChatResult(content="done.\nDONE", model="m", usage={"prompt_tokens": 130, "completion_tokens": 15})
        agent = Agent(cfg, FakeClient([r1, r2]))
        session = ReplSession(cfg, None)
        session.messages.append({"role": "user", "content": "go"})
        outcome = agent.run_turn(session, HeadlessUI(Decision.APPROVE))
        assert outcome.status == "done"
        # last call was 130/15, but the turn is the sum of both calls.
        assert session.turn_usage() == {"prompt_tokens": 230, "completion_tokens": 35, "total_tokens": 265}


def test_proactive_compaction_runs_before_a_large_request():
    """Item B: when the context is over the threshold, the agent compacts before the model call,
    in the shared loop (so headless and one-shot get it too)."""
    from scootcli.config import Config
    from scootcli.repl import ReplSession

    with tempfile.TemporaryDirectory() as d:
        cfg = Config().override(root=d, workspace_context=False, stream=False, compact_at=50)
        # A summary call, then the real answer.
        summary = ChatResult(content="SUMMARY of the earlier chat", model="m", usage={"prompt_tokens": 20, "completion_tokens": 10})
        answer = ChatResult(content="done.\nDONE", model="m", usage={"prompt_tokens": 5, "completion_tokens": 3})
        agent = Agent(cfg, FakeClient([summary, answer]))
        session = ReplSession(cfg, None)
        session.provider = agent.provider  # compact() calls session.provider.chat
        # A big history so the char-estimate exceeds compact_at=50, ending on a user turn.
        session.messages = [{"role": "user", "content": "x" * 400},
                            {"role": "assistant", "content": "y" * 400},
                            {"role": "user", "content": "now finish"}]
        outcome = agent.run_turn(session, HeadlessUI(Decision.APPROVE))
        assert outcome.status == "done"
        # The older turns were replaced by a summary; the last user turn is kept verbatim.
        assert session.messages[0]["content"].startswith("Summary of earlier conversation")
        assert any(m.get("content") == "now finish" for m in session.messages)
        assert "SUMMARY of the earlier chat" in session.messages[0]["content"]


def test_no_compaction_under_the_threshold():
    from scootcli.config import Config
    from scootcli.repl import ReplSession

    with tempfile.TemporaryDirectory() as d:
        cfg = Config().override(root=d, workspace_context=False, stream=False, compact_at=100000)
        agent = Agent(cfg, FakeClient([ChatResult(content="ok.\nDONE", model="m")]))
        session = ReplSession(cfg, None)
        session.provider = agent.provider
        session.messages = [{"role": "user", "content": "short"}]
        outcome = agent.run_turn(session, HeadlessUI(Decision.APPROVE))
        assert outcome.status == "done"
        assert session.messages[0]["content"] == "short"  # untouched


def test_agent_counts_executed_tools_per_turn():
    """0.13.0: turn_tool_calls tracks tools run this turn, for the live status bar."""
    from scootcli.config import Config
    from scootcli.repl import ReplSession

    with tempfile.TemporaryDirectory() as d:
        cfg = Config().override(root=d, workspace_context=False, stream=False)
        agent = Agent(cfg, FakeClient([
            _toolcall("list_dir", {"path": "."}, "c1"),
            _toolcall("list_dir", {"path": "."}, "c2"),
            ChatResult(content="done.\nDONE", model="m"),
        ]))
        session = ReplSession(cfg, None)
        session.messages.append({"role": "user", "content": "go"})
        outcome = agent.run_turn(session, HeadlessUI(Decision.APPROVE))
        assert outcome.status == "done"
        assert session.turn_tool_calls == 2  # two tools ran; start_turn zeroed it first
        # A second turn resets the count.
        agent2 = Agent(cfg, FakeClient([ChatResult(content="ok.\nDONE", model="m")]))
        session.messages.append({"role": "user", "content": "again"})
        agent2.run_turn(session, HeadlessUI(Decision.APPROVE))
        assert session.turn_tool_calls == 0


def test_replui_refreshes_the_bar_at_completion_boundaries():
    """0.13.0: ReplUI fires on_refresh after a streamed call and after an activity (tool/model)."""
    import threading

    from scootcli.repl import ReplUI

    calls = []
    ui = ReplUI(on_refresh=lambda: calls.append(1))
    ui._tty = True  # the refresh is gated on a TTY, which pytest's captured stdout is not
    with ui.activity("running…", threading.Event()):
        pass
    assert len(calls) == 1
    with ui.stream(threading.Event()):
        pass
    assert len(calls) == 2
    # No callback: nothing fires, nothing breaks.
    quiet = ReplUI()
    quiet._tty = True
    with quiet.activity("x", threading.Event()):
        pass
