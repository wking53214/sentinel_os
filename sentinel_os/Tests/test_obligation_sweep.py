"""Cohort assembly: the return path C2 dimensions 4 and 5 have been
structurally waiting for since to_cohort_decision existed with nothing
scheduling the sweep that calls it.

Pure logic (bucketing, assembly, review packaging) is tested here with
no I/O at all. The I/O wrappers (fetch_resolved_obligations,
fetch_group_distributions, fetch_decision_materials, sweep) are tested
against a real twin instance, a real sealed channel, and a real ledger
-- same posture as the rest of this suite: no mocked governance code.

check_statistical_outcome_equity and check_correlation_based_proxy_detection's
own statistical logic (four-fifths math, Pearson correlation, the
80%-threshold) is already proven in test_c2_statistical_outcome_equity.py
and its sibling files. This file is about whether obligation_sweep wires
resolved obligations into those checks correctly -- not re-proving the
statistics.
"""

import base64
import uuid

import psycopg2
import pytest
from fastapi.testclient import TestClient

from governance.ledger_postgres import GovernanceDecisionRecord
from obligation_sweep import (
    assemble_cohort,
    bucket_resolved_obligations,
    cohort_key,
    fetch_decision_materials,
    fetch_group_distributions,
    fetch_resolved_obligations,
    review_cohort,
    subject_of,
    sweep,
)
from regulatory_cassettes.cfpb_reg_b import CFPB_REG_B_PROFILE
from sealed_demographic_channel import SOURCE_BISG_ESTIMATED, SealedDemographicChannel

DSN = "host=localhost dbname=iceberg user=iceberg password=iceberg"
PG_KWARGS = dict(host="localhost", dbname="iceberg", user="iceberg", password="iceberg")


def _pg_available() -> bool:
    try:
        psycopg2.connect(DSN + " connect_timeout=2").close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="obligation_sweep tests need PostgreSQL (iceberg/iceberg@localhost)")


# ---------------------------------------------------------------------------
# Pure logic: cohort_key, bucket_resolved_obligations, subject_of.
# ---------------------------------------------------------------------------

def _resolved(obligation_id, domain, obligation_kind, decision_hash,
             favorable=True, subject_id=None, resolved_value=None):
    return {
        "obligation_id": obligation_id, "domain": domain,
        "obligation_kind": obligation_kind, "decision_hash": decision_hash,
        "state": "RESOLVED", "favorable": favorable, "subject_id": subject_id,
        "resolved_value": resolved_value or {"status": "x"},
        "resolved_at": 1_700_000_000.0, "resolution_provenance": "verified",
        "resolution_method": None, "opened_at": 1_600_000_000.0,
        "expected_by": 1_700_000_000.0, "reason_code": None, "detail": {},
    }


def test_cohort_key_groups_by_domain_and_obligation_kind():
    assert cohort_key(_resolved("o1", "lending", "loan_performance", "h1")) == \
        ("lending", "loan_performance")


def test_cohort_key_falls_back_to_unknown_when_absent():
    assert cohort_key({}) == ("unknown", "unknown")


def test_bucket_groups_two_domains_sharing_an_obligation_kind_separately():
    obligations = [
        _resolved("o1", "lending", "loan_performance", "h1"),
        _resolved("o2", "insurance", "loan_performance", "h2"),
        _resolved("o3", "lending", "loan_performance", "h3"),
    ]
    buckets = bucket_resolved_obligations(obligations)
    assert set(buckets) == {("lending", "loan_performance"),
                            ("insurance", "loan_performance")}
    assert len(buckets[("lending", "loan_performance")]) == 2
    assert len(buckets[("insurance", "loan_performance")]) == 1


def test_bucket_refuses_a_non_resolved_obligation():
    obligation = _resolved("o1", "lending", "loan_performance", "h1")
    obligation["state"] = "OPEN"
    with pytest.raises(ValueError, match="non-RESOLVED"):
        bucket_resolved_obligations([obligation])


