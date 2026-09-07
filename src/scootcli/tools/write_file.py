"""write_file — create or overwrite a file (with a diff preview at approval time)."""

from __future__ import annotations

from ..rendering import diff_stats, unified_diff
from . import register
from .base import Tool, ToolContext, ToolError, ToolResult, atomic_write_bytes, safe_path


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
        new = args.get("content")
        if not isinstance(new, str):
            # A call with no content is a malformed call, not a request to empty the file; only an
            # explicit "" writes an empty file.
            raise ToolError("missing 'content' (a string; pass \"\" to write an empty file)")
        if path.exists() and not path.is_file():
            raise ToolError(f"not a regular file: {args.get('path')}")
        old = path.read_text("utf-8", "replace") if path.exists() else ""
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
            atomic_write_bytes(path, new.encode("utf-8"))
        except (OSError, ToolError) as exc:
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

