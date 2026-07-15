"""
Fail-closed governor tests.

These prove the safety-critical property of the governor rebuild WITHOUT a
live API key: an unintelligible, unparseable, or malformed governor response
must be treated as a REFUSAL, never an approval, and the harness must never
run ungoverned-but-report-success when a governed decision was required.

The anthropic client is stubbed, so nothing here touches the network.
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from claude_governance_api import ClaudeGovernanceDecider
from production_harness import IcebergProductionHarness
from twilio_log_ingestion import IcebergJourney


# ---- stubs: a fake Messages API response, no network -------------------------

class _FakeBlock:
    def __init__(self, text=None, block_type="text"):
        self.type = block_type
        if text is not None:
            self.text = text


class _FakeMessage:
    def __init__(self, blocks):
        self.content = blocks


def _returns(text):
    """Client stub that returns one text block containing `text`."""
    def _create(*args, **kwargs):
        return _FakeMessage([_FakeBlock(text=text)])
    return _create


def _returns_empty():
    """Client stub that returns a response with no content blocks."""
    def _create(*args, **kwargs):
        return _FakeMessage([])
    return _create


def _raises(exc):
    """Client stub that raises a transport error."""
    def _create(*args, **kwargs):
        raise exc
    return _create


def _decider_with(create_fn):
    d = ClaudeGovernanceDecider(api_key="sk-fake-not-used")
    d.client.messages.create = create_fn
    return d


def _high_friction_journey():
    # wait_times keys must match this journey's own node names --
    # "billing_queue"/"agent_a", not the generic "queue"/"agent" this
    # fixture used before. With the mismatched keys, per-node lookup
    # found nothing for either real node and measured friction was 0;
    # these tests only governed because production_harness.py used to
    # take max(measured, journey.friction_count) and fall back to the
    # friction_count field below. That max() is gone (see R-6) -- it
    # produced a ledger row that couldn't reproduce its own decision
    # whenever the two disagreed. Governance now runs on measured
    # friction alone, so the fixture has to genuinely earn it.
    return IcebergJourney(
        caller_id="twilio_TEST",
        timestamp=0,
        journey=["root", "intent_menu", "billing_queue", "agent_a", "exit"],
        wait_times={"intent_menu": 40.0, "billing_queue": 90.0, "agent_a": 70.0},
        total_duration=350.0,
        resolved=True,
        friction_count=5,          # ingest-side estimate only; not used for gating (R-6)
        abandonment_reason=None,
    )


def _low_friction_journey():
    return IcebergJourney(
        caller_id="twilio_LOW",
        timestamp=0,
        journey=["root", "intent_menu", "billing_queue", "agent_a", "exit"],
        wait_times={"intent_menu": 5.0, "billing_queue": 10.0, "agent_a": 5.0},
        total_duration=60.0,
        resolved=True,
        friction_count=0,          # <= 2 -> governance NOT required
        abandonment_reason=None,
    )


def _harness():
    return IcebergProductionHarness(
        {"postgres_host": None, "claude_api_key": None, "twilio_account_sid": None}
    )


# ---- Gate 1: the LLM boundary (decider) -------------------------------------

def test_safety_check_fails_closed_on_bad_json():
    print("\n[TEST] safety_check: non-JSON governor output -> blocked")
    d = _decider_with(_returns("this is not json at all"))
    decision = d.safety_check("heal_queue", {"queue": "billing_queue"})
    assert decision["safe"] is False, "parse failure must NOT be safe"
    assert decision["governed"] is False
    assert decision["parse_failed"] is True
    print("  PASSED")
    return True


def test_safety_check_fails_closed_on_empty_content():
    print("\n[TEST] safety_check: empty/unintelligible response -> blocked")
    d = _decider_with(_returns_empty())
    decision = d.safety_check("heal_queue", {"queue": "billing_queue"})
    assert decision["safe"] is False
    assert decision["governed"] is False
    print("  PASSED")
    return True


def test_safety_check_fails_closed_on_nonbool_safe():
    print("\n[TEST] safety_check: 'safe' not a bool -> blocked (Gate 1)")
    d = _decider_with(_returns(json.dumps({"safe": "yes", "reasoning": "x"})))
    decision = d.safety_check("heal_queue", {"queue": "billing_queue"})
    assert decision["safe"] is False, "non-bool 'safe' is unintelligible for a gate"
    assert decision["governed"] is False
    print("  PASSED")
    return True


def test_safety_check_fails_closed_on_transport_error():
    print("\n[TEST] safety_check: client raises -> blocked, error captured")
    d = _decider_with(_raises(RuntimeError("boom")))
    decision = d.safety_check("heal_queue", {"queue": "billing_queue"})
    assert decision["safe"] is False
    assert "transport_error" in decision["reasoning"]
    print("  PASSED")
    return True


def test_safety_check_approves_valid_safe_true():
    print("\n[TEST] safety_check: valid safe=true -> approved, governed")
    payload = {
        "safe": True, "risk_level": "low", "reasoning": "reversible, in-bounds",
        "recommendations": [], "confidence": 0.9,
    }
    d = _decider_with(_returns(json.dumps(payload)))
    decision = d.safety_check("heal_queue", {"queue": "billing_queue"})
    assert decision["safe"] is True
    assert decision["governed"] is True
    assert decision["parse_failed"] is False
    print("  PASSED")
    return True


def test_healing_bounds_fails_closed_no_fabricated_target():
    print("\n[TEST] decide_healing_bounds: garbage -> should_heal False, no target")
    d = _decider_with(_returns("nope"))
    decision = d.decide_healing_bounds("billing_queue", 100.0, 50.0, 1.0)
    assert decision["should_heal"] is False, "was fail-OPEN (True) before the fix"
    assert decision["target_wait"] is None, "must not fabricate a heal target"
    assert decision["governed"] is False
    print("  PASSED")
    return True


# ---- Gate 2: the ledger boundary (harness) ----------------------------------

def test_harness_fails_closed_when_no_governor():
    print("\n[TEST] harness: governance required but no governor -> blocked")
    h = _harness()
    assert h.claude_decider is None, "no api key -> no decider"
    h.twilio_parser.parse_call_log = lambda rec: _high_friction_journey()
    result = h.process_call({"sid": "CATEST01", "status": "completed",
                             "duration": 350, "from": "+1111", "to": "+billing"})
    assert result["governance_required"] is True
    assert result["governance_approved"] is False
    assert result["governance_blocked"] is True, "must NOT run ungoverned + report success"
    # the call is still observed/scored -- fail-closed withholds the ACTION,
    # it does not abort the pipeline.
    assert result["caller_id"] == "twilio_TEST"
    assert result["quality"] is not None
    print("  PASSED")
    return True


def test_harness_blocks_unintelligible_governor():
    print("\n[TEST] harness: governor returns garbage -> blocked (Gate 2)")
    h = _harness()
    h.claude_decider = _decider_with(_returns("garbage not json"))
    h.twilio_parser.parse_call_log = lambda rec: _high_friction_journey()
    result = h.process_call({"sid": "CATEST02", "status": "completed",
                             "duration": 350, "from": "+1111", "to": "+billing"})
    assert result["governance_approved"] is False
    assert result["governance_blocked"] is True
    assert result["claude_safe"] is False
    print("  PASSED")
    return True


def test_harness_approves_valid_safe_decision():
    print("\n[TEST] harness: valid safe=true -> approved, not blocked")
    payload = {
        "safe": True, "risk_level": "low", "reasoning": "ok",
        "recommendations": [], "confidence": 0.9,
    }
    h = _harness()
    h.claude_decider = _decider_with(_returns(json.dumps(payload)))
    h.twilio_parser.parse_call_log = lambda rec: _high_friction_journey()
    result = h.process_call({"sid": "CATEST03", "status": "completed",
                             "duration": 350, "from": "+1111", "to": "+billing"})
    assert result["governance_approved"] is True
    assert result["governance_blocked"] is False
    assert result["claude_safe"] is True
    print("  PASSED")
    return True


def test_harness_low_friction_needs_no_governance():
    print("\n[TEST] harness: low friction -> governance not required, not blocked")
    h = _harness()
    h.twilio_parser.parse_call_log = lambda rec: _low_friction_journey()
    result = h.process_call({"sid": "CATEST04", "status": "completed",
                             "duration": 60, "from": "+1111", "to": "+billing"})
    assert result["governance_required"] is False
    assert result["governance_blocked"] is False
    assert result["claude_safe"] is None
    print("  PASSED")
    return True


def main():
    print("\n" + "=" * 70)
    print("FAIL-CLOSED GOVERNOR TESTS")
    print("=" * 70)
    tests = [
        test_safety_check_fails_closed_on_bad_json,
        test_safety_check_fails_closed_on_empty_content,
        test_safety_check_fails_closed_on_nonbool_safe,
        test_safety_check_fails_closed_on_transport_error,
        test_safety_check_approves_valid_safe_true,
        test_healing_bounds_fails_closed_no_fabricated_target,
        test_harness_fails_closed_when_no_governor,
        test_harness_blocks_unintelligible_governor,
        test_harness_approves_valid_safe_decision,
        test_harness_low_friction_needs_no_governance,
    ]
    results = []
    for t in tests:
        try:
            results.append(t())
        except Exception as e:
            print(f"  FAILED: {e}")
            import traceback
            traceback.print_exc()
            results.append(False)
    passed = sum(1 for r in results if r)
    print("\n" + "=" * 70)
    print(f"FAIL-CLOSED RESULTS: {passed}/{len(tests)} passed")
    print("=" * 70 + "\n")
    return all(results)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
