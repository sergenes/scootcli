"""Tool registry. Drop a new module in this package and call :func:`register` to add a tool.

The registry is the single source of truth: it feeds both the runtime ``tools`` schema sent to the
API and the ``{{tool_list}}`` rendered into the system prompt (PLAN §2, §8).
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .base import Tool, ToolContext, ToolError, ToolResult

__all__ = [
    "Tool",
    "ToolContext",
    "ToolResult",
    "ToolError",
    "register",
    "get",
    "all_tools",
    "schemas",
    "tool_list_text",
    "load_builtins",
]

_REGISTRY: Dict[str, Tool] = {}


def register(tool: Tool) -> None:
    _REGISTRY[tool.name] = tool


def get(name: str) -> Optional[Tool]:
    return _REGISTRY.get(name)


def all_tools() -> Dict[str, Tool]:
    return dict(sorted(_REGISTRY.items()))


def schemas() -> List[dict]:
    """Return the OpenAI-style tool schemas for the request ``tools`` param."""
    return [t.schema() for t in all_tools().values()]


def tool_list_text() -> str:
    """One-line-per-tool summary for the system prompt ``{{tool_list}}``."""
    return "\n".join(f"- {t.name}: {t.description}" for t in all_tools().values())


_loaded = False


def load_builtins() -> None:
    """Import the built-in tool modules so they self-register (idempotent)."""
    global _loaded
    if _loaded:
        return
    from . import read_file  # noqa: F401
    from . import list_dir  # noqa: F401
    from . import search  # noqa: F401
    from . import write_file  # noqa: F401
    from . import edit_file  # noqa: F401
    from . import run_shell  # noqa: F401
    from . import update_plan  # noqa: F401

    _loaded = True

