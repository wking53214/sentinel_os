"""Keyed attestation over a ledger row's ``authorized_by`` claim.

What the mechanism establishes: the row was written by a component holding
``ICEBERG_LEDGER_ATTESTATION_KEY`` and the ``authorized_by`` string has not
changed since -- even against an attacker who also recomputes the unkeyed
SHA-256 chain to stay self-consistent.

What it does NOT establish, and every test name here is careful about: that
the named party holds any authority, or which of several key holders wrote
the row, or anything at all once the key leaks.

Pure-function tests (build a row dict, mutate it, call the verifier) are used
wherever tampering is simulated -- the ledger table carries an
``UPDATE``-blocking immutability trigger, so an in-place edit cannot be done
against a live row. This mirrors how ``twin_custody.deep_verify_row`` is
already tested.

See governance/authorized_by_attestation.py.
"""

import hashlib
import inspect
import json

import pytest

from canonical_fields import OPTIONAL_HASHED_FIELDS
from governance import authorized_by_attestation as att
from governance.authorized_by_attestation import (
    ENV_KEY,
    ENV_REQUIRE,
    SIGNATURE_FIELD,
    STATUS_INVALID,
    STATUS_OK,
    STATUS_UNATTESTED,
    STATUS_UNVERIFIABLE,
    sign_authorized_by,
    verify_authorized_by_signature,
)
from governance.ledger_postgres import GovernanceDecisionRecord, PostgreSQLLedger
import twin_custody as tc

_PG = dict(host="localhost", port=5432, dbname="iceberg",
           user="iceberg", password="iceberg")
_KEY_STR = "unit-test-service-signing-key-not-a-real-secret"
_KEY = _KEY_STR.encode("utf-8")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _governance_params():
    from cassette_schema import validate_cassette
    from cassettes.ivr_cassette import IvrCassette
    return validate_cassette(IvrCassette())


def _record(**kw):
    base = dict(
        action_type="governance_decision", node="billing_queue",
        cassette_version="ivr:standard-ivr:2.0.2",
        input_data={"call_sid": "ATTEST-0001"},
        policy_parameters={"governance_trigger": 2},
        reasoning="AI safety check: risk elevated on repeat contact",
        output={"safe": False, "risk_level": "high"})
    base.update(kw)
    return GovernanceDecisionRecord(**base)


def _last_row(ledger):
    """The most recent ledger row as a dict with the columns these tests
    need (including the new signature column)."""
    cols = ["id", "record_kind", "previous_hash", "current_hash",
            "authorized_by", SIGNATURE_FIELD]
    conn = ledger.pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {', '.join(cols)} FROM ledger_entries "
            f"ORDER BY id DESC LIMIT 1")
        return dict(zip(cols, cur.fetchone()))
    finally:
        ledger.pool.putconn(conn)


def _twin_row(**over):
    """A governance_decision row dict shaped the way a shipped/decrypted
    row reaches twin_custody.recompute_current_hash. current_hash is filled
    in to be self-consistent unless the caller overrides it."""
    row = dict(
        record_kind="governance_decision",
        action_type="governance_decision",
        node="billing_queue",
        cassette_version="ivr:standard-ivr:2.0.2",
        input_data={"call_sid": "ATTEST-0001"},
        policy_parameters={"governance_trigger": 2},
        reason="AI safety check: risk elevated on repeat contact",
        decision_output={"safe": False},
        previous_value=0.0,
        applied_value=0.0,
        data={"parameter_changed": False},
        previous_hash="genesis",
        authorized_by=None,
    )
    row.update(over)
    if "current_hash" not in over:
        row["current_hash"] = tc.recompute_current_hash(row)
    return row


@pytest.fixture
def key_env(monkeypatch):
    monkeypatch.setenv(ENV_KEY, _KEY_STR)
    monkeypatch.delenv(ENV_REQUIRE, raising=False)
    return _KEY


@pytest.fixture
def no_key_env(monkeypatch):
    monkeypatch.delenv(ENV_KEY, raising=False)
    monkeypatch.delenv(ENV_REQUIRE, raising=False)


