"""Slash-command registry. Drop a new module in this package and call :func:`register` to add one."""

from __future__ import annotations

from typing import Dict, Optional

from .base import QUIT, SlashCommand

__all__ = ["QUIT", "SlashCommand", "register", "get", "all_commands", "load_builtins"]

_REGISTRY: Dict[str, SlashCommand] = {}


def register(command: SlashCommand) -> None:
    _REGISTRY[command.name] = command


def get(name: str) -> Optional[SlashCommand]:
    return _REGISTRY.get(name)


def all_commands() -> Dict[str, SlashCommand]:
    return dict(sorted(_REGISTRY.items()))


_loaded = False


def load_builtins() -> None:
    """Import the built-in command modules so they self-register (idempotent)."""
    global _loaded
    if _loaded:
        return
    # Importing each module triggers its register() call.
    from . import help as _help  # noqa: F401
    from . import exit as _exit  # noqa: F401
    from . import reset as _reset  # noqa: F401
    from . import save as _save  # noqa: F401
    from . import status as _status  # noqa: F401
    from . import model as _model  # noqa: F401
    from . import init as _init  # noqa: F401
    from . import compact as _compact  # noqa: F401
    from . import approve as _approve  # noqa: F401
    from . import yolo as _yolo  # noqa: F401
    from . import worktree as _worktree  # noqa: F401
    from . import auth as _auth  # noqa: F401
    from . import sessions as _sessions  # noqa: F401
    from . import resume as _resume  # noqa: F401
    from . import forget as _forget  # noqa: F401
    from . import panel as _panel  # noqa: F401
    from . import verbosity as _verbosity  # noqa: F401
    from . import copy as _copy  # noqa: F401
    from . import logo as _logo  # noqa: F401
    from . import route as _route  # noqa: F401
    from . import hooks as _hooks  # noqa: F401
    from . import scope as _scope  # noqa: F401

    _loaded = True

