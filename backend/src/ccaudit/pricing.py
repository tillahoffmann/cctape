"""Static Anthropic pricing table and cost computation.

The Anthropic API does not expose a pricing endpoint, so rates are hardcoded
here and must be updated manually when Anthropic publishes new prices. All
rates are in USD per million tokens.
"""

from __future__ import annotations

# USD per million tokens. Source: https://www.anthropic.com/pricing
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {
        "input": 15.0,
        "output": 75.0,
        "cache_write_5m": 18.75,
        "cache_write_1h": 30.0,
        "cache_read": 1.50,
    },
    "claude-sonnet-4-6": {
        "input": 3.0,
        "output": 15.0,
        "cache_write_5m": 3.75,
        "cache_write_1h": 6.0,
        "cache_read": 0.30,
    },
    "claude-haiku-4-5": {
        "input": 1.0,
        "output": 5.0,
        "cache_write_5m": 1.25,
        "cache_write_1h": 2.0,
        "cache_read": 0.10,
    },
}

# Dated model IDs that Anthropic returns in `message_start.message.model`
# alongside the canonical alias. Map them to the same rate table so cost
# computation doesn't care which form we saw.
PRICING["claude-haiku-4-5-20251001"] = PRICING["claude-haiku-4-5"]


def cost(
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cache_creation_5m_input_tokens: int | None,
    cache_creation_1h_input_tokens: int | None,
    cache_read_input_tokens: int | None,
) -> float | None:
    """Return USD cost for a single response, or None if the model is unknown."""
    if model is None:
        return None
    rates = PRICING.get(model)
    if rates is None:
        return None
    million = 1_000_000
    return (
        (input_tokens or 0) * rates["input"] / million
        + (output_tokens or 0) * rates["output"] / million
        + (cache_creation_5m_input_tokens or 0) * rates["cache_write_5m"] / million
        + (cache_creation_1h_input_tokens or 0) * rates["cache_write_1h"] / million
        + (cache_read_input_tokens or 0) * rates["cache_read"] / million
    )
