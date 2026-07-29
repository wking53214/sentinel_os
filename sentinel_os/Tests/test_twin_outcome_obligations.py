"""The twin derives what is owed, rather than being told.

This is the property locked design decision 5 exists for: the twin
computes the open-obligation set from the decision feed it already holds,
using the maturation declaration each decision carried in the clear. The
primary never signs an "obligation opened" event, so it also never gets
the chance to quietly not sign one. Suppressing an obligation costs the
operator the whole decision, and a suppressed decision is already a
MISSING verdict on the existing cross-check.

The store reuses the twin's PATTERN -- append-only, hash-chained, its own
table -- and deliberately not its verdict vocabulary. twin_detector's
PENDING means "inside the transport SLA" and its EXTRA means "wiped from
the primary". Outcome lag is unbounded and is neither of those things.
"""

import base64
import uuid

import psycopg2
import pytest
from fastapi.testclient import TestClient

DSN = "host=localhost dbname=iceberg user=iceberg password=iceberg"

# Structurally valid but meaningless ciphertext. These tests are about the
# obligation store, which by design never opens an envelope -- the twin must
# be able to derive what is owed WITHOUT decryption authority, or independent
# derivation only works in custody model A.
_ENVELOPE = {
    "v": 1, "alg": "x25519-aesgcm", "recipient_fp": "fp",
    "epk": base64.b64encode(b"\x01" * 32).decode(),
    "nonce": base64.b64encode(b"\x02" * 12).decode(),
    "ct": base64.b64encode(b"\x03" * 32).decode(),
}
NOW = 1_700_000_000.0
DAY = 86400.0


def _pg_available() -> bool:
    try:
        psycopg2.connect(DSN + " connect_timeout=2").close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="twin obligation store needs PostgreSQL (iceberg/iceberg@localhost)")


@pytest.fixture
def twin():
    """A registered replica with a private id, so tests never collide."""
    from twin_receiver import build_app

    client = TestClient(build_app(DSN, site="test"))
    rid = f"oblig-{uuid.uuid4().hex[:10]}"
    resp = client.post(f"/replica/{rid}/register", json={
        "custody_model": "A", "recipient_pub": "x", "recipient_fp": "fp",
        "customer_sign_pub": "y", "ship_token": "tok"})
    assert resp.status_code == 200, resp.text
    client.replica_id = rid
    client.ship = {"Authorization": "Bearer tok"}
    return client


def _ship(twin, primary_id, declaration=None, decided_at=NOW, current_hash=None):
    body = {
        "primary_id": primary_id,
        "previous_hash": "genesis" if primary_id == 1 else f"h{primary_id - 1}",
        "current_hash": current_hash or f"h{primary_id}",
        "envelope": _ENVELOPE,
        "outcome_obligation": declaration,
        "decided_at": decided_at,
    }
    resp = twin.post(f"/replica/{twin.replica_id}/entries", json=body,
                     headers=twin.ship)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _derive(twin):
    resp = twin.post(f"/replica/{twin.replica_id}/obligations/derive")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _list(twin, now=None):
    url = f"/replica/{twin.replica_id}/obligations"
    if now is not None:
        url += f"?now={now}"
    resp = twin.get(url)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Derivation, unaided.
# ---------------------------------------------------------------------------

def test_the_twin_derives_an_obligation_from_the_decision_feed_alone(twin):
    _ship(twin, 1, "loan_performance@24mo")
    result = _derive(twin)
    assert result["opened"] == 1
    listing = _list(twin, now=NOW + DAY)
    assert len(listing["obligations"]) == 1
    obligation = listing["obligations"][0]
    assert obligation["state"] == "OPEN"
    assert obligation["reason_code"] == "not_yet_due"
    assert obligation["obligation_kind"] == "loan_performance"
    assert obligation["expected_by"] == NOW + 720 * DAY


def test_a_decision_declaring_nothing_creates_no_obligation(twin):
    """IVR settles at hangup. A derived debt nobody owes is fabrication."""
    _ship(twin, 1, None)
    assert _derive(twin)["opened"] == 0
    assert _list(twin)["obligations"] == []


def test_derivation_is_idempotent(twin):
    _ship(twin, 1, "loan_performance@24mo")
    assert _derive(twin)["opened"] == 1
    second = _derive(twin)
    assert second["opened"] == 0 and second["already_known"] == 1
    assert len(_list(twin)["obligations"]) == 1


def test_an_unreadable_declaration_is_reported_not_silently_skipped(twin):
    _ship(twin, 1, "loan_performance@forever")
    result = _derive(twin)
    assert result["opened"] == 0
    assert len(result["unreadable"]) == 1
    assert result["unreadable"][0]["primary_id"] == 1


def test_a_declaration_with_no_decision_time_cannot_derive_a_horizon(twin):
    """Measuring the horizon from arrival would let a slow shipper stretch
    every deadline by however long it was behind."""
    _ship(twin, 1, "loan_performance@24mo", decided_at=None)
    result = _derive(twin)
    assert result["opened"] == 0
    assert "decided_at" in result["unreadable"][0]["error"]


