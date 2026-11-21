"""The session is saved while a turn runs, and on every way a turn can end."""

from __future__ import annotations

import json
import threading

import pytest

from scootcli import sessions
from scootcli.agent import Agent, AgentOutcome, HeadlessUI
from scootcli.approvals import Decision
from scootcli.config import Config
from scootcli.errors import NetworkError
from scootcli.providers import ChatResult
from scootcli.repl import Repl, ReplSession


def _toolcall(name, args, call_id="tc1"):
    return ChatResult(content="", model="m", tool_calls=[
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}])


class _Provider:
    def __init__(self, results):
        self.results, self.i = results, 0

    def chat(self, messages, **kw):
        r = self.results[min(self.i, len(self.results) - 1)]
        self.i += 1
        if isinstance(r, BaseException):
            raise r
        return r

    def list_models(self, cancel_event=None):
        return []


def _session(tmp_path, provider):
    cfg = Config().override(root=str(tmp_path), stream=False, workspace_context=False, model="openai/gpt-4o",
                            approval="always")
    return cfg, ReplSession(cfg, provider)


def _saved(session):
    rec = sessions.load(session.id)
    return None if rec is None else [m.get("role") for m in rec.messages]


def test_prompt_is_on_disk_before_the_model_is_called(tmp_path):
    seen = {}

    class _Peek(_Provider):
        def chat(self, messages, **kw):
            seen["roles"] = _saved(session)
            return super().chat(messages, **kw)

    cfg, session = _session(tmp_path, _Peek([ChatResult(content="hi\nDONE", model="m")]))
    session.messages.append({"role": "user", "content": "hello"})
    Agent(cfg, session.provider).run_turn(session, HeadlessUI(Decision.APPROVE))
    assert seen["roles"] == ["user"]


def test_tool_batch_is_on_disk_before_it_runs_and_each_result_after(tmp_path):
    snapshots = []

    class _UI(HeadlessUI):
        def approve(self, tool, args, ctx):  # called after the batch is proposed, before it runs
            snapshots.append(("before " + args["path"], _saved(session)))
            return super().approve(tool, args, ctx)

    calls = ChatResult(content="", model="m", tool_calls=[
        {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": json.dumps({"path": "a.txt", "content": "1"})}},
        {"id": "c2", "type": "function", "function": {"name": "write_file", "arguments": json.dumps({"path": "b.txt", "content": "2"})}},
    ])
    cfg, session = _session(tmp_path, _Provider([calls, ChatResult(content="ok\nDONE", model="m")]))
    session.messages.append({"role": "user", "content": "write two files"})
    Agent(cfg, session.provider).run_turn(session, _UI(Decision.APPROVE))
    assert snapshots[0] == ("before a.txt", ["user", "assistant"])
    assert snapshots[1] == ("before b.txt", ["user", "assistant", "tool"])  # the first result is saved
    saved = sessions.load(session.id)
    assert saved.messages[1]["tool_calls"][0]["id"] == "c1"


def test_a_turn_that_fails_before_any_reply_keeps_the_prompt(tmp_path):
    cfg, session = _session(tmp_path, _Provider([NetworkError("cannot resolve host")]))
    session.messages.append({"role": "user", "content": "a long prompt worth keeping"})
    outcome = Agent(cfg, session.provider).run_turn(session, HeadlessUI(Decision.APPROVE))
    assert outcome.status == "error"
    assert sessions.load(session.id).messages[0]["content"] == "a long prompt worth keeping"


def test_killed_mid_batch_resumes_with_the_batch_repaired(tmp_path):
    class _Die(BaseException):
        pass

    class _UI(HeadlessUI):
        def approve(self, tool, args, ctx):
            raise _Die()  # the process dies while a tool call is waiting

    cfg, session = _session(tmp_path, _Provider([_toolcall("write_file", {"path": "a.txt", "content": "1"})]))
    session.messages.append({"role": "user", "content": "write"})
    with pytest.raises(_Die):
        Agent(cfg, session.provider).run_turn(session, _UI(Decision.APPROVE))
    fresh = ReplSession(cfg, None)
    fresh.apply_record(sessions.load(session.id))
    assert [m["role"] for m in fresh.messages] == ["user", "assistant", "tool"]
    assert "interrupted" in fresh.messages[2]["content"]


def test_only_a_new_session_file_triggers_the_prune(tmp_path, monkeypatch):
    prunes = []
    monkeypatch.setattr(sessions, "_prune", lambda: prunes.append(1))
    cfg, session = _session(tmp_path, None)
    session.messages.append({"role": "user", "content": "x"})
    for _ in range(5):
        session.autosave()
    assert len(prunes) == 1


class _RaisingAgent:
    def __init__(self, exc):
        self.exc = exc

    def run_turn(self, session, ui, cancel):
        session.messages.append({"role": "assistant", "content": "partial work"})
        raise self.exc


@pytest.mark.parametrize("exc", [RuntimeError("boom"), KeyboardInterrupt()])
def test_repl_saves_when_a_turn_raises(tmp_path, exc):
    repl = Repl(Config().override(root=str(tmp_path), panel=False), provider=None)
    repl.agent = _RaisingAgent(exc)
    with pytest.raises(type(exc)):
        repl._run_turn("do it")
    assert _saved(repl.session) == ["user", "assistant"]


def test_one_shot_saves_when_the_turn_raises(tmp_path, monkeypatch):
    from scootcli import agent as agent_mod, cli
    from scootcli.providers import registry

    monkeypatch.setattr(registry, "readiness", lambda config: (True, ""))

    def boom(self, session, ui, cancel_event=None):
        session.messages.append({"role": "assistant", "content": "partial work"})
        raise RuntimeError("boom")

    monkeypatch.setattr(agent_mod.Agent, "run_turn", boom)
    cfg = Config().override(root=str(tmp_path), workspace_context=False)
    with pytest.raises(RuntimeError):
        cli._run_once(cfg, None, "do it", as_json=True)
    rec = sessions.latest_for_root(str(tmp_path))
    assert rec is not None and [m["role"] for m in rec.messages] == ["user", "assistant"]