def test_subject_of_prefers_subject_id_when_present():
    obligation = _resolved("o1", "lending", "loan_performance", "h1",
                           subject_id="applicant-42")
    assert subject_of(obligation) == "applicant-42"


def test_subject_of_falls_back_to_decision_hash():
    obligation = _resolved("o1", "lending", "loan_performance", "h1")
    assert subject_of(obligation) == "h1"


# ---------------------------------------------------------------------------
# Pure logic: assemble_cohort. No I/O -- distributions/materials are
# handed in directly.
# ---------------------------------------------------------------------------

def test_assemble_cohort_builds_both_dimension_cohorts_when_everything_is_on_file():
    from regulatory_cassette_interface import DecisionMaterial

    obligations = [_resolved("o1", "lending", "loan_performance", "h1")]
    distributions = {"h1": {"white": 0.6, "black": 0.4}}
    materials = {"h1": DecisionMaterial(
        subject_id="h1", domain="lending", reasons=(), input_fields={"income": 50000},
        mismatched_fields=(), outcome={"approved": True}, source="ledger")}
    assembled = assemble_cohort("lending", "loan_performance", obligations,
                                distributions, materials)
    assert len(assembled.dimension_4_cohort) == 1
    assert len(assembled.dimension_5_cohort) == 1
    assert assembled.skipped == []
    assert assembled.total_resolved == 1


def test_assemble_cohort_skips_an_obligation_with_no_group_distribution():
    obligations = [_resolved("o1", "lending", "loan_performance", "h1")]
    assembled = assemble_cohort("lending", "loan_performance", obligations, {}, {})
    assert assembled.dimension_4_cohort == []
    assert assembled.dimension_5_cohort == []
    assert len(assembled.skipped) == 1
    assert "no protected-characteristic estimate" in assembled.skipped[0].reason


def test_assemble_cohort_skips_a_genuinely_ambiguous_resolution_for_dimension_4_only():
    """favorable=None is a real, legitimate resolution (to_cohort_decision
    refuses to coerce it) -- it should drop out of dimension 4 but can
    still enter dimension 5, which only needs input_fields, not a
    favorable call."""
    from regulatory_cassette_interface import DecisionMaterial

    obligations = [_resolved("o1", "lending", "loan_performance", "h1", favorable=None)]
    distributions = {"h1": {"white": 0.6, "black": 0.4}}
    materials = {"h1": DecisionMaterial(
        subject_id="h1", domain="lending", reasons=(), input_fields={"income": 50000},
        mismatched_fields=(), outcome={}, source="ledger")}
    assembled = assemble_cohort("lending", "loan_performance", obligations,
                                distributions, materials)
    assert assembled.dimension_4_cohort == []
    assert len(assembled.dimension_5_cohort) == 1
    assert len(assembled.skipped) == 1
    assert "dimension 4" in assembled.skipped[0].reason


def test_assemble_cohort_admits_dimension_4_without_dimension_5():
    """The reverse of the above: a real favorable call with a
    distribution, but no decision material -- dimension 4 gets it,
    dimension 5 reports why it doesn't."""
    obligations = [_resolved("o1", "lending", "loan_performance", "h1")]
    distributions = {"h1": {"white": 0.6, "black": 0.4}}
    assembled = assemble_cohort("lending", "loan_performance", obligations,
                                distributions, {})
    assert len(assembled.dimension_4_cohort) == 1
    assert assembled.dimension_5_cohort == []
    assert len(assembled.skipped) == 1
    assert "dimension 5" in assembled.skipped[0].reason


def test_assemble_cohort_uses_subject_id_to_look_up_distribution_when_present():
    obligations = [_resolved("o1", "lending", "loan_performance", "h1",
                             subject_id="applicant-42")]
    distributions = {"applicant-42": {"white": 0.6, "black": 0.4}}
    assembled = assemble_cohort("lending", "loan_performance", obligations,
                                distributions, {})
    assert len(assembled.dimension_4_cohort) == 1
    assert assembled.dimension_4_cohort[0].subject_id == "applicant-42"


