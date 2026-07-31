"""AI cost capture wired into ClaudeGovernanceDecider (2026-07-31).

Reuses test_governor_failclosed.py's stub-client convention (no network) --
these tests extend it with a `usage` attribute on the fake response, since
that file predates cost tracking and its own stubs don't set one.
"""

import json

from claude_governance_api import ClaudeGovernanceDecider


class _FakeUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(self, text, model=None, usage=None):
        self.content = [_FakeBlock(text)] if text is not None else []
        if model is not None:
            self.model = model
        if usage is not None:
            self.usage = usage


def _decider_with(create_fn):
    d = ClaudeGovernanceDecider(api_key="sk-fake-not-used")
    d.client.messages.create = create_fn
    return d


_GOOD_SAFETY_JSON = json.dumps({"safe": True, "risk_level": "low",
                                "reasoning": "fine", "recommendations": [],
                                "confidence": 0.9})


def test_successful_call_captures_real_cost():
    def _create(*a, **kw):
        return _FakeMessage(_GOOD_SAFETY_JSON, model="claude-opus-4-6",
                            usage=_FakeUsage(100, 50))
    d = _decider_with(_create)
    decision = d.safety_check("heal_queue", {"queue": "billing_queue"})
    assert decision["cost"] is not None
    assert decision["cost"]["input_tokens"] == 100
    assert decision["cost"]["output_tokens"] == 50
    assert decision["cost"]["cost_usd"] is not None
    assert decision["cost"]["cost_usd"] > 0


def test_a_model_not_in_the_pricing_table_reports_unknown_not_zero():
    def _create(*a, **kw):
        return _FakeMessage(_GOOD_SAFETY_JSON, model="claude-hypothetical-99",
                            usage=_FakeUsage(100, 50))
    d = _decider_with(_create)
    decision = d.safety_check("heal_queue", {"queue": "billing_queue"})
    assert decision["cost"]["cost_usd"] is None
    assert "claude-hypothetical-99" in decision["cost"]["unpriced_reason"]
    # Token counts are still real even though the price isn't known.
    assert decision["cost"]["input_tokens"] == 100


def test_a_parse_failure_still_captured_cost_the_call_still_happened():
    """Bad JSON back from Claude doesn't mean the call was free -- the
    tokens were already spent by the time parsing fails."""
    def _create(*a, **kw):
        return _FakeMessage("not valid json", model="claude-opus-4-6",
                            usage=_FakeUsage(80, 20))
    d = _decider_with(_create)
    decision = d.safety_check("heal_queue", {"queue": "billing_queue"})
    assert decision["parse_failed"] is True
    assert decision["cost"] is not None
    assert decision["cost"]["input_tokens"] == 80


def test_no_client_configured_means_no_cost_no_call_ever_made():
    d = ClaudeGovernanceDecider(api_key=None)
    decision = d.safety_check("heal_queue", {"queue": "billing_queue"})
    assert decision["cost"] is None


def test_a_genuine_transport_error_before_any_message_is_obtained_does_not_crash():
    """The regression this session's own bug-fix covers: usage/model_identity
    must be bound before the try block, or a transport error raised by
    messages.create itself hits NameError in the except clause instead of
    degrading to cost=None."""
    def _create(*a, **kw):
        raise ConnectionError("network unreachable")
    d = _decider_with(_create)
    decision = d.safety_check("heal_queue", {"queue": "billing_queue"})
    assert decision["parse_failed"] is True
    assert decision["cost"] is None


def test_a_stub_response_with_no_usage_attribute_at_all_degrades_cleanly():
    """Older/simpler test stubs elsewhere in the suite return a fake message
    with no `usage` attribute -- must not crash, must report cost=None."""
    def _create(*a, **kw):
        return _FakeMessage(_GOOD_SAFETY_JSON, model="claude-opus-4-6")
        # no usage= passed
    d = _decider_with(_create)
    decision = d.safety_check("heal_queue", {"queue": "billing_queue"})
    assert decision["cost"] is None


def test_cost_wired_into_decide_healing_bounds_too():
    def _create(*a, **kw):
        return _FakeMessage(
            json.dumps({"should_heal": True, "reasoning": "ok",
                       "lo_bound": 1, "hi_bound": 2, "target_wait": 30,
                       "confidence": 0.8}),
            model="claude-opus-4-6", usage=_FakeUsage(60, 40))
    d = _decider_with(_create)
    decision = d.decide_healing_bounds("billing_queue", 90.0, 40.0, 1.5)
    assert decision["cost"]["input_tokens"] == 60


def test_cost_wired_into_decide_staffing_adjustment_too():
    def _create(*a, **kw):
        return _FakeMessage(
            json.dumps({"recommended_agents": 5, "reasoning": "ok",
                       "expected_wait": 30, "confidence": 0.8}),
            model="claude-opus-4-6", usage=_FakeUsage(60, 40))
    d = _decider_with(_create)
    decision = d.decide_staffing_adjustment("billing_queue", 4, 90.0, 30.0, 0.1)
    assert decision["cost"]["input_tokens"] == 60


def test_cost_wired_into_decide_queue_reordering_too():
    def _create(*a, **kw):
        return _FakeMessage(
            json.dumps({"proposed_order": ["billing_queue"], "reasoning": "ok",
                       "expected_impact": 0.1, "confidence": 0.8}),
            model="claude-opus-4-6", usage=_FakeUsage(60, 40))
    d = _decider_with(_create)
    decision = d.decide_queue_reordering(["billing_queue"], {}, {})
    assert decision["cost"]["input_tokens"] == 60
