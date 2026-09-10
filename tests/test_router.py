"""Routing for the auto alias: hints, rules, the built-in heuristic, the classifier, /route."""

from __future__ import annotations

import json

from scootcli import config as _config
from scootcli.config import Config
from scootcli.providers.router import Router, RouterConfig, compute_hints, describe, load_config


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(_config, "CREDENTIALS_FILE", tmp_path / "credentials.json")
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SCOOT_PROVIDER", "SCOOT_ROUTER"):
        monkeypatch.delenv(var, raising=False)


class _Session:
    def __init__(self, messages):
        self.messages = messages
        self.model = "auto"


_AVAILABLE = ["openai/gpt-5.3-codex", "openai/gpt-5-mini", "ollama/llama3.2", "anthropic/claude-opus-5"]


def test_compute_hints_reads_the_last_user_turn():
    s = _Session([{"role": "user", "content": "what is a closure?"},
                  {"role": "assistant", "content": "..."},
                  {"role": "user", "content": "refactor the parser module"}])
    h = compute_hints(s)
    assert h["task_text"] == "refactor the parser module" and h["complex"] is True
    assert h["has_images"] is False and h["turns"] == 2 and h["est_tokens"] > 0
    img = _Session([{"role": "user", "content": "look at this\n\nThe user attached 1 image(s); [Image 1 — a.png]\nred"}])
    assert compute_hints(img)["has_images"] is True
    parts = _Session([{"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "image_url", "image_url": {"url": "data:x"}}]}])
    assert compute_hints(parts)["has_images"] is True and compute_hints(parts)["task_text"] == "hi"


def test_builtin_heuristic_without_a_rules_file(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    r = Router()
    assert r.config.source == "built-in"
    s = _Session([{"role": "user", "content": "refactor the parser module"}])
    d = r.choose(s, compute_hints(s), _AVAILABLE, fallback="ollama/llama3.2")
    assert d.model == "openai/gpt-5.3-codex" and "strong tier" in d.reason and s.route_reason == d.reason
    s2 = _Session([{"role": "user", "content": "what is a closure?"}])
    d2 = r.choose(s2, compute_hints(s2), _AVAILABLE, fallback="ollama/llama3.2")
    assert d2.model == "openai/gpt-5-mini" and "cheap tier" in d2.reason
    assert r.choose(s2, compute_hints(s2), [], fallback="ollama/llama3.2").model == "ollama/llama3.2"


def test_rules_first_match_wins_and_unconfigured_targets_are_skipped(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-000000000000")  # anthropic has no key here
    cfg = RouterConfig(rules=[
        {"when": {"has_images": True}, "use": "anthropic/claude-opus-5"},
        {"when": {"has_images": True}, "use": "openai/gpt-5.3-codex"},
        {"when": {"prompt_matches": "(?i)translate"}, "use": "ollama/llama3.2"},
        {"when": {"est_tokens_over": 100}, "use": "openai/gpt-5-mini"},
        {"when": {"bogus_condition": True}, "use": "openai/gpt-5-mini"},
    ], default="ollama/llama3.2", source="test")
    r = Router(cfg)
    img = _Session([{"role": "user", "content": [{"type": "text", "text": "x"}, {"type": "image_url", "image_url": {"url": "d"}}]}])
    d = r.choose(img, compute_hints(img), _AVAILABLE, "ollama/llama3.2")
    assert d.model == "openai/gpt-5.3-codex"  # anthropic rule skipped: no key
    tr = _Session([{"role": "user", "content": "Translate this to French: bonjour"}])
    assert r.choose(tr, compute_hints(tr), _AVAILABLE, "x").model == "ollama/llama3.2"
    long = _Session([{"role": "user", "content": "word " * 300}])
    assert r.choose(long, compute_hints(long), _AVAILABLE, "x").model == "openai/gpt-5-mini"
    plain = _Session([{"role": "user", "content": "hi"}])
    d = r.choose(plain, compute_hints(plain), _AVAILABLE, "x")
    assert d.model == "ollama/llama3.2" and d.reason == "router default"  # the bogus rule never matches


def test_classifier_maps_tiers_and_falls_back_on_failure(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-000000000000")
    cfg = RouterConfig(classifier={"model": "ollama/llama3.2", "tiers": {
        "simple": "ollama/llama3.2", "coding": "openai/gpt-5.3-codex", "hard": "anthropic/claude-opus-5"}},
        default="openai/gpt-5-mini", source="test")
    r = Router(cfg)
    s = _Session([{"role": "user", "content": "add a flag"}])
    asked = []

    def classify(model, prompt):
        asked.append((model, prompt))
        return "Coding.\n"

    d = r.choose(s, compute_hints(s), _AVAILABLE, "x", classify=classify)
    assert d.model == "openai/gpt-5.3-codex" and "classifier" in d.reason
    assert asked[0][0] == "ollama/llama3.2" and "add a flag" in asked[0][1]
    # 'hard' maps to a provider without a key → rules/default decide instead.
    assert r.choose(s, compute_hints(s), _AVAILABLE, "x", classify=lambda m, p: "hard").model == "openai/gpt-5-mini"
    # A crashing or nonsense classifier never breaks the turn.
    assert r.choose(s, compute_hints(s), _AVAILABLE, "x", classify=lambda m, p: (_ for _ in ()).throw(RuntimeError())).model == "openai/gpt-5-mini"
    assert r.choose(s, compute_hints(s), _AVAILABLE, "x", classify=lambda m, p: "banana").model == "openai/gpt-5-mini"


def test_load_config_from_file_and_invalid_file(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    path = tmp_path / "router.json"
    monkeypatch.setenv("SCOOT_ROUTER", str(path))
    assert load_config().source == "built-in"  # missing file → built-in
    path.write_text(json.dumps({"rules": [{"when": {"complex": True}, "use": "openai/gpt-5.3-codex"}], "default": "ollama/llama3.2"}))
    cfg = load_config()
    assert cfg.source == str(path) and cfg.rules[0]["use"] == "openai/gpt-5.3-codex" and cfg.error == ""
    path.write_text('{"rules": "nope"}')
    cfg = load_config()
    assert cfg.error and cfg.rules == []  # invalid → built-in behaviour, error kept for /route
    lines = describe(Router(cfg))
    assert any("invalid" in l for l in lines)


def test_agent_routes_once_per_turn_only_for_auto(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-000000000000")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    from scootcli.agent import Agent
    from scootcli.providers import ChatResult

    calls = []

    class _Provider:
        def chat(self, messages, model=None, **kw):
            calls.append(model)
            return ChatResult(content="done", model=model)

    class _S:
        def __init__(self, model):
            self.messages = [{"role": "user", "content": "refactor everything in the repo"}]
            self.model = model
            self.active_model = ""
            self.bad_models = set()
            self.config = Config().override(root=str(tmp_path), stream=False, workspace_context=False)

        def resolved_model(self):
            return "openai/gpt-5.3-codex" if self.model == "auto" else self.model

        def available_models(self):
            return ["openai/gpt-5.3-codex", "openai/gpt-5-mini"]

        def account(self, usage, model=""):
            pass

    from scootcli.agent import HeadlessUI

    s = _S("auto")
    Agent(s.config, _Provider()).run_turn(s, HeadlessUI())
    assert s.active_model == "openai/gpt-5.3-codex" and getattr(s, "router", None) is not None
    assert "strong tier" in s.route_reason
    s2 = _S("openai/gpt-5-mini")
    Agent(s2.config, _Provider()).run_turn(s2, HeadlessUI())
    assert s2.active_model == "openai/gpt-5-mini" and not getattr(s2, "route_reason", "")


# ── 0.11.0: the router never returns a model already known unavailable (review R16) ─
def test_router_skips_excluded_models(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-000000000000")
    rc = RouterConfig(rules=[{"when": {}, "use": "openai/gpt-5.3-codex"}], default="openai/gpt-5-mini")
    r = Router(rc)
    s = _Session([{"role": "user", "content": "hi"}])
    hints = compute_hints(s)
    # No exclusion: the rule wins.
    assert r.choose(s, hints, _AVAILABLE, "openai/gpt-5-mini").model == "openai/gpt-5.3-codex"
    # Excluded: neither the rule's target nor the default (also excluded) may be returned.
    d = r.choose(s, hints, _AVAILABLE, "openai/gpt-5-mini",
                 excluded={"openai/gpt-5.3-codex", "openai/gpt-5-mini"})
    assert d.model not in ("openai/gpt-5.3-codex", "openai/gpt-5-mini")
