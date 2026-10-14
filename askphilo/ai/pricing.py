"""Anthropic API prices, used to turn recorded token usage into money.

The API has no endpoint for the remaining credit balance, and the account that
issued the key may not be reachable, so AskPhilo prices every call itself:
tokens from the API response × the official price of the model that answered.

Prices are USD per 1M tokens, from https://platform.claude.com/docs/en/about-claude/pricing
(checked 2026-10-14). Cache writes use the 5-minute TTL price because requests
send a plain {"type": "ephemeral"} cache_control. Thinking tokens are part of
output_tokens. If prices change, update this table (or set the
ANTHROPIC_PRICE_*_PER_MTOK overrides, which apply to every model).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float
    cache_write: float
    cache_read: float
    output: float


PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(input=4.0, cache_write=5.0, cache_read=0.20, output=20.0),
    "claude-sonnet-5-5": Price(input=2.0, cache_write=2.5, cache_read=0.10, output=10.0),
    "claude-haiku-5-5": Price(input=0.10, cache_write=0.125, cache_read=0.01, output=0.50),
    "claude-fable-5-1": Price(input=10.0, cache_write=12.5, cache_read=0.25, output=50.0),
}
# Haiku 5.5 is priced by prompt length: prompts over 100,000 tokens pay more.
HAIKU_LONG_PROMPT = Price(input=0.50, cache_write=0.625, cache_read=0.05, output=2.50)
HAIKU_LONG_PROMPT_TOKENS = 100_000

OVERRIDE_KEYS = {
    "input": "ANTHROPIC_PRICE_INPUT_PER_MTOK",
    "cache_write": "ANTHROPIC_PRICE_CACHE_WRITE_PER_MTOK",
    "cache_read": "ANTHROPIC_PRICE_CACHE_READ_PER_MTOK",
    "output": "ANTHROPIC_PRICE_OUTPUT_PER_MTOK",
}


def overrides_from_config(cfg) -> dict[str, float]:
    """Configured price overrides (only the ones that are set)."""
    return {field: cfg.get(key) for field, key in OVERRIDE_KEYS.items() if cfg.get(key)}


def price_for(model: str | None, prompt_tokens: int = 0,
              overrides: dict[str, float] | None = None) -> Price | None:
    """Price of a model id (dated snapshots such as claude-sonnet-5-5-2026xxxx match too)."""
    base = None
    for key in sorted(PRICES, key=len, reverse=True):
        if model and (model == key or model.startswith(key + "-")):
            base = PRICES[key]
            if key == "claude-haiku-5-5" and prompt_tokens > HAIKU_LONG_PROMPT_TOKENS:
                base = HAIKU_LONG_PROMPT
            break
    if overrides:
        fields = {**(base.__dict__ if base else {}), **overrides}
        if len(fields) == 4:
            return Price(**fields)
    return base


def cost_micro_usd(model: str | None, usage, overrides: dict[str, float] | None = None) -> int | None:
    """Cost of one call in millionths of a dollar (integers add up exactly), or None if unpriced."""
    if usage is None:
        return None
    tokens_in = usage.input_tokens or 0
    cache_read = usage.cache_read_input_tokens or 0
    cache_write = usage.cache_creation_input_tokens or 0
    price = price_for(model, tokens_in + cache_read + cache_write, overrides)
    if price is None:
        return None
    # tokens × $/1M tokens = millionths of a dollar
    cost = (tokens_in * price.input + cache_write * price.cache_write
            + cache_read * price.cache_read + (usage.output_tokens or 0) * price.output)
    return round(cost)
