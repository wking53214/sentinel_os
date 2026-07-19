"""Custody-layer tests (DAP-2/DAP-3). Every negative test is a real refused
operation, not a mocked one; the recompute tests run against real rows the
live stack wrote to the primary ledger."""

import base64
import json
import os

import psycopg2
import psycopg2.extras
import pytest

import twin_custody as tc
from twin_receiver import _structurally_valid_envelope

AAD = {"replica_id": "r-unit", "primary_id": 7, "current_hash": "h" * 64}


@pytest.fixture(scope="module")
def keypair():
    return tc.generate_recipient_keypair()


def test_seal_open_roundtrip(keypair):
    priv, pub = keypair
    env = tc.seal(b"witness me", pub, AAD)
    assert env["alg"] == tc.ENVELOPE_ALG and env["v"] == 1
    assert tc.open_envelope(env, priv, AAD) == b"witness me"


def test_wrong_key_refused(keypair):
    _, pub = keypair
    other_priv, _ = tc.generate_recipient_keypair()
    env = tc.seal(b"secret", pub, AAD)
    with pytest.raises(tc.CustodyError):
        tc.open_envelope(env, other_priv, AAD)


def test_public_key_cannot_stand_in_for_private(keypair):
    """The exact 'Sentinel tries with what it holds' shape: the recipient
    PUBLIC key is the only key material Sentinel ever sees; using it as if it
    were the private half must fail."""
    priv, pub = keypair
    env = tc.seal(b"secret", pub, AAD)
    with pytest.raises(tc.CustodyError):
        tc.open_envelope(env, pub, AAD)  # pub is 32 bytes too; still must fail


def test_slot_binding_prevents_envelope_relocation(keypair):
    priv, pub = keypair
    env = tc.seal(b"entry-7", pub, AAD)
    for wrong in (
        {**AAD, "primary_id": 8},
        {**AAD, "replica_id": "r-other"},
        {**AAD, "current_hash": "x" * 64},
    ):
        with pytest.raises(tc.CustodyError):
            tc.open_envelope(env, priv, wrong)


def test_tampered_ciphertext_refused(keypair):
    priv, pub = keypair
    env = tc.seal(b"entry", pub, AAD)
    ct = bytearray(base64.b64decode(env["ct"]))
    ct[len(ct) // 2] ^= 0x01
    with pytest.raises(tc.CustodyError):
        tc.open_envelope({**env, "ct": base64.b64encode(bytes(ct)).decode()}, priv, AAD)


def test_signatures_verify_and_reject():
    spriv, spub = tc.generate_signing_keypair()
    payload = {"event": "custody_migration", "n": 3}
    sig = tc.sign(payload, spriv)
    assert tc.verify_signature(payload, sig, spub)
    assert not tc.verify_signature({**payload, "n": 4}, sig, spub)
    other_priv, other_pub = tc.generate_signing_keypair()
    assert not tc.verify_signature(payload, sig, other_pub)


def test_structural_envelope_validation(keypair):
    _, pub = keypair
    good = tc.seal(b"x", pub, AAD)
    assert _structurally_valid_envelope(good) is None
    assert "missing field" in _structurally_valid_envelope({k: v for k, v in good.items() if k != "ct"})
    assert "base64" in _structurally_valid_envelope({**good, "epk": "!!notb64!!"})
    assert "32 bytes" in _structurally_valid_envelope({**good, "epk": base64.b64encode(b"short").decode()})
    torn = {**good, "ct": base64.b64encode(b"tiny").decode()}
    assert "GCM tag" in _structurally_valid_envelope(torn)


def _live_rows():
    conn = psycopg2.connect(host="localhost", dbname="iceberg",
                            user="iceberg", password="iceberg")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SELECT {', '.join(tc.SHIPPED_COLUMNS)} FROM ledger_entries ORDER BY id")
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def test_recompute_matches_every_live_ledger_row():
    rows = _live_rows()
    assert rows, "primary ledger unexpectedly empty"
    for row in rows:
        ok, detail = tc.deep_verify_row(row)
        assert ok, f"id={row['id']}: {detail}"


def test_recompute_catches_field_edit():
    row = _live_rows()[0]
    row["reason"] = (row.get("reason") or "") + " [edited]"
    ok, detail = tc.deep_verify_row(row)
    assert not ok and "hash-mismatch" in (detail or "")
