"""Real-Postgres integration tests for the recommendation-shadow pipeline.

Unlike test_recommendation_impact.py (fake ledger, fast logic-only tests),
these prove the actual SQL in get_decisions_by_node_in_window really
filters by node and time, and that the full pipeline -- real ledger write,
real windowed pull, real (fail-closed) decider call, real shadow-run
record -- actually connects end to end.
"""
import time
import uuid
from datetime import datetime, timedelta, timezone

from cassette_schema import validate_cassette
from cassettes.ivr_cassette import IvrCassette
from cassettes.mortgage_cassette import MortgageCassette
from claude_governance_api import ClaudeGovernanceDecider
from governance.ledger_postgres import GovernanceDecisionRecord
from recommendation_impact import run_healing_bounds_shadow

_PARAMS = validate_cassette(IvrCassette())
_MORTGAGE_PARAMS = validate_cassette(MortgageCassette())
_MORTGAGE_VERSION = "mortgage:mortgage-v1:1.0.0"


def _decision(node, wait_time, quality_tier="good", cassette_version=None,
             policy_parameters=None):
    return GovernanceDecisionRecord(
        action_type="governance_decision", node=node,
        cassette_version=cassette_version or "ivr:standard-ivr:2.0.2",
        input_data={"call_sid": f"RI{uuid.uuid4().hex[:10]}",
                   "wait_time": wait_time, "quality_tier": quality_tier},
        policy_parameters=policy_parameters or {"governance_trigger": 2},
        reasoning="test call for recommendation_impact",
        output={"safe": True})


def test_get_decisions_by_node_in_window_filters_by_node(test_ledger):
    """Node/time filtering is ledger mechanism, not IVR behavior -- proven
    here against the mortgage cassette so this test carries no IVR
    dependency at all (see test_full_pipeline below for the one test in
    this file that legitimately needs IVR, because it exercises
    decide_healing_bounds, a queue-healing concept mortgage has none of)."""
    node_a, node_b = f"node-a-{uuid.uuid4().hex[:6]}", f"node-b-{uuid.uuid4().hex[:6]}"
    test_ledger.append_decision(
        _decision(node_a, 50.0, cassette_version=_MORTGAGE_VERSION,
                 policy_parameters={"governance_trigger": 1}),
        governance_params=_MORTGAGE_PARAMS)
    test_ledger.append_decision(
        _decision(node_b, 99.0, cassette_version=_MORTGAGE_VERSION,
                 policy_parameters={"governance_trigger": 1}),
        governance_params=_MORTGAGE_PARAMS)

    since = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    until = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    rows_a = test_ledger.get_decisions_by_node_in_window(node_a, since, until)
    assert len(rows_a) == 1
    assert rows_a[0]["input_data"]["wait_time"] == 50.0


def test_get_decisions_by_node_in_window_filters_by_time(test_ledger):
    """See node-filter test above -- same mechanism-not-domain rationale."""
    node = f"node-time-{uuid.uuid4().hex[:6]}"
    test_ledger.append_decision(
        _decision(node, 50.0, cassette_version=_MORTGAGE_VERSION,
                 policy_parameters={"governance_trigger": 1}),
        governance_params=_MORTGAGE_PARAMS)

    # A window entirely in the future must not include this real, already-
    # written row.
    future_since = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    future_until = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    assert test_ledger.get_decisions_by_node_in_window(
        node, future_since, future_until) == []

    # A window spanning right now must include it.
    since = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    until = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    assert len(test_ledger.get_decisions_by_node_in_window(node, since, until)) == 1


def test_full_pipeline_real_ledger_real_pull_real_decider_real_record(test_ledger):
    """Fail-closed decider (no API key) is deliberate -- same convention
    test_sentinel_worker.py already uses for a real-without-live-API-key
    test: it's still real production_harness/ledger code on a real code
    path, just not spending real API money to prove the wiring connects."""
    node = f"pipeline-{uuid.uuid4().hex[:6]}"
    # Baseline window's real calls, then a real time gap, then recent
    # window's real calls -- run_healing_bounds_shadow's baseline window
    # is defined as the period just BEFORE the recent window, so this
    # needs two genuinely time-separated batches, not six rows all at once.
    for i in range(6):
        test_ledger.append_decision(
            _decision(node, 60.0 + i, quality_tier="excellent"),
            governance_params=_PARAMS)
    time.sleep(1.5)
    for i in range(6):
        test_ledger.append_decision(
            _decision(node, 90.0 + i, quality_tier="good"),
            governance_params=_PARAMS)

    decider = ClaudeGovernanceDecider(api_key=None)  # fail-closed, no network
    result = run_healing_bounds_shadow(
        test_ledger, decider, node, "ivr:standard-ivr:2.0.2",
        recent_window_s=1.0, baseline_window_s=3.0)
    assert result is not None
    assert result["recommendation_kind"] == "healing_bounds"
    assert test_ledger.verify_chain()["ok"]

    unscored = test_ledger.get_unscored_shadow_runs()
    assert any(u["shadow_run_hash"] == result["current_hash"] for u in unscored)
