"""Tests for persisted model preference (survives across launches).

Run: PYTHONPATH=src python3 tests/test_preferences.py
"""

from __future__ import annotations

import os
import tempfile

from scootcli.config import Config


def _fresh_config_dir():
    d = tempfile.mkdtemp(prefix="scoot-cfg-")
    os.environ["SCOOT_CONFIG_DIR"] = d
    return d


def test_set_and_get_model_roundtrip():
    _fresh_config_dir()
    from scootcli import preferences

    assert preferences.get_model() is None
    preferences.set_model("gpt-4o")
    assert preferences.get_model() == "gpt-4o"
    # Persisted on disk (a new read sees it).
    assert preferences.load_preferences()["model"] == "gpt-4o"


def test_clear_model():
    _fresh_config_dir()
    from scootcli import preferences

    preferences.set_model("claude-3.5")
    preferences.clear_model()
    assert preferences.get_model() is None


def test_config_uses_saved_model_when_env_unset(monkeypatch):
    _fresh_config_dir()
    from scootcli import preferences
    from scootcli.config import Config

    monkeypatch.setenv("SCOOT_CONFIG_DIR", os.environ["SCOOT_CONFIG_DIR"])
    os.environ.pop("SCOOT_MODEL", None)  # ensure MODEL is not set
    preferences.set_model("gpt-5-mini")
    assert Config.load().model == "gpt-5-mini"


def test_env_overrides_saved_model(monkeypatch):
    _fresh_config_dir()
    from scootcli import preferences
    from scootcli.config import Config

    preferences.set_model("gpt-4o")
    monkeypatch.setenv("SCOOT_MODEL", "o3")  # explicit env wins over the saved preference
    assert Config.load().model == "o3"


def test_default_alias_when_nothing_saved(monkeypatch):
    _fresh_config_dir()
    from scootcli.config import Config

    os.environ.pop("SCOOT_MODEL", None)
    assert Config.load().model == "default"


if __name__ == "__main__":
    import types

    class _MP:
        def setenv(self, k, v):
            os.environ[k] = v

    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            fn(_MP()) if "monkeypatch" in fn.__code__.co_varnames else fn()
            print(f"ok  {name}")
            passed += 1
    print(f"\n{passed} passed")



def test_model_flag_is_remembered_when_launching_the_repl(monkeypatch):
    """``scoot --model X`` (REPL) saves X like ``/model X`` does, so a plain ``scoot`` reuses it."""
    _fresh_config_dir()
    from scootcli import cli, preferences
    from scootcli.config import Config
    from scootcli.providers import registry
    import scootcli.repl as repl_mod

    cfg = Config.load().override(model="openai/chat-latest")  # root = this test's cwd

    class _FakePool:
        def __init__(self):
            self.config = cfg
            self.spec = registry.get("ollama")

    class _FakeRepl:
        def __init__(self, config, provider, resume=None):
            pass

        def run(self):
            return 0

    monkeypatch.setattr(repl_mod, "Repl", _FakeRepl)
    assert preferences.get_model(cfg.root) is None
    cli._interactive(_FakePool(), resume=None, remember_model="openai/chat-latest")
    assert preferences.saved_models(cfg.root) == ("openai/chat-latest", None)  # this folder, not all
    # Launching without a flag keeps the saved choice; blank never clears it.
    cli._interactive(_FakePool(), resume=None, remember_model=None)
    cli._interactive(_FakePool(), resume=None, remember_model="  ")
    assert preferences.get_model(cfg.root) == "openai/chat-latest"
    assert Config.load().model == "openai/chat-latest"  # the next launch here resolves to it


def test_model_flag_is_not_remembered_for_one_shot_prompts(monkeypatch):
    """A one-shot ``scoot --model X "prompt"`` must not change the saved preference."""
    _fresh_config_dir()
    from scootcli import cli, preferences

    calls = {}

    def _once(*a, **k):
        calls["once"] = True
        return 0

    monkeypatch.setattr(cli, "_run_once", _once)
    monkeypatch.setattr(cli, "ProviderPool", lambda config: type("P", (), {"config": config})())
    assert cli.main(["--model", "openai/chat-latest", "say hi"]) == 0
    assert calls.get("once")
    assert preferences.get_model() is None


# ── per-folder models ───────────────────────────────────────────────────────────


def test_folder_model_wins_over_global_and_global_is_the_fallback(tmp_path):
    _fresh_config_dir()
    from scootcli import preferences

    a, b = tmp_path / "a", tmp_path / "b"
    preferences.set_model("openai/gpt-5-mini")               # every folder
    preferences.set_model("ollama/llama3.2", root=a)         # this folder
    assert preferences.get_model(a) == "ollama/llama3.2"
    assert preferences.get_model(b) == "openai/gpt-5-mini"
    assert preferences.get_model() == "openai/gpt-5-mini"
    assert preferences.model_source(a) == "folder"
    assert preferences.model_source(b) == "everywhere"
    assert preferences.saved_models(a) == ("ollama/llama3.2", "openai/gpt-5-mini")
    # A symlinked or relative spelling of the folder is the same folder.
    assert preferences.get_model(str(a) + "/./") == "ollama/llama3.2"


