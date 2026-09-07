"""/hooks — list the configured hooks and what they did last."""

from __future__ import annotations

from ..hooks import EVENTS, enabled, global_path, project_path, project_trust, trust_project, untrust_project
from ..rendering import color
from . import register
from .base import SlashCommand


def _run(session, args: str):
    hooks = getattr(session, "hooks", None)
    sub = (args or "").strip().lower()
    root = getattr(getattr(session, "config", None), "root", ".")
    if sub == "trust":
        if trust_project(root):
            print(color(f"trusted {project_path(root)}; its hooks run from now on (until the file changes).", "gray"))
        else:
            print(color(f"no project hooks file at {project_path(root)}.", "yellow"))
        sub = "reload"
    elif sub == "untrust":
        print(color("project hooks are off again." if untrust_project(root) else "project hooks were not trusted.", "gray"))
        sub = "reload"
    if sub == "reload" and hooks is not None:
        hooks.reload()
        print(color("hooks reloaded.", "gray"))
    print(color("hooks:", "bold"))
    if not enabled():
        print(color("  disabled by SCOOT_HOOKS=0", "yellow"))
    print(color(f"  files: {project_path(root)} (project), {global_path()} (global)", "gray"))
    trust = project_trust(root)
    if trust == "untrusted":
        print(color("  project hooks: NOT trusted, not running. review the file, then /hooks trust", "yellow"))
    elif trust == "trusted":
        print(color("  project hooks: trusted (/hooks untrust to turn them off)", "gray"))
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
    print(color("  usage: /hooks [reload|trust|untrust]", "gray"))


register(SlashCommand("hooks", "list configured hooks and recent results; trust a project's hooks", _run,
                      usage="[reload|trust|untrust]"))
