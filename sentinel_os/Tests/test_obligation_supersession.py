"""
test_obligation_supersession -- proof suite for obligation_supersession.py,
the ABANDON-on-modification orchestration.

Pure logic (resolve_candidate's status decisions) is tested first with no
I/O at all -- same posture as test_c2_geographic_outcome_equity.py for the
regional-equity check. The I/O wrappers (fetch_replacement_candidates,
fetch_old_decision_rows, fetch_obligations_by_id, sweep, record_outcomes)
are tested against a real ledger and a real twin instance -- same posture
as test_obligation_sweep.py: no mocked governance code.
"""

import uuid

import psycopg2
import pytest
from fastapi.testclient import TestClient

import obligation_supersession
from governance.ledger_postgres import GovernanceDecisionRecord
from obligation_supersession import (
    STATUS_ABANDONED,
    STATUS_ALREADY_ABANDONED,
    STATUS_CONFLICT,
    STATUS_UNRESOLVABLE,
    ReplacementCandidate,
    fetch_obligations_by_id,
    fetch_old_decision_rows,
    fetch_replacement_candidates,
    record_outcomes,
    resolve_candidate,
    sweep,
)
from outcome_v1 import REASON_DECISION_SUPERSEDED

DSN = "host=localhost dbname=iceberg user=iceberg password=iceberg"


def _pg_available() -> bool:
    try:
        psycopg2.connect(DSN + " connect_timeout=2").close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="obligation_supersession tests need PostgreSQL (iceberg/iceberg@localhost)")


# ---------------------------------------------------------------------------
# Pure logic: resolve_candidate. No I/O -- old_decision_row/old_obligation
# handed in directly.
# ---------------------------------------------------------------------------

def _candidate(replaces_hash="oldhash", decided_at=1_700_000_000.0):
    return ReplacementCandidate(
        new_decision_hash="newhash", replaces_hash=replaces_hash,
        domain="lending", decided_at=decided_at)


def _obligation(state, reason_code=None):
    return {"obligation_id": "oldhash:loan_performance", "state": state,
           "reason_code": reason_code}


def test_open_old_obligation_resolves_to_abandoned():
    outcome = resolve_candidate(
        _candidate(), {"outcome_obligation": "loan_performance@24mo"},
        _obligation("OPEN"))
    assert outcome.status == STATUS_ABANDONED
    assert outcome.old_obligation_id == "oldhash:loan_performance"
    assert outcome.decided_at == 1_700_000_000.0


def test_already_abandoned_for_same_reason_is_idempotent_noop():
    outcome = resolve_candidate(
        _candidate(), {"outcome_obligation": "loan_performance@24mo"},
        _obligation("ABANDONED", reason_code=REASON_DECISION_SUPERSEDED))
    assert outcome.status == STATUS_ALREADY_ABANDONED


def test_already_abandoned_for_a_different_reason_is_a_conflict():
    outcome = resolve_candidate(
        _candidate(), {"outcome_obligation": "loan_performance@24mo"},
        _obligation("ABANDONED", reason_code="subject_withdrew"))
    assert outcome.status == STATUS_CONFLICT
    assert "different reason" in outcome.detail


def test_resolved_old_obligation_is_a_conflict_never_abandoned():
    outcome = resolve_candidate(
        _candidate(), {"outcome_obligation": "loan_performance@24mo"},
        _obligation("RESOLVED"))
    assert outcome.status == STATUS_CONFLICT
    assert "already RESOLVED" in outcome.detail


def test_missing_old_decision_row_is_unresolvable():
    """Should be impossible in practice (append_decision's fail-closed
    check), but reported rather than assumed if it ever happens."""
    outcome = resolve_candidate(_candidate(), None, None)
    assert outcome.status == STATUS_UNRESOLVABLE
    assert outcome.old_obligation_id is None


def test_old_decision_with_no_outcome_obligation_is_unresolvable():
    outcome = resolve_candidate(_candidate(), {"outcome_obligation": None}, None)
    assert outcome.status == STATUS_UNRESOLVABLE
    assert "never declared an outcome_obligation" in outcome.detail


def test_old_decision_with_unparseable_declaration_is_unresolvable():
    outcome = resolve_candidate(
        _candidate(), {"outcome_obligation": "not a real declaration"}, None)
    assert outcome.status == STATUS_UNRESOLVABLE
    assert "does not parse" in outcome.detail