def test_forget_folder_falls_back_and_everywhere_replaces_the_folder_choice(tmp_path):
    _fresh_config_dir()
    from scootcli import preferences

    a = tmp_path / "a"
    preferences.set_model("ollama/llama3.2", root=a)
    preferences.clear_model(root=a)
    assert preferences.get_model(a) is None and preferences.model_source(a) is None
    preferences.set_model("ollama/llama3.2", root=a)
    preferences.set_model_everywhere("anthropic/claude-sonnet-5", root=a)
    assert preferences.saved_models(a) == (None, "anthropic/claude-sonnet-5")
    assert preferences.get_model(a) == "anthropic/claude-sonnet-5"
    preferences.clear_model()  # the global one
    assert preferences.get_model(a) is None


def test_config_precedence_env_folder_global_default(monkeypatch, tmp_path):
    _fresh_config_dir()
    from scootcli import preferences

    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.chdir(root)
    cfg = Config.load()
    assert (cfg.model, cfg.model_source) == ("default", "default")
    preferences.set_model("openai/gpt-5-mini")
    cfg = Config.load()
    assert (cfg.model, cfg.model_source) == ("openai/gpt-5-mini", "everywhere")
    preferences.set_model("ollama/llama3.2", root=root)
    cfg = Config.load()
    assert (cfg.model, cfg.model_source) == ("ollama/llama3.2", "folder")
    monkeypatch.setenv("SCOOT_MODEL", "auto")
    cfg = Config.load()
    assert (cfg.model, cfg.model_source) == ("auto", "env")
    assert Config.load().override(model="openai/gpt-5.3-codex").model_source == "flag"


def test_root_flag_picks_the_target_folders_model(monkeypatch, tmp_path):
    _fresh_config_dir()
    from scootcli import preferences

    here, there = tmp_path / "here", tmp_path / "there"
    here.mkdir()
    there.mkdir()
    preferences.set_model("ollama/llama3.2", root=there)
    monkeypatch.chdir(here)
    assert Config.load().model == "default"
    cfg = Config.load().override(root=str(there))  # --root there
    assert (cfg.model, cfg.model_source) == ("ollama/llama3.2", "folder")
    # ...but never over an explicit flag or SCOOT_MODEL.
    cfg = Config.load().override(model="auto", root=str(there))
    assert (cfg.model, cfg.model_source) == ("auto", "flag")
    monkeypatch.setenv("SCOOT_MODEL", "openai/gpt-5-mini")
    cfg = Config.load().override(root=str(there))
    assert (cfg.model, cfg.model_source) == ("openai/gpt-5-mini", "env")


def _model_session(root):
    from scootcli.config import Config as _Config
    from scootcli.repl import ReplSession

    session = ReplSession(_Config.load().override(root=str(root)), provider=None)
    session._available = ["ollama/llama3.2", "openai/gpt-5-mini"]
    session.provider_ready = True
    session.refresh_readiness = lambda: True
    return session


def test_model_command_saves_per_folder_everywhere_and_forget(tmp_path, capsys):
    _fresh_config_dir()
    from scootcli import preferences
    from scootcli.commands import model as model_cmd

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    session = _model_session(a)
    model_cmd._run(session, "ollama/llama3.2")
    assert "saved for this folder" in capsys.readouterr().out
    assert preferences.saved_models(a) == ("ollama/llama3.2", None)
    assert session.model == session.active_model == "ollama/llama3.2"

    model_cmd._run(session, "openai/gpt-5-mini everywhere")
    assert "saved for every folder" in capsys.readouterr().out
    assert preferences.saved_models(a) == (None, "openai/gpt-5-mini")
    assert preferences.get_model(b) == "openai/gpt-5-mini"

    model_cmd._run(session, "ollama/llama3.2")
    model_cmd._run(session, "forget")
    out = capsys.readouterr().out
    assert "forgotten" in out and "openai/gpt-5-mini" in out
    assert preferences.saved_models(a) == (None, "openai/gpt-5-mini")
    assert session.model == "openai/gpt-5-mini"

    model_cmd._run(session, "")
    out = capsys.readouterr().out
    assert "this folder (none)" in out and "everywhere openai/gpt-5-mini" in out


def test_status_names_the_models_origin(tmp_path, capsys):
    _fresh_config_dir()
    from scootcli.commands import status as status_cmd
    from scootcli import preferences

    root = tmp_path / "p"
    root.mkdir()
    preferences.set_model("ollama/llama3.2", root=root)
    session = _model_session(root)
    status_cmd._run(session, "")
    assert "(from saved for this folder)" in capsys.readouterr().out
