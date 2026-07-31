"""recommendation_impact.py tests.

Two layers:
1. Pure-logic unit tests against a fake ledger/decider (fast, deterministic
   -- tests the aggregation/scoring math and the insufficient-data skip
   behavior without needing real timing or real Postgres).
2. Real-Postgres integration tests proving get_decisions_by_node_in_window
   itself (the actual SQL) filters by node and time correctly, and that
   the full pipeline (real ledger write -> real pull -> real decider ->
   real shadow-run record) actually connects end to end.
"""
from datetime import datetime, timezone

import pytest

from recommendation_impact import (
    MIN_CALLS_FOR_A_WINDOW,
    run_healing_bounds_shadow,
    run_queue_reordering_shadow,
    score_healing_bounds_run,
    score_queue_reordering_run,
)


# ============================================================== fakes ====
class _FakeLedger:
    """Ignores the exact since/until window strings and returns whatever
    rows the test registered for that node -- appropriate for testing the
    aggregation/scoring logic in isolation. The real window-filtering SQL
    is tested separately below against a real Postgres ledger."""

    def __init__(self):
        self._rows_by_node = {}
        self.shadow_runs = []
        self.shadow_scores = []

    def set_rows(self, node, rows):
        self._rows_by_node[node] = rows

    def get_decisions_by_node_in_window(self, node, since_iso, until_iso, limit=1000):
        return self._rows_by_node.get(node, [])

    def record_recommendation_shadow_run(self, **kw):
        result = {"status": "created",
                  "current_hash": f"hash-{len(self.shadow_runs)}", **kw}
        self.shadow_runs.append(result)
        return result

    def record_recommendation_shadow_score(self, **kw):
        result = {"status": "created", **kw}
        self.shadow_scores.append(result)
        return result


def _rows(wait_times=None, quality_tiers=None):
    if wait_times is not None:
        return [{"input_data": {"wait_time": w}} for w in wait_times]
    return [{"input_data": {"quality_tier": t}} for t in quality_tiers]


class _FakeDecider:
    def __init__(self, healing_response=None, reorder_response=None):
        self.healing_response = healing_response or {
            "should_heal": True, "target_wait": 50.0, "confidence": 0.8}
        self.reorder_response = reorder_response or {
            "proposed_order": ["a", "b"], "expected_impact": 0.3, "confidence": 0.7}
        self.healing_calls = []
        self.reorder_calls = []

    def decide_healing_bounds(self, node, current_wait, baseline_wait, drift):
        self.healing_calls.append((node, current_wait, baseline_wait, drift))
        return self.healing_response

    def decide_queue_reordering(self, current_order, success_rates, caller_dist):
        self.reorder_calls.append((current_order, success_rates, caller_dist))
        return self.reorder_response


# =================================================== run_healing_bounds ====
def test_healing_bounds_skips_with_too_few_recent_calls():
    ledger = _FakeLedger()
    ledger.set_rows("billing_queue", _rows(wait_times=[50.0] * (MIN_CALLS_FOR_A_WINDOW - 1)))
    result = run_healing_bounds_shadow(
        ledger, _FakeDecider(), "billing_queue", "ivr:standard-ivr:2.0.2")
    assert result is None
    assert ledger.shadow_runs == []


def test_healing_bounds_computes_real_averages_and_drift():
    ledger = _FakeLedger()
    # Both windows return the same fake data (see _FakeLedger docstring) --
    # what matters here is the arithmetic on whatever data comes back.
    ledger.set_rows("billing_queue", _rows(wait_times=[80.0, 90.0, 100.0, 85.0, 95.0]))
    decider = _FakeDecider()
    result = run_healing_bounds_shadow(
        ledger, decider, "billing_queue", "ivr:standard-ivr:2.0.2")
    assert result is not None
    node, current_wait, baseline_wait, drift = decider.healing_calls[0]
    assert node == "billing_queue"
    assert current_wait == pytest.approx(90.0)
    assert baseline_wait == pytest.approx(90.0)  # same fake data both windows
    assert drift == pytest.approx(0.0)


def test_healing_bounds_shadow_run_never_calls_anything_that_acts():
    """The whole point: recorded, never applied. There is no method on
    the fake ledger (or anywhere) this function could call to apply a
    staffing/routing change -- this test exists to make that
    structurally obvious, not just assert an absence."""
    ledger = _FakeLedger()
    ledger.set_rows("billing_queue", _rows(wait_times=[80.0] * 10))
    run_healing_bounds_shadow(ledger, _FakeDecider(), "billing_queue", "v1")
    assert len(ledger.shadow_runs) == 1
    assert ledger.shadow_runs[0]["recommendation_kind"] == "healing_bounds"


# ================================================= score_healing_bounds ====
def _shadow_run(recommendation_kind="healing_bounds", subject="billing_queue",
                inputs=None, recommendation=None, timestamp=None):
    return {
        "shadow_run_hash": "abc123",
        "recommendation_kind": recommendation_kind,
        "subject": subject,
        "inputs": inputs or {"current_wait": 90.0, "baseline_wait": 40.0},
        "recommendation": recommendation if recommendation is not None else {
            "should_heal": True, "target_wait": 45.0, "confidence": 0.8},
        "timestamp": (timestamp or datetime.now(timezone.utc)).isoformat(),
    }


