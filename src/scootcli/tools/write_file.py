"""write_file — create or overwrite a file (with a diff preview at approval time)."""

from __future__ import annotations

from ..rendering import diff_stats, unified_diff
from . import register
from .base import Tool, ToolContext, ToolError, ToolResult, safe_path


class WriteFile(Tool):
    name = "write_file"
    description = "Create a new file or overwrite an existing one with the given content."
    risk = "write"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to the workspace root."},
            "content": {"type": "string", "description": "Full new file content."},
        },
        "required": ["path", "content"],
    }

    def _old_new(self, args, ctx):
        path = safe_path(ctx.root, args.get("path"), ctx.scope)
        old = path.read_text("utf-8", "replace") if path.exists() and path.is_file() else ""
        new = args.get("content") or ""
        return path, old, new

    def preview(self, args: dict, ctx: ToolContext):
        try:
            path, old, new = self._old_new(args, ctx)
        except ToolError as exc:
            return str(exc)
        rel = args.get("path")
        verb = "overwrite" if old else "create"
        added, removed = diff_stats(old, new)
        header = f"{verb} {rel}  (+{added} -{removed})"
        return header + "\n" + unified_diff(old, new, rel)

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            path, old, new = self._old_new(args, ctx)
        except ToolError as exc:
            return ToolResult.fail(str(exc))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new, "utf-8")
        except OSError as exc:
            return ToolResult.fail(f"write failed: {exc}")
        added, removed = diff_stats(old, new)
        verb = "overwrote" if old else "created"
        return ToolResult(
            ok=True,
            content=f"{verb} {args.get('path')} (+{added} -{removed})",
            summary=f"{args.get('path')} +{added} -{removed}",
            meta={"added": added, "removed": removed},
        )


register(WriteFile())

