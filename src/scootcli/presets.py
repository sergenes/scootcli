"""CLI preset shortcuts (PLAN §6). Optional sugar that seed the agent with a task string.

Each preset is ``fn(paths, message) -> str`` returning the seed prompt. Adding a preset = add an entry
to :data:`PRESETS`. These are pure convenience — every preset is expressible as a plain prompt.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional


def _explain(paths: List[str], message: Optional[str]) -> str:
    target = " ".join(paths) if paths else "the current directory"
    extra = f" Focus: {message}." if message else ""
    return (f"Explain {target}: summarize its purpose, key parts, and how to use it."
            f" Read files as needed; do not modify anything.{extra}")


def _edit(paths: List[str], message: Optional[str]) -> str:
    target = " ".join(paths) if paths else "the relevant file(s)"
    instruction = message or "make the requested change"
    return f"Edit {target}: {instruction}"


# name -> (seed builder, needs_message)
PRESETS: Dict[str, "Callable[[List[str], Optional[str]], str]"] = {
    "explain": _explain,
    "edit": _edit,
}


def is_preset(word: str) -> bool:
    return word in PRESETS


def build_prompt(name: str, rest: List[str], message: Optional[str]) -> str:
    return PRESETS[name](rest, message)

