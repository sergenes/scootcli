"""edit_file — surgical unique-match replacement in an existing file (with diff preview)."""

from __future__ import annotations

from ..rendering import diff_stats, unified_diff
from . import register
from .base import (Tool, ToolContext, ToolError, ToolResult, atomic_write_bytes, dominant_newline,
                   read_text_strict, safe_path)


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
        """Return ``(path, old_text, new_text, stat, newline)``. Raises ToolError on any problem.

        Matching happens on LF-normalised text so a model can quote a CRLF file with plain ``\n``;
        the file's own line-ending style is put back on write. ``stat`` is the file as read, so the
        write can refuse to overwrite a file that changed in between.
        """
        path = safe_path(ctx.root, args.get("path"), ctx.scope)
        if not path.exists() or not path.is_file():
            raise ToolError(f"no such file: {args.get('path')}")
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        if not isinstance(old_string, str) or not isinstance(new_string, str):
            raise ToolError("both 'old_string' and 'new_string' are required (strings)")
        if old_string == "":
            raise ToolError("old_string must not be empty")
        stat = path.stat()
        raw = read_text_strict(path)
        newline = dominant_newline(raw)
        old_text = raw.replace("\r\n", "\n")
        needle = old_string.replace("\r\n", "\n")
        occurrences = old_text.count(needle)
        if occurrences == 0:
            raise ToolError("old_string not found in file")
        if occurrences > 1:
            raise ToolError(f"old_string is not unique ({occurrences} matches); add more context")
        new_text = old_text.replace(needle, new_string.replace("\r\n", "\n"), 1)
        return path, old_text, new_text, stat, newline

    def preview(self, args: dict, ctx: ToolContext):
        try:
            _, old_text, new_text, _, _ = self._compute(args, ctx)
        except ToolError as exc:
            return str(exc)
        rel = args.get("path")
        added, removed = diff_stats(old_text, new_text)
        return f"edit {rel}  (+{added} -{removed})\n" + unified_diff(old_text, new_text, rel)

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            path, old_text, new_text, stat, newline = self._compute(args, ctx)
        except ToolError as exc:
            return ToolResult.fail(str(exc))
        out = new_text if newline == "\n" else new_text.replace("\n", "\r\n")
        try:
            atomic_write_bytes(path, out.encode("utf-8"), expect_stat=stat)
        except (OSError, ToolError) as exc:
            return ToolResult.fail(f"write failed: {exc}")
        added, removed = diff_stats(old_text, new_text)
        return ToolResult(
            ok=True,
            content=f"edited {args.get('path')} (+{added} -{removed})",
            summary=f"{args.get('path')} +{added} -{removed}",
            meta={"added": added, "removed": removed},
        )


register(EditFile())

