"""ai_cost_tracking.py -- pricing lookup and cost arithmetic.

Pure logic, no API calls, no ledger. Wiring these numbers into
claude_governance_api.py's decision dicts and governance/ledger_postgres.py's
ledger rows is covered separately (test_governor_failclosed.py for the
former; the ledger-side hash/verify tests for the latter).
"""

from ai_cost_tracking import MODEL_PRICING, PRICING_SOURCE, cost_of_call


def test_a_known_model_computes_real_cost():
    result = cost_of_call("claude-opus-4-6", input_tokens=1_000_000,
                          output_tokens=1_000_000)
    input_rate, output_rate = MODEL_PRICING["claude-opus-4-6"]
    assert result.cost_usd == round(input_rate + output_rate, 6)
    assert result.unpriced_reason is None


def test_cost_scales_linearly_with_tokens():
    half = cost_of_call("claude-opus-4-6", input_tokens=500_000, output_tokens=0)
    full = cost_of_call("claude-opus-4-6", input_tokens=1_000_000, output_tokens=0)
    assert full.cost_usd == round(half.cost_usd * 2, 6)


def test_zero_tokens_costs_zero_for_a_priced_model():
    result = cost_of_call("claude-opus-4-6", input_tokens=0, output_tokens=0)
    assert result.cost_usd == 0.0
    assert result.unpriced_reason is None


def test_an_unpriced_model_returns_none_not_zero_not_a_guess():
    """The core invariant: an unlisted model is NEVER priced at $0 and
    NEVER priced by borrowing a similarly-named model's rate."""
    result = cost_of_call("claude-some-future-model-9", input_tokens=1000,
                          output_tokens=1000)
    assert result.cost_usd is None
    assert "claude-some-future-model-9" in result.unpriced_reason
    assert "no pricing data" in result.unpriced_reason


def test_unpriced_model_still_reports_the_real_token_counts():
    """Not knowing the PRICE doesn't mean not knowing what happened --
    the token counts are real and reported either way."""
    result = cost_of_call("claude-unknown", input_tokens=123, output_tokens=456)
    assert result.input_tokens == 123
    assert result.output_tokens == 456
    assert result.cost_usd is None


def test_as_dict_omits_pricing_source_when_unpriced():
    """pricing_source claims 'this number came from here' -- it must not
    appear at all when there IS no number."""
    priced = cost_of_call("claude-opus-4-6", 1000, 1000).as_dict()
    unpriced = cost_of_call("claude-unknown", 1000, 1000).as_dict()
    assert priced["pricing_source"] == PRICING_SOURCE
    assert unpriced["pricing_source"] is None


def test_as_dict_has_a_stable_key_set_whether_priced_or_not():
    """A caller that does .get('cost_usd') on every call outcome should
    never hit a KeyError depending on which branch fired."""
    priced = cost_of_call("claude-opus-4-6", 1000, 1000).as_dict()
    unpriced = cost_of_call("claude-unknown", 1000, 1000).as_dict()
    assert set(priced.keys()) == set(unpriced.keys())


def test_every_model_in_the_pricing_table_has_a_positive_output_rate_above_input():
    """Sanity check on the table itself, not the function -- output has
    always been priced higher than input for every real Claude model;
    a transposed pair would silently under-report actual spend."""
    for model, (input_rate, output_rate) in MODEL_PRICING.items():
        assert input_rate > 0, f"{model} has a non-positive input rate"
        assert output_rate > 0, f"{model} has a non-positive output rate"
        assert output_rate >= input_rate, (
            f"{model}: output rate ({output_rate}) should not be cheaper "
            f"than input ({input_rate}) -- likely a transposed entry")


def test_the_models_this_codebase_actually_uses_are_priced():
    """claude_governance_api.py's self.model is claude-opus-4-6 -- if this
    ever goes unpriced, every real safety_check call silently loses its
    cost number."""
    assert "claude-opus-4-6" in MODEL_PRICING
