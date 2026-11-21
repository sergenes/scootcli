"""List prices: the models in use have one, newer versions are not billed as older ones."""

from __future__ import annotations

import pytest

from scootcli import pricing
from scootcli.config import Config
from scootcli.repl import ReplSession


@pytest.mark.parametrize("model, expected", [
    ("anthropic/claude-haiku-5-5", (0.10, 0.01, 0.50)),
    ("openai/chat-latest", (5.00, 0.50, 30.00)),
    ("anthropic/claude-fable-5-2", (8.00, 0.20, 40.00)),
    ("anthropic/claude-mythos-5-2", (8.00, 0.20, 40.00)),
    ("anthropic/claude-fable-5-1", (10.00, 0.25, 50.00)),
    ("anthropic/claude-fable-5", (10.00, 1.00, 50.00)),
    ("anthropic/claude-opus-5-5", (4.00, 0.20, 20.00)),
    ("anthropic/claude-opus-5", (5.00, 0.50, 25.00)),
    ("anthropic/claude-sonnet-5-5", (2.00, 0.10, 10.00)),
    ("anthropic/claude-sonnet-5", (2.00, 0.20, 10.00)),
    ("anthropic/claude-haiku-4-5", (1.00, 0.10, 5.00)),
])
def test_list_prices(model, expected):
    assert pricing.price_for(model) == expected


def test_unknown_model_still_has_no_price():
    assert pricing.price_for("openai/some-future-model") is None
    assert pricing.cost("openai/some-future-model", 1000, 1000) is None


def test_haiku_5_5_long_prompt_is_billed_at_the_higher_rate():
    short = pricing.call_cost("anthropic/claude-haiku-5-5", 100_000, 1_000_000)
    assert short == pytest.approx(0.01 + 0.50)
    long = pricing.call_cost("anthropic/claude-haiku-5-5", 200_000, 1_000_000, cached=100_000)
    assert long == pytest.approx(0.1 * 0.50 + 0.1 * 0.05 + 2.50)
    assert pricing.surcharge("anthropic/claude-haiku-5-5", 100_000, 1_000_000) == 0
    assert pricing.surcharge("anthropic/claude-opus-5-5", 900_000, 1000) == 0


def test_session_cost_counts_each_call_at_its_own_tier(tmp_path):
    s = ReplSession(Config().override(root=str(tmp_path)), None)
    model = "anthropic/claude-haiku-5-5"
    s.account({"prompt_tokens": 50_000, "completion_tokens": 1000}, model=model)
    s.account({"prompt_tokens": 150_000, "completion_tokens": 1000}, model=model)
    expected = (pricing.call_cost(model, 50_000, 1000) + pricing.call_cost(model, 150_000, 1000))
    assert s.session_cost() == pytest.approx(expected)
    assert pricing.usage_cost(model, s.usage_by_model[model]) == pytest.approx(expected)
    # a record saved before tiers existed has no surcharge key and still prices
    old = {"prompt": 10, "completion": 10, "cached": 0, "calls": 1}
    assert pricing.usage_cost(model, old) == pytest.approx(pricing.cost(model, 10, 10))
