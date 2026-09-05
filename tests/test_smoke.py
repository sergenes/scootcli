"""Fast, network-free smoke tests for the M1/M2 surface. Run with: PYTHONPATH=src python3 -m pytest -q
(or plain: PYTHONPATH=src python3 tests/test_smoke.py)."""

from __future__ import annotations

import threading

from scootcli import commands
from scootcli.config import Config


def test_command_registry_has_builtins():
    commands.load_builtins()
    names = set(commands.all_commands())
    assert {"help", "exit", "reset", "save", "status"} <= names
    assert commands.get("exit").handler(None, "") == commands.QUIT


def test_config_precedence_and_auto(monkeypatch, tmp_path):
    from scootcli import config as _config

    monkeypatch.delenv("SCOOT_MODEL", raising=False)
    monkeypatch.setattr("scootcli.preferences.get_model", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SCOOT_PROVIDER", raising=False)
    monkeypatch.setattr(_config, "CREDENTIALS_FILE", tmp_path / "credentials.json")
    cwd = tmp_path / "work"  # an empty tree: no project .env can influence the test
    cwd.mkdir()
    cfg = Config.load(cwd=cwd)
    assert cfg.model == "default"
    # No key anywhere → the local provider is the default, and "default" is its first preferred model.
    assert cfg.resolve_model() == "ollama/llama3.2"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-000000000000")
    assert Config.load(cwd=cwd).resolve_model() == "openai/gpt-5.3-codex"
    # CLI-flag layer overrides env; a bare name is qualified with the default provider.
    assert cfg.override(model="gpt-4o").model == "gpt-4o"
    assert Config.load(cwd=cwd).override(model="gpt-4o").resolve_model() == "openai/gpt-4o"


def test_repl_session_accounting():
    from scootcli.repl import ReplSession

    cfg = Config()
    session = ReplSession(cfg, provider=None)
    session.account({"prompt_tokens": 10, "completion_tokens": 3})
    session.account({"prompt_tokens": 5, "completion_tokens": 2})
    assert session.total_prompt == 15 and session.total_completion == 5
    session.reset()
    assert session.messages == [] and session.last_usage == {}


def test_version_flag_reports_package_version(capsys):
    from scootcli import __version__
    from scootcli.cli import main

    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    assert capsys.readouterr().out.strip() == f"scoot {__version__}"


def test_interruptible_section_is_noop_without_tty():
    # In CI / piped stdin there is no TTY; the section must be a safe no-op.
    from scootcli.keys import InterruptibleSection

    cancel = threading.Event()
    with InterruptibleSection(cancel):
        pass  # must not raise or hang
    assert not cancel.is_set()


def test_presets_build_prompts():
    from scootcli import presets

    assert presets.is_preset("explain") and presets.is_preset("edit")
    assert not presets.is_preset("frobnicate")
    ex = presets.build_prompt("explain", ["foo.py"], None)
    assert "foo.py" in ex and "do not modify" in ex.lower()
    ed = presets.build_prompt("edit", ["foo.py"], "add a docstring")
    assert "foo.py" in ed and "add a docstring" in ed


def test_redact_masks_tokens_fully():
    from scootcli.rendering import redact

    # GitHub OAuth/PAT tokens.
    for tok in ("gho_" + "a" * 36, "ghp_" + "b" * 36, "ghu_" + "c" * 20, "ghs_" + "d" * 30):
        assert tok not in redact(f"token={tok} end")
    # A legacy JWT-style value (tid=...;...:sig) must be masked in its entirety, not just up to ';'.
    jwt = "tid=abc123;exp=1699999999;sku=x;ol=1:0a1b2c3d4e5f6071"
    out = redact(f"Authorization uses {jwt} now")
    assert "1699999999" not in out and "0a1b2c3d4e5f6071" not in out and "<redacted>" in out
    # Bearer values (which contain separators) are fully masked.
    assert "abc" not in redact("Bearer abc;def:ghi")
    # Non-secret text is left alone.
    assert redact("just a normal sentence") == "just a normal sentence"


if __name__ == "__main__":
    # Minimal runner so tests work even without pytest installed (offline env).
    import types

    class _MP:
        def setenv(self, k, v):
            import os
            os.environ[k] = v

    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            fn(_MP()) if "monkeypatch" in fn.__code__.co_varnames else fn()
            print(f"ok  {name}")
            passed += 1
    print(f"\n{passed} passed")

