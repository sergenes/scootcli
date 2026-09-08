"""read_file — read a text file (optionally a line range), sandboxed to the workspace root."""

from __future__ import annotations

from . import register
from .base import MAX_READ_BYTES, Tool, ToolContext, ToolError, ToolResult, safe_path, truncate


class ReadFile(Tool):
    name = "read_file"
    description = (
        "Read a UTF-8 text file within the workspace, optionally a line range. "
        "Returns line-numbered content."
    )
    risk = "read"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to the workspace root."},
            "start": {"type": "integer", "description": "1-based first line (optional)."},
            "end": {"type": "integer", "description": "1-based last line, inclusive (optional)."},
        },
        "required": ["path"],
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            path = safe_path(ctx.root, args.get("path"), ctx.scope)
        except ToolError as exc:
            return ToolResult.fail(str(exc))
        if not path.exists():
            return ToolResult.fail(f"no such file: {args.get('path')}")
        if path.is_dir():
            return ToolResult.fail(f"is a directory: {args.get('path')}")
        if not path.is_file():
            return ToolResult.fail(f"not a regular file: {args.get('path')}")  # a FIFO or device would block

        try:
            with open(path, "rb") as fh:
                data = fh.read(MAX_READ_BYTES + 1)  # never the whole file: the cap applies to the read itself
        except OSError as exc:
            return ToolResult.fail(f"read failed: {exc}")
        note = ""
        if len(data) > MAX_READ_BYTES:
            data = data[:MAX_READ_BYTES]
            note = " [file truncated to 200 KB]"
        lines = data.decode("utf-8", "replace").splitlines()

        total = len(lines)
        start = max(1, int(args.get("start") or 1))
        end = int(args.get("end") or total)
        end = min(end, total)
        selected = lines[start - 1 : end]

        width = len(str(end))
        numbered = "\n".join(f"{i:>{width}} | {line}" for i, line in enumerate(selected, start))
        summary = f"{len(selected)} lines" + (f" ({start}-{end} of {total})" if selected else "")
        return ToolResult(ok=True, content=truncate(numbered) + note, summary=summary)


register(ReadFile())

