"""AC-13: six twin-receiver routes carried no authentication check at all,
sitting directly under the obligation system's differentiating claim -- that
the twin derives obligations independently, so an operator cannot suppress
one by refusing to report it. That guarantee is worth nothing if closing an
obligation needs no credential either. This file proves the fix: each route
below now requires the same Bearer ship-token already enforced on
POST /entries, and rejects a missing or wrong one with 401 before doing
anything else.

Not in scope here: cryptographic verification of the customer signature on
custody-event and obligation-transition payloads. That is a deliberate,
pre-existing design choice (see _append_obligation's docstring -- "signature
optional and verified by whoever reads the chain") shared with custody_log,
not part of AC-13, and not touched by this fix.
"""

import uuid

import psycopg2
import pytest
from fastapi.testclient import TestClient

DSN = "host=localhost dbname=iceberg user=iceberg password=iceberg"

_ENVELOPE = {
    "v": 1, "alg": "x25519-aesgcm", "recipient_fp": "fp",
    "epk": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=",
    "nonce": "AgICAgICAgICAgIC",
    "ct": "AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwM=",
}
NOW = 1_700_000_000.0


def _pg_available() -> bool:
    try:
        psycopg2.connect(DSN + " connect_timeout=2").close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="twin receiver auth needs PostgreSQL (iceberg/iceberg@localhost)")


@pytest.fixture
def twin():
    """A registered replica with NO default Authorization header -- tests
    in this file attach the header explicitly per-call so the presence or
    absence of auth is always visible at the call site, not hidden in a
    fixture default the way the other twin test files now do it."""
    from twin_receiver import build_app

    client = TestClient(build_app(DSN, site="test"))
    rid = f"auth-{uuid.uuid4().hex[:10]}"
    resp = client.post(f"/replica/{rid}/register", json={
        "custody_model": "A", "recipient_pub": "x", "recipient_fp": "fp",
        "customer_sign_pub": "y", "ship_token": "tok"})
    assert resp.status_code == 200, resp.text
    client.replica_id = rid
    client.good = {"Authorization": "Bearer tok"}
    client.bad = {"Authorization": "Bearer wrong-token"}
    return client


def _custody_event_body():
    return {"event": "creation", "detail": {"note": "test"},
            "actor": "tester", "signature": "sig", "signer_pub": "pub"}


def _cohort_review_body():
    return {"domain": "lending", "obligation_kind": "loan_performance",
            "total_resolved": 1, "dimension_4_cohort_size": 1,
            "dimension_5_cohort_size": 0, "dimension_6_cohort_size": 0,
            "dimension_4_findings": [], "dimension_5_findings": [],
            "dimension_6_findings": [], "skipped": [], "swept_at": NOW}


def _ship_and_derive(twin):
    """Ship one decision that declares an obligation, then derive it, so
    transition tests have a real obligation_id to act on."""
    twin.post(f"/replica/{twin.replica_id}/entries", json={
        "primary_id": 1, "previous_hash": "genesis", "current_hash": "h1",
        "envelope": _ENVELOPE, "outcome_obligation": "loan_performance@24mo",
        "decided_at": NOW, "domain": "lending"}, headers=twin.good)
    resp = twin.post(f"/replica/{twin.replica_id}/obligations/derive",
                     headers=twin.good)
    assert resp.status_code == 200, resp.text
    obligations = twin.get(f"/replica/{twin.replica_id}/obligations",
                           headers=twin.good).json()["obligations"]
    return obligations[0]["obligation_id"]


# ---------------------------------------------------------------------------
# Each route: no header -> 401, wrong token -> 401, right token -> succeeds.
# ---------------------------------------------------------------------------

def test_custody_event_requires_token(twin):
    url = f"/replica/{twin.replica_id}/custody-event"
    assert twin.post(url, json=_custody_event_body()).status_code == 401
    assert twin.post(url, json=_custody_event_body(),
                     headers=twin.bad).status_code == 401
    resp = twin.post(url, json=_custody_event_body(), headers=twin.good)
    assert resp.status_code == 200, resp.text


def test_derive_obligations_requires_token(twin):
    url = f"/replica/{twin.replica_id}/obligations/derive"
    assert twin.post(url).status_code == 401
    assert twin.post(url, headers=twin.bad).status_code == 401
    resp = twin.post(url, headers=twin.good)
    assert resp.status_code == 200, resp.text


def test_list_obligations_requires_token(twin):
    url = f"/replica/{twin.replica_id}/obligations"
    assert twin.get(url).status_code == 401
    assert twin.get(url, headers=twin.bad).status_code == 401
    resp = twin.get(url, headers=twin.good)
    assert resp.status_code == 200, resp.text


def test_transition_obligation_requires_token(twin):
    obligation_id = _ship_and_derive(twin)
    url = f"/replica/{twin.replica_id}/obligations/{obligation_id}/transition"
    body = {"state": "RESOLVED", "resolved_at": NOW,
            "resolved_value": {"outcome": "paid_as_agreed"},
            "provenance": "verified"}
    # Auth is checked before the obligation lookup, so a garbage id still
    # proves the point without needing a second real obligation.
    bogus_url = f"/replica/{twin.replica_id}/obligations/does-not-exist/transition"
    assert twin.post(bogus_url, json=body).status_code == 401
    assert twin.post(bogus_url, json=body, headers=twin.bad).status_code == 401
    resp = twin.post(url, json=body, headers=twin.good)
    assert resp.status_code == 200, resp.text


def test_store_cohort_review_requires_token(twin):
    url = f"/replica/{twin.replica_id}/cohort-reviews"
    assert twin.post(url, json=_cohort_review_body()).status_code == 401
    assert twin.post(url, json=_cohort_review_body(),
                     headers=twin.bad).status_code == 401
    resp = twin.post(url, json=_cohort_review_body(), headers=twin.good)
    assert resp.status_code == 200, resp.text


def test_list_cohort_reviews_requires_token(twin):
    twin.post(f"/replica/{twin.replica_id}/cohort-reviews",
             json=_cohort_review_body(), headers=twin.good)
    url = f"/replica/{twin.replica_id}/cohort-reviews"
    assert twin.get(url).status_code == 401
    assert twin.get(url, headers=twin.bad).status_code == 401
    resp = twin.get(url, headers=twin.good)
    assert resp.status_code == 200, resp.text


def test_entries_still_enforces_token_unchanged(twin):
    """Not a new behavior -- confirms the pre-existing check on the one
    route that already had auth was not disturbed by this fix."""
    url = f"/replica/{twin.replica_id}/entries"
    body = {"primary_id": 1, "previous_hash": "genesis", "current_hash": "h1",
            "envelope": _ENVELOPE}
    assert twin.post(url, json=body).status_code == 401
    resp = twin.post(url, json=body, headers=twin.good)
    assert resp.status_code == 200, resp.text
