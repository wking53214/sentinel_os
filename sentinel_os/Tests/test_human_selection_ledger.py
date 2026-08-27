"""human_selection as a new ledger record kind (F2, 2026-08-07) -- the
founding idea that had never been implemented: capturing which
recommendation a human accepted, overrode, or rejected. Piloted on
exactly one surface: a human's review of GovernanceDecider.safety_check's
verdict on a governed episode (a governance_decision row) -- see
governance/human_selection_v1.py's module docstring for why this is the
only surface confirmed live, and for the full survey of everything else
that was ruled out.

Tested against a real ledger, same convention
test_recommendation_shadow_ledger.py / test_ai_cost_ledger.py already
established.
"""
import pytest

from canonical_fields import OPTIONAL_HASHED_FIELDS
from governance.human_selection_v1 import (
    HUMAN_SELECTION_CONCUR,
    HUMAN_SELECTION_ESCALATE,
    HUMAN_SELECTION_OVERRIDE,
)
from governance.ledger_postgres import GovernanceDecisionRecord

CASSETTE_VERSION = "ivr:standard-ivr:2.0.2"


def _governance_params():
    from cassette_schema import validate_cassette
    from cassettes.ivr_cassette import IvrCassette
    return validate_cassette(IvrCassette())


def _decision_record(**kw):
    base = dict(
        action_type="governance_decision", node="billing_queue",
        cassette_version=CASSETTE_VERSION,
        input_data={"call_sid": "HUMANSEL001"},
        policy_parameters={"governance_trigger": 2},
        reasoning="AI safety check: risk elevated on repeat contact",
        output={"safe": False, "risk_level": "high", "confidence": 0.87})
    base.update(kw)
    return GovernanceDecisionRecord(**base)


def _make_real_decision(test_ledger, **kw):
    """Write a real governance_decision row and return its current_hash
    plus the reasoning/output it actually carries -- so a test can assert
    a human_selection row's recommendation_shown against the REAL verdict,
    never a value the test just made up."""
    record = _decision_record(**kw)
    assert test_ledger.append_decision(record, governance_params=_governance_params())
    decisions = test_ledger.get_decisions(limit=1)
    latest = decisions[0]
    return latest["current_hash"], record.reasoning, record.output


def test_decision_hash_is_an_optional_hashed_field():
    assert "decision_hash" in OPTIONAL_HASHED_FIELDS


def test_a_concurring_selection_records_and_verifies(test_ledger):
    decision_hash, _, _ = _make_real_decision(test_ledger)
    result = test_ledger.record_human_selection(
        decision_hash=decision_hash, human_selection=HUMAN_SELECTION_CONCUR,
        selected_by="reviewer-alice")
    assert result["status"] == "created"
    assert result["decision_hash"] == decision_hash
    assert test_ledger.verify_chain()["ok"]


def test_an_override_captures_the_real_divergence(test_ledger):
    """Not a happy-path no-op: the governor said unsafe/high-risk, a human
    reviewed it and disagreed. The row must faithfully record BOTH the
    real recommendation the governor actually made and that the human's
    selection diverged from it."""
    decision_hash, reasoning, output = _make_real_decision(
        test_ledger,
        reasoning="AI safety check: risk elevated on repeat contact",
        output={"safe": False, "risk_level": "high", "confidence": 0.87})

    result = test_ledger.record_human_selection(
        decision_hash=decision_hash, human_selection=HUMAN_SELECTION_OVERRIDE,
        selected_by="reviewer-bob",
        rationale="reviewed the call recording, risk was miscalibrated")
    assert result["status"] == "created"
    assert test_ledger.verify_chain()["ok"]

    stored = test_ledger.get_human_selections(decision_hash=decision_hash)
    assert len(stored) == 1
    row = stored[0]
    assert row["human_selection"] == HUMAN_SELECTION_OVERRIDE
    assert row["selected_by"] == "reviewer-bob"
    assert row["rationale"] == "reviewed the call recording, risk was miscalibrated"
    # The recommendation shown must be the REAL governor verdict, looked
    # up from the parent row -- not anything the caller supplied.
    assert row["recommendation_shown"]["reasoning"] == reasoning
    assert row["recommendation_shown"]["output"] == output
    # And it must actually diverge from the human's own selection --
    # the governor said unsafe, the human overrode that.
    assert output["safe"] is False
    assert row["human_selection"] == HUMAN_SELECTION_OVERRIDE


def test_an_escalation_records_and_verifies(test_ledger):
    decision_hash, _, _ = _make_real_decision(test_ledger)
    result = test_ledger.record_human_selection(
        decision_hash=decision_hash, human_selection=HUMAN_SELECTION_ESCALATE,
        selected_by="reviewer-carol")
    assert result["status"] == "created"
    stored = test_ledger.get_human_selections(decision_hash=decision_hash)
    assert stored[0]["human_selection"] == HUMAN_SELECTION_ESCALATE


def test_recommendation_shown_is_looked_up_not_caller_supplied(test_ledger):
    """The writer's signature has no recommendation_shown parameter at
    all -- there is nothing a caller could pass to misrepresent what was
    actually recommended."""
    import inspect
    sig = inspect.signature(test_ledger.record_human_selection)
    assert "recommendation_shown" not in sig.parameters


