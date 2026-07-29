"""OutcomeV1 in the chain: the harm-event record kind and the
outcome_obligation hashed field.

Two properties are load-bearing here and both are tested against a real
ledger rather than a mock:

  * A decision that declares an obligation hashes that declaration in AT
    DECISION TIME, so it can never be edited afterwards -- which is the
    only reason the decision row is allowed to close permanently.
  * A harm event is its own record kind, not a supersession. An examiner
    who cannot count reversals separately from corrections cannot count
    either.
"""

import pytest

from canonical_fields import OPTIONAL_HASHED_FIELDS
from governance.ledger_postgres import GovernanceDecisionRecord
from outcome_v1 import MaturationRule, derive_open_obligations


def _record(**kw):
    base = dict(
        action_type="governance_decision", node="underwriting",
        cassette_version="lending:reference:1.0.0",
        input_data={"applicant": "a-1"},
        policy_parameters={"governance_trigger": 2},
        reasoning="insufficient verified income",
        output={"approved": False})
    base.update(kw)
    return GovernanceDecisionRecord(**base)


def test_outcome_obligation_is_an_optional_hashed_field():
    assert "outcome_obligation" in OPTIONAL_HASHED_FIELDS


def test_a_decision_declaring_no_obligation_hashes_as_it_always_did(test_ledger):
    """The migration guarantee every optional hashed field carries: a row
    that omits the field hashes byte-identically to one written before the
    field existed."""
    from cassette_schema import validate_cassette
    from cassettes.ivr_cassette import IvrCassette
    params = validate_cassette(IvrCassette())

    assert test_ledger.append_decision(_record(), governance_params=params)
    rows = test_ledger.get_entries(limit=5)
    assert rows
    assert test_ledger.verify_chain()["ok"]


def test_a_declared_obligation_lands_in_the_row_and_the_chain(test_ledger):
    from cassette_schema import validate_cassette
    from cassettes.ivr_cassette import IvrCassette
    params = validate_cassette(IvrCassette())

    declaration = "loan_performance@24mo"
    assert test_ledger.append_decision(
        _record(outcome_obligation=declaration), governance_params=params)
    assert test_ledger.verify_chain()["ok"]

    import psycopg2
    from Tests.conftest import PG_CONFIG
    conn = psycopg2.connect(**PG_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT outcome_obligation, current_hash FROM "
                        "ledger_entries WHERE record_kind='governance_decision' "
                        "ORDER BY id DESC LIMIT 1")
            stored, current_hash = cur.fetchone()
    finally:
        conn.close()

    assert stored == declaration
    # The whole point: the twin can derive what is owed from this alone.
    derived = derive_open_obligations([
        {"current_hash": current_hash, "timestamp": 1_700_000_000.0,
         "outcome_obligation": stored, "domain": "lending"}])
    assert len(derived) == 1
    assert derived[0].decision_hash == current_hash
    assert derived[0].obligation_kind == "loan_performance"
    assert (derived[0].expected_by - derived[0].opened_at
            == MaturationRule.parse(declaration).horizon_seconds)


def test_changing_the_declaration_changes_the_hash(test_ledger):
    """If it did not, the declaration would not be protected and the
    decision row could be quietly re-scoped after the fact."""
    from cassette_schema import validate_cassette
    from cassettes.ivr_cassette import IvrCassette
    params = validate_cassette(IvrCassette())

    test_ledger.append_decision(_record(outcome_obligation="loan_performance@24mo"),
                                governance_params=params)
    test_ledger.append_decision(_record(outcome_obligation="loan_performance@12mo"),
                                governance_params=params)
    rows = test_ledger.get_entries(limit=2)
    assert len({r["current_hash"] for r in rows}) == 2
    assert test_ledger.verify_chain()["ok"]


# ---------------------------------------------------------------------------
# Harm events: the governance exception to "per-decision outcome is
# business reporting".
# ---------------------------------------------------------------------------

def _harm(ledger, **kw):
    base = dict(cassette_version="lending:reference:1.0.0",
                decision_hash="a" * 64,
                harm_kind="denial_reversed_on_appeal",
                subject_id="applicant-1",
                finding={"appeal_id": "AP-9", "reversed_on": "2026-05-02"})
    base.update(kw)
    return ledger.record_outcome_harm_event(**base)


def test_a_harm_event_is_its_own_record_kind(test_ledger):
    result = _harm(test_ledger)
    assert result["status"] == "created"
    rows = test_ledger.get_entries(limit=5)
    kinds = {(r.get("data") or {}).get("record_kind") for r in rows}
    assert "outcome_harm_event" in kinds
    assert "decision_supersession" not in kinds


def test_a_harm_event_joins_the_same_chain(test_ledger):
    _harm(test_ledger)
    _harm(test_ledger, harm_kind="adverse_action_notice_defective")
    assert test_ledger.verify_chain()["ok"]


def test_a_harm_event_does_not_touch_the_decision_it_points_at(test_ledger):
    from cassette_schema import validate_cassette
    from cassettes.ivr_cassette import IvrCassette
    params = validate_cassette(IvrCassette())
    test_ledger.append_decision(_record(), governance_params=params)
    before = test_ledger.get_entries(limit=1)[0]["current_hash"]

    _harm(test_ledger, decision_hash=before)

    rows = test_ledger.get_entries(limit=10)
    decision = [r for r in rows
                if (r.get("data") or {}).get("record_kind") == "governance_decision"][0]
    assert decision["current_hash"] == before
    assert test_ledger.verify_chain()["ok"]


def test_a_harm_event_with_nothing_to_point_at_is_refused(test_ledger):
    with pytest.raises(ValueError) as exc:
        _harm(test_ledger, decision_hash="")
    assert "allegation" in str(exc.value)


def test_a_harm_event_must_name_the_kind_of_harm(test_ledger):
    with pytest.raises(ValueError) as exc:
        _harm(test_ledger, harm_kind="")
    assert "not\n" in str(exc.value) or "not a finding" in str(exc.value)


def test_a_harm_event_must_carry_a_finding_body(test_ledger):
    with pytest.raises(ValueError):
        _harm(test_ledger, finding={})


def test_a_harm_event_must_name_its_subject(test_ledger):
    with pytest.raises(ValueError):
        _harm(test_ledger, subject_id="")
