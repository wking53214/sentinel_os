"""cohort_review_ledger: the twin's tamper-evident store for
obligation_sweep.py's output. The twin never computes a
cohort_equity_review -- see obligation_sweep's module docstring -- it
only receives and chain-links what the primary already computed, same
posture as every other record kind here.
"""

import uuid

import psycopg2
import pytest
from fastapi.testclient import TestClient

DSN = "host=localhost dbname=iceberg user=iceberg password=iceberg"


def _pg_available() -> bool:
    try:
        psycopg2.connect(DSN + " connect_timeout=2").close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="cohort review store needs PostgreSQL (iceberg/iceberg@localhost)")


@pytest.fixture
def twin():
    from twin_receiver import build_app

    client = TestClient(build_app(DSN, site="test"))
    rid = f"review-{uuid.uuid4().hex[:10]}"
    resp = client.post(f"/replica/{rid}/register", json={
        "custody_model": "A", "recipient_pub": "x", "recipient_fp": "fp",
        "customer_sign_pub": "y", "ship_token": "tok"})
    assert resp.status_code == 200, resp.text
    client.replica_id = rid
    return client


def _review(domain="lending", obligation_kind="loan_performance",
           dimension_4_findings=None, swept_at=1_700_000_000.0):
    return {
        "domain": domain, "obligation_kind": obligation_kind,
        "total_resolved": 1, "dimension_4_cohort_size": 1,
        "dimension_5_cohort_size": 0,
        "dimension_4_findings": dimension_4_findings or [
            {"check": "statistical_outcome_equity", "subject_id": "cohort:1",
             "regulation": "reg_b", "action": "flag",
             "classification": "indeterminate_insufficient_cohort",
             "score": 0.0, "evidence": {}}],
        "dimension_5_findings": [],
        "skipped": [], "swept_at": swept_at,
    }


def test_a_review_is_stored_and_returns_seq_one(twin):
    resp = twin.post(f"/replica/{twin.replica_id}/cohort-reviews", json=_review())
    assert resp.status_code == 200, resp.text
    assert resp.json()["seq"] == 1
    assert resp.json()["curr_hash"]


def test_a_missing_required_field_is_rejected(twin):
    body = _review()
    del body["dimension_4_findings"]
    resp = twin.post(f"/replica/{twin.replica_id}/cohort-reviews", json=body)
    assert resp.status_code == 422
    assert "dimension_4_findings" in resp.json()["detail"]


def test_storing_against_an_unregistered_replica_is_refused(twin):
    resp = twin.post("/replica/never-registered/cohort-reviews", json=_review())
    assert resp.status_code == 404


def test_reviews_chain_by_sequence(twin):
    twin.post(f"/replica/{twin.replica_id}/cohort-reviews", json=_review(domain="lending"))
    second = twin.post(f"/replica/{twin.replica_id}/cohort-reviews",
                       json=_review(domain="insurance"))
    assert second.json()["seq"] == 2
    with psycopg2.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT seq, prev_hash, curr_hash FROM cohort_review_ledger
                           WHERE replica_id=%s ORDER BY seq ASC""", (twin.replica_id,))
            rows = cur.fetchall()
    assert rows[0][1] == "genesis"
    assert rows[1][1] == rows[0][2]  # second row's prev_hash == first row's curr_hash


def test_two_reviews_that_differ_only_in_findings_hash_differently(twin):
    resp_a = twin.post(f"/replica/{twin.replica_id}/cohort-reviews", json=_review(
        dimension_4_findings=[{"check": "x", "subject_id": "s", "regulation": "r",
                               "action": "flag", "classification": "pass",
                               "score": 0.0, "evidence": {}}]))
    resp_b = twin.post(f"/replica/{twin.replica_id}/cohort-reviews", json=_review(
        dimension_4_findings=[{"check": "x", "subject_id": "s", "regulation": "r",
                               "action": "flag", "classification": "flag",
                               "score": 1.0, "evidence": {}}]))
    assert resp_a.json()["curr_hash"] != resp_b.json()["curr_hash"]


def test_reviews_are_never_updated_in_place(twin):
    """Append-only: a re-sweep of the same cohort is a new row, not an
    overwrite of the last one, so the history of what a cohort looked
    like at each sweep survives."""
    twin.post(f"/replica/{twin.replica_id}/cohort-reviews", json=_review())
    twin.post(f"/replica/{twin.replica_id}/cohort-reviews", json=_review())
    listed = twin.get(f"/replica/{twin.replica_id}/cohort-reviews").json()["reviews"]
    assert len(listed) == 2
    assert listed[0]["seq"] == 1
    assert listed[1]["seq"] == 2


def test_listing_filters_by_domain(twin):
    twin.post(f"/replica/{twin.replica_id}/cohort-reviews", json=_review(domain="lending"))
    twin.post(f"/replica/{twin.replica_id}/cohort-reviews", json=_review(domain="insurance"))
    listed = twin.get(f"/replica/{twin.replica_id}/cohort-reviews",
                      params={"domain": "lending"}).json()["reviews"]
    assert len(listed) == 1
    assert listed[0]["domain"] == "lending"


def test_listing_filters_by_obligation_kind(twin):
    twin.post(f"/replica/{twin.replica_id}/cohort-reviews",
             json=_review(obligation_kind="loan_performance"))
    twin.post(f"/replica/{twin.replica_id}/cohort-reviews",
             json=_review(obligation_kind="coverage_performance"))
    listed = twin.get(f"/replica/{twin.replica_id}/cohort-reviews",
                      params={"obligation_kind": "coverage_performance"}).json()["reviews"]
    assert len(listed) == 1
    assert listed[0]["obligation_kind"] == "coverage_performance"


def test_the_cohort_review_table_is_not_obligation_ledger(twin):
    """A cohort review is not an obligation and doesn't share its
    state machine -- posting a review must not touch obligation_ledger
    at all."""
    twin.post(f"/replica/{twin.replica_id}/cohort-reviews", json=_review())
    obligations = twin.get(f"/replica/{twin.replica_id}/obligations").json()["obligations"]
    assert obligations == []


def test_record_reviews_wires_obligation_sweep_output_into_the_twin(twin):
    """obligation_sweep.record_reviews posts a CohortEquityReview
    straight through -- the shape review.as_dict() produces must match
    what this endpoint actually requires."""
    from obligation_sweep import CohortEquityReview, record_reviews

    review = CohortEquityReview(
        domain="lending", obligation_kind="loan_performance", total_resolved=5,
        dimension_4_cohort_size=5, dimension_5_cohort_size=3,
        dimension_4_findings=[], dimension_5_findings=[], skipped=[])
    results = record_reviews(twin, twin.replica_id, [review], swept_at=1_700_000_000.0)
    assert len(results) == 1
    assert results[0]["seq"] == 1
    listed = twin.get(f"/replica/{twin.replica_id}/cohort-reviews").json()["reviews"]
    assert listed[0]["total_resolved"] == 5
    assert listed[0]["dimension_5_cohort_size"] == 3