def test_an_invalid_selection_value_is_refused(test_ledger):
    decision_hash, _, _ = _make_real_decision(test_ledger)
    with pytest.raises(ValueError, match="one of"):
        test_ledger.record_human_selection(
            decision_hash=decision_hash, human_selection="approve",
            selected_by="reviewer-alice")


def test_a_missing_selected_by_is_refused(test_ledger):
    decision_hash, _, _ = _make_real_decision(test_ledger)
    with pytest.raises(ValueError, match="selected_by"):
        test_ledger.record_human_selection(
            decision_hash=decision_hash, human_selection=HUMAN_SELECTION_CONCUR,
            selected_by="")


def test_a_decision_hash_that_does_not_exist_is_refused(test_ledger):
    with pytest.raises(ValueError, match="does not match any governance_decision"):
        test_ledger.record_human_selection(
            decision_hash="not-a-real-hash-" + "0" * 40,
            human_selection=HUMAN_SELECTION_CONCUR, selected_by="reviewer-alice")


def test_a_decision_hash_pointing_at_a_non_decision_row_is_refused(test_ledger):
    """Fail-closed, same posture as record_recommendation_shadow_score:
    a human_selection must reference a REAL governance_decision, never
    a hash that merely exists on the chain for some other reason."""
    shadow_run = test_ledger.record_recommendation_shadow_run(
        recommendation_kind="healing_bounds", subject="billing_queue",
        cassette_version=CASSETTE_VERSION,
        inputs={"current_wait": 90.0}, recommendation={"should_heal": True})
    with pytest.raises(ValueError, match="does not match any governance_decision"):
        test_ledger.record_human_selection(
            decision_hash=shadow_run["current_hash"],
            human_selection=HUMAN_SELECTION_CONCUR, selected_by="reviewer-alice")


def test_get_human_selections_filters_by_decision_hash(test_ledger):
    d1, _, _ = _make_real_decision(test_ledger, input_data={"call_sid": "HS-A"})
    d2, _, _ = _make_real_decision(test_ledger, input_data={"call_sid": "HS-B"})
    test_ledger.record_human_selection(
        decision_hash=d1, human_selection=HUMAN_SELECTION_CONCUR,
        selected_by="reviewer-alice")
    test_ledger.record_human_selection(
        decision_hash=d2, human_selection=HUMAN_SELECTION_OVERRIDE,
        selected_by="reviewer-bob")

    only_d1 = test_ledger.get_human_selections(decision_hash=d1)
    assert len(only_d1) == 1
    assert only_d1[0]["decision_hash"] == d1
    assert only_d1[0]["human_selection"] == HUMAN_SELECTION_CONCUR


def test_two_different_selections_on_two_decisions_do_not_collide_hashes(test_ledger):
    """Tamper-evidence: two human_selection rows with different content
    must not hash identically."""
    d1, _, _ = _make_real_decision(test_ledger, input_data={"call_sid": "HS-C"})
    d2, _, _ = _make_real_decision(test_ledger, input_data={"call_sid": "HS-D"})
    r1 = test_ledger.record_human_selection(
        decision_hash=d1, human_selection=HUMAN_SELECTION_CONCUR,
        selected_by="reviewer-alice")
    r2 = test_ledger.record_human_selection(
        decision_hash=d2, human_selection=HUMAN_SELECTION_OVERRIDE,
        selected_by="reviewer-bob")
    assert r1["current_hash"] != r2["current_hash"]
    assert test_ledger.verify_chain()["ok"]


def test_twin_recomputes_human_selection_identically(test_ledger):
    """Recompute site 3. If the witness disagrees with the writer, every
    honest row reads as DIVERGE on the twin -- indistinguishable from
    tampering, the highest-risk failure mode of adding a new record kind."""
    from twin_custody import SHIPPED_COLUMNS, recompute_current_hash
    from Tests.conftest import PG_CONFIG
    import psycopg2

    decision_hash, _, _ = _make_real_decision(test_ledger, input_data={"call_sid": "HS-TWIN"})
    test_ledger.record_human_selection(
        decision_hash=decision_hash, human_selection=HUMAN_SELECTION_OVERRIDE,
        selected_by="reviewer-alice", rationale="twin-verification row")

    conn = psycopg2.connect(connect_timeout=2, **PG_CONFIG)
    try:
        cur = conn.cursor()
        # Select exactly the columns the twin ships, in SHIPPED_COLUMNS order,
        # so the row dict carries every optional hashed field (authorized_by_sig
        # included) -- a hand-picked subset silently drops the newest field.
        cur.execute(
            f"SELECT {', '.join(SHIPPED_COLUMNS)} FROM ledger_entries "  # nosec B608 -- SHIPPED_COLUMNS is a fixed, code-defined column list, never external input
            f"WHERE record_kind = 'human_selection' ORDER BY id ASC"
        )
        fetched = cur.fetchall()
    finally:
        conn.close()

    assert fetched
    for r in fetched:
        row = dict(zip(SHIPPED_COLUMNS, r))
        assert recompute_current_hash(row) == row["current_hash"], \
            "twin disagrees with the writer on human_selection"
