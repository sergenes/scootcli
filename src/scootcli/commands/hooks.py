"""/hooks — list the configured hooks and what they did last."""

from __future__ import annotations

from ..hooks import EVENTS, enabled, global_path, project_path
from ..rendering import color
from . import register
from .base import SlashCommand


def _run(session, args: str):
    hooks = getattr(session, "hooks", None)
    if (args or "").strip() == "reload" and hooks is not None:
        hooks.reload()
        print(color("hooks reloaded.", "gray"))
    print(color("hooks:", "bold"))
    if not enabled():
        print(color("  disabled by SCOOT_HOOKS=0", "yellow"))
    root = getattr(getattr(session, "config", None), "root", ".")
    print(color(f"  files: {project_path(root)} (project), {global_path()} (global)", "gray"))
    if hooks is None or not hooks.config:
        print(color("  none configured", "gray"))
    else:
        for event in EVENTS:
            for entry in hooks.config.get(event, []):
                matcher = f"  [{entry['matcher']}]" if entry.get("matcher") else ""
                for hook in entry.get("hooks", []):
                    print(f"  {color(event, 'cyan')}{matcher}  {hook.get('command', '')}")
        if hooks.history:
            print(color("  recent:", "gray"))
            for r in hooks.history[-8:]:
                detail = f"  {r.detail[:60]}" if r.detail else ""
                print(f"    {r.event:<16} {r.outcome:<8} {r.seconds:>5.2f}s  {r.command[:40]}{detail}")
    print(color("  usage: /hooks [reload]", "gray"))


register(SlashCommand("hooks", "list configured hooks and recent results", _run, usage="[reload]"))
