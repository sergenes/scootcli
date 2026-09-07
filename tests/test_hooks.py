"""Hooks: configuration merge, every outcome a script can produce, matchers, and the call sites."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from scootcli import hooks as H
from scootcli.config import Config

PY = sys.executable


def _script(tmp: Path, name: str, body: str) -> str:
    """Write a small Python hook script and return the shell command that runs it."""
    p = tmp / f"{name}.py"
    p.write_text("import json, sys\npayload = json.loads(sys.stdin.read())\n" + body)
    return f'"{PY}" "{p}"'


def _hooks(root: Path, config: dict) -> H.Hooks:
    return H.Hooks(root, config=config)


class _Session:
    def __init__(self, root):
        self.id = "sess-1"
        self.config = Config().override(root=str(root))
        self.active_model = "openai/gpt-5.3-codex"
        self.last_usage = {}
        self.messages = []


# ── config loading + merge ─────────────────────────────────────────────────────
def test_config_merge_project_first_and_claude_code_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("SCOOT_CONFIG_DIR", str(tmp_path / "cfg"))
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "hooks.json").write_text(json.dumps({"hooks": {  # nested "hooks" key as Claude Code writes it
        "Stop": [{"hooks": [{"type": "command", "command": "echo global"}]}]}}))
    root = tmp_path / "repo"
    (root / ".scoot").mkdir(parents=True)
    (root / ".scoot" / "hooks.json").write_text(json.dumps({
        "Stop": [{"hooks": [{"type": "command", "command": "echo project"}]}],
        "Bogus": [{"hooks": [{"command": "echo never"}]}],
        "PreToolUse": "not a list"}))
    h = H.Hooks(root)
    assert [Path(s).name for s in h.sources] == ["hooks.json", "hooks.json"] and "repo" in h.sources[0]
    assert [e["hooks"][0]["command"] for e in h.config["Stop"]] == ["echo project", "echo global"]
    assert "Bogus" not in h.config and "PreToolUse" not in h.config
    assert h.has("Stop") and not h.has("PreToolUse")


def test_disabled_by_env(monkeypatch, tmp_path):
    h = _hooks(tmp_path, {"Stop": [{"hooks": [{"command": "exit 2"}]}]})
    monkeypatch.setenv("SCOOT_HOOKS", "0")
    assert not h.has("Stop") and h.run("Stop", {}).action == ""


# ── outcomes ───────────────────────────────────────────────────────────────────
def test_every_outcome_a_script_can_produce(tmp_path):
    allow = _script(tmp_path, "allow", 'print(json.dumps({"permissionDecision": "allow", "reason": "trusted"}))')
    deny = _script(tmp_path, "deny", 'print(json.dumps({"permissionDecision": "deny", "reason": "nope"}))')
    ask = _script(tmp_path, "ask", 'print(json.dumps({"permissionDecision": "ask", "reason": "check"}))')
    block2 = _script(tmp_path, "block2", 'sys.stderr.write("policy says no"); sys.exit(2)')
    crash = _script(tmp_path, "crash", 'sys.exit(1)')
    badjson = _script(tmp_path, "badjson", 'print("{not json")')
    context = _script(tmp_path, "context", 'print("remember: use pytest")')
    slow = _script(tmp_path, "slow", 'import time; time.sleep(5)')
    payload = {"tool_name": "run_shell", "tool_input": {"command": "ls"}}

    def one(cmd, event="PreToolUse", timeout=60):
        return _hooks(tmp_path, {event: [{"hooks": [{"command": cmd, "timeout": timeout}]}]}).run(event, payload)

    assert one(allow).action == "allow" and one(allow).reason == "trusted"
    assert one(deny).action == "deny" and one(deny).blocks
    assert one(ask).action == "ask"
    d = one(block2)
    assert d.action == "deny" and d.reason == "policy says no"
    assert one(block2, event="Stop").action == "block"
    assert one(crash).action == "" and not one(crash).blocks
    assert one(badjson).action == ""
    c = one(context, event="UserPromptSubmit")
    assert c.action == "" and c.context == "remember: use pytest"
    t = one(slow, timeout=1)
    assert t.action == ""
    h = _hooks(tmp_path, {"PreToolUse": [{"hooks": [{"command": slow, "timeout": 1}]}]})
    h.run("PreToolUse", payload)
    assert h.history[-1].outcome == "timeout"


def test_matcher_and_first_blocking_decision_wins(tmp_path):
    deny = _script(tmp_path, "deny", 'print(json.dumps({"permissionDecision": "deny", "reason": "first"}))')
    allow = _script(tmp_path, "allow", 'print(json.dumps({"permissionDecision": "allow"}))')
    h = _hooks(tmp_path, {"PreToolUse": [
        {"matcher": "write_file|edit_file", "hooks": [{"command": deny}]},
        {"hooks": [{"command": allow}]},
    ]})
    assert h.run("PreToolUse", {"tool_name": "read_file"}).action == "allow"   # matcher skipped the deny
    assert h.run("PreToolUse", {"tool_name": "edit_file"}).action == "deny"    # first blocking wins
    assert h.run("PreToolUse", {"tool_name": "edit_file"}).reason == "first"


def test_payload_and_environment_reach_the_script(tmp_path):
    echo = _script(tmp_path, "echo", 'import os\nprint(json.dumps({"context": payload["hook_event_name"] + ":" + payload["tool_name"] + ":" + os.environ["SCOOT_HOOK_EVENT"] + ":" + os.environ["SCOOT_SESSION_ID"]}))')
    h = _hooks(tmp_path, {"PostToolUse": [{"hooks": [{"command": echo}]}]})
    s = _Session(tmp_path)
    payload = h.payload(s, "PostToolUse", tool_name="search", tool_input={})
    assert payload["session_id"] == "sess-1" and payload["cwd"] == str(tmp_path.resolve()) and payload["transcript_path"].endswith("sess-1.json")
    d = h.run("PostToolUse", payload)
    assert d.context == "PostToolUse:search:PostToolUse:sess-1"


# ── call sites ─────────────────────────────────────────────────────────────────
def test_submit_prompt_block_and_context(tmp_path):
    s = _Session(tmp_path)
    block = _script(tmp_path, "block", 'sys.stderr.write("no secrets in prompts"); sys.exit(2)')
    s.hooks = _hooks(tmp_path, {"UserPromptSubmit": [{"hooks": [{"command": block}]}]})
    assert H.submit_prompt(s, "here is my password") is None and s.hook_block_reason == "no secrets in prompts"
    ctx = _script(tmp_path, "ctx", 'print("branch: main")')
    s.hooks = _hooks(tmp_path, {"UserPromptSubmit": [{"hooks": [{"command": ctx}]}]})
    assert H.submit_prompt(s, "hi") == "hi\n\n[context from hook]\nbranch: main"
    s.hooks = _hooks(tmp_path, {})
    assert H.submit_prompt(s, "hi") == "hi"


def test_agent_pre_tool_deny_allow_ask_and_post_tool(tmp_path):
    from scootcli import tools
    from scootcli.agent import Agent, HeadlessUI
    from scootcli.providers import ChatResult

    log = tmp_path / "post.log"
    deny_shell = _script(tmp_path, "denyshell", 'print(json.dumps({"permissionDecision": "deny", "reason": "no shell today"}))')
    post = _script(tmp_path, "post", f'open(r"{log}", "a").write(payload["tool_name"] + ":" + str(payload["tool_response"]["ok"]) + ":" + payload["tool_kind"] + "\\n")')
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("hello")

    def call(name, args, cid):
        return ChatResult(content="", model="m", tool_calls=[{"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}])

    class _Provider:
        def __init__(self, results):
            self.results, self.i = results, 0

        def chat(self, *a, **k):
            r = self.results[min(self.i, len(self.results) - 1)]
            self.i += 1
            return r

    class _S(_Session):
        def __init__(self, root):
            super().__init__(root)
            self.model = "m"
            self.approval_mode = "yolo"
            self.bad_models = set()
            self.config = Config().override(root=str(root), stream=False, workspace_context=False)

        def resolved_model(self):
            return "m"

        def available_models(self):
            return ["m"]

        def account(self, usage, model=""):
            pass

    tools.load_builtins()
    s = _S(root)
    s.hooks = _hooks(root, {"PreToolUse": [{"matcher": "run_shell", "hooks": [{"command": deny_shell}]}],
                            "PostToolUse": [{"hooks": [{"command": post}]}]})
    provider = _Provider([call("run_shell", {"command": "echo hi"}, "c1"), call("read_file", {"path": "a.txt"}, "c2"),
                          ChatResult(content="done", model="m")])
    ui = HeadlessUI()
    out = Agent(s.config, provider).run_turn(s, ui)
    assert out.status == "done"
    tool_msgs = [m for m in s.messages if m.get("role") == "tool"]
    assert tool_msgs[0]["content"] == "user declined via hook: no shell today"  # shell never ran
    assert "hello" in tool_msgs[1]["content"]                                   # read_file did run
    assert log.read_text().splitlines() == ["read_file:True:read"]              # PostToolUse only for tools that ran
    # "ask" forces the prompt even in yolo; "allow" skips it even in always.
    ask = _script(tmp_path, "askall", 'print(json.dumps({"permissionDecision": "ask", "reason": "double check"}))')
    s2 = _S(root)
    s2.hooks = _hooks(root, {"PreToolUse": [{"hooks": [{"command": ask}]}]})
    ui2 = HeadlessUI()
    Agent(s2.config, _Provider([call("read_file", {"path": "a.txt"}, "c3"), ChatResult(content="ok", model="m")])).run_turn(s2, ui2)
    assert any(e[0] == "approve" for e in ui2.events)
    allow = _script(tmp_path, "allowall", 'print(json.dumps({"permissionDecision": "allow"}))')
    s3 = _S(root)
    s3.approval_mode = "always"
    s3.hooks = _hooks(root, {"PreToolUse": [{"hooks": [{"command": allow}]}]})
    ui3 = HeadlessUI()
    Agent(s3.config, _Provider([call("read_file", {"path": "a.txt"}, "c4"), ChatResult(content="ok", model="m")])).run_turn(s3, ui3)
    assert not any(e[0] == "approve" for e in ui3.events) and any(e[0] == "auto" for e in ui3.events)


def test_stop_hook_can_ask_for_more_bounded(tmp_path):
    from scootcli.agent import Agent, HeadlessUI
    from scootcli.providers import ChatResult

    block = _script(tmp_path, "stopblock", 'print(json.dumps({"decision": "block", "reason": "run the tests first"}))')
    calls = []

    class _Provider:
        def chat(self, messages, **k):
            calls.append(len(messages))
            return ChatResult(content="I think I'm done", model="m")

    class _S(_Session):
        def __init__(self, root):
            super().__init__(root)
            self.model = "m"
            self.approval_mode = "yolo"
            self.bad_models = set()
            self.config = Config().override(root=str(root), stream=False, workspace_context=False)

        def resolved_model(self):
            return "m"

        def available_models(self):
            return ["m"]

        def account(self, usage, model=""):
            pass

    s = _S(tmp_path)
    s.messages.append({"role": "user", "content": "add a feature"})
    s.hooks = _hooks(tmp_path, {"Stop": [{"hooks": [{"command": block}]}]})
    out = Agent(s.config, _Provider()).run_turn(s, HeadlessUI())
    assert out.status == "done" and len(calls) == 4  # first answer + three bounded continuations
    assert [m["content"] for m in s.messages if m["role"] == "user"][1:] == ["run the tests first"] * 3


def test_tool_kind_mapping():
    from scootcli import tools

    tools.load_builtins()
    assert H.tool_kind(tools.get("read_file")) == "read"
    assert H.tool_kind(tools.get("edit_file")) == "write"
    assert H.tool_kind(tools.get("run_shell")) == "shell"
    assert H.tool_kind(tools.get("update_plan")) == "meta"


def test_interpret_accepts_claude_codes_nested_output():
    from scootcli.hooks import Hooks

    i = Hooks._interpret
    assert i("PreToolUse", {"hookSpecificOutput": {"permissionDecision": "allow"}}).action == "allow"
    d = i("PreToolUse", {"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "no"}})
    assert d.action == "deny" and d.reason == "no"
    assert i("PreToolUse", {"hookSpecificOutput": {"permissionDecision": "approve"}}).action == "allow"
    assert i("PreToolUse", {"hookSpecificOutput": {"permissionDecision": "ask"}}).action == "ask"
    # extra context nested the way Claude Code emits it for UserPromptSubmit / PostToolUse
    c = i("UserPromptSubmit", {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "branch: main"}})
    assert c.action == "" and c.context == "branch: main"
    # the pre-existing flat shapes still work
    assert i("PreToolUse", {"permissionDecision": "deny", "reason": "flat"}).reason == "flat"
    assert i("Stop", {"decision": "block", "reason": "more"}).action == "block"
    assert i("PreToolUse", {"decision": "approve"}).action == "allow"
    assert i("PreToolUse", {"additionalContext": "x"}).context == "x"


def test_matcher_matches_claude_code_tool_names(tmp_path):
    from scootcli.hooks import matcher_hits, tool_alias

    assert tool_alias("run_shell") == "Bash" and tool_alias("edit_file") == "Edit" and tool_alias("nope") == "nope"
    assert matcher_hits("Bash|Write|Edit", "run_shell") and matcher_hits("Bash|Write|Edit", "edit_file")
    assert not matcher_hits("Bash|Write|Edit", "read_file")
    assert matcher_hits("run_shell", "run_shell") and matcher_hits("^Read$", "read_file")
    assert not matcher_hits("[", "run_shell")  # a broken regex never matches
    deny = _script(tmp_path, "denybash", 'print(json.dumps({"permissionDecision": "deny", "reason": "claude-style matcher"}))')
    h = _hooks(tmp_path, {"PreToolUse": [{"matcher": "Bash", "hooks": [{"command": deny}]}]})
    assert h.run("PreToolUse", {"tool_name": "run_shell"}).action == "deny"
    assert h.run("PreToolUse", {"tool_name": "read_file"}).action == ""


# ── 0.9.0: a hook honours ESC and cannot outlive its timeout (review R10) ────────
def test_hook_is_cancelled_by_the_cancel_event(tmp_path):
    import threading
    import time

    h = _hooks(tmp_path, {"Stop": [{"hooks": [{"command": "sleep 5", "timeout": 30}]}]})
    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()
    started = time.monotonic()
    decision = h.run("Stop", {"session_id": "s"}, cancel)
    assert time.monotonic() - started < 3
    assert decision.action == "" and h.history[-1].outcome == "cancelled"


def test_hook_timeout_is_bounded_even_with_a_lingering_child(tmp_path):
    import time

    h = _hooks(tmp_path, {"Stop": [{"hooks": [{"command": "(sleep 5) & wait", "timeout": 1}]}]})
    started = time.monotonic()
    decision = h.run("Stop", {"session_id": "s"})
    assert time.monotonic() - started < 4
    assert decision.action == "" and h.history[-1].outcome == "timeout"
