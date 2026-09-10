"""Network-free tests for the tool layer (sandboxing, read/write/edit/list/search, truncation).

Run: PYTHONPATH=src python3 tests/test_tools.py   (or with pytest if available)
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from scootcli import tools
from scootcli.config import Config
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
                     "open_editor", "update_plan"}
    # schemas() and tool_list_text() derive from the same registry.
    assert len(tools.schemas()) == 8
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


def test_open_editor_missing_launcher_fails_cleanly():
    # No editor launcher on PATH in CI → the tool reports a helpful error, never crashes.
    import scootcli.tools.open_editor as oe

    tools.load_builtins()
    tool = tools.get("open_editor")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "note.md").write_text("hi")
        ctx = ToolContext(root=root, config=Config())
        # nonexistent path is rejected before any launch attempt
        assert tool.run({"path": "nope.md"}, ctx).ok is False
        # force "launcher not found" regardless of the host by stubbing which()
        orig = oe.shutil.which
        oe.shutil.which = lambda _name: None
        try:
            res = tool.run({"path": "note.md", "editor": "vscode"}, ctx)
        finally:
            oe.shutil.which = orig
        assert res.ok is False and "not found" in res.error


def test_open_editor_editor_selection_and_default():
    import scootcli.tools.open_editor as oe

    tools.load_builtins()
    tool = tools.get("open_editor")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.txt").write_text("x")
        launched = []
        orig_which = oe.shutil.which
        orig_popen = oe.subprocess.Popen
        oe.shutil.which = lambda name: "/usr/bin/" + name
        oe.subprocess.Popen = lambda cmd, **kw: launched.append(cmd) or None
        try:
            # default editor from config (idea) → "idea -e <path>"
            ctx = ToolContext(root=root, config=Config())
            assert tool.run({"path": "a.txt"}, ctx).ok is True
            assert launched[-1][:2] == ["idea", "-e"]
            # per-call override → vscode "code <path>"
            assert tool.run({"path": "a.txt", "editor": "vscode"}, ctx).ok is True
            assert launched[-1][0] == "code"
        finally:
            oe.shutil.which = orig_which
            oe.subprocess.Popen = orig_popen


def test_run_shell_uses_noninteractive_env_and_closed_stdin():
    from scootcli.tools.base import noninteractive_env

    env = noninteractive_env({"PATH": "/bin"})
    assert env["GIT_PAGER"] == "cat" and env["GIT_TERMINAL_PROMPT"] == "0" and env["PATH"] == "/bin"
    tools.load_builtins()
    with tempfile.TemporaryDirectory() as d:
        ctx = ToolContext(root=Path(d), config=Config())
        # `cat` with no args reads stdin: with stdin closed it exits at once instead of hanging.
        res = tools.get("run_shell").run({"command": "cat; echo done"}, ctx)
        assert res.ok and "done" in res.content


if __name__ == "__main__":
    import types

    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            fn()
            print(f"ok  {name}")
            passed += 1
    print(f"\n{passed} passed")



# ── 0.9.0: writes that cannot lose data (review R08) ────────────────────────────
def test_write_file_without_content_is_rejected_not_emptied():
    """A malformed call carrying only a path used to empty the file and report success."""
    tools.load_builtins()
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "keep.txt").write_text("precious\n")
        res = tools.get("write_file").run({"path": "keep.txt"}, _ctx(root))
        assert not res.ok and "content" in res.error
        assert (root / "keep.txt").read_text() == "precious\n"
        # None is the same malformed call; an explicit empty string is a real request.
        res = tools.get("write_file").run({"path": "keep.txt", "content": None}, _ctx(root))
        assert not res.ok and (root / "keep.txt").read_text() == "precious\n"
        res = tools.get("write_file").run({"path": "keep.txt", "content": ""}, _ctx(root))
        assert res.ok and (root / "keep.txt").read_text() == ""


def test_edit_file_preserves_crlf_and_refuses_non_utf8():
    tools.load_builtins()
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        crlf = root / "win.txt"
        crlf.write_bytes(b"one\r\ntwo\r\nthree\r\n")
        # The model quotes the file with plain \n; the file keeps its CRLF endings everywhere.
        res = tools.get("edit_file").run({"path": "win.txt", "old_string": "two\n", "new_string": "TWO\n"}, _ctx(root))
        assert res.ok, res.error
        assert crlf.read_bytes() == b"one\r\nTWO\r\nthree\r\n"
        binary = root / "bin.txt"
        binary.write_bytes(b"one\xff\ntwo\n")
        res = tools.get("edit_file").run({"path": "bin.txt", "old_string": "two", "new_string": "TWO"}, _ctx(root))
        assert not res.ok and "UTF-8" in res.error
        assert binary.read_bytes() == b"one\xff\ntwo\n"  # untouched, not "repaired"
        res = tools.get("edit_file").run({"path": "win.txt", "old_string": "", "new_string": "x"}, _ctx(root))
        assert not res.ok


def test_atomic_write_keeps_old_file_on_failure_and_preserves_mode():
    import os
    from scootcli.tools.base import FileChangedError, atomic_write_bytes

    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / "script.sh"
        target.write_text("#!/bin/sh\necho old\n")
        os.chmod(target, 0o755)
        atomic_write_bytes(target, b"#!/bin/sh\necho new\n")
        assert target.read_text() == "#!/bin/sh\necho new\n"
        assert (target.stat().st_mode & 0o777) == 0o755
        assert [p.name for p in Path(d).iterdir()] == ["script.sh"]  # no temp file left behind
        # A file that changed after it was read is not overwritten.
        before = target.stat()
        os.utime(target, (before.st_atime, before.st_mtime + 5))
        try:
            atomic_write_bytes(target, b"stale", expect_stat=before)
            assert False, "expected FileChangedError"
        except FileChangedError:
            pass
        assert target.read_text() == "#!/bin/sh\necho new\n"
        assert [p.name for p in Path(d).iterdir()] == ["script.sh"]


# ── 0.9.0: cancellation reaches the whole process tree (review R10) ─────────────
def test_run_subprocess_timeout_kills_descendants_and_returns_promptly():
    import os
    import time
    from scootcli.tools.base import ToolError, run_subprocess

    if os.name != "posix":
        return
    with tempfile.TemporaryDirectory() as d:
        marker = Path(d) / "marker"
        # A grandchild that keeps stdout open and would write a marker after the shell is gone.
        cmd = ["/bin/sh", "-c", f"(sleep 1.5; touch {marker}) & wait"]
        started = time.monotonic()
        try:
            run_subprocess(cmd, Path(d), timeout=0.3)
            assert False, "expected a timeout"
        except ToolError as exc:
            assert "timed out" in str(exc)
        assert time.monotonic() - started < 3, "the final wait must be bounded"
        time.sleep(1.8)
        assert not marker.exists(), "the descendant outlived the cancelled command"


def test_run_subprocess_cancel_kills_process_group():
    import os
    import threading
    import time
    from scootcli.errors import Interrupted
    from scootcli.tools.base import run_subprocess

    if os.name != "posix":
        return
    with tempfile.TemporaryDirectory() as d:
        marker = Path(d) / "marker"
        cancel = threading.Event()
        threading.Timer(0.2, cancel.set).start()
        try:
            run_subprocess(["/bin/sh", "-c", f"(sleep 1.5; touch {marker}) & wait"], Path(d), cancel, timeout=10)
            assert False, "expected Interrupted"
        except Interrupted:
            pass
        time.sleep(1.8)
        assert not marker.exists()


def test_run_subprocess_feeds_stdin_text():
    from scootcli.tools.base import run_subprocess

    rc, out, _err = run_subprocess(["/bin/sh", "-c", "cat"], Path("."), timeout=5, input_text="from stdin")
    assert rc == 0 and out == "from stdin"


# ── 0.10.0: the stdlib search cannot read outside the workspace (review R07) ─────
def test_python_search_skips_symlinks_and_hidden_files():
    import os
    from scootcli.tools.search import Search

    tools.load_builtins()
    with tempfile.TemporaryDirectory() as d:
        outside = Path(d) / "outside.txt"
        outside.write_text("MARKER in an external file\n")
        root = Path(d) / "workspace"
        root.mkdir()
        (root / "inside.txt").write_text("MARKER in the workspace\n")
        (root / ".env").write_text("MARKER=secret\n")
        (root / ".github").mkdir()
        (root / ".github" / "ci.yml").write_text("MARKER hidden dir\n")
        os.symlink(outside, root / "linked.txt")
        os.symlink(Path(d), root / "linked_dir")
        matches, _ = Search()._python_search("MARKER", False, None, _ctx(root))
        assert [m[0] for m in matches] == ["inside.txt"]


def test_workspace_map_does_not_follow_symlinks():
    import os
    from scootcli.workspace import render_tree

    with tempfile.TemporaryDirectory() as d:
        elsewhere = Path(d) / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "private-notes.md").write_text("x")
        root = Path(d) / "workspace"
        root.mkdir()
        (root / "real.py").write_text("x")
        os.symlink(elsewhere, root / "link")
        os.symlink(elsewhere / "private-notes.md", root / "linked-file.md")
        tree = render_tree(root)
        assert "real.py" in tree and "private-notes" not in tree and "link" not in tree


# ── 0.10.0: read_file never reads more than the cap, never a FIFO (review R15) ───
def test_read_file_is_bounded_and_refuses_special_files():
    import os
    from scootcli.tools.base import MAX_READ_BYTES

    tools.load_builtins()
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        big = root / "big.log"
        with open(big, "wb") as fh:
            fh.seek(3 * MAX_READ_BYTES)  # a sparse 600 KB file; reading it whole would be the bug
            fh.write(b"end\n")
        res = tools.get("read_file").run({"path": "big.log"}, _ctx(root))
        assert res.ok and "truncated" in res.content
        fifo = root / "pipe"
        os.mkfifo(fifo)
        res = tools.get("read_file").run({"path": "pipe"}, _ctx(root))
        assert not res.ok and "regular file" in res.error


def test_image_over_the_cap_is_rejected_before_reading():
    from scootcli.images import ImageTooLargeError, to_data_uri

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "huge.png"
        with open(p, "wb") as fh:
            fh.seek(10 * 1024 * 1024)
            fh.write(b"\x00")
        try:
            to_data_uri(p, max_bytes=1024)
            assert False, "expected ImageTooLargeError"
        except ImageTooLargeError as exc:
            assert "over the 1 KB limit" in str(exc)


# ── 0.11.0: ripgrep errors are not "no matches" (review corrections) ─────────────
def test_search_reports_ripgrep_errors():
    import shutil as _sh
    from scootcli.tools.search import Search

    if _sh.which("rg") is None:
        return
    with tempfile.TemporaryDirectory() as d:
        # An invalid regex makes ripgrep exit 2; that must surface as an error, not "0 matches".
        res = tools.get("search").run({"query": "(unclosed", "is_regex": True}, _ctx(Path(d)))
        assert not res.ok and "search failed" in res.error


def test_agents_md_is_bounded():
    from scootcli.prompts import load_agents_md, _AGENTS_MD_MAX

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "AGENTS.md").write_text("x" * (_AGENTS_MD_MAX + 5000))
        text = load_agents_md(root)
        assert len(text) <= _AGENTS_MD_MAX + 40 and "truncated" in text


# ── 0.11.0: subprocess capture is bounded (review R15) ───────────────────────────
def test_run_subprocess_bounds_captured_output():
    import sys as _sys
    from scootcli.tools.base import _MAX_CAPTURE_CHARS, run_subprocess

    # A command that prints far more than the cap, then exits: the return is bounded, not the full 4 MB.
    rc, out, _err = run_subprocess(
        [_sys.executable, "-c", "import sys; sys.stdout.write('x' * 4_000_000)"],
        Path("."), timeout=30)
    assert rc == 0
    assert len(out) <= _MAX_CAPTURE_CHARS + 40
    assert "truncated" in out
    # A small command is still captured in full, no marker.
    rc, out, _err = run_subprocess([_sys.executable, "-c", "print('small')"], Path("."), timeout=10)
    assert rc == 0 and out.strip() == "small" and "truncated" not in out


def test_run_subprocess_bounds_output_without_hanging_on_a_runaway():
    import sys as _sys
    import time as _time
    from scootcli.tools.base import ToolError, run_subprocess

    # An endless printer must be stopped by the timeout with a bounded buffer, not fill memory first.
    started = _time.monotonic()
    try:
        run_subprocess([_sys.executable, "-c", "import sys\nwhile True: sys.stdout.write('x' * 4096)"],
                       Path("."), timeout=1)
        assert False, "expected a timeout"
    except ToolError as exc:
        assert "timed out" in str(exc)
    assert _time.monotonic() - started < 4  # stopped promptly, did not buffer without end