def test_no_obligation_derived_yet_is_unresolvable():
    outcome = resolve_candidate(
        _candidate(), {"outcome_obligation": "loan_performance@24mo"}, None)
    assert outcome.status == STATUS_UNRESOLVABLE
    assert "derive the open-obligation set" in outcome.detail
    # old_obligation_id IS derivable even though the twin has nothing yet --
    # the declaration parsed fine, only the twin-side lookup came up empty.
    assert outcome.old_obligation_id == "oldhash:loan_performance"


def test_unrecognized_obligation_state_is_a_conflict_not_a_guess():
    outcome = resolve_candidate(
        _candidate(), {"outcome_obligation": "loan_performance@24mo"},
        _obligation("SOME_FUTURE_STATE"))
    assert outcome.status == STATUS_CONFLICT


def test_decided_at_threads_through_to_the_outcome():
    outcome = resolve_candidate(
        _candidate(decided_at=1_650_000_000.0),
        {"outcome_obligation": "loan_performance@24mo"}, _obligation("OPEN"))
    assert outcome.decided_at == 1_650_000_000.0


# ---------------------------------------------------------------------------
# I/O wrappers, against real infrastructure.
# ---------------------------------------------------------------------------

@pytest.fixture
def twin():
    from twin_receiver import build_app
    client = TestClient(build_app(DSN, site="test"))
    rid = f"supersession-{uuid.uuid4().hex[:10]}"
    resp = client.post(f"/replica/{rid}/register", json={
        "custody_model": "A", "recipient_pub": "x", "recipient_fp": "fp",
        "customer_sign_pub": "y", "ship_token": "tok"})
    assert resp.status_code == 200, resp.text
    client.replica_id = rid
    client.ship = {"Authorization": "Bearer tok"}
    # obligations (GET/derive) and cohort-reviews now check the ship
    # token (AC-13 fix); set it as a client default so this file's
    # existing calls, written before that fix, keep working without
    # touching every call site (same fix test_twin_cohort_reviews.py
    # already applied for the same reason).
    client.headers.update(client.ship)
    return client


_ENVELOPE = {
    "v": 1, "alg": "x25519-aesgcm", "recipient_fp": "fp",
    "epk": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=",
    "nonce": "AgICAgICAgICAgIC",
    "ct": "AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwM=",
}


def _ship_open(twin, primary_id, decision_hash, domain="lending"):
    """Ship a decision to the twin and derive its (still OPEN) obligation."""
    twin.post(f"/replica/{twin.replica_id}/entries", json={
        "primary_id": primary_id,
        "previous_hash": "genesis" if primary_id == 1 else f"h{primary_id - 1}",
        "current_hash": decision_hash, "envelope": _ENVELOPE,
        "outcome_obligation": "loan_performance@24mo",
        "decided_at": 1_700_000_000.0, "domain": domain,
    }, headers=twin.ship)
    twin.post(f"/replica/{twin.replica_id}/obligations/derive")
    obligations = twin.get(f"/replica/{twin.replica_id}/obligations").json()["obligations"]
    return next(o["obligation_id"] for o in obligations if o["decision_hash"] == decision_hash)


def _append_original_and_replacement(test_ledger):
    """One original mortgage decision, plus a second decision that
    declares replaces_hash pointing at it. Returns (old_hash, new_hash)."""
    test_ledger.append_decision(GovernanceDecisionRecord(
        action_type="decision", node="test", cassette_version="lending:mortgage-v1:1.0.0",
        input_data={"income": 60000}, policy_parameters={"min_income": 40000},
        reasoning="qualifies", output={"approved": True},
        outcome_obligation="loan_performance@24mo",
    ))
    old_hash = test_ledger.get_entries(limit=1)[0]["current_hash"]
    test_ledger.append_decision(GovernanceDecisionRecord(
        action_type="decision", node="test", cassette_version="lending:mortgage-v1:1.0.0",
        input_data={"income": 65000}, policy_parameters={"min_income": 40000},
        reasoning="permanent modification", output={"approved": True},
        outcome_obligation="loan_performance@24mo", replaces_hash=old_hash,
    ))
    new_hash = test_ledger.get_entries(limit=1)[0]["current_hash"]
    return old_hash, new_hash


def test_fetch_replacement_candidates_finds_only_decisions_with_replaces_hash(test_ledger):
    old_hash, new_hash = _append_original_and_replacement(test_ledger)
    conn = test_ledger.pool.getconn()
    try:
        candidates = fetch_replacement_candidates(conn)
    finally:
        test_ledger.pool.putconn(conn)
    matching = [c for c in candidates if c.new_decision_hash == new_hash]
    assert len(matching) == 1
    assert matching[0].replaces_hash == old_hash
    assert matching[0].domain == "lending"
    assert matching[0].decided_at is not None
    # the ORIGINAL decision itself never shows up as a candidate -- it has
    # no replaces_hash of its own.
    assert not any(c.new_decision_hash == old_hash for c in candidates)


