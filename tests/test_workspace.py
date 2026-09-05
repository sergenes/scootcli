"""Network-free tests for workspace context injection (repo map in the agent prompt).

Run: PYTHONPATH=src python3 tests/test_workspace.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from scootcli.workspace import render_tree, workspace_context


def _make_tree(root: Path) -> None:
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "__pycache__").mkdir()              # should be skipped
    (root / ".git").mkdir()                      # should be skipped
    (root / "node_modules").mkdir()              # should be skipped
    (root / "src" / "pkg" / "a.py").write_text("x")
    (root / "src" / "pkg" / "b.py").write_text("y")
    (root / "tests" / "test_a.py").write_text("z")
    (root / "README.md").write_text("hi")
    (root / ".hidden").write_text("nope")        # dotfile → skipped


def test_render_tree_skips_noise_and_shows_structure():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _make_tree(root)
        tree = render_tree(root)
        assert "src/" in tree and "pkg/" in tree and "tests/" in tree
        assert "README.md" in tree and "a.py" in tree
        # Noise directories and dotfiles are excluded.
        for noise in ("__pycache__", ".git", "node_modules", ".hidden"):
            assert noise not in tree


def test_render_tree_respects_depth_limit():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        # depth: L1/ (0) -> L2/ (1) -> L3/ (2) -> deep.py (3, beyond max_depth=2)
        (root / "L1" / "L2" / "L3").mkdir(parents=True)
        (root / "L1" / "L2" / "L3" / "deep.py").write_text("x")
        tree = render_tree(root, max_depth=2)
        assert "L1/" in tree and "L2/" in tree
        assert "deep.py" not in tree  # too deep to be listed


def test_render_tree_caps_files_per_dir():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for i in range(30):
            (root / f"f{i:02d}.txt").write_text("x")
        tree = render_tree(root)
        assert "more files)" in tree  # per-dir file cap kicks in


def test_workspace_context_includes_files_section():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _make_tree(root)
        ctx = workspace_context(root)
        assert "- files:" in ctx
        assert "README.md" in ctx


def test_workspace_context_empty_for_missing_dir():
    assert workspace_context("/no/such/path/really") == ""


def test_agent_refresh_sets_and_can_disable_context():
    from scootcli.agent import Agent
    from scootcli.config import Config

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _make_tree(root)

        class _S:
            pass

        # Enabled (default): session gets a non-empty map.
        agent = Agent(Config().override(root=str(root)), provider=None)
        s = _S()
        agent._refresh_workspace(s, agent.config)
        assert "README.md" in s.workspace_ctx

        # Disabled: empty string.
        agent2 = Agent(Config().override(root=str(root), workspace_context=False), provider=None)
        s2 = _S()
        agent2._refresh_workspace(s2, agent2.config)
        assert s2.workspace_ctx == ""


def test_system_prompt_embeds_workspace():
    from scootcli.prompts import build_agent_system_prompt

    prompt = build_agent_system_prompt(
        root=Path("/tmp"), model="gpt-4o", tool_list="- read_file: ...",
        agent_md="(none)", workspace="- git: branch main, 0 uncommitted change(s)",
    )
    assert "WORKSPACE" in prompt
    assert "branch main" in prompt
    # When empty, a helpful placeholder is used instead.
    empty = build_agent_system_prompt(root=Path("/tmp"), model="m", tool_list="", agent_md="(none)")
    assert "use list_dir/search to explore" in empty


if __name__ == "__main__":
    import types

    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            fn()
            print(f"ok  {name}")
            passed += 1
    print(f"\n{passed} passed")

