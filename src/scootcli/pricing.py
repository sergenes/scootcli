"""Published list prices per million tokens, for the cost column in ``/status``.

This table goes stale; ``UPDATED`` says when it was last checked against the vendors' pricing pages.
Unknown models get no cost, never a guess. Local models cost nothing. Prices are USD.
"""

from __future__ import annotations

from typing import Optional, Tuple

from .providers.base import split_model_id

UPDATED = "2026-09-06"

# (model id prefix, input, cached input, output). Longest matching prefix wins.
# Anthropic: cache reads are 10% of input; cache writes (25% premium) are counted as plain input here.
PRICES = [
    # OpenAI (developers.openai.com/api/docs/pricing)
    ("gpt-5.6-astra", 10.00, 1.00, 50.00), ("gpt-5.6-sol", 4.00, 0.40, 20.00),
    ("gpt-5.6-terra", 2.00, 0.20, 12.00), ("gpt-5.6-luna", 0.20, 0.02, 1.20),
    ("gpt-5.5-pro", 30.00, 30.00, 180.00), ("gpt-5.5", 5.00, 0.50, 30.00),
    ("gpt-5.4-pro", 30.00, 30.00, 180.00), ("gpt-5.4-mini", 0.75, 0.075, 4.50),
    ("gpt-5.4-nano", 0.20, 0.02, 1.25), ("gpt-5.4", 2.50, 0.25, 15.00),
    ("gpt-5.3-codex", 1.75, 0.175, 14.00), ("gpt-5.2-pro", 21.00, 21.00, 168.00),
    ("gpt-5.2", 1.75, 0.175, 14.00), ("gpt-5.1", 1.25, 0.125, 10.00),
    ("gpt-5-pro", 15.00, 15.00, 120.00), ("gpt-5-mini", 0.25, 0.025, 2.00),
    ("gpt-5-nano", 0.05, 0.005, 0.40), ("gpt-5", 1.25, 0.125, 10.00),
    ("gpt-4.1-mini", 0.40, 0.10, 1.60), ("gpt-4.1", 2.00, 0.50, 8.00),
    ("gpt-4o-mini", 0.15, 0.075, 0.60), ("gpt-4o", 2.50, 1.25, 10.00),
    # Anthropic (first-party API rates)
    ("claude-fable-5", 10.00, 1.00, 50.00), ("claude-mythos-5", 10.00, 1.00, 50.00),
    ("claude-opus-5", 5.00, 0.50, 25.00), ("claude-opus-4-8", 5.00, 0.50, 25.00),
    ("claude-opus-4-7", 5.00, 0.50, 25.00), ("claude-opus-4-6", 5.00, 0.50, 25.00),
    ("claude-sonnet-5", 2.00, 0.20, 10.00), ("claude-sonnet-4-6", 3.00, 0.30, 15.00),
    ("claude-haiku-4-5", 1.00, 0.10, 5.00),
]
_FREE_PROVIDERS = ("ollama",)


def price_for(model: str) -> Optional[Tuple[float, float, float]]:
    """``(input, cached_input, output)`` per million tokens, or ``None`` when unknown."""
    provider, name = split_model_id(model)
    if provider in _FREE_PROVIDERS:
        return (0.0, 0.0, 0.0)
    name = (name or model or "").lower()
    best = None
    for prefix, inp, cached, out in PRICES:
        if name.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, inp, cached, out)
    return (best[1], best[2], best[3]) if best else None


def cost(model: str, prompt: int, completion: int, cached: int = 0) -> Optional[float]:
    """USD for one or more calls; ``cached`` is the part of ``prompt`` served from cache."""
    p = price_for(model)
    if p is None:
        return None
    inp, cached_price, out = p
    cached = max(0, min(int(cached or 0), int(prompt or 0)))
    fresh = int(prompt or 0) - cached
    return (fresh * inp + cached * cached_price + int(completion or 0) * out) / 1_000_000


def cached_tokens(usage: dict) -> int:
    """Cached-input tokens from a normalized usage dict, whichever vendor shape it came in."""
    if not isinstance(usage, dict):
        return 0
    n = usage.get("cache_read_input_tokens")
    if n is None:
        details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
        n = details.get("cached_tokens") if isinstance(details, dict) else 0
    return int(n or 0)


def fmt(usd: Optional[float]) -> str:
    if usd is None:
        return "?"
    if usd == 0:
        return "$0"
    return f"${usd:.4f}" if usd < 0.01 else f"${usd:.2f}"