# ---------------------------------------------------------------------------
# the field is wired into the shared hash contract (D5)
# ---------------------------------------------------------------------------

def test_signature_is_an_optional_hashed_field():
    assert SIGNATURE_FIELD in OPTIONAL_HASHED_FIELDS
    assert SIGNATURE_FIELD in tc.SHIPPED_COLUMNS


# ---------------------------------------------------------------------------
# Required test 1: a valid key produces a signature that verifies
# ---------------------------------------------------------------------------

def test_1_valid_key_produces_a_verifying_signature(test_ledger, key_env):
    assert test_ledger.append_decision(
        _record(authorized_by="harness:production"),
        governance_params=_governance_params())

    row = _last_row(test_ledger)
    assert row["authorized_by"] == "harness:production"
    assert row[SIGNATURE_FIELD] is not None
    assert len(row[SIGNATURE_FIELD]) == 64  # one HMAC-SHA256 hex digest

    status, detail = verify_authorized_by_signature(row, key_env)
    assert status == STATUS_OK, detail
    # and the full-chain verifier agrees, with the key configured
    assert test_ledger.verify_chain()["ok"]


# ---------------------------------------------------------------------------
# Required test 2: altering authorized_by after the fact breaks verification
# -- and does so EVEN when the unkeyed SHA-256 chain was recomputed to stay
#    self-consistent, which is the whole point of the keyed layer.
# ---------------------------------------------------------------------------

def test_2_altering_authorized_by_breaks_attestation_even_after_rehash():
    prev = "0" * 64
    sig = sign_authorized_by("cfo:approvals", prev, "governance_decision", _KEY)
    row = _twin_row(previous_hash=prev, authorized_by="cfo:approvals",
                    **{SIGNATURE_FIELD: sig})

    # honest row: chain verifies AND attestation verifies
    ok, _ = tc.deep_verify_row(row)
    assert ok
    assert verify_authorized_by_signature(row, _KEY)[0] == STATUS_OK

    # attacker swaps the claimed identity and re-derives current_hash so the
    # unkeyed chain is self-consistent again...
    row["authorized_by"] = "intern:nobody"
    row["current_hash"] = tc.recompute_current_hash(row)

    # ...the unkeyed chain check is now fooled -- it has no secret to notice
    ok, _ = tc.deep_verify_row(row)
    assert ok, "precondition: recomputed chain is self-consistent"

    # ...but the keyed attestation still catches it
    status, detail = verify_authorized_by_signature(row, _KEY)
    assert status == STATUS_INVALID, detail


def test_2_wrong_key_is_also_reported_invalid():
    prev = "0" * 64
    sig = sign_authorized_by("cfo:approvals", prev, "governance_decision", _KEY)
    row = _twin_row(previous_hash=prev, authorized_by="cfo:approvals",
                    **{SIGNATURE_FIELD: sig})
    status, _ = verify_authorized_by_signature(row, b"a-different-key")
    assert status == STATUS_INVALID


# ---------------------------------------------------------------------------
# Required test 3: altering the signature breaks CHAIN verification (D5) --
# the signature is inside the hash chain, not sitting beside it.
# ---------------------------------------------------------------------------

def test_3_altering_the_signature_breaks_chain_verification():
    # any 64-hex value stands in for a real signature here; test 3 is about
    # the column being covered by the chain hash, not about HMAC validity.
    sig = "a" * 64
    row = _twin_row(authorized_by="harness:production", **{SIGNATURE_FIELD: sig})
    ok, _ = tc.deep_verify_row(row)
    assert ok

    row[SIGNATURE_FIELD] = "b" * 64
    ok, detail = tc.deep_verify_row(row)
    assert not ok and "hash-mismatch" in (detail or ""), (
        "the signature column must be inside the hash chain (D5) -- a "
        "signature that can be swapped without breaking current_hash could "
        "be stripped from a row undetected")


