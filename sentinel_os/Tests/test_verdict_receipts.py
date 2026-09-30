"""Verdict receipts (TACK Layer 5): every governance decision the ledger
writes is bound to what it judged, signed over its content, hash-chained and
anchored, so an auditor with no access to the running system can verify it.

These tests grow with the steps of the change; each section names its step.
They run against a real ledger (the `test_ledger` fixture drops the table
before each test), the same convention as test_authorized_by_attestation.py.
"""

import dataclasses
import json
import math
import os

import psycopg2
import pytest

from canonical_fields import OPTIONAL_HASHED_FIELDS
from cns.gate import GateOutcome, GatePosition, subject_digest
from governance.ledger_postgres import (
    GovernanceDecisionRecord, PostgreSQLLedger, gate_outcome_of)
import twin_custody as tc

_PG = dict(host="localhost", port=5432, dbname="iceberg",
           user="iceberg", password="iceberg")
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "ledger_export_pre_receipts.json")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _params():
    from cassette_schema import validate_cassette
    from cassettes.ivr_cassette import IvrCassette
    return validate_cassette(IvrCassette())


_sid = iter(range(1, 100_000))


def _record(**kw):
    base = dict(
        action_type="governance_decision", node="billing_queue",
        cassette_version="ivr:standard-ivr:2.0.2",
        input_data={"call_sid": f"RCPT-{next(_sid):05d}", "score": 0.5,
                    "factors": ["wait", "repeat"], "resolved": True},
        policy_parameters={"governance_trigger": 2},
        reasoning="AI safety check: risk elevated on repeat contact",
        output={"safe": False, "risk_level": "high"})
    base.update(kw)
    return GovernanceDecisionRecord(**base)


