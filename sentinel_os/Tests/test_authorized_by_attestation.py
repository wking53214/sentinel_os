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
    ENV_KEY_FILE,
    ENV_KEYS_PREVIOUS,
    ENV_KEYS_PREVIOUS_FILE,
    ENV_KEYS_RETIRED,
    ENV_KEYS_RETIRED_FILE,
    ENV_REQUIRE,
    KeySet,
    SIGNATURE_FIELD,
    STATUS_INVALID,
    STATUS_OK,
    STATUS_RETIRED_KEY,
    STATUS_UNATTESTED,
    STATUS_UNKNOWN_KEY,
    STATUS_UNVERIFIABLE,
    attestation_keyset,
    key_fingerprint,
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


_sid_counter = iter(range(1, 10_000))


def _record(**kw):
    base = dict(
        action_type="governance_decision", node="billing_queue",
        cassette_version="ivr:standard-ivr:2.0.2",
        input_data={"call_sid": f"ATTEST-{next(_sid_counter):04d}"},
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


_ATT_ENV_VARS = (ENV_KEY, ENV_KEY_FILE, ENV_KEYS_PREVIOUS, ENV_KEYS_PREVIOUS_FILE,
                 ENV_KEYS_RETIRED, ENV_KEYS_RETIRED_FILE, ENV_REQUIRE)


@pytest.fixture
def key_env(monkeypatch):
    for v in _ATT_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv(ENV_KEY, _KEY_STR)
    return _KEY


@pytest.fixture
def no_key_env(monkeypatch):
    for v in _ATT_ENV_VARS:
        monkeypatch.delenv(v, raising=False)


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
    sig = row[SIGNATURE_FIELD]
    assert sig is not None
    # v2 envelope: abv2.<16-hex keyfp>.<64-hex digest>, naming the signing key
    tag, keyfp, digest = sig.split(".")
    assert tag == "abv2"
    assert keyfp == key_fingerprint(key_env)
    assert len(digest) == 64

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


def test_2_unrelated_key_is_unknown_not_invalid():
    # a v2 signature names its key. A verifier holding only some other,
    # unrelated key can't even find the named key -> UNKNOWN_KEY, which is
    # honestly different from "the signature is wrong" (INVALID).
    prev = "0" * 64
    sig = sign_authorized_by("cfo:approvals", prev, "governance_decision", _KEY)
    row = _twin_row(previous_hash=prev, authorized_by="cfo:approvals",
                    **{SIGNATURE_FIELD: sig})
    status, _ = verify_authorized_by_signature(row, b"a-different-key")
    assert status == STATUS_UNKNOWN_KEY


def test_2_v2_digest_tamper_under_the_named_key_is_invalid():
    # flip the digest but keep the (correct) keyfp -> the verifier finds the
    # named key, recomputes, and the HMAC does not match -> INVALID.
    prev = "0" * 64
    sig = sign_authorized_by("cfo:approvals", prev, "governance_decision", _KEY)
    tag, keyfp, digest = sig.split(".")
    flipped = digest[:-1] + ("0" if digest[-1] != "0" else "1")
    row = _twin_row(previous_hash=prev, authorized_by="cfo:approvals",
                    **{SIGNATURE_FIELD: f"{tag}.{keyfp}.{flipped}"})
    status, _ = verify_authorized_by_signature(row, _KEY)
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


# ---------------------------------------------------------------------------
# key resolution: ICEBERG_LEDGER_ATTESTATION_KEY_FILE (Option B)
# ---------------------------------------------------------------------------

def test_key_can_come_from_a_file(tmp_path, monkeypatch, test_ledger):
    keyfile = tmp_path / "attestation.key"
    keyfile.write_text(_KEY_STR + "\n")  # trailing newline, as `echo` / mounts produce
    monkeypatch.delenv(ENV_KEY, raising=False)
    monkeypatch.setenv(ENV_KEY_FILE, str(keyfile))

    assert att.attestation_key() == _KEY  # newline stripped -> same bytes as env

    assert test_ledger.append_decision(
        _record(authorized_by="harness:production"),
        governance_params=_governance_params())
    row = _last_row(test_ledger)
    assert row[SIGNATURE_FIELD] is not None
    assert verify_authorized_by_signature(row, att.attestation_key())[0] == STATUS_OK
    assert test_ledger.verify_chain()["ok"]


def test_env_var_wins_over_key_file(tmp_path, monkeypatch):
    keyfile = tmp_path / "attestation.key"
    keyfile.write_text("the-file-key")
    monkeypatch.setenv(ENV_KEY, "the-env-key")
    monkeypatch.setenv(ENV_KEY_FILE, str(keyfile))
    assert att.attestation_key() == b"the-env-key"


def test_key_file_set_but_missing_raises(monkeypatch):
    monkeypatch.delenv(ENV_KEY, raising=False)
    monkeypatch.setenv(ENV_KEY_FILE, "/no/such/attestation/key/file")
    with pytest.raises(RuntimeError, match="could not be read"):
        att.attestation_key()


def test_key_file_empty_raises(tmp_path, monkeypatch):
    keyfile = tmp_path / "empty.key"
    keyfile.write_text("   \n")
    monkeypatch.delenv(ENV_KEY, raising=False)
    monkeypatch.setenv(ENV_KEY_FILE, str(keyfile))
    with pytest.raises(RuntimeError, match="empty"):
        att.attestation_key()


def test_enforcement_on_with_broken_key_file_refuses_to_start(monkeypatch):
    monkeypatch.setenv(ENV_REQUIRE, "1")
    monkeypatch.delenv(ENV_KEY, raising=False)
    monkeypatch.setenv(ENV_KEY_FILE, "/no/such/attestation/key/file")
    with pytest.raises(RuntimeError):
        PostgreSQLLedger(**_PG)


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


# ---------------------------------------------------------------------------
# Question 2: key rotation. Verification key set = current + PREVIOUS +
# RETIRED; UNKNOWN_KEY always fails verify_chain, RETIRED_KEY only under
# enforcement.
# ---------------------------------------------------------------------------

_KEY_A = b"attestation-key-A-original-rotation-test"
_KEY_B = b"attestation-key-B-post-rotation-rotation-test"
_KEY_C = b"attestation-key-C-never-configured-here"


def _sig_row(key, authorized_by="cfo:approvals", prev="0" * 64):
    return _twin_row(
        previous_hash=prev, authorized_by=authorized_by,
        **{SIGNATURE_FIELD: sign_authorized_by(
            authorized_by, prev, "governance_decision", key)})


def test_v2_envelope_carries_the_signing_key_fingerprint():
    sig = sign_authorized_by("x", "0" * 64, "governance_decision", _KEY_A)
    tag, keyfp, digest = sig.split(".")
    assert tag == "abv2"
    assert keyfp == key_fingerprint(_KEY_A)
    assert keyfp != key_fingerprint(_KEY_B)
    assert len(digest) == 64


def test_keyset_partitions_and_dedupes():
    ks = KeySet(current=_KEY_A, previous=[_KEY_A, _KEY_B], retired=[_KEY_B, _KEY_C])
    # _KEY_A appears once; _KEY_B is trusted (via previous) so it drops out of retired
    assert ks.trusted == [_KEY_A, _KEY_B]
    assert ks.retired == [_KEY_C]
    assert ks.trusted_key(key_fingerprint(_KEY_A)) == _KEY_A
    assert ks.retired_key(key_fingerprint(_KEY_C)) == _KEY_C
    assert not ks.is_empty()
    assert KeySet(None).is_empty()


def test_rotation_old_key_in_previous_still_verifies_ok():
    ks = KeySet(current=_KEY_B, previous=[_KEY_A])
    assert verify_authorized_by_signature(_sig_row(_KEY_A), ks)[0] == STATUS_OK
    assert verify_authorized_by_signature(_sig_row(_KEY_B), ks)[0] == STATUS_OK


def test_retired_key_verifies_as_retired_not_ok():
    ks = KeySet(current=_KEY_B, previous=[], retired=[_KEY_A])
    status, detail = verify_authorized_by_signature(_sig_row(_KEY_A), ks)
    assert status == STATUS_RETIRED_KEY, detail


def test_unknown_key_when_fingerprint_matches_nothing():
    ks = KeySet(current=_KEY_B, previous=[_KEY_A])  # never told about _KEY_C
    status, _ = verify_authorized_by_signature(_sig_row(_KEY_C), ks)
    assert status == STATUS_UNKNOWN_KEY


def test_legacy_bare_digest_still_verifies_by_trying_every_key():
    # a v1 row (no key id) written before rotation support: verification has
    # to fall back to trying each held key.
    prev = "0" * 64
    legacy = att._hmac_hex(_KEY_A, att._payload("cfo:approvals", prev,
                                                "governance_decision"))
    assert "." not in legacy and len(legacy) == 64
    row = _twin_row(previous_hash=prev, authorized_by="cfo:approvals",
                    **{SIGNATURE_FIELD: legacy})
    assert verify_authorized_by_signature(
        row, KeySet(current=_KEY_B, previous=[_KEY_A]))[0] == STATUS_OK
    # and a legacy row signed by a retired key reports RETIRED_KEY
    assert verify_authorized_by_signature(
        row, KeySet(current=_KEY_B, retired=[_KEY_A]))[0] == STATUS_RETIRED_KEY


def test_previous_keys_read_from_comma_separated_env(monkeypatch):
    for v in _ATT_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv(ENV_KEY, _KEY_B.decode())
    monkeypatch.setenv(ENV_KEYS_PREVIOUS,
                       f"{_KEY_A.decode()},{_KEY_C.decode()}")
    ks = attestation_keyset()
    assert ks.trusted == [_KEY_B, _KEY_A, _KEY_C]


def test_previous_keys_read_one_per_line_from_file(tmp_path, monkeypatch):
    for v in _ATT_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv(ENV_KEY, _KEY_B.decode())
    f = tmp_path / "previous.keys"
    f.write_text(f"{_KEY_A.decode()}\n\n{_KEY_C.decode()}\n")
    monkeypatch.setenv(ENV_KEYS_PREVIOUS_FILE, str(f))
    ks = attestation_keyset()
    assert set(ks.trusted) == {_KEY_A, _KEY_B, _KEY_C}


def test_broken_previous_keys_file_raises(monkeypatch):
    for v in _ATT_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv(ENV_KEY, _KEY_B.decode())
    monkeypatch.setenv(ENV_KEYS_PREVIOUS_FILE, "/no/such/previous/keys/file")
    with pytest.raises(RuntimeError, match="could not be read"):
        attestation_keyset()


def test_broken_previous_keys_file_refuses_ledger_start(monkeypatch):
    for v in _ATT_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv(ENV_KEY, _KEY_B.decode())
    monkeypatch.setenv(ENV_KEYS_PREVIOUS_FILE, "/no/such/previous/keys/file")
    with pytest.raises(RuntimeError):
        PostgreSQLLedger(**_PG)


def test_verify_chain_after_a_rotation_is_clean(test_ledger, monkeypatch):
    for v in _ATT_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    # row signed under key A
    monkeypatch.setenv(ENV_KEY, _KEY_A.decode())
    assert test_ledger.append_decision(
        _record(authorized_by="harness:production"),
        governance_params=_governance_params())
    # rotate: A -> previous, B -> current; new row signed under B
    monkeypatch.setenv(ENV_KEY, _KEY_B.decode())
    monkeypatch.setenv(ENV_KEYS_PREVIOUS, _KEY_A.decode())
    assert test_ledger.append_decision(
        _record(authorized_by="harness:production"),
        governance_params=_governance_params())
    # whole chain verifies: old row via A (previous), new row via B (current)
    result = test_ledger.verify_chain()
    assert result["ok"], result["violations"]


def test_verify_chain_flags_unknown_key_even_without_enforcement(
        test_ledger, monkeypatch):
    for v in _ATT_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv(ENV_KEY, _KEY_A.decode())
    assert test_ledger.append_decision(
        _record(authorized_by="harness:production"),
        governance_params=_governance_params())
    # operator later runs verify holding ONLY key B -- the row was signed by A,
    # which is now configured in no role at all. enforcement is OFF.
    monkeypatch.setenv(ENV_KEY, _KEY_B.decode())
    monkeypatch.delenv(ENV_REQUIRE, raising=False)
    result = test_ledger.verify_chain(mode="lenient")
    assert result["ok"] is False
    assert any("unrecognised key" in v for v in result["violations"]), result


def test_verify_chain_flags_retired_key_only_under_enforcement(
        test_ledger, monkeypatch):
    for v in _ATT_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv(ENV_KEY, _KEY_A.decode())
    assert test_ledger.append_decision(
        _record(authorized_by="harness:production"),
        governance_params=_governance_params())
    # key A moved to RETIRED (suspected compromise), B is current
    monkeypatch.setenv(ENV_KEY, _KEY_B.decode())
    monkeypatch.setenv(ENV_KEYS_RETIRED, _KEY_A.decode())

    # enforcement OFF -> the retired-key row is reported but not a violation
    monkeypatch.delenv(ENV_REQUIRE, raising=False)
    assert test_ledger.verify_chain(mode="lenient")["ok"] is True

    # enforcement ON -> it is a violation
    monkeypatch.setenv(ENV_REQUIRE, "1")
    result = test_ledger.verify_chain(mode="lenient")
    assert result["ok"] is False
    assert any("retired key" in v for v in result["violations"]), result