def test_fetch_replacement_candidates_filters_by_domain(test_ledger):
    old_hash, new_hash = _append_original_and_replacement(test_ledger)
    conn = test_ledger.pool.getconn()
    try:
        matching = fetch_replacement_candidates(conn, domain="lending")
        other = fetch_replacement_candidates(conn, domain="insurance")
    finally:
        test_ledger.pool.putconn(conn)
    assert any(c.new_decision_hash == new_hash for c in matching)
    assert not any(c.new_decision_hash == new_hash for c in other)


def test_fetch_old_decision_rows_looks_up_by_hash(test_ledger):
    old_hash, new_hash = _append_original_and_replacement(test_ledger)
    conn = test_ledger.pool.getconn()
    try:
        rows = fetch_old_decision_rows(conn, {old_hash, "not-a-real-hash"})
    finally:
        test_ledger.pool.putconn(conn)
    assert set(rows) == {old_hash}
    assert rows[old_hash]["outcome_obligation"] == "loan_performance@24mo"


def test_fetch_obligations_by_id_filters_to_requested_ids(twin):
    oid = _ship_open(twin, 1, "h1")
    result = fetch_obligations_by_id(twin, twin.replica_id, {oid, "not-a-real-id"})
    assert set(result) == {oid}
    assert result[oid]["state"] == "OPEN"


def test_sweep_end_to_end_abandons_an_open_old_obligation(test_ledger, twin):
    old_hash, new_hash = _append_original_and_replacement(test_ledger)
    old_obligation_id = _ship_open(twin, 1, old_hash)

    conn = test_ledger.pool.getconn()
    try:
        outcomes = sweep(conn, twin, twin.replica_id, domain="lending")
    finally:
        test_ledger.pool.putconn(conn)

    matching = [o for o in outcomes if o.new_decision_hash == new_hash]
    assert len(matching) == 1
    outcome = matching[0]
    assert outcome.status == STATUS_ABANDONED
    assert outcome.old_obligation_id == old_obligation_id

    results = record_outcomes(twin, twin.replica_id, [outcome], fallback_at=1_700_500_000.0)
    assert len(results) == 1
    assert results[0]["state"] == "ABANDONED"

    obligations = twin.get(f"/replica/{twin.replica_id}/obligations").json()["obligations"]
    updated = next(o for o in obligations if o["obligation_id"] == old_obligation_id)
    assert updated["state"] == "ABANDONED"
    assert updated["reason_code"] == REASON_DECISION_SUPERSEDED


def test_sweep_is_idempotent_on_a_second_run(test_ledger, twin):
    """Re-running the sweep after the old obligation is already abandoned
    reports ALREADY_ABANDONED, and record_outcomes writes nothing more --
    the twin refuses a second transition on a non-OPEN obligation anyway,
    but this proves the orchestration itself never tries."""
    old_hash, new_hash = _append_original_and_replacement(test_ledger)
    _ship_open(twin, 1, old_hash)

    conn = test_ledger.pool.getconn()
    try:
        first = sweep(conn, twin, twin.replica_id, domain="lending")
    finally:
        test_ledger.pool.putconn(conn)
    record_outcomes(twin, twin.replica_id, first, fallback_at=1_700_500_000.0)

    conn = test_ledger.pool.getconn()
    try:
        second = sweep(conn, twin, twin.replica_id, domain="lending")
    finally:
        test_ledger.pool.putconn(conn)
    matching = [o for o in second if o.new_decision_hash == new_hash]
    assert len(matching) == 1
    assert matching[0].status == STATUS_ALREADY_ABANDONED

    results = record_outcomes(twin, twin.replica_id, matching, fallback_at=1_700_500_000.0)
    assert results == []  # nothing written for a no-op status


