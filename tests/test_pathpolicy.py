"""The shared path policy (0.17.0): what search, the workspace map, the /init scan, and Tab
completion may show. Hidden files are visible; noise, secret-bearing files, and symlinks are not.

This reverses part of review finding R07 (0.10.0) on purpose, so the secret exclusions and the
symlink rule are pinned here for both search backends.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from scootcli import pathpolicy, tools
from scootcli.tools.base import Scope, ToolContext
from scootcli.tools.search import Search

SECRETS = [".env", ".env.local", ".env.production", "server.pem", "tls.key", "id_rsa", "id_ed25519",
           ".netrc", "credentials.json", "gcp-credentials.json", "service-account-prod.json",
           ".npmrc", ".pypirc", ".git-credentials", "store.p12", "PyPI-Recovery-Codes-me-2026.txt"]
NOT_SECRETS = [".env.example", ".env.sample", ".env.template", "id_rsa.pub", "keys.py", "README.md",
               ".gitlab-ci.yml", ".gitignore", "package.json", "environment.yml"]


def _workspace(tmp_path: Path) -> Path:
    """A checkout with hidden config, noise, secrets, and links that point out of it."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "external.txt").write_text("MARKER external\n")
    root = tmp_path / "ws"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("MARKER app\n")
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text("MARKER ci\n")
    (root / ".gitlab-ci.yml").write_text("MARKER gitlab\n")
    (root / ".env.example").write_text("MARKER=example\n")
    for name in SECRETS:
        (root / name).write_text("MARKER secret\n")
    for noise in (".git", ".venv", "node_modules", "__pycache__", ".scoot", "pkg.egg-info", ".ssh"):
        (root / noise).mkdir()
        (root / noise / "inner.txt").write_text("MARKER noise\n")
    os.symlink(outside / "external.txt", root / "linked.txt")
    os.symlink(outside, root / "linked_dir")
    return root


def _ctx(root: Path, hidden: bool = True, scope=None) -> ToolContext:
    return ToolContext(root=root, config=SimpleNamespace(hidden_files=hidden), scope=scope)


VISIBLE = {".env.example", ".github/workflows/ci.yml", ".gitlab-ci.yml", "src/app.py"}


def _need_rg() -> None:
    if not shutil.which("rg"):
        pytest.skip("ripgrep is not installed")


# ── the policy itself ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", SECRETS)
def test_secret_bearing_names_are_excluded(name):
    assert pathpolicy.is_secret_file(name) and pathpolicy.skip_file(name)
    assert not pathpolicy.allows(f"deploy/{name}")


@pytest.mark.parametrize("name", NOT_SECRETS)
def test_ordinary_and_example_names_are_allowed(name):
    assert not pathpolicy.skip_file(name)
    assert pathpolicy.allows(f"deploy/{name}")


def test_noise_directories_are_excluded_at_any_depth():
    for rel in (".git/config", "a/.venv/lib/x.py", "a/node_modules/b/index.js", ".scoot/hooks.json",
                "x.egg-info/PKG-INFO", "home/.ssh/config", "a/__pycache__/m.pyc"):
        assert not pathpolicy.allows(rel), rel
    assert pathpolicy.allows(".github/workflows/ci.yml") and pathpolicy.allows("./src/app.py")
    assert pathpolicy.allows("dist/bundle.js")            # build output is searchable
    assert pathpolicy.skip_dir("dist", outputs=True)       # and left out of the map only


def test_hidden_off_drops_every_dot_name():
    assert not pathpolicy.allows(".github/workflows/ci.yml", hidden=False)
    assert not pathpolicy.allows(".gitlab-ci.yml", hidden=False)
    assert pathpolicy.allows("src/app.py", hidden=False)
    assert not pathpolicy.allows("server.pem", hidden=False)   # secrets stay out either way


def test_walk_drops_symlinks_noise_and_secrets(tmp_path):
    root = _workspace(tmp_path)
    found = {p.relative_to(root).as_posix() for p in pathpolicy.iter_files(root)}
    assert found == VISIBLE


def test_hidden_files_setting_defaults_on_and_reads_the_variable(monkeypatch):
    from scootcli.config import Config

    monkeypatch.delenv("SCOOT_HIDDEN_FILES", raising=False)
    assert Config.load().hidden_files is True
    monkeypatch.setenv("SCOOT_HIDDEN_FILES", "0")
    assert Config.load().hidden_files is False


# ── search: both backends agree with the policy ─────────────────────────────────
def _files(result) -> set:
    assert result.ok, result.error
    return {line.strip().rsplit(" (", 1)[0] for line in result.content.splitlines()[1:]
            if line.startswith("  ")}


