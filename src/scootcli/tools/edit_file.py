"""edit_file — surgical unique-match replacement in an existing file (with diff preview)."""

from __future__ import annotations

from ..rendering import diff_stats, unified_diff
from . import register
from .base import Tool, ToolContext, ToolError, ToolResult, safe_path


class EditFile(Tool):
    name = "edit_file"
    description = (
        "Replace an exact, unique snippet in an existing file. 'old_string' must occur exactly once."
    )
    risk = "write"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to the workspace root."},
            "old_string": {"type": "string", "description": "Exact text to replace (must be unique)."},
            "new_string": {"type": "string", "description": "Replacement text."},
        },
        "required": ["path", "old_string", "new_string"],
    }

    def _compute(self, args, ctx):
        """Return (path, old_text, new_text). Raises ToolError on any problem."""
        path = safe_path(ctx.root, args.get("path"))
        if not path.exists() or not path.is_file():
            raise ToolError(f"no such file: {args.get('path')}")
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        if old_string is None or new_string is None:
            raise ToolError("both 'old_string' and 'new_string' are required")
        old_text = path.read_text("utf-8", "replace")
        occurrences = old_text.count(old_string)
        if occurrences == 0:
            raise ToolError("old_string not found in file")
        if occurrences > 1:
            raise ToolError(f"old_string is not unique ({occurrences} matches); add more context")
        new_text = old_text.replace(old_string, new_string, 1)
        return path, old_text, new_text

    def preview(self, args: dict, ctx: ToolContext):
        try:
            _, old_text, new_text = self._compute(args, ctx)
        except ToolError as exc:
            return str(exc)
        rel = args.get("path")
        added, removed = diff_stats(old_text, new_text)
        return f"edit {rel}  (+{added} -{removed})\n" + unified_diff(old_text, new_text, rel)

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            path, old_text, new_text = self._compute(args, ctx)
        except ToolError as exc:
            return ToolResult.fail(str(exc))
        try:
            path.write_text(new_text, "utf-8")
        except OSError as exc:
            return ToolResult.fail(f"write failed: {exc}")
        added, removed = diff_stats(old_text, new_text)
        return ToolResult(
            ok=True,
            content=f"edited {args.get('path')} (+{added} -{removed})",
            summary=f"{args.get('path')} +{added} -{removed}",
            meta={"added": added, "removed": removed},
        )


register(EditFile())

