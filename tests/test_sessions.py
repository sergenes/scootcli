"""Network-free tests for session persistence (save/load, resume, retention, redaction).

Run: PYTHONPATH=src python3 tests/test_sessions.py
"""

from __future__ import annotations

import os
import tempfile

from scootcli import sessions
from scootcli.config import SESSION_RETENTION, Config
from scootcli.repl import ReplSession


def _fresh_state():
    """Point the session store at a brand-new temp dir; return its path."""
    d = tempfile.mkdtemp(prefix="scoot-state-")
    os.environ["SCOOT_STATE_DIR"] = d
    return d


def _record(root="/proj/a", msgs=None, model="auto", active="gpt-5-mini"):
    return sessions.SessionRecord(
        id=sessions.new_session_id(),
        root=root,
        created=1.0,
        updated=1.0,
        model=model,
        active_model=active,
        approval_mode="always",
        total_prompt=10,
        total_completion=5,
        messages=msgs if msgs is not None else [{"role": "user", "content": "hi"}],
    )


def test_save_and_load_roundtrip():
    _fresh_state()
    rec = _record(msgs=[{"role": "user", "content": "hello"},
                        {"role": "assistant", "content": "hi there"}])
    path = sessions.save(rec)
    assert path is not None and path.exists()
    # Owner-only permissions.
    assert (os.stat(path).st_mode & 0o777) == 0o600
    loaded = sessions.load(rec.id)
    assert loaded is not None
    assert loaded.messages == rec.messages
    assert loaded.model == "auto" and loaded.active_model == "gpt-5-mini"
    assert loaded.turns == 1


def test_latest_and_filter_by_root():
    _fresh_state()
    a1 = _record(root="/proj/a", msgs=[{"role": "user", "content": "a1"}])
    sessions.save(a1)
    a2 = _record(root="/proj/a", msgs=[{"role": "user", "content": "a2"}])
    a2.updated = a1.updated + 100  # ensure ordering regardless of save() timestamp
    sessions.save(a2)
    b1 = _record(root="/proj/b", msgs=[{"role": "user", "content": "b1"}])
    sessions.save(b1)

    a_sessions = sessions.list_sessions("/proj/a")
    assert {r.id for r in a_sessions} == {a1.id, a2.id}
    assert sessions.latest_for_root("/proj/a").id == a2.id  # newest first
    assert sessions.latest_for_root("/proj/b").id == b1.id
    assert sessions.latest_for_root("/proj/c") is None


def test_retention_prunes_oldest():
    _fresh_state()
    for i in range(SESSION_RETENTION + 5):
        sessions.save(_record(msgs=[{"role": "user", "content": f"m{i}"}]))
    assert len(sessions.list_sessions()) == SESSION_RETENTION


def test_delete_and_delete_all():
    _fresh_state()
    rec = _record()
    sessions.save(rec)
    assert sessions.delete(rec.id) is True
    assert sessions.load(rec.id) is None
    assert sessions.delete("nope") is False
    sessions.save(_record(root="/proj/x"))
    sessions.save(_record(root="/proj/x"))
    assert sessions.delete_all("/proj/x") == 2


def test_tokens_are_redacted_on_save():
    _fresh_state()
    secret = "ghp_" + "A" * 36
    rec = _record(msgs=[{"role": "user", "content": f"my token is {secret}"}])
    sessions.save(rec)
    loaded = sessions.load(rec.id)
    assert secret not in loaded.messages[0]["content"]
    assert "<redacted>" in loaded.messages[0]["content"]


def test_replsession_record_roundtrip_and_autosave():
    _fresh_state()
    cfg = Config().override(root="/proj/round")
    s = ReplSession(cfg, provider=None)
    s.messages = [{"role": "user", "content": "remember 42"},
                  {"role": "assistant", "content": "noted 42"}]
    s.approval_mode = "yolo"
    s.active_model = "gpt-5-mini"
    s.total_prompt, s.total_completion = 100, 40
    s.autosave()

    # A new session in the same root can resume the saved conversation.
    latest = sessions.latest_for_root("/proj/round")
    assert latest is not None and latest.id == s.id
    s2 = ReplSession(cfg, provider=None)
    assert s2.id != s.id
    s2.apply_record(latest)
    assert s2.resumed is True
    assert s2.messages == s.messages
    assert s2.approval_mode == "yolo"
    assert s2.active_model == "gpt-5-mini"
    assert (s2.total_prompt, s2.total_completion) == (100, 40)


