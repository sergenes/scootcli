"""/model: list available models or switch the active one.

Models are ``provider/model`` ids (``openai/gpt-5.3-codex``, ``ollama/llama3.2``); a bare name means
the default provider. A switch made here is **persisted** (``~/.config/scoot/preferences.json``) for
this folder, so the next launch here reuses it; ``/model X everywhere`` saves it for every folder
without a choice of its own, and ``/model forget`` drops this folder's choice so it follows that. An
explicit ``--model`` flag or ``SCOOT_MODEL`` still overrides it for that run.
"""

from __future__ import annotations

from .. import preferences
from ..providers.base import qualify, split_model_id
from ..rendering import color
from . import register
from .base import SlashCommand

_USAGE = "/model <provider/model> · /model default · /model auto (pick per task) · add 'everywhere' for all folders · /model forget"


def _root(session):
    return getattr(getattr(session, "config", None), "root", None)


def _saved_line(session) -> str:
    folder, everywhere = preferences.saved_models(_root(session))
    return f"saved: this folder {folder or '(none)'} · everywhere {everywhere or '(none)'}"


def _remember(session, name: str, everywhere: bool) -> str:
    """Persist ``name`` for this folder (or every folder); return how to say so."""
    root = _root(session)
    if everywhere:
        preferences.set_model_everywhere(name, root=root)
        _note_origin(session, name, preferences.EVERYWHERE)
        return "saved for every folder"
    preferences.set_model(name, root=root)
    _note_origin(session, name, preferences.FOLDER)
    return "saved for this folder"


def _note_origin(session, name: str, source: str) -> None:
    """Keep ``config.model_source`` truthful after a switch, so ``/status`` names the right layer."""
    cfg = getattr(session, "config", None)
    if cfg is not None and hasattr(cfg, "override"):
        session.config = cfg.override(model=name, model_source=source)


def _run(session, args: str):
    words = args.split()
    everywhere = False
    if words and words[-1].lower() == "everywhere":
        everywhere = True
        words = words[:-1]
    arg = " ".join(words)
    available = session.available_models()

    if not arg:
        print(color(f"models  (preference: {session.model} → active: {session.active_model})", "bold"))
        print(color(f"  {_saved_line(session)}", "gray"))
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
        print(color(f"  use: {_USAGE}", "gray"))
        return

    if arg == "forget":
        preferences.clear_model(root=_root(session))
        session.model = preferences.get_model(_root(session)) or "default"
        session.active_model = session.resolved_model()
        _note_origin(session, session.model, preferences.model_source(_root(session)) or "default")
        print(color(f"this folder's model choice forgotten; now {session.model} → {session.active_model}. "
                    f"{_saved_line(session)}", "gray"))
        if hasattr(session, "refresh_readiness") and not session.refresh_readiness():
            print(color(session.setup_message, "yellow"))
        return

    if arg in ("auto", "default"):
        session.model = arg
        session.active_model = session.resolved_model()
        how = _remember(session, arg, everywhere)
        what = "picked per task from the live list" if arg == "auto" else f"the provider's preferred model, now {session.active_model}"
        print(color(f"model preference set to {arg} ({what}); {how}.", "gray"))
        if hasattr(session, "refresh_readiness") and not session.refresh_readiness():
            print(color(session.setup_message, "yellow"))
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
    how = _remember(session, arg, everywhere)
    print(color(f"model set to {arg}; {how}.", "gray"))
    if hasattr(session, "refresh_readiness") and not session.refresh_readiness():
        print(color(session.setup_message, "yellow"))


register(SlashCommand("model", "list or switch model (default|auto|provider/model), per folder or everywhere",
                      _run, usage="[provider/model|default|auto] [everywhere] | forget"))

