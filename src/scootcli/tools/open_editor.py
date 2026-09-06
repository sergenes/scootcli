"""open_editor: open a workspace file in an external GUI editor (IntelliJ IDEA / VS Code).

Launches the editor's CLI launcher (``idea -e <path>`` by default, or ``code <path>``) as a
detached process so the assistant can hand a just-created/edited file straight to the user's
editor. The default editor is configurable (``SCOOT_EDITOR`` / ``config.editor``); the tool
also accepts a per-call ``editor`` argument.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from . import register
from .base import Tool, ToolContext, ToolError, ToolResult, safe_path

# editor key -> launch argv template (path is appended)
_EDITORS = {
    "idea": ["idea", "-e"],   # IntelliJ IDEA "lightweight edit" mode
    "vscode": ["code"],       # VS Code CLI launcher
}
_ALIASES = {"intellij": "idea", "ij": "idea", "code": "vscode", "vs": "vscode", "vscode": "vscode"}

# macOS fallbacks: the in-bundle launcher binary (same one the shell command points at) when the
# `idea`/`code` shell launcher isn't on PATH. We call the binary directly — NOT `open -a` — so
# IDEA keeps the lightweight `-e` editor window instead of opening the folder as a full project.
_MACOS_BUNDLE_BINS = {
    "idea": [
        "/Applications/IntelliJ IDEA.app/Contents/MacOS/idea",
        "/Applications/IntelliJ IDEA CE.app/Contents/MacOS/idea",
        str(Path.home() / "Applications/IntelliJ IDEA.app/Contents/MacOS/idea"),
    ],
    "vscode": [
        "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
        str(Path.home() / "Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),
    ],
}


def _resolve_launcher(editor: str) -> "list | None":
    """Return the argv prefix for ``editor`` (PATH launcher first, then macOS bundle binary)."""
    template = _EDITORS.get(editor)
    if template is None:
        return None
    if shutil.which(template[0]) is not None:
        return list(template)
    if sys.platform == "darwin":
        for candidate in _MACOS_BUNDLE_BINS.get(editor, []):
            if Path(candidate).is_file():
                # keep any extra flags (e.g. idea's "-e") after the resolved binary
                return [candidate, *template[1:]]
    return None


class OpenEditor(Tool):
    name = "open_editor"
    description = (
        "Open a workspace file in an external GUI editor (IntelliJ IDEA by default, or VS Code). "
        "Use when the user asks to open/show a file in their editor."
    )
    risk = "shell"  # spawns an external process — approval-gated like run_shell
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File (or directory) path to open, within the workspace."},
            "editor": {
                "type": "string",
                "enum": ["idea", "vscode"],
                "description": "Editor to use. Omit to use the configured default (idea).",
            },
        },
        "required": ["path"],
    }

    def _resolve_editor(self, args: dict, ctx: ToolContext) -> str:
        raw = (args.get("editor") or getattr(ctx.config, "editor", "idea") or "idea").strip().lower()
        return _ALIASES.get(raw, raw)

    def preview(self, args: dict, ctx: ToolContext):
        editor = self._resolve_editor(args, ctx)
        launcher = _resolve_launcher(editor) or _EDITORS.get(editor, [editor])
        return f"open in {editor}: {' '.join(launcher)} {args.get('path', '')}"

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        raw = args.get("path")
        if not raw:
            return ToolResult.fail("missing 'path'")
        try:
            path = safe_path(ctx.root, raw, ctx.scope)
        except ToolError as exc:
            return ToolResult.fail(str(exc))
        if not path.exists():
            return ToolResult.fail(f"no such file: {raw}")

        editor = self._resolve_editor(args, ctx)
        if editor not in _EDITORS:
            return ToolResult.fail(f"unknown editor '{editor}' (choose: {', '.join(_EDITORS)})")

        launcher = _resolve_launcher(editor)
        if launcher is None:
            hint = (
                "install the JetBrains 'idea' shell launcher (Tools ▸ Create Command-line Launcher)"
                if editor == "idea"
                else "install the VS Code 'code' shell command (Command Palette ▸ Shell Command)"
            )
            return ToolResult.fail(f"launcher for '{editor}' not found — {hint}")

        cmd = launcher + [str(path)]
        try:
            # Detached: don't block the agent on the editor process; ignore its stdio.
            subprocess.Popen(
                cmd, cwd=str(ctx.root),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            return ToolResult.fail(f"failed to launch {editor}: {exc}")

        return ToolResult(ok=True, content=f"opened {raw} in {editor}", summary=f"opened in {editor}")


register(OpenEditor())