def test_reset_starts_new_session_id():
    _fresh_state()
    cfg = Config().override(root="/proj/reset")
    s = ReplSession(cfg, provider=None)
    original_id = s.id
    s.messages = [{"role": "user", "content": "x"}]
    s.resumed = True
    s.reset()
    assert s.id != original_id
    assert s.messages == []
    assert s.resumed is False


def test_empty_session_not_saved():
    _fresh_state()
    cfg = Config().override(root="/proj/empty")
    s = ReplSession(cfg, provider=None)
    s.autosave()  # no messages -> nothing persisted
    assert sessions.list_sessions("/proj/empty") == []


if __name__ == "__main__":
    import types

    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            fn()
            print(f"ok  {name}")
            passed += 1
    print(f"\n{passed} passed")



def test_resume_keeps_an_explicit_model_over_the_saved_one(monkeypatch, tmp_path):
    from scootcli import config as _config
    from scootcli.repl import ReplSession
    from scootcli.sessions import SessionRecord

    monkeypatch.setattr(_config, "CREDENTIALS_FILE", tmp_path / "credentials.json")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SCOOT_PROVIDER", raising=False)
    rec = SessionRecord(id="s1", root="/tmp", created=1.0, updated=2.0, model="auto",
                        active_model="openai/gpt-5.3-codex", approval_mode="always", messages=[])
    # Explicit model for this run: the record must not override it.
    s = ReplSession(Config().override(root="/tmp", model="ollama/llama3.2"), provider=None)
    s.apply_record(rec)
    assert s.model == "ollama/llama3.2" and s.active_model == "ollama/llama3.2"
    # Nothing explicit, but the saved model's provider has no key here: keep the default.
    s2 = ReplSession(Config().override(root="/tmp"), provider=None)
    s2.apply_record(rec)
    assert s2.model == "default" and s2.active_model == "ollama/llama3.2"
    # Nothing explicit and the provider is configured: adopt the saved model.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-000000000000")
    s3 = ReplSession(Config().override(root="/tmp"), provider=None)
    s3.apply_record(rec)
    assert s3.model == "auto" and s3.active_model == "openai/gpt-5.3-codex"


# ── 0.9.0: resume never raises privileges; history stays valid (review R04, R11) ─
def test_resume_keeps_this_runs_approval_mode():
    from scootcli.config import Config
    from scootcli.repl import ReplSession
    from scootcli.sessions import SessionRecord

    session = ReplSession(Config().override(approval="always"), None)
    session.apply_record(SessionRecord(id="20260907-000000-ab12", root=".", created=1.0, updated=2.0,
                                       model="auto", active_model="gpt-4o", approval_mode="yolo",
                                       messages=[{"role": "user", "content": "hi"}]))
    assert session.approval_mode == "always"
    assert session.messages == [{"role": "user", "content": "hi"}]


def test_complete_tool_results_fills_missing_results_in_place():
    from scootcli.sessions import complete_tool_results

    call = lambda i: {"id": i, "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}
    messages = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [call("c1"), call("c2")]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        {"role": "user", "content": "and?"},
        {"role": "assistant", "content": "", "tool_calls": [call("c3")]},
    ]
    fixed = complete_tool_results(messages)
    roles = [(m["role"], m.get("tool_call_id")) for m in fixed]
    assert roles == [("user", None), ("assistant", None), ("tool", "c1"), ("tool", "c2"),
                     ("user", None), ("assistant", None), ("tool", "c3")]
    assert "interrupted" in fixed[3]["content"]
    assert complete_tool_results(fixed) == fixed  # already complete: unchanged
