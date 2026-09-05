"""/model — list available models or switch the active one ('auto' by default).

Models are ``provider/model`` ids (``openai/gpt-5.3-codex``, ``ollama/llama3.2``); a bare name means
the default provider. A switch made here is **persisted** (``~/.config/scoot/preferences.json``) so the
next launch reuses it. An explicit ``--model`` flag or ``SCOOT_MODEL`` still overrides it for that run.
"""

from __future__ import annotations

from ..preferences import set_model
from ..providers.base import qualify, split_model_id
from ..rendering import color
from . import register
from .base import SlashCommand


def _run(session, args: str):
    arg = args.strip()
    available = session.available_models()

    if not arg:
        print(color(f"models  (preference: {session.model} → active: {session.active_model})", "bold"))
        current = ""
        for mid in available:
            head, _ = split_model_id(mid)
            if head != current:
                current = head
                print(color(f"  {head}", "bold"))
            mark = color(" ← active", "green") if mid == session.active_model else ""
            print("    " + color(mid, "cyan") + mark)
        errors = getattr(getattr(session, "provider", None), "list_errors", None) or {}
        for name, err in sorted(errors.items()):
            print(color(f"  {name}: unavailable ({err})", "yellow"))
        print(color("  use: /model <provider/model> · /model default · /model auto (pick per task)", "gray"))
        return

    if arg in ("auto", "default"):
        session.model = arg
        session.active_model = session.resolved_model()
        set_model(arg)
        what = "picked per task from the live list" if arg == "auto" else f"the provider's preferred model, now {session.active_model}"
        print(color(f"model preference set to {arg} ({what}); saved for next launch.", "gray"))
        return

    if arg not in available:
        head, _ = split_model_id(arg)
        if head is None:
            try:
                arg = qualify(session.provider.default_name, arg)
            except Exception:
                pass
        if arg not in available:
            print(color(f"warning: '{arg}' is not in the models list; setting it anyway.", "yellow"))
    session.model = arg
    session.active_model = arg
    set_model(arg)
    print(color(f"model set to {arg}; saved for next launch.", "gray"))


register(SlashCommand("model", "list or switch model (default|auto|provider/model)", _run, usage="[provider/model|default|auto]"))

