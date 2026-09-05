"""Tests for the client-side 'auto' model resolver and chat-model filtering.

Run: PYTHONPATH=src python3 tests/test_models.py
"""

from __future__ import annotations

from scootcli.models import is_chat_model, looks_complex, resolve_auto

_AVAILABLE = [
    "claude-opus-4.8", "gpt-5.3-codex", "gpt-5.4-mini", "gemini-3.5-flash", "gpt-4o", "gpt-4o-mini",
]


def test_complex_detection():
    assert looks_complex("refactor the auth module")
    assert looks_complex("add a --verbose flag to cli.py")
    assert not looks_complex("what is a closure?")


def test_auto_picks_strong_for_complex():
    m = resolve_auto("refactor the transport layer to use httpx", _AVAILABLE)
    # 'codex' is first in the strong tier and present.
    assert m == "gpt-5.3-codex"


def test_auto_picks_cheap_for_trivial():
    m = resolve_auto("what does this function return?", _AVAILABLE)
    # 'mini' is first in the cheap tier and present.
    assert "mini" in m


def test_trivial_never_picks_gemini_via_mini_substring():
    # Regression: "gemini" contains the substring "mini"; the cheap tier must not match it.
    available = ["gemini-3.1-pro-preview", "gemini-3.5-flash", "gpt-5.4-mini", "gpt-4o"]
    m = resolve_auto("what is the capital of France?", available)
    assert m == "gpt-5.4-mini"  # precise "-mini" match, not gemini


def test_strong_never_picks_small_model():
    available = ["gpt-5.4-mini", "gpt-5.4", "gpt-4o"]
    m = resolve_auto("refactor the whole module", available)
    assert m == "gpt-5.4"  # skips the -mini even though it also contains 'gpt-5.4'


def test_auto_fallback_when_empty():
    assert resolve_auto("anything", [], fallback="openai/gpt-5.3-codex") == "openai/gpt-5.3-codex"
    assert resolve_auto("anything", []) == ""


def test_is_chat_model_filters_embeddings():
    assert is_chat_model({"id": "gpt-4o", "capabilities": {"type": "chat"}})
    assert not is_chat_model({"id": "text-embedding-3-small", "capabilities": {"type": "embeddings"}})
    assert not is_chat_model({"id": "some-embedding-model"})


# ── M22: vision model resolution ────────────────────────────────────────────────


def test_is_vision_model_explicit_flag_and_heuristic():
    from scootcli.models import is_vision_model

    assert is_vision_model({"id": "x", "capabilities": {"supports": {"vision": True}}})
    assert is_vision_model({"id": "gpt-4o"})          # name heuristic
    assert is_vision_model({"id": "claude-sonnet-4.6"})
    assert not is_vision_model({"id": "text-embedding-3-small"})
    assert not is_vision_model({"id": "o1-preview"})


def test_resolve_vision_prefers_tier_order():
    from scootcli.models import resolve_vision

    available = ["openai/gpt-4o", "openai/gpt-5.3-codex", "ollama/qwen2.5vl:7b"]
    # gpt-5.3-codex heads VISION_TIER; local vision models come after the hosted ones.
    assert resolve_vision(available) == "openai/gpt-5.3-codex"
    assert resolve_vision(["ollama/llama3.2", "ollama/qwen2.5vl:7b"]) == "ollama/qwen2.5vl:7b"


def test_resolve_vision_falls_back_to_present_model():
    from scootcli.models import resolve_vision

    assert resolve_vision([], fallback="openai/gpt-4o") == "openai/gpt-4o"
    assert resolve_vision([]) == ""
    assert resolve_vision(["some-unknown-model"]) == "some-unknown-model"


if __name__ == "__main__":
    import types

    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            fn()
            print(f"ok  {name}")
            passed += 1
    print(f"\n{passed} passed")



def test_auto_skips_dated_snapshots_when_a_plain_id_exists():
    from scootcli.models import is_snapshot, resolve_auto

    assert is_snapshot("gpt-4o-mini-2024-07-18") and is_snapshot("gpt-4-0613")
    assert not is_snapshot("gpt-5.3-codex") and not is_snapshot("openai/gpt-5-mini")
    available = ["openai/gpt-4o-mini-2024-07-18", "openai/gpt-5-mini", "openai/gpt-5.3-codex"]
    assert resolve_auto("what is a closure?", available) == "openai/gpt-5-mini"
    # Only snapshots present → still pick one rather than nothing.
    assert resolve_auto("what is a closure?", ["openai/gpt-4o-mini-2024-07-18"]) == "openai/gpt-4o-mini-2024-07-18"