# ---------------------------------------------------------------------------
# Pure logic: review_cohort. Real check functions, small (sub-30) cohorts
# to prove INDETERMINATE wiring -- the checks' own statistical logic is
# proven elsewhere.
# ---------------------------------------------------------------------------

def test_review_cohort_reports_indeterminate_below_minimum_size():
    obligations = [_resolved("o1", "lending", "loan_performance", "h1")]
    distributions = {"h1": {"white": 0.6, "black": 0.4}}
    assembled = assemble_cohort("lending", "loan_performance", obligations,
                                distributions, {})
    review = review_cohort(assembled, CFPB_REG_B_PROFILE)
    assert review.dimension_4_cohort_size == 1
    assert len(review.dimension_4_findings) == 1
    assert review.dimension_4_findings[0]["classification"] == \
        "indeterminate_insufficient_cohort"
    # dimension 5 cohort is empty (no decision material) -- its own
    # size gate reports indeterminate for the same reason, size 0.
    assert review.dimension_5_cohort_size == 0
    assert len(review.dimension_5_findings) == 1


def test_review_cohort_as_dict_is_json_safe_and_carries_skips():
    obligations = [_resolved("o1", "lending", "loan_performance", "h1")]
    assembled = assemble_cohort("lending", "loan_performance", obligations, {}, {})
    review = review_cohort(assembled, CFPB_REG_B_PROFILE)
    payload = review.as_dict()
    assert payload["domain"] == "lending"
    assert payload["obligation_kind"] == "loan_performance"
    assert payload["total_resolved"] == 1
    assert payload["skipped"] == [
        {"obligation_id": "o1",
         "reason": "no protected-characteristic estimate on file for this subject"}]
    import json
    json.dumps(payload)  # must not raise


# ---------------------------------------------------------------------------
# I/O wrappers, against real infrastructure.
# ---------------------------------------------------------------------------

_ENVELOPE = {
    "v": 1, "alg": "x25519-aesgcm", "recipient_fp": "fp",
    "epk": base64.b64encode(b"\x01" * 32).decode(),
    "nonce": base64.b64encode(b"\x02" * 12).decode(),
    "ct": base64.b64encode(b"\x03" * 32).decode(),
}


@pytest.fixture
def twin():
    from twin_receiver import build_app
    client = TestClient(build_app(DSN, site="test"))
    rid = f"sweep-{uuid.uuid4().hex[:10]}"
    resp = client.post(f"/replica/{rid}/register", json={
        "custody_model": "A", "recipient_pub": "x", "recipient_fp": "fp",
        "customer_sign_pub": "y", "ship_token": "tok"})
    assert resp.status_code == 200, resp.text
    client.replica_id = rid
    client.ship = {"Authorization": "Bearer tok"}
    return client


@pytest.fixture
def channel():
    return SealedDemographicChannel(
        **PG_KWARGS, runtime_user="sealed_channel_writer",
        runtime_password="sweep_test_pw")


def _ship_and_resolve(twin, primary_id, domain, decision_hash, favorable=True):
    twin.post(f"/replica/{twin.replica_id}/entries", json={
        "primary_id": primary_id,
        "previous_hash": "genesis" if primary_id == 1 else f"h{primary_id - 1}",
        "current_hash": decision_hash, "envelope": _ENVELOPE,
        "outcome_obligation": "loan_performance@24mo",
        "decided_at": 1_700_000_000.0, "domain": domain,
    }, headers=twin.ship)
    twin.post(f"/replica/{twin.replica_id}/obligations/derive")
    obligations = twin.get(f"/replica/{twin.replica_id}/obligations").json()["obligations"]
    oid = next(o["obligation_id"] for o in obligations if o["decision_hash"] == decision_hash)
    twin.post(f"/replica/{twin.replica_id}/obligations/{oid}/transition", json={
        "state": "RESOLVED", "resolved_at": 1_700_100_000.0,
        "resolved_value": {"status": "paid"}, "provenance": "verified",
        "favorable": favorable,
    })
    return oid


