"""/save <file> — dump the current transcript to a file."""

from __future__ import annotations

from pathlib import Path

from ..rendering import color, eprint
from . import register
from .base import SlashCommand


def _run(session, args: str):
    target = args.strip() or "scoot-transcript.md"
    path = Path(target).expanduser()
    lines = [f"# scoot transcript\n\n_model: {session.resolved_model()}_\n"]
    for msg in session.messages:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        lines.append(f"\n## {role}\n\n{content}\n")
    try:
        path.write_text("".join(lines))
    except OSError as exc:
        eprint(color(f"could not write {path}: {exc}", "red"))
        return
    print(color(f"saved transcript → {path}", "gray"))


register(SlashCommand("save", "save the transcript", _run, usage="<file>"))

