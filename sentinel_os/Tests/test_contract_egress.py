"""Egress chokepoint (Part 2) and subcontractor approval gate (Part 4).

Pure decision logic first, then the fail-closed proofs against a real
ledger. The fail-closed behaviour is PROVEN here, not just documented
in a docstring -- that was an explicit requirement, and a chokepoint
whose refusal path is only described is a chokepoint nobody has tested.
"""
from datetime import datetime, timedelta, timezone

import pytest

from contract_cassette import (
    RECIPIENT_CLASS_AFFILIATE,
    RECIPIENT_CLASS_SUBCONTRACTOR,
    TERM_EGRESS_PROHIBITED,
    TERM_EGRESS_REQUIRES_APPROVAL,
    TERM_PURPOSE_RESTRICTION,
    TERM_RETENTION_MAX_DAYS,
    ContractCassette,
    ContractCassetteRegistry,
    ContractTerm,
)
from contract_egress import (
    APPROVAL_FLAG,
    APPROVAL_INDETERMINATE,
    APPROVAL_PASS,
    EGRESS_AUTHORIZED,
    EGRESS_REFUSED,
    REFUSAL_APPROVAL_NOT_FOUND,
    REFUSAL_APPROVAL_NOT_LIVE,
    REFUSAL_APPROVAL_REQUIRED_MISSING,
    REFUSAL_NO_CONTRACT,
    REFUSAL_PURPOSE_NOT_PERMITTED,
    REFUSAL_PURPOSE_PROHIBITED,
    EgressLedgerUnavailable,
    EgressRequest,
    approval_live_at,
    check_subcontractor_approvals,
    evaluate_egress,
    latest_approval_states,
    request_egress,
)
from contract_cassettes.reference_dpa import ReferenceDPAContract

T0 = "2026-06-01T00:00:00+00:00"
T1 = "2026-06-15T00:00:00+00:00"
T2 = "2026-07-01T00:00:00+00:00"


def _contract(counterparty="acme-bank"):
    class _Lens(ContractCassette):
        def get_counterparty_id(self):
            return counterparty

        def get_contract_reference(self):
            return f"DPA-{counterparty}"

        def get_contract_version(self):
            return "1.0.0"

        def get_terms(self):
            return (
                ContractTerm(TERM_RETENTION_MAX_DAYS, {"max_days": 90}),
                ContractTerm(TERM_EGRESS_PROHIBITED,
                             {"purpose": "model_training"}),
                ContractTerm(TERM_EGRESS_REQUIRES_APPROVAL,
                             {"recipient_class": RECIPIENT_CLASS_SUBCONTRACTOR}),
                ContractTerm(TERM_PURPOSE_RESTRICTION,
                             {"permitted_purposes": ["fraud_screening",
                                                     "statement_generation"]}),
            )

    return _Lens()


def _request(**kw):
    base = dict(counterparty_id="acme-bank", data_scope="customer_records",
                recipient="vendor-x", recipient_class=RECIPIENT_CLASS_AFFILIATE,
                purpose="fraud_screening", occurred_at=T1)
    base.update(kw)
    return EgressRequest(**base)


def _approval_row(approval_id="AP-1", state="granted", granted_at=T0,
                  expires_at="", revoked_at="", approver="compliance-officer",
                  recipient="vendor-x",
                  recipient_class=RECIPIENT_CLASS_SUBCONTRACTOR):
    return {
        "record_kind": "contract_approval",
        "authorized_by": approver,
        "current_hash": f"h-{approval_id}-{state}",
        "data": {"approval_id": approval_id, "state": state,
                 "granted_at": granted_at, "expires_at": expires_at,
                 "revoked_at": revoked_at, "recipient": recipient,
                 "recipient_class": recipient_class,
                 "counterparty": "acme-bank", "scope": "customer_records"},
    }


def _egress_row(decision=EGRESS_AUTHORIZED,
                recipient_class=RECIPIENT_CLASS_SUBCONTRACTOR,
                approval_reference="AP-1", occurred_at=T1):
    return {
        "record_kind": "contract_egress",
        "current_hash": f"eh-{occurred_at}-{approval_reference}",
        "data": {"decision": decision, "recipient_class": recipient_class,
                 "approval_reference": approval_reference,
                 "occurred_at": occurred_at, "recipient": "vendor-x",
                 "purpose": "fraud_screening", "counterparty": "acme-bank"},
    }


# -- pure decision logic ----------------------------------------------

