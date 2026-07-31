"""AI cost tracking, end to end through the live harness (2026-07-31).

Reuses test_cassette_governs_every_decision.py's real-Postgres harness
convention (TunedCassette/_call/PG_CONFIG/requires_pg) -- this file adds
one thing on top: a decider stub that returns a `cost` key, proving the
whole chain (claude_governance_api's shape -> production_harness's
GovernanceDecisionRecord -> the ledger's ai_cost column -> get_decisions)
actually connects, not just each piece in isolation.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

from production_harness import IcebergProductionHarness

from test_cassette_governs_every_decision import (
    PG_CONFIG, TunedCassette, _call, requires_pg,
)


class _CostReportingDecider:
    """Same contract as ClaudeGovernanceDecider.safety_check, with a
    realistic `cost` key attached -- exactly the shape
    claude_governance_api.py actually returns now."""

    def safety_check(self, action, details):
        return {
            "safe": True, "risk_level": "low",
            "reasoning": "stub: within declared bounds",
            "recommendations": [], "confidence": 0.99,
            "model_identity": "claude-opus-4-6",
            "cost": {"model": "claude-opus-4-6", "input_tokens": 512,
                     "output_tokens": 128, "cost_usd": 0.00576,
                     "unpriced_reason": None,
                     "pricing_source": "https://platform.claude.com/docs/en/about-claude/pricing"},
        }


@requires_pg
def test_a_real_governed_call_carries_its_ai_cost_into_the_ledger():
    from cassette_schema import validate_cassette
    cassette = TunedCassette(trigger=2, tag="cost-e2e")
    version = validate_cassette(cassette).cassette_version
    harness = IcebergProductionHarness(PG_CONFIG, cassette=cassette)
    harness.claude_decider = _CostReportingDecider()

    result = harness.process_call(_call(250))  # friction 2, governed
    assert result["governed"] is True

    rows = harness.ledger.get_decisions(cassette_version=version)
    assert len(rows) == 1
    assert rows[0]["ai_cost"] is not None
    assert rows[0]["ai_cost"]["cost_usd"] == 0.00576
    assert rows[0]["ai_cost"]["input_tokens"] == 512
    assert harness.ledger.verify_chain()["ok"]
    harness.shutdown()


@requires_pg
def test_a_governed_call_with_a_decider_that_reports_no_cost_stores_none():
    """A decider stub (or an old-style caller) that doesn't include a
    `cost` key at all must not crash the ledger write -- .get('cost')
    degrades to None cleanly, matching the no-usage-data case."""
    from cassette_schema import validate_cassette
    from test_cassette_governs_every_decision import StubDecider
    cassette = TunedCassette(trigger=2, tag="cost-e2e-none")
    version = validate_cassette(cassette).cassette_version
    harness = IcebergProductionHarness(PG_CONFIG, cassette=cassette)
    harness.claude_decider = StubDecider()  # no "cost" key in its return

    harness.process_call(_call(250))
    rows = harness.ledger.get_decisions(cassette_version=version)
    assert rows[0]["ai_cost"] is None
    assert harness.ledger.verify_chain()["ok"]
    harness.shutdown()