def test_3_stripping_the_signature_also_breaks_the_chain():
    sig = "a" * 64
    row = _twin_row(authorized_by="harness:production", **{SIGNATURE_FIELD: sig})
    assert tc.deep_verify_row(row)[0]
    row[SIGNATURE_FIELD] = None
    assert not tc.deep_verify_row(row)[0]


# ---------------------------------------------------------------------------
# Required test 4: enforcement off + no key -> row writes, honestly unattested
# ---------------------------------------------------------------------------

def test_4_no_key_writes_cleanly_and_row_is_unattested(test_ledger, no_key_env):
    assert test_ledger.append_decision(
        _record(authorized_by="harness:production"),
        governance_params=_governance_params())

    row = _last_row(test_ledger)
    assert row["authorized_by"] == "harness:production"
    assert row[SIGNATURE_FIELD] is None  # NULL, never a sentinel string (D4)

    status, _ = verify_authorized_by_signature(row, None)
    assert status == STATUS_UNATTESTED

    # the chain is unaffected: an absent optional field is omitted from the
    # canonical form, so the row hashes as it would have pre-attestation
    assert test_ledger.verify_chain()["ok"]


# ---------------------------------------------------------------------------
# Required test 5: enforcement on + no key -> refuse to start
# ---------------------------------------------------------------------------

def test_5_enforcement_on_without_key_refuses_to_start(monkeypatch):
    monkeypatch.setenv(ENV_REQUIRE, "1")
    monkeypatch.delenv(ENV_KEY, raising=False)
    with pytest.raises(RuntimeError, match="ATTESTATION"):
        PostgreSQLLedger(**_PG)


def test_5_enforcement_on_with_key_is_allowed(monkeypatch):
    # sanity companion: the same enforcement flag with a key present must
    # NOT trip the fail-closed guard (it gets past it to real construction).
    monkeypatch.setenv(ENV_REQUIRE, "1")
    monkeypatch.setenv(ENV_KEY, _KEY_STR)
    assert att.enforcement_required() and att.attestation_key() is not None


# ---------------------------------------------------------------------------
# Required test 6: enforcement on -> an authorized_by claim that could not be
# signed is refused, rather than written unattested.
# ---------------------------------------------------------------------------

def test_6_enforcement_on_refuses_an_unsignable_claim(
        test_ledger, monkeypatch):
    monkeypatch.setenv(ENV_KEY, _KEY_STR)
    monkeypatch.setenv(ENV_REQUIRE, "1")
    # simulate signing failing to produce a signature
    monkeypatch.setattr("governance.ledger_postgres.sign_authorized_by",
                        lambda *a, **k: None, raising=False)

    with pytest.raises(RuntimeError, match="attestation enforcement"):
        test_ledger.append_decision(
            _record(authorized_by="harness:production"),
            governance_params=_governance_params())


def test_6_enforcement_on_allows_a_row_with_no_claim(test_ledger, monkeypatch):
    # a row that carries no authorized_by claim has nothing to attest and
    # must still write with enforcement on.
    monkeypatch.setenv(ENV_KEY, _KEY_STR)
    monkeypatch.setenv(ENV_REQUIRE, "1")
    assert test_ledger.append_decision(
        _record(), governance_params=_governance_params())
    assert _last_row(test_ledger)[SIGNATURE_FIELD] is None


# ---------------------------------------------------------------------------
# Required test 7: an existing row with NULL in the new column still passes
# chain verification, unchanged.
# ---------------------------------------------------------------------------

def test_7_null_signature_row_passes_chain_verification(test_ledger, monkeypatch):
    # written with no key -> NULL signature, exactly like every row that
    # predates this column
    monkeypatch.delenv(ENV_KEY, raising=False)
    monkeypatch.delenv(ENV_REQUIRE, raising=False)
    assert test_ledger.append_decision(
        _record(authorized_by="harness:production"),
        governance_params=_governance_params())
    row = _last_row(test_ledger)
    assert row[SIGNATURE_FIELD] is None
    assert test_ledger.verify_chain()["ok"]

    # and it still verifies once a key is later configured: a NULL signature
    # is an unattested row, never a tampered one
    monkeypatch.setenv(ENV_KEY, _KEY_STR)
    assert test_ledger.verify_chain()["ok"]

    twin_view = _twin_row(
        previous_hash=row["previous_hash"],
        current_hash=row["current_hash"],
        authorized_by=row["authorized_by"],
        **{SIGNATURE_FIELD: None},
    )
    # (twin_view is reconstructed, not the real row; the point is that the
    # shared contract omits a NULL optional field on both sides)
    assert SIGNATURE_FIELD not in json.dumps(
        _canonical_of(twin_view), sort_keys=True)