def test_fetch_resolved_obligations_filters_to_resolved_only(twin):
    _ship_and_resolve(twin, 1, "lending", "h1")
    twin.post(f"/replica/{twin.replica_id}/entries", json={
        "primary_id": 2, "previous_hash": "h1", "current_hash": "h2",
        "envelope": _ENVELOPE, "outcome_obligation": "loan_performance@24mo",
        "decided_at": 1_700_000_000.0, "domain": "lending",
    }, headers=twin.ship)
    twin.post(f"/replica/{twin.replica_id}/obligations/derive")  # h2 stays OPEN
    resolved = fetch_resolved_obligations(twin, twin.replica_id)
    assert len(resolved) == 1
    assert resolved[0]["decision_hash"] == "h1"


def test_fetch_group_distributions_looks_up_each_subject(channel):
    channel.record_estimate("h1", SOURCE_BISG_ESTIMATED, {"white": 0.7, "black": 0.3})
    result = fetch_group_distributions(channel, {"h1", "h2"})
    assert result == {"h1": {"white": 0.7, "black": 0.3}}  # h2 absent, not an error


def test_fetch_decision_materials_looks_up_by_hash(test_ledger):
    test_ledger.append_decision(GovernanceDecisionRecord(
        action_type="decision", node="test", cassette_version="lending:test_cassette:1.0.0",
        input_data={"income": 60000}, policy_parameters={"min_income": 40000}, reasoning="qualifies",
        output={"approved": True},
    ))
    entries = test_ledger.get_entries(limit=1)
    decision_hash = entries[0]["current_hash"]
    conn = test_ledger.pool.getconn()
    try:
        materials = fetch_decision_materials(conn, {decision_hash, "not-a-real-hash"})
    finally:
        test_ledger.pool.putconn(conn)
    assert set(materials) == {decision_hash}
    assert materials[decision_hash].input_fields == {"income": 60000}


def test_sweep_end_to_end_produces_one_review_per_cohort(twin, channel, test_ledger):
    """Full wiring: ship two decisions in two different domains, resolve
    both obligations, record a demographic estimate for one, and confirm
    sweep() produces one CohortEquityReview per (domain, obligation_kind)
    bucket -- proving fetch -> bucket -> assemble -> review runs
    end-to-end without needing a 30-row cohort to prove it."""
    test_ledger.append_decision(GovernanceDecisionRecord(
        action_type="decision", node="test", cassette_version="lending:test_cassette:1.0.0",
        input_data={"income": 60000}, policy_parameters={"min_income": 40000}, reasoning="qualifies",
        output={"approved": True}, outcome_obligation="loan_performance@24mo",
    ))
    lending_hash = test_ledger.get_entries(limit=1)[0]["current_hash"]
    _ship_and_resolve(twin, 1, "lending", lending_hash, favorable=True)

    test_ledger.append_decision(GovernanceDecisionRecord(
        action_type="decision", node="test", cassette_version="insurance:test_cassette:1.0.0",
        input_data={"claims": 2}, policy_parameters={"max_claims": 5}, reasoning="qualifies",
        output={"approved": False}, outcome_obligation="loan_performance@24mo",
    ))
    insurance_hash = test_ledger.get_entries(limit=1)[0]["current_hash"]
    _ship_and_resolve(twin, 2, "insurance", insurance_hash, favorable=False)

    channel.record_estimate(lending_hash, SOURCE_BISG_ESTIMATED, {"white": 0.7, "black": 0.3})
    channel.record_estimate(insurance_hash, SOURCE_BISG_ESTIMATED, {"white": 0.5, "black": 0.5})

    conn = test_ledger.pool.getconn()
    try:
        reviews = sweep(twin, twin.replica_id, conn, channel, CFPB_REG_B_PROFILE)
    finally:
        test_ledger.pool.putconn(conn)

    assert len(reviews) == 2
    keys = {(r.domain, r.obligation_kind) for r in reviews}
    assert keys == {("lending", "loan_performance"), ("insurance", "loan_performance")}
    for review in reviews:
        assert review.total_resolved == 1
        assert review.dimension_4_cohort_size == 1
        assert review.dimension_5_cohort_size == 1
        assert review.skipped == []
