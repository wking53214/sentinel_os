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

import twin_custody as _tc

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

    rid = f"auth-{uuid.uuid4().hex[:10]}"
    priv, pub = _tc.generate_signing_keypair()
    client = TestClient(build_app(DSN, site="test", pinned_customer_keys={rid: pub}))
    resp = client.post(f"/replica/{rid}/register", json=_tc.make_registration(
        rid, priv, pub, read_token="read-tok"))
    assert resp.status_code == 200, resp.text
    client.replica_id = rid
    client.good = {"Authorization": "Bearer tok"}
    client.bad = {"Authorization": "Bearer wrong-token"}
    client.read_good = {"Authorization": "Bearer read-tok"}
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


READ_ROUTES = ("meta", "head", "entries", "custody-log")


def test_read_routes_refuse_missing_and_wrong_read_token(twin):
    for path in READ_ROUTES:
        url = f"/replica/{twin.replica_id}/{path}"
        assert twin.get(url).status_code == 401, path
        assert twin.get(url, headers={"Authorization": "Bearer nope"}).status_code == 401, path


def test_read_routes_refuse_the_ship_token(twin):
    """The write token must not open the read side: the read token is a
    separate credential the customer holds."""
    for path in READ_ROUTES:
        url = f"/replica/{twin.replica_id}/{path}"
        assert twin.get(url, headers=twin.good).status_code == 401, path


def test_read_routes_accept_the_read_token(twin):
    for path in READ_ROUTES:
        url = f"/replica/{twin.replica_id}/{path}"
        resp = twin.get(url, headers=twin.read_good)
        assert resp.status_code == 200, (path, resp.text)


def test_meta_does_not_return_either_token(twin):
    body = twin.get(f"/replica/{twin.replica_id}/meta", headers=twin.read_good).json()
    assert "ship_token" not in body and "read_token" not in body


def test_replica_registered_without_read_token_refuses_every_read(twin):
    from twin_receiver import build_app

    rid = f"noread-{uuid.uuid4().hex[:10]}"
    priv, pub = _tc.generate_signing_keypair()
    client = TestClient(build_app(DSN, site="test", pinned_customer_keys={rid: pub}))
    resp = client.post(f"/replica/{rid}/register", json=_tc.make_registration(rid, priv, pub))
    assert resp.status_code == 200, resp.text
    for path in READ_ROUTES:
        url = f"/replica/{rid}/{path}"
        assert client.get(url, headers=twin.good).status_code == 401, path
        assert client.get(url, headers=twin.read_good).status_code == 401, path


# ---------------- registration: idempotent for the same credentials -------------
#
# The register route keeps the first record it stores. Repeating the same
# registration is a harmless no-op. A registration that presents different
# credentials for an existing replica must be refused with a conflict, not
# reported as a silent success. Each replica is pinned to a customer key and
# every body is signed by it, so the request reaches the conflict check.

def _pinned_client(rid):
    from twin_receiver import build_app

    priv, pub = _tc.generate_signing_keypair()
    client = TestClient(build_app(DSN, site="test", pinned_customer_keys={rid: pub}))
    return client, priv, pub


def test_repeating_the_same_registration_is_idempotent():
    rid = f"idem-{uuid.uuid4().hex[:10]}"
    client, priv, pub = _pinned_client(rid)
    body = _tc.make_registration(rid, priv, pub, ship_token="tok", read_token="read-tok")
    first = client.post(f"/replica/{rid}/register", json=body)
    again = client.post(f"/replica/{rid}/register", json=body)
    assert first.status_code == 200 and first.json()["created"] is True
    assert again.status_code == 200 and again.json()["created"] is False


def test_a_different_ship_token_for_a_registered_replica_is_a_conflict():
    rid = f"ship-{uuid.uuid4().hex[:10]}"
    client, priv, pub = _pinned_client(rid)
    ok = _tc.make_registration(rid, priv, pub, ship_token="tok", read_token="read-tok")
    assert client.post(f"/replica/{rid}/register", json=ok).status_code == 200
    other = _tc.make_registration(rid, priv, pub, ship_token="someone-else",
                                  read_token="read-tok")
    assert client.post(f"/replica/{rid}/register", json=other).status_code == 409


def test_a_different_read_token_for_a_registered_replica_is_a_conflict():
    rid = f"read-{uuid.uuid4().hex[:10]}"
    client, priv, pub = _pinned_client(rid)
    ok = _tc.make_registration(rid, priv, pub, ship_token="tok", read_token="read-tok")
    assert client.post(f"/replica/{rid}/register", json=ok).status_code == 200
    other = _tc.make_registration(rid, priv, pub, ship_token="tok",
                                  read_token="someone-elses-read")
    assert client.post(f"/replica/{rid}/register", json=other).status_code == 409


def test_adding_a_read_token_to_a_replica_registered_without_one_is_a_conflict():
    rid = f"noread-{uuid.uuid4().hex[:10]}"
    client, priv, pub = _pinned_client(rid)
    ok = _tc.make_registration(rid, priv, pub, ship_token="tok", read_token=None)
    assert client.post(f"/replica/{rid}/register", json=ok).status_code == 200
    other = _tc.make_registration(rid, priv, pub, ship_token="tok", read_token="now-set")
    assert client.post(f"/replica/{rid}/register", json=other).status_code == 409
