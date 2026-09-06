"""The 'nothing configured yet' state: readiness, guidance, no retry storms, no empty sessions."""

from __future__ import annotations

import json
import socket

from scootcli import config as _config
from scootcli.config import Config
from scootcli.errors import NetworkError
from scootcli.providers import registry


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(_config, "CREDENTIALS_FILE", tmp_path / "credentials.json")
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SCOOT_PROVIDER"):
        monkeypatch.delenv(var, raising=False)


def _dead_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_default_provider_order_openai_then_anthropic_then_ollama(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert registry.default_provider_name(Config()) == "ollama"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-000000000000")
    assert registry.default_provider_name(Config()) == "anthropic"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-000000000000")
    assert registry.default_provider_name(Config()) == "openai"


def test_readiness_with_nothing_configured_and_no_ollama(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("SCOOT_OLLAMA_BASE_URL", f"http://127.0.0.1:{_dead_port()}")
    ready, msg = registry.readiness(Config())
    assert ready is False
    assert "scoot auth set openai" in msg and "scoot auth set anthropic" in msg and "ollama pull" in msg


def test_readiness_when_ollama_answers(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        monkeypatch.setenv("SCOOT_OLLAMA_BASE_URL", f"http://127.0.0.1:{port}")
        assert registry.readiness(Config()) == (True, "")
    finally:
        srv.close()


def test_readiness_hosted_key_needs_no_network(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-000000000000")
    monkeypatch.setattr(registry, "reachable", lambda url, timeout=0.3: (_ for _ in ()).throw(AssertionError("probed")))
    assert registry.readiness(Config()) == (True, "")
    # A hosted key present but ollama chosen explicitly and down: a targeted message, not the setup block.
    monkeypatch.setattr(registry, "reachable", lambda url, timeout=0.3: False)
    ready, msg = registry.readiness(Config().override(provider="ollama"))
    assert ready is False and "ollama is not running" in msg and "--provider" in msg


def test_auth_rows_report_local_server_state(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from scootcli.auth import status_rows

    monkeypatch.setattr(registry, "reachable", lambda url, timeout=0.3: False)
    rows = {r["name"]: r for r in status_rows(Config())}
    assert rows["ollama"]["reachable"] is False and rows["openai"]["reachable"] is None


def test_local_connection_failure_is_not_retried(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    class _Dead:
        def __init__(self):
            self.calls = 0

        def request(self, *a, **k):
            self.calls += 1
            raise NetworkError("connection failed")

    t = _Dead()
    p = registry.make_provider("ollama", Config(), transport=t)
    p._backoff = lambda attempt, ce: (_ for _ in ()).throw(AssertionError("must not back off"))
    try:
        p.chat([{"role": "user", "content": "hi"}], model="llama3.2")
        assert False
    except NetworkError as exc:
        assert t.calls == 1 and "ollama serve" in exc.hint and "scoot auth set openai" in exc.hint


def test_pool_list_models_skips_dead_local_server_with_message(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("SCOOT_OLLAMA_BASE_URL", f"http://127.0.0.1:{_dead_port()}")
    from scootcli.providers import ProviderPool

    pool = ProviderPool(Config())
    assert pool.list_models() == []
    assert "not running" in pool.list_errors["ollama"] and "ollama serve" in pool.list_errors["ollama"]


def test_one_shot_fails_fast_with_guidance(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("SCOOT_OLLAMA_BASE_URL", f"http://127.0.0.1:{_dead_port()}")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    from scootcli.cli import main

    rc = main(["--root", str(tmp_path), "--json", "say hi"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["status"] == "error" and "scoot auth set openai" in out["error"]
    rc = main(["--root", str(tmp_path), "say hi"])
    err = capsys.readouterr().err
    assert rc == 1 and "ollama pull llama3.2" in err


def test_session_without_an_assistant_reply_is_not_saved(monkeypatch, tmp_path):
    from scootcli import sessions
    from scootcli.repl import ReplSession

    monkeypatch.setenv("SCOOT_STATE_DIR", str(tmp_path / "state"))
    s = ReplSession(Config().override(root=str(tmp_path)), provider=None)
    s.messages.append({"role": "user", "content": "hi"})
    s.autosave()
    assert sessions.latest_for_root(str(tmp_path)) is None
    s.messages.append({"role": "assistant", "content": "hello"})
    s.autosave()
    assert sessions.latest_for_root(str(tmp_path)) is not None