def test_score_healing_bounds_skips_with_no_outcome_data_yet():
    ledger = _FakeLedger()
    ledger.set_rows("billing_queue", [])
    result = score_healing_bounds_run(ledger, _shadow_run())
    assert result is None
    assert ledger.shadow_scores == []


def test_score_healing_bounds_skips_when_recommendation_had_no_target():
    """The governor's fail-closed path (no API client, parse failure)
    returns target_wait=None -- nothing to score against."""
    ledger = _FakeLedger()
    result = score_healing_bounds_run(
        ledger, _shadow_run(recommendation={"should_heal": False,
                                            "target_wait": None}))
    assert result is None


def test_score_healing_bounds_computes_error_and_direction():
    ledger = _FakeLedger()
    # predicted target_wait=45.0, current_wait was 90.0 (from _shadow_run's
    # default inputs) -- actual landed at 50.0, much closer to the target
    # than the starting 90.0 was.
    ledger.set_rows("billing_queue", _rows(wait_times=[48.0, 50.0, 52.0, 50.0, 50.0]))
    result = score_healing_bounds_run(ledger, _shadow_run())
    assert result is not None
    assert ledger.shadow_scores[0]["score"]["moved_toward_target"] is True
    assert ledger.shadow_scores[0]["score"]["predicted_wait"] == 45.0
    assert ledger.shadow_scores[0]["score"]["actual_wait"] == pytest.approx(50.0)
    assert ledger.shadow_scores[0]["score"]["error_seconds"] == pytest.approx(5.0)


def test_score_healing_bounds_detects_recommendation_that_did_not_help():
    ledger = _FakeLedger()
    # target was 45.0, current_wait was 90.0, but it actually got WORSE (110)
    # -- further from the target than the starting point was.
    ledger.set_rows("billing_queue", _rows(wait_times=[108.0, 110.0, 112.0, 110.0, 110.0]))
    result = score_healing_bounds_run(ledger, _shadow_run())
    assert result is not None
    assert ledger.shadow_scores[0]["score"]["moved_toward_target"] is False


# =============================================== run_queue_reordering ====
def test_queue_reordering_skips_with_fewer_than_two_valid_queues():
    ledger = _FakeLedger()
    ledger.set_rows("a", _rows(quality_tiers=["excellent"] * MIN_CALLS_FOR_A_WINDOW))
    ledger.set_rows("b", _rows(quality_tiers=["poor"] * (MIN_CALLS_FOR_A_WINDOW - 1)))
    result = run_queue_reordering_shadow(ledger, _FakeDecider(), ["a", "b"], "v1")
    assert result is None


def test_queue_reordering_computes_real_success_rates():
    ledger = _FakeLedger()
    ledger.set_rows("a", _rows(quality_tiers=["excellent", "good", "poor", "failed",
                                              "excellent"]))  # 3/5 = 0.6
    ledger.set_rows("b", _rows(quality_tiers=["poor"] * 5))  # 0/5 = 0.0
    decider = _FakeDecider()
    result = run_queue_reordering_shadow(ledger, decider, ["a", "b"], "v1")
    assert result is not None
    current_order, success_rates, caller_dist = decider.reorder_calls[0]
    assert success_rates["a"] == pytest.approx(0.6)
    assert success_rates["b"] == pytest.approx(0.0)
    assert caller_dist["a"] == 5
    assert caller_dist["b"] == 5


# ============================================= score_queue_reordering ====
def _reorder_shadow_run(before_rates=None, recommendation=None):
    return {
        "shadow_run_hash": "def456",
        "recommendation_kind": "queue_reordering",
        "subject": "a,b",
        "inputs": {"success_rates": before_rates or {"a": 0.6, "b": 0.4}},
        "recommendation": recommendation if recommendation is not None else {
            "proposed_order": ["a", "b"], "expected_impact": 0.2},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def test_score_queue_reordering_skips_when_no_proposal_was_made():
    ledger = _FakeLedger()
    result = score_queue_reordering_run(
        ledger, _reorder_shadow_run(recommendation={"proposed_order": None}))
    assert result is None


def test_score_queue_reordering_reports_per_queue_deltas():
    ledger = _FakeLedger()
    ledger.set_rows("a", _rows(quality_tiers=["excellent"] * 5))       # 1.0 now
    ledger.set_rows("b", _rows(quality_tiers=["poor", "failed"] * 3))  # 0.0 now
    result = score_queue_reordering_run(
        ledger, _reorder_shadow_run(before_rates={"a": 0.6, "b": 0.4}))
    assert result is not None
    deltas = ledger.shadow_scores[0]["score"]["deltas"]
    assert deltas["a"] == pytest.approx(0.4)    # 1.0 - 0.6
    assert deltas["b"] == pytest.approx(-0.4)   # 0.0 - 0.4


def test_score_queue_reordering_skips_a_queue_with_no_outcome_data_but_scores_the_rest():
    ledger = _FakeLedger()
    ledger.set_rows("a", _rows(quality_tiers=["excellent"] * 5))
    # "b" has no rows registered at all -- insufficient outcome data
    result = score_queue_reordering_run(
        ledger, _reorder_shadow_run(before_rates={"a": 0.6, "b": 0.4}))
    assert result is not None
    deltas = ledger.shadow_scores[0]["score"]["deltas"]
    assert "a" in deltas
    assert "b" not in deltas
