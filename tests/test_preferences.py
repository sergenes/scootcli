"""Tests for persisted model preference (survives across launches).

Run: PYTHONPATH=src python3 tests/test_preferences.py
"""

from __future__ import annotations

import os
import tempfile


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

