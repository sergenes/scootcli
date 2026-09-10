"""search — find text in the workspace. Uses ripgrep when available, else a stdlib fallback.

Results are **grouped by file** with per-file counts and a few sample lines each, so large result
sets stay compact (token-efficient) instead of dumping hundreds of raw lines into the context. Pass
``files_only`` for just a file+count list (great for "where is X used?").
"""

from __future__ import annotations

import shutil
from collections import OrderedDict

from . import register
from .base import Tool, ToolContext, ToolError, ToolResult, run_subprocess, truncate

_HIDDEN_DIRS = {".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache", "node_modules"}
_SCAN_CAP = 1000     # max raw matches collected internally (keeps work + counts bounded)
_MAX_FILES = 20      # files shown in the grouped output
_MAX_PER_FILE = 3    # sample lines shown per file
_LINE_TRIM = 160     # per-line character cap
_MAX_FILE_BYTES = 1_000_000


class Search(Tool):
    name = "search"
    description = (
        "Search file contents in the workspace for a string or regex. Returns matches grouped by "
        "file with per-file counts and a few sample lines. Set files_only=true for just a file list."
    )
    risk = "read"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Text or regex to search for."},
            "is_regex": {"type": "boolean", "description": "Treat query as a regex (default false)."},
            "include_glob": {"type": "string", "description": "Only search files matching this glob."},
            "files_only": {
                "type": "boolean",
                "description": "Return only matching file paths with counts (most compact).",
            },
        },
        "required": ["query"],
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        query = args.get("query")
        if not query:
            return ToolResult.fail("missing 'query'")
        is_regex = bool(args.get("is_regex"))
        glob = args.get("include_glob")
        files_only = bool(args.get("files_only"))

        try:
            if shutil.which("rg"):
                matches, capped = self._ripgrep(query, is_regex, glob, ctx)
            else:
                matches, capped = self._python_search(query, is_regex, glob, ctx)
        except ToolError as exc:
            return ToolResult.fail(str(exc))

        if not matches:
            return ToolResult(ok=True, content="(no matches)", summary="0 matches")

        groups = self._group(matches)
        content = self._format(groups, len(matches), capped, files_only)
        total = f"{len(matches)}{'+' if capped else ''}"
        summary = f"{total} matches in {len(groups)} file(s)"
        return ToolResult(ok=True, content=truncate(content), summary=summary)

    # ── formatting ────────────────────────────────────────────────────────────────
    @staticmethod
    def _group(matches):
        groups = OrderedDict()
        for rel, n, text in matches:
            groups.setdefault(rel, []).append((n, text))
        # Most-hit files first, then alphabetical for stability.
        return OrderedDict(sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])))

    def _format(self, groups, total, capped, files_only):
        nfiles = len(groups)
        shown = list(groups.items())[:_MAX_FILES]
        head = f"{total}{'+' if capped else ''} match(es) in {nfiles} file(s)"
        if nfiles > _MAX_FILES:
            head += f" (showing top {_MAX_FILES})"
        lines = [head + ":"]

        if files_only:
            for rel, hits in shown:
                lines.append(f"  {rel} ({len(hits)})")
        else:
            for rel, hits in shown:
                lines.append("")
                lines.append(f"{rel} ({len(hits)})")
                for n, text in hits[:_MAX_PER_FILE]:
                    lines.append(f"  {n}: {text[:_LINE_TRIM]}")
                if len(hits) > _MAX_PER_FILE:
                    lines.append(f"  … +{len(hits) - _MAX_PER_FILE} more in this file")

        if nfiles > _MAX_FILES or capped:
            lines.append("")
            extra = f"+{nfiles - _MAX_FILES} more file(s). " if nfiles > _MAX_FILES else ""
            lines.append(
                f"… {extra}Narrow with include_glob or a more specific query, "
                f"or read a specific file."
            )
        return "\n".join(lines)

    # ── backends ────────────────────────────────────────────────────────────────
    def _ripgrep(self, query, is_regex, glob, ctx):
        cmd = ["rg", "--line-number", "--no-heading", "--color", "never"]
        if not is_regex:
            cmd.append("--fixed-strings")
        if glob:
            cmd += ["--glob", glob]
        cmd += ["--", query]
        rc, out, err = run_subprocess(cmd, ctx.root, ctx.cancel_event, timeout=30)
        # ripgrep: 0 = matches, 1 = no matches (not an error), 2+ = a real failure (bad regex, I/O).
        if rc >= 2:
            raise ToolError(f"search failed: {err.strip() or f'ripgrep exited {rc}'}")
        return self._parse_rg(out.splitlines())

    @staticmethod
    def _parse_rg(raw_lines):
        matches = []
        capped = False
        for line in raw_lines:
            if not line:
                continue
            parts = line.split(":", 2)  # path:lineno:text
            if len(parts) < 3:
                continue
            rel, num, text = parts
            try:
                num_int = int(num)
            except ValueError:
                continue
            matches.append((rel, num_int, text.strip()))
            if len(matches) >= _SCAN_CAP:
                capped = True
                break
        return matches, capped

    def _python_search(self, query, is_regex, glob, ctx):
        """The fallback without ripgrep, walking the way ripgrep does by default: no hidden files or
        directories (``.env``, ``.git``), no symlinks (a link inside the workspace must not read a file
        outside it through an auto-approved tool), only regular files."""
        import fnmatch
        import os
        import re

        pattern = re.compile(query) if is_regex else None
        matches = []
        capped = False
        for path in _walk_files(ctx.root):
            if len(matches) >= _SCAN_CAP:
                capped = True
                break
            rel = path.relative_to(ctx.root).as_posix()
            if glob and not fnmatch.fnmatch(rel, glob):
                continue
            try:
                st = os.lstat(path)
                if st.st_size > _MAX_FILE_BYTES:
                    continue
                text = path.read_text("utf-8", "ignore")
            except (OSError, ValueError):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                hit = pattern.search(line) if pattern else (query in line)
                if hit:
                    matches.append((rel, n, line.strip()))
                    if len(matches) >= _SCAN_CAP:
                        capped = True
                        break
        return matches, capped


def _walk_files(root):
    """Regular files under ``root``: hidden names and ``_HIDDEN_DIRS`` pruned, symlinks never followed
    or read (``os.walk`` does not descend into linked directories; linked files are skipped here)."""
    import os
    from pathlib import Path

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in _HIDDEN_DIRS
                             and not os.path.islink(os.path.join(dirpath, d)))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            yield Path(full)


register(Search())

