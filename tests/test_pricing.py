"""Per-call API cost from official prices (the API has no balance endpoint)."""

from askphilo.ai.base import AIUsage
from askphilo.ai.pricing import cost_micro_usd, price_for


def usage(i=0, o=0, read=0, write=0):
    return AIUsage(input_tokens=i, output_tokens=o, cache_read_input_tokens=read,
                   cache_creation_input_tokens=write)


def test_sonnet_reply_cost():
    # 1000 × $2 + 80 × $10 + 600 × $0.10 per 1M tokens = $0.00286
    assert cost_micro_usd("claude-sonnet-5-5", usage(1000, 80, 600)) == 2860


def test_cache_write_uses_five_minute_price():
    # Opus 5.5: input $4, 5m cache write $5, output $20
    assert cost_micro_usd("claude-opus-5-5", usage(2, 232, 0, 3094)) == 8 + 4640 + 15470


def test_dated_model_ids_match():
    assert price_for("claude-sonnet-5-5-20261001") == price_for("claude-sonnet-5-5")
    assert price_for("claude-sonnet-5") is None  # a different model, not a prefix match


def test_haiku_long_prompts_pay_more():
    short = cost_micro_usd("claude-haiku-5-5", usage(90_000, 100))
    long = cost_micro_usd("claude-haiku-5-5", usage(120_000, 100))
    assert short == round(90_000 * 0.10 + 100 * 0.50)
    assert long == round(120_000 * 0.50 + 100 * 2.50)


def test_unknown_models_are_unpriced_unless_overridden():
    assert cost_micro_usd("test-model", usage(1000, 80)) is None
    full = {"input": 1.0, "cache_write": 1.25, "cache_read": 0.1, "output": 5.0}
    assert cost_micro_usd("test-model", usage(1000, 80), full) == 1000 + 400
    # a partial override replaces only that price of a known model
    assert cost_micro_usd("claude-sonnet-5-5", usage(0, 100), {"output": 20.0}) == 2000


def test_no_usage_no_cost():
    assert cost_micro_usd("claude-sonnet-5-5", None) is None


def test_live_eval_run_matches_hand_calculation():
    # 2026-10-14 live run: 16 Sonnet calls → about $0.072
    cost = cost_micro_usd("claude-sonnet-5-5", usage(36, 3177, 34908, 14838))
    assert round(cost / 1_000_000, 4) == 0.0724
