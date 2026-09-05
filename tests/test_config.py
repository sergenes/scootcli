"""Network-free tests for ``.env`` discovery and loading: project + global files, the key allowlist,
precedence, and what Config records about it."""

from __future__ import annotations

import os
from pathlib import Path

from scootcli import config
from scootcli.config import Config


def _touch(path: Path, text: str = "SCOOT_X=1\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _home(monkeypatch, tmp_path) -> Path:
    home = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    return home / "scoot"


# ── discovery ─────────────────────────────────────────────────────────────────
def test_project_env_in_start_dir_and_walking_up(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    root = tmp_path / "repo"
    env = _touch(root / ".env")
    deep = root / "src" / "pkg"
    deep.mkdir(parents=True)
    assert config.find_project_env(root) == env
    assert config.find_project_env(deep) == env


def test_nearest_project_env_wins(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    root = tmp_path / "repo"
    _touch(root / ".env", "SCOOT_FAR=1\n")
    near = _touch(root / "sub" / ".env", "SCOOT_NEAR=1\n")
    assert config.find_project_env(root / "sub") == near


def test_env_env_is_no_longer_discovered(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    root = tmp_path / "repo"
    _touch(root / "env.env")
    assert config.find_project_env(root) is None
    assert config.env_files(root) == []


def test_env_files_order_project_then_global(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    glob = _touch(home / ".env", "SCOOT_G=1\n")
    root = tmp_path / "repo"
    proj = _touch(root / ".env", "SCOOT_P=1\n")
    assert config.global_env_path() == glob
    assert config.env_files(root) == [proj, glob]
    assert config.env_files(tmp_path / "elsewhere") == [glob]


# ── loading + allowlist ───────────────────────────────────────────────────────
def test_allowlist_covers_scoot_provider_and_proxy_keys():
    assert config.allowed_env_key("SCOOT_MODEL")
    assert config.allowed_env_key("OPENAI_API_KEY")
    assert config.allowed_env_key("HTTPS_PROXY") and config.allowed_env_key("NO_PROXY")
    assert not config.allowed_env_key("DATABASE_URL")
    assert not config.allowed_env_key("MODEL")  # un-namespaced settings are not imported any more


def test_load_dotenv_imports_only_allowed_keys(monkeypatch, tmp_path):
    for k in ("SCOOT_T_A", "OPENAI_API_KEY", "DATABASE_URL", "MODEL", "SCOOT_T_Q"):
        monkeypatch.delenv(k, raising=False)
    env = _touch(tmp_path / ".env", "\n".join([
        "# comment",
        "SCOOT_T_A=alpha",
        "export OPENAI_API_KEY='sk-test-000000000000'",
        'SCOOT_T_Q="quoted value"',
        "DATABASE_URL=postgres://secret",
        "MODEL=gpt-4o",
        "garbage line",
    ]) + "\n")
    imported = config.load_dotenv(env)
    assert imported == ["SCOOT_T_A", "OPENAI_API_KEY", "SCOOT_T_Q"]
    assert os.environ["SCOOT_T_A"] == "alpha"
    assert os.environ["OPENAI_API_KEY"] == "sk-test-000000000000"
    assert os.environ["SCOOT_T_Q"] == "quoted value"
    assert "DATABASE_URL" not in os.environ and "MODEL" not in os.environ


def test_precedence_env_over_project_over_global(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    for k in ("SCOOT_T_ONLY_GLOBAL", "SCOOT_T_BOTH", "SCOOT_T_ENV"):
        monkeypatch.delenv(k, raising=False)
    _touch(home / ".env", "SCOOT_T_ONLY_GLOBAL=g\nSCOOT_T_BOTH=g\nSCOOT_T_ENV=g\n")
    root = tmp_path / "repo"
    _touch(root / ".env", "SCOOT_T_BOTH=p\nSCOOT_T_ENV=p\n")
    monkeypatch.setenv("SCOOT_T_ENV", "real")
    for f in config.env_files(root):
        config.load_dotenv(f)
    assert os.environ["SCOOT_T_ONLY_GLOBAL"] == "g"
    assert os.environ["SCOOT_T_BOTH"] == "p"      # project beats global
    assert os.environ["SCOOT_T_ENV"] == "real"    # the real environment beats both


def test_config_load_records_env_files_and_reads_settings(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    for k in ("SCOOT_EFFORT", "SCOOT_PROVIDER", "SCOOT_MODEL", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(config, "CREDENTIALS_FILE", tmp_path / "credentials.json")
    glob = _touch(home / ".env", "SCOOT_EFFORT=high\nOPENAI_API_KEY=sk-test-000000000000\n")
    root = tmp_path / "repo"
    proj = _touch(root / ".env", "SCOOT_MODEL=openai/gpt-4.1\nDATABASE_URL=nope\n")
    cfg = Config.load(cwd=root)
    assert cfg.env_files == (str(proj), str(glob))
    assert cfg.effort == "high" and cfg.model == "openai/gpt-4.1"
    assert cfg.resolve_model() == "openai/gpt-4.1"
    assert "DATABASE_URL" not in os.environ


def test_config_load_without_any_env_file(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    cfg = Config.load(cwd=tmp_path / "empty")
    assert cfg.env_files == ()
