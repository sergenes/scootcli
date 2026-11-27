"""search: find text in the workspace or under a given path. Uses ripgrep when available, else a
stdlib fallback.

Both backends see hidden files (``.github/``, ``.gitlab-ci.yml``) and both are held to the shared
:mod:`scootcli.pathpolicy`: no VCS internals, caches or virtual environments, no files that usually
hold credentials, no symlinks. ripgrep's results are filtered again against the policy, so the two
backends agree on what is searchable.

Results are **grouped by file** with per-file counts and a few sample lines each, so large result
sets stay compact (token-efficient) instead of dumping hundreds of raw lines into the context. Pass
``files_only`` for just a file+count list (great for "where is X used?").
"""

from __future__ import annotations

import shutil
from collections import OrderedDict
from pathlib import Path

from .. import pathpolicy
from . import register
from .base import Tool, ToolContext, ToolError, ToolResult, _inside, run_subprocess, safe_path, truncate

_SCAN_CAP = 1000     # max raw matches collected internally (keeps work + counts bounded)
_MAX_FILES = 20      # files shown in the grouped output
_MAX_PER_FILE = 3    # sample lines shown per file
_LINE_TRIM = 160     # per-line character cap
_MAX_FILE_BYTES = 1_000_000


class Search(Tool):
    name = "search"
    description = (
        "Search file contents for a string or regex, in the workspace or under `path`. Hidden files "
        "such as .github/ are included; VCS internals, caches, and files that hold credentials are "
        "not. Returns matches grouped by file with per-file counts and a few sample lines. Set "
        "files_only=true for just a file list."
    )
    risk = "read"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Text or regex to search for."},
            "is_regex": {"type": "boolean", "description": "Treat query as a regex (default false)."},
            "include_glob": {"type": "string", "description": "Only search files matching this glob."},
            "path": {
                "type": "string",
                "description": "Directory or file to search (default: the workspace root). May be "
                               "outside the workspace, for example a sibling repository.",
            },
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
        hidden = pathpolicy.hidden_enabled(ctx.config)

        try:
            base, only, prefix = self._target(args.get("path"), ctx)
            if shutil.which("rg"):
                matches, capped = self._ripgrep(query, is_regex, glob, ctx, base, only, hidden)
            else:
                matches, capped = self._python_search(query, is_regex, glob, ctx, base, only, hidden)
        except ToolError as exc:
            return ToolResult.fail(str(exc))
        if prefix:
            matches = [(prefix + rel, n, text) for rel, n, text in matches]

        if not matches:
            return ToolResult(ok=True, content="(no matches)", summary="0 matches")

        groups = self._group(matches)
        content = self._format(groups, len(matches), capped, files_only)
        total = f"{len(matches)}{'+' if capped else ''}"
        summary = f"{total} matches in {len(groups)} file(s)"
        return ToolResult(ok=True, content=truncate(content), summary=summary)

    # ── where to search ───────────────────────────────────────────────────────────
    @staticmethod
    def _target(raw, ctx):
        """``(base directory, single file name or None, display prefix)`` for the ``path`` argument.

        The path is resolved (symlinks included) and checked against the scope, so a search outside
        the workspace needs the same grant as a read there. Results under the workspace are shown
        relative to its root, results elsewhere with the absolute base.
        """
        root = Path(ctx.root).resolve()
        if not raw or not str(raw).strip():
            return root, None, ""
        target = safe_path(ctx.root, raw, ctx.scope)
        if not target.exists():
            raise ToolError(f"no such file or directory: {raw}")
        only = None
        base = target
        if not target.is_dir():
            if not target.is_file():
                raise ToolError(f"not a regular file or directory: {raw}")
            if pathpolicy.is_secret_file(target.name):
                raise ToolError(f"{target.name} is excluded from search: it usually holds credentials")
            base, only = target.parent, target.name
        if _inside(base, root):
            rel = base.relative_to(root).as_posix()
            prefix = "" if rel == "." else rel + "/"
        else:
            prefix = base.as_posix().rstrip("/") + "/"
        return base, only, prefix

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
                f"… {extra}Narrow with include_glob, path, or a more specific query, "
                f"or read a specific file."
            )
        return "\n".join(lines)

    # ── backends ────────────────────────────────────────────────────────────────
    def _ripgrep(self, query, is_regex, glob, ctx, base=None, only=None, hidden=True):
        base = Path(base or ctx.root)
        cmd = ["rg", "--line-number", "--no-heading", "--with-filename", "--color", "never"]
        if hidden:
            cmd.append("--hidden")
        if not is_regex:
            cmd.append("--fixed-strings")
        if glob:
            cmd += ["--glob", glob]
        if hidden:
            for exclude in pathpolicy.ripgrep_globs():  # after the user's glob: a later glob wins
                cmd += ["--glob", exclude]
        cmd += ["--", query, only if only else "."]
        rc, out, err = run_subprocess(cmd, base, ctx.cancel_event, timeout=30)
        # ripgrep: 0 = matches, 1 = no matches (not an error), 2+ = a real failure (bad regex, I/O).
        if rc >= 2:
            raise ToolError(f"search failed: {err.strip() or f'ripgrep exited {rc}'}")
        return self._parse_rg(out.splitlines(), hidden)

    @staticmethod
    def _parse_rg(raw_lines, hidden=True):
        """ripgrep's ``path:line:text`` rows as matches, minus the paths the policy excludes."""
        matches = []
        capped = False
        for line in raw_lines:
            if not line:
                continue
            parts = line.split(":", 2)  # path:lineno:text
            if len(parts) < 3:
                continue
            rel, num, text = parts
            if rel.startswith("./"):
                rel = rel[2:]
            if not pathpolicy.allows(rel, hidden):
                continue
            try:
                num_int = int(num)
            except ValueError:
                continue
            matches.append((rel, num_int, text.strip()))
            if len(matches) >= _SCAN_CAP:
                capped = True
                break
        return matches, capped

    def _python_search(self, query, is_regex, glob, ctx, base=None, only=None, hidden=True):
        """The fallback without ripgrep. It walks with the path policy: no symlinks (a link inside the
        workspace must not read a file outside it through an auto-approved tool), only regular files,
        no noise directories, no files that usually hold credentials."""
        import fnmatch
        import os
        import re

        base = Path(base or ctx.root)
        try:
            pattern = re.compile(query) if is_regex else None
        except re.error as exc:
            raise ToolError(f"search failed: bad regex: {exc}")
        files = [base / only] if only else pathpolicy.iter_files(base, hidden)
        matches = []
        capped = False
        for path in files:
            if len(matches) >= _SCAN_CAP:
                capped = True
                break
            rel = path.relative_to(base).as_posix()
            if glob and not (fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(path.name, glob)):
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


register(Search())