def test_permitted_purpose_to_an_unrestricted_class_is_authorized():
    decision, reasons, _ = evaluate_egress(_contract(), _request(), {})
    assert decision == EGRESS_AUTHORIZED
    assert reasons == ()


def test_no_registered_contract_refuses():
    decision, reasons, _ = evaluate_egress(None, _request(), {})
    assert decision == EGRESS_REFUSED
    assert reasons == (REFUSAL_NO_CONTRACT,)


def test_prohibited_purpose_refuses():
    decision, reasons, _ = evaluate_egress(
        _contract(), _request(purpose="model_training"), {})
    assert decision == EGRESS_REFUSED
    assert REFUSAL_PURPOSE_PROHIBITED in reasons


def test_purpose_outside_the_permitted_list_refuses():
    decision, reasons, _ = evaluate_egress(
        _contract(), _request(purpose="marketing"), {})
    assert decision == EGRESS_REFUSED
    assert REFUSAL_PURPOSE_NOT_PERMITTED in reasons


def test_both_purpose_reasons_can_fire_together():
    """Reason codes accumulate; the report shows every basis for the
    refusal, not just the first one hit."""
    _, reasons, _ = evaluate_egress(
        _contract(), _request(purpose="model_training"), {})
    assert REFUSAL_PURPOSE_PROHIBITED in reasons
    assert REFUSAL_PURPOSE_NOT_PERMITTED in reasons


def test_subcontractor_without_an_approval_reference_refuses():
    decision, reasons, _ = evaluate_egress(
        _contract(),
        _request(recipient_class=RECIPIENT_CLASS_SUBCONTRACTOR), {})
    assert decision == EGRESS_REFUSED
    assert reasons == (REFUSAL_APPROVAL_REQUIRED_MISSING,)


def test_subcontractor_with_an_unknown_approval_reference_refuses():
    decision, reasons, _ = evaluate_egress(
        _contract(),
        _request(recipient_class=RECIPIENT_CLASS_SUBCONTRACTOR,
                 approval_reference="AP-does-not-exist"), {})
    assert decision == EGRESS_REFUSED
    assert reasons == (REFUSAL_APPROVAL_NOT_FOUND,)


def test_subcontractor_with_a_live_approval_is_authorized():
    approvals = latest_approval_states([_approval_row()])
    decision, reasons, evidence = evaluate_egress(
        _contract(),
        _request(recipient_class=RECIPIENT_CLASS_SUBCONTRACTOR,
                 approval_reference="AP-1"), approvals)
    assert decision == EGRESS_AUTHORIZED
    assert evidence["approval_check"]["verdict"] == APPROVAL_PASS


def test_subcontractor_with_a_revoked_approval_refuses():
    approvals = latest_approval_states([
        _approval_row(),
        _approval_row(state="revoked", revoked_at=T1),
    ])
    decision, reasons, _ = evaluate_egress(
        _contract(),
        _request(recipient_class=RECIPIENT_CLASS_SUBCONTRACTOR,
                 approval_reference="AP-1", occurred_at=T2), approvals)
    assert decision == EGRESS_REFUSED
    assert REFUSAL_APPROVAL_NOT_LIVE in reasons


def test_indeterminate_approval_fails_closed_rather_than_authorizing():
    """'We could not tell' must never authorize. This is the branch a
    lenient implementation gets wrong."""
    approvals = latest_approval_states([_approval_row(granted_at="not-a-date")])
    decision, reasons, _ = evaluate_egress(
        _contract(),
        _request(recipient_class=RECIPIENT_CLASS_SUBCONTRACTOR,
                 approval_reference="AP-1"), approvals)
    assert decision == EGRESS_REFUSED
    assert REFUSAL_APPROVAL_NOT_LIVE in reasons


def test_affiliate_class_needs_no_approval_under_this_contract():
    decision, _, evidence = evaluate_egress(
        _contract(), _request(recipient_class=RECIPIENT_CLASS_AFFILIATE), {})
    assert decision == EGRESS_AUTHORIZED
    assert "approval_check" not in evidence


# -- approval liveness -------------------------------------------------

def test_approval_missing_is_flag_not_indeterminate():
    verdict, _ = approval_live_at(None, T1)
    assert verdict == APPROVAL_FLAG


def test_egress_before_the_grant_is_flagged():
    approval = latest_approval_states([_approval_row(granted_at=T2)])["AP-1"]
    verdict, evidence = approval_live_at(approval, T1)
    assert verdict == APPROVAL_FLAG
    assert evidence["reason"] == "egress_preceded_the_grant"


