"""/error: the latest failure of a session, kept outside the conversation."""

from __future__ import annotations

import io
import json
import os
import stat
from contextlib import redirect_stdout

from scootcli import commands, diagnostics, sessions
from scootcli.agent import Agent, HeadlessUI
from scootcli.approvals import Decision
from scootcli.config import Config
from scootcli.errors import ApiError, NetworkError, ServerError
from scootcli.repl import Repl, ReplSession


def _raise_chain(secret):
    def inner(payload):
        token = secret  # a local that must never be recorded
        raise ConnectionRefusedError(61, f"refused {token} {payload}")

    try:
        inner({"prompt": secret})
    except OSError:
        raise NetworkError(f"connection failed for {secret}", hint="is the endpoint up?")


def _caught(secret="sk-" + "a" * 40):
    try:
        _raise_chain(secret)
    except NetworkError as exc:
        return exc


def test_describe_is_metadata_only():
    secret = "hunter2-very-private-value"
    info = diagnostics.describe(_caught(secret))
    assert info["kind"] == "NetworkError"
    assert info["errno"] == 61
    assert "ConnectionRefusedError" in info["causes"]
    assert info["frames"] and set(info["frames"][-1]) == {"file", "line", "func"}
    assert info["frames"][-1]["func"] == "_raise_chain"
    assert secret not in json.dumps(info)  # no exception text, no locals
    assert os.path.expanduser("~") not in json.dumps(info)  # no home directory in the locations


def test_describe_keeps_http_status_and_code():
    info = diagnostics.describe(ServerError("upstream died", status=503, code="overloaded", payload={"x": "secret-payload"}))
    assert (info["status"], info["code"]) == (503, "overloaded")
    assert "secret-payload" not in json.dumps(info)


def test_record_is_owner_only_capped_and_survives_a_restart(tmp_path):
    cfg = Config().override(root=str(tmp_path))
    s = ReplSession(cfg, None)
    s.messages.append({"role": "user", "content": "x"})
    s.autosave()
    s.record_error(ApiError("bad " + "y" * 5000 + " ghp_" + "A" * 36, status=400, hint="h" * 5000))
    path = diagnostics.path_for(s.id)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.stat().st_size <= diagnostics.MAX_BYTES
    assert "ghp_" not in path.read_text()
    assert s.last_error == "ApiError"

    again = ReplSession(cfg, None)
    again.apply_record(sessions.load(s.id))
    rec = diagnostics.load(again.id)
    assert rec["kind"] == "ApiError" and rec["status"] == 400


def test_unexpected_exception_text_is_not_recorded(tmp_path):
    s = ReplSession(Config().override(root=str(tmp_path)), None)
    try:
        raise KeyError("the-users-private-key-name")
    except KeyError as exc:
        s.record_error(exc)
    text = diagnostics.path_for(s.id).read_text()
    assert "the-users-private-key-name" not in text
    assert json.loads(text)["kind"] == "KeyError" and json.loads(text)["frames"]


def test_error_survives_compaction_and_is_replaced_by_the_next_one(tmp_path):
    s = ReplSession(Config().override(root=str(tmp_path)), None)
    s.record_error(NetworkError("first"))
    s.messages[:] = [{"role": "user", "content": "summary of earlier work"}]  # what compaction leaves
    assert diagnostics.load(s.id)["message"] == "first"
    s.record_error(ServerError("second", status=500))
    assert diagnostics.load(s.id)["message"] == "second"


def test_forget_removes_the_record(tmp_path):
    s = ReplSession(Config().override(root=str(tmp_path)), None)
    s.messages.append({"role": "user", "content": "x"})
    s.autosave()
    s.record_error(NetworkError("down"))
    assert diagnostics.path_for(s.id).exists()
    assert sessions.delete(s.id)
    assert not diagnostics.path_for(s.id).exists()


def test_agent_error_outcome_carries_the_detail(tmp_path):
    class _P:
        def chat(self, messages, **kw):
            raise ServerError("upstream died", status=503)

        def list_models(self, cancel_event=None):
            return []

    cfg = Config().override(root=str(tmp_path), stream=False, workspace_context=False, model="openai/gpt-4o")
    s = ReplSession(cfg, _P())
    s.messages.append({"role": "user", "content": "x"})
    outcome = Agent(cfg, s.provider).run_turn(s, HeadlessUI(Decision.APPROVE))
    assert outcome.status == "error"
    assert outcome.detail["kind"] == "ServerError" and outcome.detail["status"] == 503
    assert any(f["file"].endswith("agent.py") for f in outcome.detail["frames"])


def _run_command(session, name, args=""):
    commands.load_builtins()
    buf = io.StringIO()
    with redirect_stdout(buf):
        commands.get(name).handler(session, args)
    return buf.getvalue()


def test_error_command_shows_the_latest_and_says_when_there_is_none(tmp_path):
    s = ReplSession(Config().override(root=str(tmp_path)), None)
    assert "no error" in _run_command(s, "error")
    s.record_error(_caught("zzz"))
    out = _run_command(s, "error")
    assert "NetworkError" in out and "errno 61" in out and "is the endpoint up?" in out
    assert "_raise_chain" in out


def test_repl_records_outcome_errors_and_unexpected_ones(tmp_path):
    from scootcli.agent import AgentOutcome

    class _A:
        def run_turn(self, session, ui, cancel):
            return AgentOutcome("error", error="upstream died", steps=1,
                                detail=diagnostics.describe(ServerError("upstream died", status=503)))

    repl = Repl(Config().override(root=str(tmp_path), panel=False), provider=None)
    repl.agent = _A()
    repl._run_turn("go")
    assert repl.session.last_error == "ServerError"
    assert diagnostics.load(repl.session.id)["status"] == 503
    repl._report_turn_error(RuntimeError("boom"))
    assert diagnostics.load(repl.session.id)["kind"] == "RuntimeError"
