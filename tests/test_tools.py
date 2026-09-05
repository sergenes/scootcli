"""Network-free tests for the tool layer (sandboxing, read/write/edit/list/search, truncation).

Run: PYTHONPATH=src python3 tests/test_tools.py   (or with pytest if available)
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from scootcli import tools
from scootcli.tools.base import ToolContext, PathEscapeError, safe_path, truncate


def _ctx(root: Path) -> ToolContext:
    return ToolContext(root=root)


def test_path_sandboxing_rejects_escape():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        assert safe_path(root, "sub/file.txt") == (root / "sub/file.txt").resolve()
        for bad in ("../outside.txt", "/etc/passwd", "sub/../../oops"):
            try:
                safe_path(root, bad)
                assert False, f"expected escape rejection for {bad}"
            except PathEscapeError:
                pass


def test_registry_has_expected_tools():
    tools.load_builtins()
    names = set(tools.all_tools())
    assert names == {"read_file", "list_dir", "search", "write_file", "edit_file", "run_shell",
                     "update_plan"}
    # schemas() and tool_list_text() derive from the same registry.
    assert len(tools.schemas()) == 7
    assert "read_file:" in tools.tool_list_text().replace(" ", "")


def test_write_then_read_roundtrip():
    tools.load_builtins()
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        ctx = _ctx(root)
        wr = tools.get("write_file").run({"path": "a.txt", "content": "one\ntwo\n"}, ctx)
        assert wr.ok and (root / "a.txt").read_text() == "one\ntwo\n"
        rd = tools.get("read_file").run({"path": "a.txt"}, ctx)
        assert rd.ok and "1 | one" in rd.content and "2 | two" in rd.content


def test_edit_unique_match_enforced():
    tools.load_builtins()
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        ctx = _ctx(root)
        (root / "b.txt").write_text("x = 1\nx = 1\n")
        # Not unique -> fail.
        res = tools.get("edit_file").run(
            {"path": "b.txt", "old_string": "x = 1", "new_string": "x = 2"}, ctx
        )
        assert not res.ok and "unique" in res.error
        # Unique context -> succeeds.
        (root / "c.txt").write_text("alpha\nbeta\n")
        ok = tools.get("edit_file").run(
            {"path": "c.txt", "old_string": "beta", "new_string": "gamma"}, ctx
        )
        assert ok.ok and (root / "c.txt").read_text() == "alpha\ngamma\n"


def test_write_preview_produces_diff():
    tools.load_builtins()
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        preview = tools.get("write_file").preview({"path": "n.txt", "content": "hi\n"}, _ctx(root))
        assert "create n.txt" in preview and "+1" in preview


def test_list_dir_and_search():
    tools.load_builtins()
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        ctx = _ctx(root)
        (root / "pkg").mkdir()
        (root / "pkg" / "mod.py").write_text("def hello():\n    return 42\n")
        ls = tools.get("list_dir").run({"path": "."}, ctx)
        assert ls.ok and "pkg/" in ls.content
        sr = tools.get("search").run({"query": "hello"}, ctx)
        assert sr.ok and "mod.py" in sr.content


def test_search_groups_by_file_with_counts():
    tools.load_builtins()
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        ctx = _ctx(root)
        # 5 hits in one file, 1 in another.
        (root / "many.py").write_text("\n".join(f"x = TARGET  # {i}" for i in range(5)))
        (root / "few.py").write_text("only TARGET here\n")
        sr = tools.get("search").run({"query": "TARGET"}, ctx)
        assert sr.ok
        assert "6 match(es) in 2 file(s)" in sr.content
        assert "many.py (5)" in sr.content        # per-file count
        assert "few.py (1)" in sr.content
        # Per-file sample is capped (3) with a "+N more" note for the busy file.
        assert "more in this file" in sr.content
        assert sr.summary == "6 matches in 2 file(s)"


def test_search_files_only_is_compact():
    tools.load_builtins()
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        ctx = _ctx(root)
        (root / "a.py").write_text("TARGET\nTARGET\n")
        sr = tools.get("search").run({"query": "TARGET", "files_only": True}, ctx)
        assert sr.ok
        assert "a.py (2)" in sr.content
        # files_only should not include sample source lines with line numbers.
        assert "  1: " not in sr.content


def test_search_ripgrep_output_parser():
    from scootcli.tools.search import Search

    raw = [
        "src/app.py:10:def handler(req):",
        "src/app.py:42:    return handler(x)",
        "lib/util.py:3:handler = None",
        "",                       # blank line ignored
        "not-a-match-line",       # no line number -> ignored
    ]
    matches, capped = Search._parse_rg(raw)
    assert capped is False
    assert matches == [
        ("src/app.py", 10, "def handler(req):"),
        ("src/app.py", 42, "return handler(x)"),
        ("lib/util.py", 3, "handler = None"),
    ]


def test_truncate_marks_cut():
    big = "\n".join(str(i) for i in range(10_000))
    out = truncate(big, max_chars=100, max_lines=50)
    assert "output truncated" in out and len(out) < len(big)


def test_run_shell_captures_exit_code():
    tools.load_builtins()
    with tempfile.TemporaryDirectory() as d:
        ctx = _ctx(Path(d))
        ok = tools.get("run_shell").run({"command": "echo hello"}, ctx)
        assert ok.ok and "hello" in ok.content and "exit 0" in ok.content
        bad = tools.get("run_shell").run({"command": "exit 3"}, ctx)
        assert not bad.ok and "exit 3" in bad.content


def test_update_plan_normalizes_and_reports_progress():
    from scootcli.tools.update_plan import normalize_plan, plan_progress

    raw = [
        "just a string",                              # → pending
        {"step": "do X", "status": "in-progress"},    # hyphen normalized
        {"step": "do Y", "status": "done"},           # alias → completed
        {"step": "", "status": "pending"},            # dropped (empty)
        {"step": "do Z", "status": "weird"},          # unknown → pending
        42,                                            # dropped (not str/dict)
    ]
    plan = normalize_plan(raw)
    assert [s["step"] for s in plan] == ["just a string", "do X", "do Y", "do Z"]
    assert [s["status"] for s in plan] == ["pending", "in_progress", "completed", "pending"]
    assert plan_progress(plan) == (1, 4)
    assert normalize_plan("not a list") == []


def test_update_plan_tool_run_and_auto_approve():
    tools.load_builtins()
    tool = tools.get("update_plan")
    assert tool is not None and tool.auto_approve is True
    with tempfile.TemporaryDirectory() as d:
        ctx = _ctx(Path(d))
        res = tool.run({"plan": [{"step": "a", "status": "completed"},
                                 {"step": "b", "status": "in_progress"}]}, ctx)
        assert res.ok and res.summary == "1/2 steps"
        assert res.meta["plan"][0]["step"] == "a"
        # An empty/invalid plan fails cleanly.
        assert tool.run({"plan": []}, ctx).ok is False


def test_update_plan_is_auto_approved_even_in_always_mode():
    from scootcli.approvals import needs_prompt

    tools.load_builtins()
    tool = tools.get("update_plan")
    assert needs_prompt("always", tool, {"plan": []}) is None  # meta tool: never prompts


if __name__ == "__main__":
    import types

    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            fn()
            print(f"ok  {name}")
            passed += 1
    print(f"\n{passed} passed")

