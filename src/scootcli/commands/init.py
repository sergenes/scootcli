"""/init — learn the project and write AGENTS.md (auto-loaded into the system prompt)."""

from __future__ import annotations

from ..activity import activity
from ..errors import ScootError, Interrupted
from ..keys import read_key
from ..project import generate_agents_md
from ..rendering import color, eprint
from . import register
from .base import SlashCommand


def _run(session, args: str):
    root = session.config.root
    target = root / "AGENTS.md"

    if target.exists():
        print(color("AGENTS.md already exists — overwrite? [y/N] ", "yellow"), end="", flush=True)
        key = read_key()
        print(key)
        if key != "y":
            print(color("cancelled.", "gray"))
            return

    try:
        with activity("scanning project & writing AGENTS.md…") as cancel:
            content = generate_agents_md(session, cancel)
    except Interrupted:
        print(color("⏹ interrupted", "yellow"))
        return
    except ScootError as exc:
        eprint(color(f"/init failed: {exc}", "red"))
        return

    if not content:
        eprint(color("model returned no content.", "yellow"))
        return
    try:
        target.write_text(content, "utf-8")
    except OSError as exc:
        eprint(color(f"could not write AGENTS.md: {exc}", "red"))
        return
    print(color(
        f"wrote {target} ({len(content.splitlines())} lines). "
        "It will load into context automatically on future turns.", "gray"))


register(SlashCommand("init", "learn the project → AGENTS.md", _run))

