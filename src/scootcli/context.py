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


_KEEP_TAIL = 1                    # keep at least the last user turn verbatim when compacting proactively
_SUMMARY_INPUT_CAP = 100_000     # chars of older conversation sent for summarization (keep it bounded)


def maybe_compact(session, cfg, cancel_event: Optional[threading.Event] = None) -> bool:
    """Before a model request, compact when the context estimate exceeds ``cfg.compact_at``.

    Unlike ``/compact`` (which collapses the whole conversation), this keeps the most recent user turn
    verbatim and summarizes only what came before it, and it bounds the text sent for summarization so
    the summarization call itself cannot overflow the context. Best-effort: on any error nothing
    changes and the turn proceeds. Returns True when it compacted.
    """
    threshold = int(getattr(cfg, "compact_at", 100_000) or 100_000)
    msgs = getattr(session, "messages", None) or []
    if not msgs or estimate_context_tokens(session) <= threshold:
        return False
    # Split at the last user message so the kept tail begins cleanly (never orphaning a tool result).
    tail_start = next((i for i in range(len(msgs) - 1, -1, -1) if msgs[i].get("role") == "user"), 0)
    older, recent = msgs[:tail_start], msgs[tail_start:]
    if not older:
        return False  # only one turn present; nothing older to summarize
    conversation = _render_conversation(older)[:_SUMMARY_INPUT_CAP]
    try:
        result = session.provider.chat(
            [{"role": "system", "content": _COMPACT_SYSTEM}, {"role": "user", "content": conversation}],
            model=session.active_model, max_tokens=900, cancel_event=cancel_event,
        )
    except Exception:
        return False
    try:
        session.account(result.usage, model=session.active_model)
    except Exception:
        pass
    summary = (result.content or "").strip()
    if not summary:
        return False
    session.messages = [
        {"role": "user", "content": "Summary of earlier conversation (context was compacted):\n" + summary},
        *recent,
    ]
    return True


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

