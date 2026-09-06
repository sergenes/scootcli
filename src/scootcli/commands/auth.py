"""/auth — show which providers have an API key, or save / forget one.

  /auth                 table of providers: key source, default marker, base URL
  /auth set <provider>  paste a key (hidden input), validate it against the provider, save it
  /auth clear <provider>  forget the saved key (an environment key is not ours to remove)
"""

from __future__ import annotations

import getpass

from ..activity import activity
from ..auth import key_source, missing_key_hint, status_rows
from ..credentials import credentials_path, delete_key, save_key
from ..errors import ScootError
from ..providers import registry
from ..rendering import color, eprint, redact
from . import register
from .base import SlashCommand


def _show(session) -> None:
    rows = status_rows(session.config)
    print(color("providers:", "bold"))
    for row in rows:
        name = row["name"] + ("  (default)" if row["default"] else "")
        if not row["required"] and row.get("reachable") is False:
            state = color("not running (ollama serve; install from https://ollama.com)", "yellow")
        elif not row["required"]:
            state = color("no key needed" + (", running" if row.get("reachable") else ""), "gray")
        elif row["source"]:
            state = color(f"key from {row['source']}", "green")
        else:
            state = color("no key", "yellow")
        print(f"  {color(name, 'cyan'):<32} {state}  {color(row['base_url'], 'gray')}")
    print(color("  usage: /auth set <provider> · /auth clear <provider>", "gray"))
    print(color(f"  saved keys live in {credentials_path()} (owner-only)", "gray"))


def _set(session, name: str) -> None:
    spec = registry.get(name)
    if spec is None:
        eprint(color(f"unknown provider '{name}'. known: {', '.join(registry.names())}", "yellow"))
        return
    if not spec.key_required:
        print(color(f"{name} needs no API key.", "gray"))
        return
    print(color(f"paste the {name} API key; it is validated before being saved and never echoed.", "gray"))
    try:
        key = getpass.getpass(f"{name} API key (hidden, blank to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if not key:
        print(color("no key entered; nothing changed.", "gray"))
        return
    probe = registry.make_provider(name, session.config)
    probe._api_key = key
    try:
        with activity(f"checking {name} key…") as cancel:
            probe.list_models(cancel_event=cancel)
    except ScootError as exc:
        eprint(color(f"✗ key rejected: {redact(str(exc))}", "red"))
        if getattr(exc, "hint", ""):
            print(color(f"  {exc.hint}", "gray"))
        return
    path = save_key(name, key)
    pool = getattr(session, "provider", None)
    if pool is not None and hasattr(pool, "_providers"):
        pool._providers.pop(name, None)  # rebuild with the new key on next use
    print(color(f"✔ {name} key saved to {path} (owner-only).", "green"))


def _clear(session, name: str) -> None:
    spec = registry.get(name)
    if spec is None:
        eprint(color(f"unknown provider '{name}'. known: {', '.join(registry.names())}", "yellow"))
        return
    if delete_key(name):
        print(color(f"forgot the saved {name} key.", "green"))
    else:
        print(color(f"no saved {name} key.", "gray"))
    pool = getattr(session, "provider", None)
    if pool is not None and hasattr(pool, "_providers"):
        pool._providers.pop(name, None)
    src = key_source(spec)
    if src:
        print(color(f"  note: {name} still has a key from {src} (not managed by scoot).", "gray"))
    elif spec.key_required:
        print(color(f"  {missing_key_hint(spec)}", "gray"))


def _run(session, args: str):
    words = (args or "").split()
    if not words:
        _show(session)
        return
    action, name = words[0].lower(), (words[1].lower() if len(words) > 1 else "")
    if action in ("set", "add", "login") and name:
        _set(session, name)
    elif action in ("clear", "remove", "forget", "logout") and name:
        _clear(session, name)
    elif action == "status":
        _show(session)
    else:
        eprint(color("usage: /auth [set <provider> | clear <provider>]", "yellow"))


register(SlashCommand("auth", "show provider keys, or set/clear one", _run, usage="[set|clear <provider>]"))