def test_sweep_reports_conflict_when_old_obligation_already_resolved(test_ledger, twin):
    old_hash, new_hash = _append_original_and_replacement(test_ledger)
    old_obligation_id = _ship_open(twin, 1, old_hash)
    twin.post(f"/replica/{twin.replica_id}/obligations/{old_obligation_id}/transition", json={
        "state": "RESOLVED", "resolved_at": 1_700_100_000.0,
        "resolved_value": {"status": "paid"}, "provenance": "verified",
        "favorable": True,
    })

    conn = test_ledger.pool.getconn()
    try:
        outcomes = sweep(conn, twin, twin.replica_id, domain="lending")
    finally:
        test_ledger.pool.putconn(conn)

    matching = [o for o in outcomes if o.new_decision_hash == new_hash]
    assert len(matching) == 1
    assert matching[0].status == STATUS_CONFLICT

    # a conflict is never written -- confirm record_outcomes leaves the
    # RESOLVED obligation exactly as it was.
    record_outcomes(twin, twin.replica_id, matching, fallback_at=1_700_500_000.0)
    obligations = twin.get(f"/replica/{twin.replica_id}/obligations").json()["obligations"]
    unchanged = next(o for o in obligations if o["obligation_id"] == old_obligation_id)
    assert unchanged["state"] == "RESOLVED"


def test_sweep_reports_unresolvable_when_twin_has_not_derived_the_old_obligation_yet(
        test_ledger, twin):
    """The new decision exists and declares its replacement, but nobody
    has shipped/derived the OLD decision's obligation on the twin yet --
    reported, not silently skipped."""
    old_hash, new_hash = _append_original_and_replacement(test_ledger)

    conn = test_ledger.pool.getconn()
    try:
        outcomes = sweep(conn, twin, twin.replica_id, domain="lending")
    finally:
        test_ledger.pool.putconn(conn)

    matching = [o for o in outcomes if o.new_decision_hash == new_hash]
    assert len(matching) == 1
    assert matching[0].status == STATUS_UNRESOLVABLE


def test_record_outcomes_uses_each_outcomes_own_decided_at(test_ledger, twin):
    """The abandonment is timestamped with the REPLACEMENT decision's own
    decided_at, not the sweep's run time."""
    old_hash, new_hash = _append_original_and_replacement(test_ledger)
    old_obligation_id = _ship_open(twin, 1, old_hash)

    conn = test_ledger.pool.getconn()
    try:
        outcomes = sweep(conn, twin, twin.replica_id, domain="lending")
    finally:
        test_ledger.pool.putconn(conn)
    matching = [o for o in outcomes if o.new_decision_hash == new_hash]
    assert matching[0].decided_at is not None

    # fallback_at deliberately set to something obviously wrong -- if the
    # write used it instead of decided_at, this test would catch that.
    record_outcomes(twin, twin.replica_id, matching, fallback_at=1.0)
    obligations = twin.get(f"/replica/{twin.replica_id}/obligations").json()["obligations"]
    updated = next(o for o in obligations if o["obligation_id"] == old_obligation_id)
    assert updated["resolved_at"] == matching[0].decided_at


# ---------------------------------------------------------------------------
# CLI -- added 2026-08-01. This module had no CLI at all before this (only
# sweep()/record_outcomes() called directly, from tests), which meant it
# could not run in production. New main() follows obligation_sweep.py's
# CLI pattern exactly, including the same ship-token requirement.
# ---------------------------------------------------------------------------


def test_main_requires_ship_token_or_env_var(monkeypatch):
    """Same requirement as obligation_sweep.py's CLI -- every twin route
    this module calls is auth-gated as of AC-13."""
    monkeypatch.delenv("SENTINEL_SHIP_TOKEN", raising=False)
    monkeypatch.setattr(
        "sys.argv",
        ["obligation_supersession.py", "--ledger-dsn", "x",
         "--receiver-url", "y", "--replica-id", "z"])
    with pytest.raises(SystemExit):
        obligation_supersession.main()


def test_main_sends_ship_token_as_bearer_header(monkeypatch):
    """--ship-token reaches the twin client as a real Authorization header.
    No real Postgres/twin needed -- this only proves the CLI's own wiring;
    sweep()/record_outcomes()'s own logic is proven elsewhere in this file
    against real infra."""
    import httpx as _httpx
    import psycopg2 as _psycopg2

    captured = {}

    class _FakeClient:
        def __init__(self, *, base_url, timeout, headers=None):
            captured["headers"] = headers

        def close(self):
            pass

    class _FakeConn:
        def close(self):
            pass

    monkeypatch.setattr(_httpx, "Client", _FakeClient)
    monkeypatch.setattr(_psycopg2, "connect", lambda dsn: _FakeConn())
    monkeypatch.setattr(obligation_supersession, "sweep", lambda *a, **k: [])
    monkeypatch.setattr(
        "sys.argv",
        ["obligation_supersession.py", "--ledger-dsn", "x",
         "--receiver-url", "y", "--replica-id", "z",
         "--ship-token", "secret-tok", "--dry-run"])

    obligation_supersession.main()

    assert captured["headers"] == {"Authorization": "Bearer secret-tok"}
