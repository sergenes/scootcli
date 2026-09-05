"""Conversation context management: token estimation + /compact (summarize → clear → reseed).

When the context grows large we ask the model to summarize the conversation, drop the old turns, and
keep the summary as the new base — freeing tokens without losing the thread (PLAN §6 /compact).
"""

from __future__ import annotations

import threading
from typing import Optional

_COMPACT_SYSTEM = (
    "You compress a coding assistant conversation into a compact hand-off note. "
    "Capture: the user's goal, key decisions, files created/changed, commands run, current plan, and "
    "any open TODOs. Be terse and factual. Output only the note."
)


def estimate_context_tokens(session) -> int:
    """Best-effort current context size from the last API ``usage.prompt_tokens``.

    Falls back to a rough char/4 estimate over the live messages when there has been no API call yet
    (e.g. immediately after resuming a saved session, before the first turn of the new process).
    """
    usage = session.last_usage or {}
    tokens = int(usage.get("prompt_tokens", 0) or 0)
    if tokens:
        return tokens
    return estimate_messages_tokens(getattr(session, "messages", None))


def estimate_messages_tokens(messages) -> int:
    """Rough token estimate (~4 chars/token) over a message list; used before any usage is known."""
    if not messages:
        return 0
    chars = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            chars += len(content)
    return chars // 4


def _render_conversation(messages) -> str:
    parts = []
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content", "")
        if not content and m.get("tool_calls"):
            calls = ", ".join(c.get("function", {}).get("name", "?") for c in m["tool_calls"])
            content = f"[called tools: {calls}]"
        parts.append(f"{role}: {content}")
    return "\n".join(parts)


def compact(session, cancel_event: Optional[threading.Event] = None) -> str:
    """Summarize and replace the session's messages. Returns the summary text (or "" if nothing)."""
    if not session.messages:
        return ""
    conversation = _render_conversation(session.messages)
    result = session.provider.chat(
        [
            {"role": "system", "content": _COMPACT_SYSTEM},
            {"role": "user", "content": conversation},
        ],
        model=session.active_model,
        max_tokens=900,
        cancel_event=cancel_event,
    )
    summary = (result.content or "").strip()
    session.account(result.usage)
    if not summary:
        return ""
    session.messages = [
        {"role": "user", "content": "Summary of earlier conversation (context was compacted):\n" + summary},
        {"role": "assistant", "content": "Understood — continuing with that context."},
    ]
    # Drop the stale usage from the summarization call so the context estimate (bar ``ctx``,
    # auto-compact check) reflects the new, small message set until the next real turn.
    session.last_usage = {}
    return summary