def _rows(where="TRUE"):
    conn = psycopg2.connect(connect_timeout=2, **_PG)
    try:
        cur = conn.cursor()
        cur.execute(  # nosec B608 -- SHIPPED_COLUMNS is a fixed code-defined list
            f"SELECT {', '.join(tc.SHIPPED_COLUMNS)} FROM ledger_entries "
            f"WHERE {where} ORDER BY id ASC")
        return [dict(zip(tc.SHIPPED_COLUMNS, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def _count():
    conn = psycopg2.connect(connect_timeout=2, **_PG)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM ledger_entries")
        return cur.fetchone()[0]
    finally:
        conn.close()


def _fixture_rows():
    with open(FIXTURE) as fh:
        return json.load(fh)["rows"]


# ---------------------------------------------------------------------------
# Step 2.1: the binding and the reserved seed field
# ---------------------------------------------------------------------------

def test_the_two_fields_are_in_the_shared_contract_and_shipped():
    assert "subject_digest" in OPTIONAL_HASHED_FIELDS
    assert "shuffle_seed" in OPTIONAL_HASHED_FIELDS
    assert "subject_digest" in tc.SHIPPED_COLUMNS
    assert "shuffle_seed" in tc.SHIPPED_COLUMNS


def test_decision_carries_the_cns_digest_of_its_stored_input(test_ledger):
    assert test_ledger.append_decision(_record(), governance_params=_params())
    row = _rows("record_kind = 'governance_decision'")[-1]
    assert row["subject_digest"] == subject_digest(row["input_data"])
    assert len(row["subject_digest"]) == 64
    # the writer, the primary verifier and the witness agree on the bytes
    assert test_ledger.verify_chain()["ok"]
    ok, detail = tc.deep_verify_row(row)
    assert ok, detail


def test_gate_identity_rides_inside_the_hashed_output(test_ledger):
    params = _params()
    assert test_ledger.append_decision(
        _record(output={"safe": False, "risk_level": "high"}), governance_params=params)
    assert test_ledger.append_decision(
        _record(output={"approved": True}), governance_params=params)
    blocked, approved = _rows("record_kind = 'governance_decision'")[-2:]
    assert blocked["decision_output"]["gate"] == {
        "name": "ivr:standard-ivr:2.0.2",
        "position": GatePosition.OMEGA.value,
        "outcome": GateOutcome.TERMINAL_BREACH.value,
    }
    assert approved["decision_output"]["gate"]["outcome"] == GateOutcome.PASS.value
    # the caller's own keys are untouched
    assert blocked["decision_output"]["risk_level"] == "high"
    # and no column was added for any of it
    assert "gate" not in tc.SHIPPED_COLUMNS
    assert test_ledger.verify_chain()["ok"]


def test_gate_outcome_is_fail_closed():
    assert gate_outcome_of({"approved": True}) == "pass"
    assert gate_outcome_of({"safe": True}) == "pass"
    assert gate_outcome_of({"approved": False}) == "terminal_breach"
    assert gate_outcome_of({"safe": "yes"}) == "terminal_breach"   # truthy is not True
    assert gate_outcome_of({"decision": "whatever"}) == "terminal_breach"


def test_caller_supplied_gate_entry_is_refused(test_ledger):
    before = _count()
    with pytest.raises(ValueError, match="'gate' entry"):
        test_ledger.append_decision(
            _record(output={"safe": True, "gate": {"name": "forged"}}),
            governance_params=_params())
    assert _count() == before


def test_h_nan_input_is_refused_before_any_row_is_written(test_ledger):
    """Acceptance (h): content the CNS cannot canonically encode is a gate
    defect, surfaced as a refused write, not papered over."""
    before = _count()
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="not canonically encodable"):
            test_ledger.append_decision(
                _record(input_data={"call_sid": "RCPT-NAN", "score": bad}),
                governance_params=_params())
    assert _count() == before


def test_j_shuffle_seed_is_never_read_from_the_record(test_ledger):
    """Acceptance (j), write path: the seed is the server's to derive. A
    record that tries to carry one is not a field the writer looks at, and
    the row is written with the field absent."""
    assert "shuffle_seed" not in {f.name for f in dataclasses.fields(GovernanceDecisionRecord)}
    rec = _record(input_data={"call_sid": "RCPT-SEED", "shuffle_seed": "f" * 64},
                  output={"safe": True, "shuffle_seed": "f" * 64})
    assert test_ledger.append_decision(rec, governance_params=_params())
    row = _rows("record_kind = 'governance_decision'")[-1]
    assert row["shuffle_seed"] is None
    assert test_ledger.verify_chain()["ok"]


def test_f_rows_written_before_this_change_keep_their_bytes():
    """Acceptance (f), witness half: a fixture ledger exported before the
    change, with no marker row and none of the new fields, recomputes
    byte-for-byte under the extended contract."""
    rows = _fixture_rows()
    assert rows and all("subject_digest" not in r or r["subject_digest"] is None for r in rows)
    prev = "genesis"
    for row in rows:
        assert row["previous_hash"] == prev
        ok, detail = tc.deep_verify_row(row)
        assert ok, f"fixture row {row['id']} ({row['record_kind']}): {detail}"
        prev = row["current_hash"]


# ---------------------------------------------------------------------------
# Step 2.3: the server-derived seed rule (helper only; nothing shuffles yet)
# ---------------------------------------------------------------------------

import hashlib
import inspect

from governance import authorized_by_attestation as att

_KEY = b"receipts-test-attestation-key-not-a-real-secret"
_OTHER_KEY = b"some-other-key-the-server-never-held"
_PREV = "a" * 64


def test_j_seed_same_inputs_give_the_same_seed():
    one = att.derive_shuffle_seed(_PREV, "governance_decision", _KEY)
    two = att.derive_shuffle_seed(_PREV, "governance_decision", _KEY)
    assert one == two
    assert len(one) == 64 and int(one, 16) >= 0
    assert att.verify_shuffle_seed(one, _PREV, "governance_decision", _KEY)[0] == att.STATUS_OK


def test_j_a_changed_key_previous_hash_or_kind_gives_a_different_seed():
    base = att.derive_shuffle_seed(_PREV, "governance_decision", _KEY)
    assert att.derive_shuffle_seed(_PREV, "governance_decision", _OTHER_KEY) != base
    assert att.derive_shuffle_seed("b" * 64, "governance_decision", _KEY) != base
    assert att.derive_shuffle_seed(_PREV, "observed_event", _KEY) != base


def test_j_without_the_key_the_seed_cannot_be_predicted_from_the_previous_hash():
    seed = att.derive_shuffle_seed(_PREV, "governance_decision", _KEY)
    # every keyless derivation an agent could try from the public inputs
    public = (_PREV, _PREV + "governance_decision", "governance_decision" + _PREV,
              f"{_PREV}:governance_decision")
    for guess in public:
        assert seed != hashlib.sha256(guess.encode()).hexdigest()
    # a wrong key is refused, and no key at all is honestly unverifiable
    assert att.verify_shuffle_seed(seed, _PREV, "governance_decision", _OTHER_KEY)[0] == att.STATUS_INVALID
    assert att.verify_shuffle_seed(seed, _PREV, "governance_decision", None)[0] == att.STATUS_UNVERIFIABLE
    assert att.verify_shuffle_seed(None, _PREV, "governance_decision", _KEY)[0] == att.STATUS_ABSENT
    with pytest.raises(ValueError, match="needs the ledger attestation key"):
        att.derive_shuffle_seed(_PREV, "governance_decision", None)


def test_j_nothing_on_the_write_path_reads_or_derives_a_seed():
    """The helper exists for Layer 1 and the witness. The writer neither
    calls it nor reads a seed off the record: the field is left absent."""
    src = inspect.getsource(PostgreSQLLedger.append_decision)
    assert "derive_shuffle_seed(" not in src   # a comment may name it; no call does
    assert "record.shuffle_seed" not in src
    assert 'getattr(record, "shuffle_seed"' not in src
