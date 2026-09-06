"""run_shell — execute a shell command in the workspace (always approval-gated, interruptible)."""

from __future__ import annotations

from ..rendering import color
from . import register
from .base import (
    DEFAULT_SHELL_TIMEOUT,
    Tool,
    ToolContext,
    ToolError,
    ToolResult,
    noninteractive_env,
    run_subprocess,
    safe_path,
    truncate,
)


class RunShell(Tool):
    name = "run_shell"
    description = (
        "Run a shell command from the workspace root and return its output. "
        "Prefer short, read-only/idempotent commands."
    )
    risk = "shell"
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run."},
            "cwd": {"type": "string", "description": "Working directory (optional): relative to the workspace, or absolute."},
        },
        "required": ["command"],
    }

    def preview(self, args: dict, ctx: ToolContext):
        cwd = args.get("cwd")
        loc = f" (in {cwd})" if cwd else ""
        return color(f"$ {args.get('command', '')}{loc}", "yellow")

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        command = args.get("command")
        if not command:
            return ToolResult.fail("missing 'command'")
        cwd = ctx.root
        if args.get("cwd"):
            try:
                cwd = safe_path(ctx.root, args["cwd"], ctx.scope)
            except ToolError as exc:
                return ToolResult.fail(str(exc))
            if not cwd.is_dir():
                return ToolResult.fail(f"cwd is not a directory: {args['cwd']}")

        try:
            rc, out, err = run_subprocess(
                ["/bin/sh", "-c", command], cwd, ctx.cancel_event,
                timeout=DEFAULT_SHELL_TIMEOUT, env=noninteractive_env(),
            )
        except ToolError as exc:
            return ToolResult.fail(str(exc))

        body = ""
        if out.strip():
            body += out
        if err.strip():
            body += ("\n" if body else "") + "[stderr]\n" + err
        body = truncate(body.strip()) or "(no output)"
        content = f"exit {rc}\n{body}"
        ok = rc == 0
        summary = f"exit {rc}"
        return ToolResult(ok=ok, content=content, error="" if ok else f"exit {rc}", summary=summary)


register(RunShell())

