"""Client-side model resolution for the ``auto`` alias.

Picks a concrete model id from the *live* model list (qualified ``provider/model`` ids) using a
lightweight, rule-based heuristic: a strong coding model for multi-step/edit tasks, a cheaper/faster
one for trivial Q&A. Tiers are substring-preference lists so they keep working as model names evolve.
This heuristic is the seed of the routing provider planned for 0.3.0.
"""

from __future__ import annotations

import re
from typing import List

# Preference-ordered substrings. First available match wins.
# NOTE: tokens are chosen to avoid accidental substring hits (e.g. "mini" ⊂ "geMINI").
STRONG_TIER = [
    "gpt-5.3-codex", "codex", "opus", "gpt-5.6", "gpt-5.5", "gpt-5.4", "gpt-5.3", "sonnet", "gpt-4.1",
    "gpt-4o", "qwen3", "llama3.3", "llama3.1", "llama3.2", "mistral",
]
CHEAP_TIER = [
    "-mini", "-nano", "flash", "haiku", "gpt-4o-mini", "llama3.2", "qwen3", "mistral",
]

# Markers of small/fast models — the STRONG tier must never pick one of these.
_SMALL_MARKERS = ("-mini", "flash", "haiku", "nano", "turbo")

# Words that suggest a multi-step / code-changing task (→ strong tier).
_COMPLEX_HINTS = (
    "refactor", "implement", "fix", "add ", "create", "edit", "rewrite", "debug",
    "test", "build", "migrate", "optimize", "generate", "write ", "change", "rename",
)


def looks_complex(text: str) -> bool:
    t = (text or "").lower()
    return len(t) > 240 or any(h in t for h in _COMPLEX_HINTS)


def _is_small(model_id: str) -> bool:
    return any(marker in model_id for marker in _SMALL_MARKERS)


_SNAPSHOT_RE = re.compile(r"-\d{4}-\d{2}-\d{2}$|-\d{4}$")


def is_snapshot(model_id: str) -> bool:
    """Dated snapshot ids (``gpt-4o-mini-2024-07-18``, ``gpt-4-0613``) lose to their plain alias."""
    return bool(_SNAPSHOT_RE.search(model_id or ""))


def _prefer_plain(available: List[str]) -> List[str]:
    """Drop snapshot ids when any plain id exists, so tiers pick ``gpt-5-mini`` over a dated variant."""
    plain = [m for m in available if not is_snapshot(m)]
    return plain or list(available)


def is_chat_model(descriptor: dict) -> bool:
    caps = descriptor.get("capabilities")
    ctype = caps.get("type") if isinstance(caps, dict) else None
    mid = descriptor.get("id", "")
    if "embedding" in mid:
        return False
    return ctype == "chat" or ctype is None


# ── Vision (M22) ───────────────────────────────────────────────────────────────
# Preference-ordered substrings for picking a vision model (seeded from the proven candidate list in
# First available match wins.
VISION_TIER = [
    "gpt-5.3-codex", "gpt-5", "gpt-4.1", "gpt-4o", "claude-sonnet", "claude-opus", "gemini",
    "qwen2.5vl", "qwen3-vl", "llama3.2-vision", "gemma3", "minicpm-v", "llava", "claude",
]

# Substring markers of models that are (very likely) vision-capable, used when the /models descriptor
# doesn't expose an explicit capability flag.
_VISION_MARKERS = ("gpt-4o", "gpt-4.1", "gpt-5", "claude", "gemini", "vision", "vl", "llava",
                   "gemma3", "minicpm-v")


def is_vision_model(descriptor: dict) -> bool:
    """True if a /models descriptor looks vision-capable (explicit flag, else a name heuristic)."""
    caps = descriptor.get("capabilities")
    if isinstance(caps, dict):
        supports = caps.get("supports")
        if isinstance(supports, dict) and supports.get("vision"):
            return True
    mid = (descriptor.get("id") or "").lower()
    return any(marker in mid for marker in _VISION_MARKERS)


def resolve_vision(available: List[str], fallback: str = "") -> str:
    """Choose a vision model id from live ids (VISION_TIER order); ``fallback`` when nothing matches."""
    ids = [m for m in available if m]
    if not ids:
        return fallback
    for pref in VISION_TIER:
        for mid in ids:
            if pref in mid:
                return mid
    return fallback if fallback in ids else ids[0]


def resolve_auto(task_text: str, available: List[str], fallback: str = "") -> str:
    """Choose a model id for an ``auto`` request from ``available``; ``fallback`` when the list is empty."""
    if not available:
        return fallback
    available = _prefer_plain(available)
    if looks_complex(task_text):
        for pref in STRONG_TIER:
            for model_id in available:
                if pref in model_id and not _is_small(model_id):
                    return model_id
    else:
        for pref in CHEAP_TIER:
            for model_id in available:
                if pref in model_id:
                    return model_id
    # No tier match: fall back to a present model.
    return fallback if fallback in available else available[0]

