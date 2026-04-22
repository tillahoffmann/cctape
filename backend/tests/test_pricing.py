from cctape.pricing import PRICING, cost


def test_cost_known_model():
    # 1M input tokens at opus rate = $15
    c = cost("claude-opus-4-7", 1_000_000, 0, 0, 0, 0)
    assert c == 15.0


def test_cost_all_buckets():
    rates = PRICING["claude-sonnet-4-6"]
    c = cost("claude-sonnet-4-6", 1000, 2000, 3000, 5000, 4000)
    expected = (
        1000 * rates["input"]
        + 2000 * rates["output"]
        + 3000 * rates["cache_write_5m"]
        + 5000 * rates["cache_write_1h"]
        + 4000 * rates["cache_read"]
    ) / 1_000_000
    assert c == expected


def test_cost_1h_cache_priced_higher_than_5m():
    """Regression: 1h cache writes cost 2x the 5m rate. Earlier versions
    priced the combined total at 5m and silently underbilled 1h writers."""
    c_5m = cost("claude-opus-4-7", 0, 0, 1_000_000, 0, 0)
    c_1h = cost("claude-opus-4-7", 0, 0, 0, 1_000_000, 0)
    assert c_5m is not None and c_1h is not None
    assert c_5m == PRICING["claude-opus-4-7"]["cache_write_5m"]
    assert c_1h == PRICING["claude-opus-4-7"]["cache_write_1h"]
    assert c_1h > c_5m


def test_cost_unknown_model_returns_none():
    assert cost("claude-not-a-real-model", 100, 100, 0, 0, 0) is None


def test_cost_none_model_returns_none():
    assert cost(None, 100, 100, 0, 0, 0) is None


def test_cost_handles_none_token_counts():
    c = cost("claude-opus-4-7", None, None, None, None, None)
    assert c == 0.0
