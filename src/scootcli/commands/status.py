"""/status — show current model, token usage, root, and session info."""

from __future__ import annotations

from ..rendering import color
from . import register
from .base import SlashCommand


def _run(session, args: str):
    cfg = session.config
    try:
        provider_row = session.provider.default_name
        from ..auth import key_source

        src = key_source(session.provider.spec)
        provider_row += f"  (key: {src})" if src else ("" if not session.provider.spec.key_required
                                                       else "  (no key)")
    except Exception:
        provider_row = getattr(cfg, "provider", "") or "?"
    rows = [
        ("provider", provider_row),
        ("model", session.active_model + ("  (auto)" if session.model.lower() == "auto" else "")),
        ("effort", getattr(cfg, "effort", "medium")),
        ("env files", ", ".join(getattr(cfg, "env_files", ()) or ()) or "none"),
        ("root", str(cfg.root)),
        ("approval", session.approval_mode),
        ("streaming", "on" if getattr(cfg, "stream", True) else "off"),
        ("panel", "on" if getattr(session, "status_bar", None) and session.status_bar.enabled
                  else "off"),
        ("input dock", "on" if getattr(session, "dock", False) else "off"),
        ("resume", getattr(cfg, "resume", "hint")),
        ("workspace context", "on" if getattr(cfg, "workspace_context", True) else "off"),
        ("feed verbosity", getattr(getattr(session, "ui", None), "verbosity",
                                   getattr(cfg, "verbosity", "full"))),
        ("max steps", str(cfg.max_steps)),
        ("turns", str(len(session.messages))),
        ("tokens (session)", f"prompt={session.total_prompt} completion={session.total_completion}"),
        ("tokens (last)", f"prompt={session.last_usage.get('prompt_tokens', '?')} "
                          f"completion={session.last_usage.get('completion_tokens', '?')}"),
    ]
    print(color("status:", "bold"))
    for label, value in rows:
        print(f"  {color(label + ':', 'gray'):<28} {value}")
    by_model = getattr(session, "usage_by_model", None) or {}
    if len(by_model) > 1 or (by_model and session.model.lower() == "auto"):
        print(f"  {color('tokens by model:', 'gray')}")
        for name, u in sorted(by_model.items(), key=lambda kv: -(kv[1]['prompt'] + kv[1]['completion'])):
            print(f"    {color(name, 'cyan'):<40} prompt={u['prompt']} completion={u['completion']} calls={u['calls']}")
    if session.model.lower() == "auto" and getattr(session, "route_reason", ""):
        print(f"  {color('routed by:', 'gray'):<28} {session.route_reason}")
    trusted = getattr(session, "trusted_tools", None)
    if trusted:
        print(f"  {color('trusted tools:', 'gray'):<28} {', '.join(sorted(trusted))}")
    if getattr(session, "worktree", None) is not None:
        print(f"  {color('worktree:', 'gray'):<28} {session.worktree.branch}")
    plan = getattr(session, "plan", None)
    if plan:
        done = sum(1 for s in plan if s.get("status") == "completed")
        current = next((s.get("step", "") for s in plan if s.get("status") == "in_progress"), "")
        detail = f"{done}/{len(plan)}" + (f" · now: {current}" if current else "")
        print(f"  {color('plan:', 'gray'):<28} {detail}")


register(SlashCommand("status", "show model + token usage", _run))

