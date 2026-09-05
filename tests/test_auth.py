"""API-key storage and lookup: saved credentials, environment precedence, provider status rows."""

from __future__ import annotations

import os
import stat

from scootcli import auth, config as _config, credentials as creds
from scootcli.providers import registry
from scootcli.providers.base import ProviderSpec


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(_config, "CREDENTIALS_FILE", tmp_path / "config" / "credentials.json")
    for var in ("OPENAI_API_KEY", "OLLAMA_API_KEY", "SCOOT_PROVIDER"):
        monkeypatch.delenv(var, raising=False)


def test_save_load_delete_roundtrip(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert creds.load_key("openai") is None
    path = creds.save_key("openai", "sk-secret-000000000000")
    assert creds.load_key("openai") == "sk-secret-000000000000"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    creds.save_key("anthropic", "sk-ant-000000000000")
    assert creds.saved_providers() == ["anthropic", "openai"]
    assert creds.delete_key("openai") is True
    assert creds.delete_key("openai") is False
    assert creds.load_key("anthropic") == "sk-ant-000000000000"


def test_legacy_credentials_file_is_ignored(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _config.CREDENTIALS_FILE.parent.mkdir(parents=True)
    _config.CREDENTIALS_FILE.write_text('{"oauth_token": "gho_old"}')
    assert creds.load_key("openai") is None


def test_env_beats_saved_key(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    spec = registry.get("openai")
    assert auth.api_key_for(spec) is None and auth.key_source(spec) is None
    creds.save_key("openai", "sk-saved-000000000000")
    assert auth.api_key_for(spec) == "sk-saved-000000000000"
    assert auth.key_source(spec) == "saved"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-000000000000")
    assert auth.api_key_for(spec) == "sk-env-000000000000"
    assert auth.key_source(spec) == "env:OPENAI_API_KEY"


def test_keyless_provider_is_always_configured(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert auth.is_configured(registry.get("ollama")) is True
    assert auth.is_configured(registry.get("openai")) is False


def test_status_rows_mark_default_and_sources(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from scootcli.config import Config

    rows = {r["name"]: r for r in auth.status_rows(Config())}
    assert rows["ollama"]["default"] is True and rows["openai"]["configured"] is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-000000000000")
    rows = {r["name"]: r for r in auth.status_rows(Config())}
    assert rows["openai"]["default"] is True and rows["openai"]["source"] == "env:OPENAI_API_KEY"


def test_missing_key_hint_names_env_and_command():
    spec = ProviderSpec(name="acme", base_url="https://x", key_env=("ACME_API_KEY",))
    hint = auth.missing_key_hint(spec)
    assert "ACME_API_KEY" in hint and "scoot auth set acme" in hint
