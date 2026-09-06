"""Scope: the workspace is the default, not a wall. Outside paths ask once and can be granted."""

from __future__ import annotations

import json
import os
from pathlib import Path

from scootcli import tools
from scootcli.agent import Agent, HeadlessUI
from scootcli.config import Config
from scootcli.providers import ChatResult
from scootcli.tools.base import PathEscapeError, Scope, ToolContext, resolve_path, safe_path


def test_scope_allows_workspace_grants_and_everything(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    s = Scope(ws)
    assert s.allows(ws / "a.txt") and not s.allows(other / "b.txt")
    s.grant(other / "b.txt")
    assert s.allows(other / "b.txt") and not s.allows(other / "c.txt")
    s.grant_dir(other / "c.txt")
    assert s.allows(other / "c.txt") and s.allows(other / "deep" / "d.txt")
    assert "granted" in s.describe()
    s.grant_all()
    assert s.allows("/etc/hosts") and "anywhere" in s.describe()


def test_safe_path_expands_home_and_honours_scope(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_path(ws, "~/x.txt") == (tmp_path / "x.txt").resolve()
    assert safe_path(ws, "inside.txt") == (ws / "inside.txt").resolve()
    try:
        safe_path(ws, "~/x.txt")
        assert False
    except PathEscapeError as exc:
        assert exc.path == (tmp_path / "x.txt").resolve() and "user can allow" in str(exc)
    scope = Scope(ws)
    scope.grant_dir(tmp_path)
    assert safe_path(ws, "~/x.txt", scope) == (tmp_path / "x.txt").resolve()
    assert safe_path(ws, "../x.txt", Scope(ws, everything=True)) == (tmp_path / "x.txt").resolve()


def _toolcall(name, args, cid):
    return ChatResult(content="", model="m", tool_calls=[{"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}])


class _Provider:
    def __init__(self, results):
        self.results, self.i = results, 0

    def chat(self, *a, **k):
        r = self.results[min(self.i, len(self.results) - 1)]
        self.i += 1
        return r


class _S:
    def __init__(self, root, **cfg):
        self.messages, self.model, self.active_model = [], "m", "m"
        self.approval_mode = "yolo"
        self.bad_models, self.trusted_tools = set(), set()
        self.config = Config().override(root=str(root), stream=False, workspace_context=False, **cfg)
        self.last_usage = {}

    def resolved_model(self):
        return "m"

    def available_models(self):
        return ["m"]

    def account(self, usage, model=""):
        pass


def _run(root, results, decision="dir", **cfg):
    tools.load_builtins()
    s = _S(root, **cfg)
    ui = HeadlessUI()
    ui.scope_decision = decision
    out = Agent(s.config, _Provider(results)).run_turn(s, ui)
    return s, ui, out


def test_agent_asks_once_then_reads_outside_the_workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    secret = tmp_path / "elsewhere" / "notes.txt"
    secret.parent.mkdir()
    secret.write_text("outside content")
    results = [_toolcall("read_file", {"path": str(secret)}, "c1"),
               _toolcall("read_file", {"path": str(secret.parent / "other.txt")}, "c2"),
               ChatResult(content="done", model="m")]
    (secret.parent / "other.txt").write_text("second file")
    s, ui, out = _run(ws, results, decision="dir")
    scope_events = [e for e in ui.events if e[0] == "scope"]
    assert len(scope_events) == 1 and scope_events[0][2] == str(secret)  # asked once, the dir was granted
    tool_msgs = [m["content"] for m in s.messages if m["role"] == "tool"]
    assert "outside content" in tool_msgs[0] and "second file" in tool_msgs[1]
    assert s.scope.granted == [secret.parent.resolve()]


def test_agent_deny_abort_once_and_all(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    target = tmp_path / "x" / "f.txt"
    target.parent.mkdir()
    target.write_text("x")
    call = [_toolcall("read_file", {"path": str(target)}, "c1"), ChatResult(content="done", model="m")]
    s, ui, out = _run(ws, call, decision="deny")
    assert out.status == "done"
    assert [m["content"] for m in s.messages if m["role"] == "tool"][0].startswith("user declined access outside the workspace")
    s, ui, out = _run(ws, call, decision="abort")
    assert out.status == "aborted"
    s, ui, out = _run(ws, call, decision="once")
    assert s.scope.granted == [target.resolve()] and not s.scope.allows(target.parent / "g.txt")
    s, ui, out = _run(ws, call, decision="all")
    assert s.scope.everything and s.scope.allows("/etc/hosts")


def test_scope_anywhere_never_asks(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    target = tmp_path / "y.txt"
    target.write_text("y")
    call = [_toolcall("read_file", {"path": str(target)}, "c1"), ChatResult(content="done", model="m")]
    s, ui, out = _run(ws, call, decision="deny", scope="anywhere")
    assert not any(e[0] == "scope" for e in ui.events)
    assert "y" in [m["content"] for m in s.messages if m["role"] == "tool"][0]
    # write and shell cwd outside the workspace go through the same gate
    out_file = tmp_path / "z" / "new.txt"
    calls = [_toolcall("write_file", {"path": str(out_file), "content": "hi"}, "c1"),
             _toolcall("run_shell", {"command": "pwd", "cwd": str(tmp_path / "z")}, "c2"),
             ChatResult(content="done", model="m")]
    s, ui, out = _run(ws, calls, decision="dir")
    assert out_file.read_text() == "hi" and len([e for e in ui.events if e[0] == "scope"]) == 1
    assert str((tmp_path / "z").resolve()) in [m["content"] for m in s.messages if m["role"] == "tool"][1]


def test_yes_flag_implies_scope_anywhere_and_env_setting(monkeypatch, tmp_path):
    from scootcli.cli import _build_parser, _config_from_args

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert _config_from_args(_build_parser().parse_args(["--yes", "x"])).scope == "anywhere"
    assert _config_from_args(_build_parser().parse_args(["--scope", "anywhere", "x"])).scope == "anywhere"
    assert _config_from_args(_build_parser().parse_args(["x"])).scope == "workspace"
    monkeypatch.setenv("SCOOT_SCOPE", "anywhere")
    assert Config.load(cwd=tmp_path).scope == "anywhere"


def test_prompt_no_longer_claims_a_wall():
    from scootcli.prompts import AGENT_SYSTEM_PROMPT

    assert "must stay inside the root" not in AGENT_SYSTEM_PROMPT
    assert "asks the user once" in AGENT_SYSTEM_PROMPT


def test_headless_scope_request_round_trip(tmp_path):
    import queue
    import threading
    import time
    import io

    from scootcli.headless import Headless

    ws = tmp_path / "ws"
    ws.mkdir()
    target = tmp_path / "far.txt"
    target.write_text("far away")
    provider = _Provider([_toolcall("read_file", {"path": str(target)}, "c1"), ChatResult(content="ok", model="m")])
    s = _S(ws)
    s.id, s.provider, s.resumed, s.autosave = "hl", provider, False, lambda: None

    class _In:
        def __init__(self):
            self.q = queue.Queue()

        def __iter__(self):
            while True:
                l = self.q.get()
                if l is None:
                    return
                yield l + "\n"

    class _Out:
        def __init__(self):
            self.lines, self.lock = [], threading.Lock()

        def write(self, t):
            with self.lock:
                self.lines.append(t)

        def flush(self):
            pass

        def events(self):
            with self.lock:
                return [json.loads(l) for l in "".join(self.lines).splitlines() if l.strip()]

    inp, out = _In(), _Out()
    h = Headless(s, Agent(s.config, provider), stdin=inp, stdout=out, approval_timeout_s=5, heartbeat_s=0)
    threading.Thread(target=h.run, daemon=True).start()

    def wait(t):
        end = time.time() + 5
        while time.time() < end:
            for e in out.events():
                if e["type"] == t:
                    return e
            time.sleep(0.02)
        raise AssertionError(t)

    wait("ready")
    inp.q.put(json.dumps({"type": "prompt", "text": "read far.txt"}))
    req = wait("scope_request")
    assert req["name"] == "read_file" and req["path"] == str(target.resolve()) and "allow_dir" in req["options"]
    inp.q.put(json.dumps({"type": "approve", "id": req["id"], "decision": "allow_dir"}))
    end = wait("turn_end")
    assert end["status"] == "done" and "far away" in wait("tool_result")["content"]
    inp.q.put(None)