def test_expired_approval_is_flagged():
    approval = latest_approval_states(
        [_approval_row(expires_at=T1)])["AP-1"]
    verdict, evidence = approval_live_at(approval, T2)
    assert verdict == APPROVAL_FLAG
    assert evidence["reason"] == "approval_had_expired"


def test_egress_exactly_at_revocation_time_is_flagged_not_allowed():
    """Boundary: revoked_at is the moment authority stopped, so an
    egress AT that instant is not covered."""
    approval = latest_approval_states([
        _approval_row(), _approval_row(state="revoked", revoked_at=T1)])["AP-1"]
    verdict, _ = approval_live_at(approval, T1)
    assert verdict == APPROVAL_FLAG


def test_egress_exactly_at_grant_time_is_covered():
    approval = latest_approval_states([_approval_row(granted_at=T1)])["AP-1"]
    verdict, _ = approval_live_at(approval, T1)
    assert verdict == APPROVAL_PASS


def test_unparseable_expiry_is_indeterminate_not_ignored():
    approval = latest_approval_states(
        [_approval_row(expires_at="whenever")])["AP-1"]
    verdict, _ = approval_live_at(approval, T2)
    assert verdict == APPROVAL_INDETERMINATE


def test_revocation_row_does_not_erase_the_grant_row():
    """Revocation is a new row, never an edit. Both facts survive."""
    states = latest_approval_states([
        _approval_row(), _approval_row(state="revoked", revoked_at=T2)])
    assert states["AP-1"]["granted_at"] == T0
    assert states["AP-1"]["revoked_at"] == T2


# -- the standing subcontractor check ---------------------------------

def test_authorized_subcontractor_egress_with_live_approval_passes():
    findings = check_subcontractor_approvals([_egress_row()], [_approval_row()])
    assert [f["verdict"] for f in findings] == [APPROVAL_PASS]


def test_refused_egress_is_not_checked_for_approval():
    """Nothing moved, so there is nothing to have been approved."""
    findings = check_subcontractor_approvals(
        [_egress_row(decision=EGRESS_REFUSED, approval_reference="")], [])
    assert findings == []


def test_non_subcontractor_egress_is_not_checked():
    findings = check_subcontractor_approvals(
        [_egress_row(recipient_class=RECIPIENT_CLASS_AFFILIATE)], [])
    assert findings == []


def test_authorized_subcontractor_egress_with_no_approval_is_flagged():
    findings = check_subcontractor_approvals(
        [_egress_row(approval_reference="")], [])
    assert findings[0]["verdict"] == APPROVAL_FLAG


def test_egress_with_no_recorded_time_is_indeterminate_not_pass():
    findings = check_subcontractor_approvals(
        [_egress_row(occurred_at="")], [_approval_row()])
    assert findings[0]["verdict"] == APPROVAL_INDETERMINATE


def test_every_finding_carries_the_screening_disclaimer():
    findings = check_subcontractor_approvals([_egress_row()], [_approval_row()])
    assert all("disclaimer" in f for f in findings)


# -- fail-closed, proven against a real ledger ------------------------

def _registry():
    registry = ContractCassetteRegistry()
    registry.register(_contract())
    return registry


def test_refusal_is_chained_not_merely_returned(test_ledger):
    """The refusal must exist in the chain. A log of successes only is
    the one shape of egress log nobody should trust."""
    decision = request_egress(
        test_ledger, _registry(),
        _request(purpose="model_training"),
        authorized_by="export-service")
    assert decision.decision == EGRESS_REFUSED
    assert decision.current_hash

    rows = test_ledger.get_contract_rows("acme-bank",
                                         record_kinds=("contract_egress",))
    assert len(rows) == 1
    assert rows[0]["data"]["decision"] == EGRESS_REFUSED
    assert REFUSAL_PURPOSE_PROHIBITED in rows[0]["decision_output"]["reasons"]


def test_authorization_is_chained_before_it_is_returned(test_ledger):
    decision = request_egress(test_ledger, _registry(), _request(),
                              authorized_by="export-service")
    assert decision.authorized
    rows = test_ledger.get_contract_rows("acme-bank",
                                         record_kinds=("contract_egress",))
    assert rows[0]["current_hash"] == decision.current_hash


