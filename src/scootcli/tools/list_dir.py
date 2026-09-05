"""list_dir — list a directory's contents, sandboxed to the workspace root."""

from __future__ import annotations

from . import register
from .base import Tool, ToolContext, ToolError, ToolResult, safe_path, truncate

_HIDDEN = {".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache", "node_modules"}
_MAX_ENTRIES = 300


class ListDir(Tool):
    name = "list_dir"
    description = "List the entries of a directory within the workspace (directories end with '/')."
    risk = "read"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path (default: workspace root)."},
        },
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        raw = args.get("path") or "."
        try:
            path = safe_path(ctx.root, raw)
        except ToolError as exc:
            return ToolResult.fail(str(exc))
        if not path.exists():
            return ToolResult.fail(f"no such directory: {raw}")
        if not path.is_dir():
            return ToolResult.fail(f"not a directory: {raw}")

        entries = []
        for child in sorted(path.iterdir(), key=lambda c: (c.is_file(), c.name.lower())):
            if child.name in _HIDDEN:
                continue
            entries.append(child.name + ("/" if child.is_dir() else ""))

        shown = entries[:_MAX_ENTRIES]
        note = "" if len(entries) <= _MAX_ENTRIES else f"\n… [{len(entries) - _MAX_ENTRIES} more]"
        content = truncate("\n".join(shown)) + note
        return ToolResult(ok=True, content=content or "(empty)", summary=f"{len(entries)} entries")


register(ListDir())