def test_fallback_search_sees_hidden_files_and_no_secrets(tmp_path, monkeypatch):
    root = _workspace(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    res = Search().run({"query": "MARKER", "files_only": True}, _ctx(root))
    assert _files(res) == VISIBLE
    res = Search().run({"query": "MARKER", "files_only": True}, _ctx(root, hidden=False))
    assert _files(res) == {"src/app.py"}


def test_ripgrep_search_sees_hidden_files_and_no_secrets(tmp_path, monkeypatch):
    root = _workspace(tmp_path)
    _need_rg()
    res = Search().run({"query": "MARKER", "files_only": True}, _ctx(root))
    assert _files(res) == VISIBLE
    res = Search().run({"query": "MARKER", "files_only": True}, _ctx(root, hidden=False))
    assert _files(res) == {"src/app.py"}


def test_ripgrep_results_are_refiltered_against_the_policy():
    raw = ["./src/app.py:1:MARKER", ".env:1:MARKER=1", "./.git/config:3:MARKER",
           "deploy/tls.key:1:MARKER", ".github/ci.yml:2:MARKER"]
    matches, _ = Search._parse_rg(raw)
    assert [m[0] for m in matches] == ["src/app.py", ".github/ci.yml"]
    matches, _ = Search._parse_rg(raw, hidden=False)
    assert [m[0] for m in matches] == ["src/app.py"]


# ── search takes a path ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("backend", ["python", "ripgrep"])
def test_search_path_narrows_to_a_directory_or_a_file(tmp_path, monkeypatch, backend):
    root = _workspace(tmp_path)
    (root / "src" / "other.py").write_text("MARKER other\n")
    if backend == "python":
        monkeypatch.setattr(shutil, "which", lambda _name: None)
    else:
        _need_rg()
    res = Search().run({"query": "MARKER", "files_only": True, "path": "src"}, _ctx(root))
    assert _files(res) == {"src/app.py", "src/other.py"}        # shown relative to the workspace root
    res = Search().run({"query": "MARKER", "files_only": True, "path": ".github"}, _ctx(root))
    assert _files(res) == {".github/workflows/ci.yml"}
    res = Search().run({"query": "MARKER", "files_only": True, "path": "src/app.py"}, _ctx(root))
    assert _files(res) == {"src/app.py"}
    res = Search().run({"query": "MARKER", "path": ".env"}, _ctx(root))
    assert not res.ok and "credentials" in res.error
    res = Search().run({"query": "MARKER", "path": "nope"}, _ctx(root))
    assert not res.ok and "no such" in res.error


@pytest.mark.parametrize("backend", ["python", "ripgrep"])
def test_search_outside_the_workspace_needs_a_grant_and_keeps_the_policy(tmp_path, monkeypatch, backend):
    root = _workspace(tmp_path)
    sibling = tmp_path / "sibling"
    (sibling / "lib").mkdir(parents=True)
    (sibling / "lib" / "core.py").write_text("MARKER sibling\n")
    (sibling / ".env").write_text("MARKER secret\n")
    os.symlink(root / "src" / "app.py", sibling / "link.py")
    if backend == "python":
        monkeypatch.setattr(shutil, "which", lambda _name: None)
    else:
        _need_rg()
    scope = Scope(root)
    res = Search().run({"query": "MARKER", "path": str(sibling)}, _ctx(root, scope=scope))
    assert not res.ok and "outside the workspace" in res.error
    scope.grant_dir(sibling)
    res = Search().run({"query": "MARKER", "files_only": True, "path": "../sibling"}, _ctx(root, scope=scope))
    assert _files(res) == {f"{sibling.resolve().as_posix()}/lib/core.py"}   # absolute outside the root


def test_a_linked_search_path_is_judged_by_where_it_leads(tmp_path, monkeypatch):
    root = _workspace(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    res = Search().run({"query": "MARKER", "path": "linked_dir"}, _ctx(root, scope=Scope(root)))
    assert not res.ok and "outside the workspace" in res.error


def test_the_agent_asks_before_searching_outside_the_workspace(tmp_path):
    tools.load_builtins()
    assert tools.get("search").paths({"query": "x", "path": "../sibling"}) == ["../sibling"]
    assert tools.get("search").paths({"query": "x"}) == []


# ── the workspace map and the /init scan ────────────────────────────────────────
def test_workspace_map_shows_hidden_config_and_no_secrets(tmp_path):
    from scootcli.workspace import render_tree

    root = _workspace(tmp_path)
    (root / "dist").mkdir()
    (root / "dist" / "bundle.js").write_text("x")
    lines = [line.strip() for line in render_tree(root).splitlines()]
    assert ".github/" in lines and "workflows/" in lines and ".gitlab-ci.yml" in lines
    assert ".env.example" in lines and "src/" in lines
    for gone in SECRETS + [".git/", ".venv/", ".scoot/", ".ssh/", "dist/", "linked.txt", "linked_dir/"]:
        assert gone not in lines, gone
    assert lines.index("src/") < lines.index(".github/")       # dot-names sort after the others
    old = [line.strip() for line in render_tree(root, hidden=False).splitlines()]
    assert ".github/" in old and ".env.example" in old and ".gitlab-ci.yml" not in old


def test_init_scan_uses_the_policy(tmp_path):
    from scootcli.project import _iter_tree

    root = _workspace(tmp_path)
    entries = _iter_tree(root)
    assert ".github/workflows/ci.yml" in entries and "src/" in entries and "src/app.py" in entries
    for name in SECRETS + ["linked.txt", "linked_dir/"]:
        assert name not in entries
    assert not any(e.startswith((".git/", ".venv/", "node_modules/", ".scoot/")) for e in entries)
    assert ".gitlab-ci.yml" not in _iter_tree(root, hidden=False)


# ── /status lists what was granted ──────────────────────────────────────────────
def test_status_lists_granted_paths(tmp_path, capsys):
    from scootcli import commands
    from scootcli.config import Config
    from scootcli.repl import ReplSession

    other = tmp_path / "other"
    other.mkdir()
    more = tmp_path / "more"
    more.mkdir()
    session = ReplSession(Config.load().override(root=str(tmp_path)), None)
    commands.load_builtins()
    session.scope.grant_dir(other)
    session.scope.grant_dir(more)
    commands.get("status").handler(session, "")
    out = capsys.readouterr().out
    assert "granted paths:" in out and str(other.resolve()) in out and str(more.resolve()) in out
    assert out.count("granted paths:") == 1