def _canonical_of(row):
    # mirror of twin_custody's governance_decision canonical builder, only
    # far enough to inspect which optional keys were folded in
    from canonical_fields import apply_optional_hashed_fields
    canonical = {
        "record_kind": "governance_decision",
        "action_type": row["action_type"], "node": row["node"],
        "cassette_version": row["cassette_version"],
        "input_data": row["input_data"],
        "policy_parameters": row["policy_parameters"],
        "reasoning": row["reason"], "output": row["decision_output"],
        "previous_value": row["previous_value"],
        "applied_value": row["applied_value"],
        "parameter_changed": False, "previous_hash": row["previous_hash"],
    }
    apply_optional_hashed_fields(canonical, row)
    return canonical


# ---------------------------------------------------------------------------
# Required test 8: verification uses a constant-time comparison
# ---------------------------------------------------------------------------

def test_8_verification_uses_constant_time_comparison():
    src = inspect.getsource(verify_authorized_by_signature)
    assert "hmac.compare_digest" in src, (
        "signature comparison must be constant-time (hmac.compare_digest), "
        "never ==")
    # behavioural: a bad signature is reported, never raised
    row = _twin_row(authorized_by="x", **{SIGNATURE_FIELD: "deadbeef"})
    assert verify_authorized_by_signature(row, _KEY)[0] == STATUS_INVALID


# ---------------------------------------------------------------------------
# extra coverage: honesty of the "no key" verifier state, and cross-record
# -kind drift with a key configured.
# ---------------------------------------------------------------------------

def test_signature_present_but_no_key_is_unverifiable_not_invalid():
    row = _twin_row(authorized_by="x", **{SIGNATURE_FIELD: "a" * 64})
    assert verify_authorized_by_signature(row, None)[0] == STATUS_UNVERIFIABLE


def test_row_with_no_claim_is_absent_not_unattested():
    row = _twin_row(authorized_by=None)
    assert verify_authorized_by_signature(row, _KEY)[0] == "absent"


def test_multiple_record_kinds_verify_with_a_key_set(test_ledger, key_env):
    """Drift guard: with a key configured, write rows through several
    different writer paths and confirm both the primary verifier and the
    twin's independent recompute accept every one -- i.e. the new optional
    field landed on all three recompute sites, not just append_decision."""
    params = _governance_params()
    assert test_ledger.append_decision(
        _record(authorized_by="harness:production"), governance_params=params)
    test_ledger.record_regulatory_cassette_event(
        event="regulatory_cassette_inserted",
        cassette_version="reg:cfpb-lens:1.0.0", cassette_hash="c" * 64,
        cassette_code_hash=None, mode="observer", regulation="CFPB UDAAP",
        authorized_by="auditor:jane-doe")
    test_ledger.record_contract_ingest(
        contract_version="contract:acme:1.0.0", counterparty="acme",
        ingest_id="ing-1", data_scope="call-records",
        received_at="2026-08-27T00:00:00Z", authorized_by="dpo:acme")

    assert test_ledger.verify_chain()["ok"]

    # independent twin recompute over every row
    conn = test_ledger.pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {', '.join(tc.SHIPPED_COLUMNS)} FROM ledger_entries "
            f"ORDER BY id ASC")
        for r in cur.fetchall():
            row = dict(zip(tc.SHIPPED_COLUMNS, r))
            ok, detail = tc.deep_verify_row(row)
            assert ok, f"{row['record_kind']} row failed twin recompute: {detail}"
    finally:
        test_ledger.pool.putconn(conn)