def test_unregistered_counterparty_fails_closed_and_is_still_chained(test_ledger):
    registry = ContractCassetteRegistry()  # nothing registered
    decision = request_egress(test_ledger, registry, _request(),
                              authorized_by="export-service")
    assert decision.decision == EGRESS_REFUSED
    assert REFUSAL_NO_CONTRACT in decision.reasons
    rows = test_ledger.get_contract_rows("acme-bank",
                                         record_kinds=("contract_egress",))
    assert len(rows) == 1


def test_a_ledger_that_cannot_chain_raises_and_never_returns_authorized():
    """The one case with no chained refusal: there is nowhere to chain
    it. It must raise, so a caller cannot read a broken ledger as a
    grant."""

    class _BrokenLedger:
        def get_contract_rows(self, *a, **kw):
            return []

        def record_contract_egress(self, **kw):
            raise RuntimeError("ledger down")

    with pytest.raises(EgressLedgerUnavailable):
        request_egress(_BrokenLedger(), _registry(), _request())


def test_live_approval_end_to_end_authorizes_and_revocation_then_refuses(test_ledger):
    """The full Part 4 loop against a real chain: grant, use, revoke,
    refuse."""
    contract_version = "contract:acme-bank:1.0.0"
    test_ledger.record_contract_approval(
        contract_version=contract_version, counterparty="acme-bank",
        approval_id="AP-1", state="granted", approver="compliance-officer",
        recipient="vendor-x", recipient_class=RECIPIENT_CLASS_SUBCONTRACTOR,
        scope="customer_records", granted_at=T0)

    first = request_egress(
        test_ledger, _registry(),
        _request(recipient_class=RECIPIENT_CLASS_SUBCONTRACTOR,
                 approval_reference="AP-1", occurred_at=T1),
        authorized_by="export-service")
    assert first.authorized

    test_ledger.record_contract_approval(
        contract_version=contract_version, counterparty="acme-bank",
        approval_id="AP-1", state="revoked", approver="compliance-officer",
        recipient="vendor-x", recipient_class=RECIPIENT_CLASS_SUBCONTRACTOR,
        scope="customer_records", granted_at=T0, revoked_at=T1)

    second = request_egress(
        test_ledger, _registry(),
        _request(recipient_class=RECIPIENT_CLASS_SUBCONTRACTOR,
                 approval_reference="AP-1", occurred_at=T2),
        authorized_by="export-service")
    assert second.decision == EGRESS_REFUSED
    assert REFUSAL_APPROVAL_NOT_LIVE in second.reasons


def test_a_revocation_without_a_revocation_time_is_refused_at_write(test_ledger):
    with pytest.raises(ValueError):
        test_ledger.record_contract_approval(
            contract_version="contract:acme-bank:1.0.0",
            counterparty="acme-bank", approval_id="AP-2", state="revoked",
            approver="compliance-officer", recipient="vendor-x",
            recipient_class=RECIPIENT_CLASS_SUBCONTRACTOR,
            scope="customer_records", granted_at=T0)


def test_reference_contract_refuses_model_training_egress():
    """The shipped reference lens, not a test fixture, exercising the
    prohibition it declares."""
    registry = ContractCassetteRegistry()
    registry.register(ReferenceDPAContract())
    contract = registry.for_counterparty("example-counterparty")
    decision, reasons, _ = evaluate_egress(
        contract,
        EgressRequest(counterparty_id="example-counterparty",
                      data_scope="records", recipient="labs-inc",
                      recipient_class=RECIPIENT_CLASS_AFFILIATE,
                      purpose="model_training", occurred_at=T1),
        {})
    assert decision == EGRESS_REFUSED
    assert REFUSAL_PURPOSE_PROHIBITED in reasons


def test_scope_note_rides_in_every_chained_egress_finding(test_ledger):
    """The honest-scope sentence is not just in a docstring: it is in
    the record itself."""
    request_egress(test_ledger, _registry(), _request(),
                   authorized_by="export-service")
    rows = test_ledger.get_contract_rows("acme-bank",
                                         record_kinds=("contract_egress",))
    assert "never called the chokepoint" in rows[0]["decision_output"]["scope_note"]


def test_future_dated_approval_expiry_still_authorizes():
    later = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    approvals = latest_approval_states([_approval_row(expires_at=later)])
    decision, _, _ = evaluate_egress(
        _contract(),
        _request(recipient_class=RECIPIENT_CLASS_SUBCONTRACTOR,
                 approval_reference="AP-1",
                 occurred_at=datetime.now(timezone.utc).isoformat()),
        approvals)
    assert decision == EGRESS_AUTHORIZED