def test_the_obligation_chain_links(twin):
    _ship(twin, 1, "loan_performance@24mo")
    _ship(twin, 2, "loan_performance@12mo")
    _derive(twin)
    with psycopg2.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT seq, prev_hash, curr_hash FROM obligation_ledger
                           WHERE replica_id=%s ORDER BY seq ASC""", (twin.replica_id,))
            rows = cur.fetchall()
    assert len(rows) == 2
    assert rows[0][1] == "genesis"
    assert rows[1][1] == rows[0][2]


# ---------------------------------------------------------------------------
# Overdue is computed here too, never stored.
# ---------------------------------------------------------------------------

def test_overdue_is_computed_against_the_clock_the_caller_passes(twin):
    _ship(twin, 1, "loan_performance@24mo")
    _derive(twin)
    assert _list(twin, now=NOW + DAY)["obligations"][0]["overdue"] is False
    late = _list(twin, now=NOW + 721 * DAY)
    assert late["obligations"][0]["overdue"] is True
    assert late["summary"]["overdue"] == 1


def test_the_summary_breaks_open_obligations_down_by_reason(twin):
    _ship(twin, 1, "loan_performance@24mo")
    _derive(twin)
    summary = _list(twin, now=NOW + DAY)["summary"]
    assert summary["OPEN"] == 1
    assert summary["open:not_yet_due"] == 1


# ---------------------------------------------------------------------------
# Transitions: append-only, typed, and refused when untyped.
# ---------------------------------------------------------------------------

def _transition(twin, obligation_id, **body):
    return twin.post(
        f"/replica/{twin.replica_id}/obligations/{obligation_id}/transition",
        json=body)


def _one(twin):
    _ship(twin, 1, "loan_performance@24mo")
    _derive(twin)
    return _list(twin)["obligations"][0]["obligation_id"]


def test_a_resolution_is_recorded_with_its_provenance(twin):
    oid = _one(twin)
    resp = _transition(twin, oid, state="RESOLVED", resolved_at=NOW + 730 * DAY,
                       resolved_value={"status": "charged_off"},
                       provenance="verified", favorable=False,
                       signature="sig", signer_pub="pub")
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "RESOLVED"
    assert _list(twin)["summary"]["RESOLVED"] == 1


def test_an_estimated_resolution_without_a_method_is_refused(twin):
    oid = _one(twin)
    resp = _transition(twin, oid, state="RESOLVED", resolved_at=NOW + 730 * DAY,
                       resolved_value={"status": "charged_off"},
                       provenance="estimated", favorable=False)
    assert resp.status_code == 422
    assert any("how it was estimated" in v
               for v in resp.json()["detail"]["violations"])


def test_an_untyped_open_reason_is_refused(twin):
    oid = _one(twin)
    resp = _transition(twin, oid, state="OPEN", reason_code="still looking into it")
    assert resp.status_code == 422


def test_a_matured_obligation_can_move_to_a_more_specific_open_reason(twin):
    oid = _one(twin)
    resp = _transition(twin, oid, state="OPEN",
                       reason_code="data_source_unreachable")
    assert resp.status_code == 200, resp.text
    listing = _list(twin, now=NOW + DAY)
    assert listing["summary"]["open:data_source_unreachable"] == 1


def test_a_closed_obligation_is_not_reopened(twin):
    oid = _one(twin)
    _transition(twin, oid, state="RESOLVED", resolved_at=NOW + 730 * DAY,
                resolved_value={"status": "paid"}, provenance="verified",
                favorable=True)
    resp = _transition(twin, oid, state="OPEN", reason_code="not_yet_due")
    assert resp.status_code == 409


def test_every_transition_is_appended_never_updated(twin):
    """The history of what was owed and when is itself evidence."""
    oid = _one(twin)
    _transition(twin, oid, state="OPEN", reason_code="insufficient_cohort")
    _transition(twin, oid, state="RESOLVED", resolved_at=NOW + 730 * DAY,
                resolved_value={"status": "paid"}, provenance="verified",
                favorable=True)
    with psycopg2.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT state, reason_code FROM obligation_ledger
                           WHERE replica_id=%s AND obligation_id=%s
                           ORDER BY seq ASC""", (twin.replica_id, oid))
            rows = cur.fetchall()
    assert [r[0] for r in rows] == ["OPEN", "OPEN", "RESOLVED"]
    assert rows[1][1] == "insufficient_cohort"


def test_a_transition_on_an_underived_obligation_is_refused(twin):
    resp = _transition(twin, "nothing:here", state="RESOLVED",
                       resolved_at=NOW, resolved_value={"a": 1},
                       provenance="verified")
    assert resp.status_code == 404


def test_the_obligation_table_is_not_replica_entries(twin):
    """Own table, own vocabulary -- decision 4. Overloading the replica
    feed's verdicts would make a regulator read two unrelated conditions
    as one."""
    _ship(twin, 1, "loan_performance@24mo")
    _derive(twin)
    with psycopg2.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM obligation_ledger WHERE replica_id=%s",
                        (twin.replica_id,))
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT count(*) FROM replica_entries WHERE replica_id=%s",
                        (twin.replica_id,))
            assert cur.fetchone()[0] == 1
