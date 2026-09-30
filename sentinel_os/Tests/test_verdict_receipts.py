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


@pytest.fixture(scope="module", autouse=True)
def _leave_a_verifiable_ledger_behind():
    """test_twin_custody.py's live-row tests read whatever the primary ledger
    holds when they run, and several tests here drop or empty it. Leave it
    holding a short verifiable chain, the state every earlier module leaves."""
    yield
    for var in ("ICEBERG_LEDGER_ANCHOR_PATH",):
        os.environ.pop(var, None)
    ledger = _fresh_ledger()
    try:
        for _ in range(2):
            assert ledger.append_decision(_record(), governance_params=_params())
    finally:
        ledger.close()


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


# ---------------------------------------------------------------------------
# Step 2.2: the witness checks the binding; the primary verifier agrees
# ---------------------------------------------------------------------------

def _tamper_last_row(**cols):
    """Edit the newest row past the immutability triggers, the way the
    observed_event tests do: as the table owner, triggers disabled."""
    conn = psycopg2.connect(connect_timeout=2, **_PG)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("ALTER TABLE ledger_entries DISABLE TRIGGER USER;")
    sets = ", ".join(f"{k} = %s" for k in cols)
    cur.execute(  # nosec B608 -- column names are this test's own literals
        f"UPDATE ledger_entries SET {sets} WHERE id = (SELECT max(id) FROM ledger_entries)",
        tuple(cols.values()))
    cur.execute("ALTER TABLE ledger_entries ENABLE TRIGGER USER;")
    conn.close()


def _two_decisions(ledger):
    params = _params()
    assert ledger.append_decision(_record(), governance_params=params)
    assert ledger.append_decision(_record(), governance_params=params)
    return _rows("record_kind = 'governance_decision'")[-2:]


def test_b_witness_catches_a_transplanted_verdict(test_ledger):
    """Acceptance (b), witness half: another row's subject_digest is copied
    onto this row and the chain is rehashed to stay self-consistent. The
    chain is fooled; the binding is not."""
    first, second = _two_decisions(test_ledger)
    assert tc.deep_verify_row(second)[0]
    second["subject_digest"] = first["subject_digest"]
    second["current_hash"] = tc.recompute_current_hash(second)
    ok, detail = tc.deep_verify_row(second)
    assert not ok and detail.startswith(tc.VIOLATION_TRANSPLANTED), detail


def test_b_primary_verifier_reports_transplanted(test_ledger):
    first, second = _two_decisions(test_ledger)
    second["subject_digest"] = first["subject_digest"]
    _tamper_last_row(subject_digest=first["subject_digest"],
                     current_hash=tc.recompute_current_hash(second))
    result = test_ledger.verify_chain(mode="audit")
    assert not result["ok"]
    assert any("TRANSPLANTED" in v for v in result["violations"]), result["violations"]
    assert not any("content hash mismatch" in v for v in result["violations"])


def test_c_witness_re_derives_the_seed_and_refuses_a_forged_one(test_ledger):
    """Acceptance (c), witness half: a seed the server would have derived
    passes; one the agent chose does not; one the witness cannot re-derive
    (no key held) fails closed."""
    _, row = _two_decisions(test_ledger)
    honest = att.derive_shuffle_seed(row["previous_hash"], row["record_kind"], _KEY)
    row["shuffle_seed"] = honest
    row["current_hash"] = tc.recompute_current_hash(row)
    assert tc.deep_verify_row(row, keys=_KEY)[0]
    ok, detail = tc.deep_verify_row(row, keys=None)
    assert not ok and detail.startswith(tc.VIOLATION_SEED_FORGED)

    row["shuffle_seed"] = "0" * 64
    row["current_hash"] = tc.recompute_current_hash(row)
    ok, detail = tc.deep_verify_row(row, keys=_KEY)
    assert not ok and detail.startswith(tc.VIOLATION_SEED_FORGED), detail


def test_c_primary_verifier_reports_seed_forged(test_ledger, monkeypatch):
    monkeypatch.setenv(att.ENV_KEY, _KEY.decode())
    _, row = _two_decisions(test_ledger)
    honest = att.derive_shuffle_seed(row["previous_hash"], row["record_kind"], _KEY)
    row["shuffle_seed"] = honest
    _tamper_last_row(shuffle_seed=honest, current_hash=tc.recompute_current_hash(row))
    assert test_ledger.verify_chain()["ok"], "a server-derived seed verifies"

    row["shuffle_seed"] = "0" * 64
    _tamper_last_row(shuffle_seed="0" * 64, current_hash=tc.recompute_current_hash(row))
    result = test_ledger.verify_chain(mode="audit")
    assert any("SEED_FORGED" in v for v in result["violations"]), result["violations"]

    # and with no key held at all the seed cannot be vouched for
    monkeypatch.delenv(att.ENV_KEY, raising=False)
    result = test_ledger.verify_chain(mode="audit")
    assert any("SEED_FORGED" in v for v in result["violations"]), result["violations"]


def test_witness_and_primary_verifier_agree_on_honest_rows(test_ledger):
    _two_decisions(test_ledger)
    assert test_ledger.verify_chain()["ok"]
    for row in _rows():
        ok, detail = tc.deep_verify_row(row)
        assert ok, detail


# ---------------------------------------------------------------------------
# Step 2.4: attestation covers content (abv3), and the witness verifies it
# ---------------------------------------------------------------------------

_FIXTURE_KEY = b"fixture-attestation-key-not-a-real-secret"


def _signed_decision(ledger):
    assert ledger.append_decision(
        _record(authorized_by="harness:production"), governance_params=_params())
    return _rows("record_kind = 'governance_decision'")[-1]


def test_decision_signature_is_abv3_and_the_witness_verifies_it(test_ledger, monkeypatch):
    monkeypatch.setenv(att.ENV_KEY, _KEY.decode())
    row = _signed_decision(test_ledger)
    assert row["authorized_by_sig"].startswith("abv3.")
    assert tc.verify_row_attestation(row, _KEY)[0] == att.STATUS_OK
    ok, detail = tc.deep_verify_row(row, keys=_KEY)
    assert ok, detail
    assert test_ledger.verify_chain()["ok"]


def test_abv3_covers_the_content_where_abv2_covered_only_the_name(test_ledger, monkeypatch):
    """Edit a hashed field that is not the accountable name and rehash the
    row so the unkeyed chain is self-consistent again. abv3 notices, because
    the content pre-hash is inside the signature; an abv2 signature over the
    same row is blind to it."""
    monkeypatch.setenv(att.ENV_KEY, _KEY.decode())
    row = _signed_decision(test_ledger)
    row["reason"] = row["reason"] + " [quietly edited]"
    row["current_hash"] = tc.recompute_current_hash(row)
    status, detail = tc.verify_row_attestation(row, _KEY)
    assert status == att.STATUS_INVALID, detail
    ok, detail = tc.deep_verify_row(row, keys=_KEY)
    assert not ok and detail.startswith(tc.VIOLATION_TAMPERED), detail

    # the same edit under an abv2 signature (name and position only) passes,
    # which is exactly the gap abv3 closes
    row["authorized_by_sig"] = att.sign_authorized_by(
        row["authorized_by"], row["previous_hash"], row["record_kind"], _KEY)
    row["current_hash"] = tc.recompute_current_hash(row)
    assert row["authorized_by_sig"].startswith("abv2.")
    assert tc.verify_row_attestation(row, _KEY)[0] == att.STATUS_OK


def test_abv2_rows_written_before_the_change_still_verify():
    """Backward compatibility: the fixture's abv2 row verifies under the key
    that signed it, through the same witness entry point."""
    rows = [r for r in _fixture_rows() if (r["authorized_by_sig"] or "").startswith("abv2.")]
    assert rows
    for row in rows:
        assert tc.verify_row_attestation(row, _FIXTURE_KEY)[0] == att.STATUS_OK
        assert tc.deep_verify_row(row, keys=_FIXTURE_KEY)[0]
        # and a verifier that does not hold that key says so, honestly
        assert tc.verify_row_attestation(row, _KEY)[0] == att.STATUS_UNKNOWN_KEY


def test_abv3_without_the_content_prehash_is_not_a_verification(test_ledger, monkeypatch):
    monkeypatch.setenv(att.ENV_KEY, _KEY.decode())
    row = _signed_decision(test_ledger)
    status, detail = att.verify_authorized_by_signature(row, _KEY)
    assert status == att.STATUS_INVALID and "pre-hash" in detail
    assert att.verify_authorized_by_signature(
        row, _KEY, content_prehash=tc.content_prehash_of(row))[0] == att.STATUS_OK


# ---------------------------------------------------------------------------
# Step 2.5: unattested rows become visible (the attestation_policy marker)
# ---------------------------------------------------------------------------

def _fresh_ledger():
    conn = psycopg2.connect(connect_timeout=2, **_PG)
    conn.autocommit = True
    conn.cursor().execute("DROP TABLE IF EXISTS ledger_entries CASCADE;")
    conn.close()
    return PostgreSQLLedger(**_PG)


def _enforcement_on(monkeypatch, key=_KEY):
    monkeypatch.delenv(att.ENV_DEV_OVERRIDE, raising=False)
    monkeypatch.setenv(att.ENV_REQUIRE, "1")
    monkeypatch.setenv(att.ENV_KEY, key.decode())


def _enforcement_off_no_key(monkeypatch):
    """Enforcement off is the dev-only override since step 2.7."""
    monkeypatch.delenv(att.ENV_REQUIRE, raising=False)
    monkeypatch.delenv(att.ENV_KEY, raising=False)
    monkeypatch.setenv(att.ENV_DEV_OVERRIDE, "1")


def test_marker_is_appended_once_per_key_when_enforcement_is_on(monkeypatch):
    _enforcement_on(monkeypatch)
    ledger = _fresh_ledger()
    try:
        rows = _rows()
        assert [r["record_kind"] for r in rows] == ["attestation_policy"]
        marker = rows[0]
        assert marker["data"]["key_fingerprint"] == att.key_fingerprint(_KEY)
        assert marker["data"]["enforced_at"]
        assert marker["previous_hash"] == "genesis"
        # writer, primary verifier and witness agree on the marker's bytes
        assert ledger.verify_chain()["ok"]
        assert tc.deep_verify_row(marker)[0]
        # a restart appends nothing
        PostgreSQLLedger(**_PG).close()
        assert len(_rows()) == 1
        # a key rotation appends one marker naming the new key
        monkeypatch.setenv(att.ENV_KEYS_PREVIOUS, _KEY.decode())
        monkeypatch.setenv(att.ENV_KEY, _OTHER_KEY.decode())
        PostgreSQLLedger(**_PG).close()
        assert [r["data"]["key_fingerprint"] for r in _rows("record_kind = 'attestation_policy'")] == [
            att.key_fingerprint(_KEY), att.key_fingerprint(_OTHER_KEY)]
        assert ledger.verify_chain()["ok"]
    finally:
        ledger.close()


def test_d_a_signature_nulled_after_the_marker_is_unattested(monkeypatch):
    """Acceptance (d), verifier and witness halves: null a row's signature
    after the marker and recompute the hash so the unkeyed chain is
    self-consistent. That attack passed before the marker existed."""
    _enforcement_on(monkeypatch)
    ledger = _fresh_ledger()
    try:
        assert ledger.append_decision(
            _record(authorized_by="harness:production"), governance_params=_params())
        row = _rows()[-1]
        assert row["authorized_by_sig"].startswith("abv3.")
        assert ledger.verify_chain()["ok"]
        assert tc.verify_rows(_rows(), keys=_KEY) is None

        row["authorized_by_sig"] = None
        _tamper_last_row(authorized_by_sig=None,
                         current_hash=tc.recompute_current_hash(row))
        result = ledger.verify_chain(mode="audit")
        assert any("UNATTESTED" in v for v in result["violations"]), result["violations"]
        assert not any("content hash mismatch" in v for v in result["violations"])
        failing = tc.verify_rows(_rows(), keys=_KEY)
        assert failing is not None
        assert failing[0] == row["id"] and failing[1] == tc.VIOLATION_UNATTESTED, failing
    finally:
        ledger.close()


def test_rows_before_the_marker_are_untouched(monkeypatch):
    """The backward-compatibility guarantee: an accountable claim written
    unsigned before enforcement began is not a violation afterwards, and a
    chain with no marker (the fixture) is not judged at all."""
    _enforcement_off_no_key(monkeypatch)
    ledger = _fresh_ledger()
    try:
        assert ledger.append_decision(
            _record(authorized_by="harness:legacy"), governance_params=_params())
    finally:
        ledger.close()
    assert _rows()[-1]["authorized_by_sig"] is None

    _enforcement_on(monkeypatch)
    ledger = PostgreSQLLedger(**_PG)   # appends the marker after the legacy row
    try:
        assert [r["record_kind"] for r in _rows()] == ["governance_decision", "attestation_policy"]
        assert ledger.verify_chain()["ok"]
        assert tc.verify_rows(_rows(), keys=_KEY) is None
    finally:
        ledger.close()
    fixture = _fixture_rows()
    assert any(r["authorized_by"] and not r["authorized_by_sig"] for r in fixture)
    assert tc.verify_rows(fixture, keys=_FIXTURE_KEY) is None


# ---------------------------------------------------------------------------
# Step 2.6: the external head anchor
# ---------------------------------------------------------------------------

def _delete_last_rows(n):
    conn = psycopg2.connect(connect_timeout=2, **_PG)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("ALTER TABLE ledger_entries DISABLE TRIGGER USER;")
    cur.execute("DELETE FROM ledger_entries WHERE id IN "
                "(SELECT id FROM ledger_entries ORDER BY id DESC LIMIT %s)", (n,))
    cur.execute("ALTER TABLE ledger_entries ENABLE TRIGGER USER;")
    conn.close()


def _anchored_ledger(tmp_path, monkeypatch, appends=3):
    _enforcement_on(monkeypatch)
    path = tmp_path / "ledger.anchor"
    monkeypatch.setenv(tc.HEAD_ANCHOR_ENV, str(path))
    ledger = _fresh_ledger()          # boot appends the marker, which anchors
    for _ in range(appends):
        assert ledger.append_decision(_record(), governance_params=_params())
    assert ledger.append("threshold_adjust", "billing_queue", 0.5, 0.6, "legacy", {"why": "anchor"})
    return ledger, str(path)


def test_anchor_builds_reads_verifies_and_refuses_to_be_unsigned(tmp_path):
    path = str(tmp_path / "ledger.anchor")
    anchor = tc.write_head_anchor(path, "h" * 64, 3, _KEY)
    assert tc.read_head_anchor(path) == anchor
    assert set(anchor) == {"v", "head", "entries", "sealed_at", "key_fingerprint", "hmac"}
    assert anchor["key_fingerprint"] == att.key_fingerprint(_KEY)
    assert tc.verify_head_anchor(anchor, _KEY)[0] == att.STATUS_OK
    assert tc.verify_head_anchor(anchor, _OTHER_KEY)[0] == att.STATUS_UNKNOWN_KEY
    for field, value in (("entries", 2), ("head", "g" * 64), ("sealed_at", "later")):
        assert tc.verify_head_anchor(dict(anchor, **{field: value}), _KEY)[0] == att.STATUS_INVALID
    with pytest.raises(tc.CustodyError):
        tc.build_head_anchor("h" * 64, 1, None)
    with pytest.raises(tc.CustodyError):
        tc.read_head_anchor(str(tmp_path / "missing.anchor"))


def test_the_ledger_anchors_its_head_after_every_append(tmp_path, monkeypatch):
    ledger, path = _anchored_ledger(tmp_path, monkeypatch)
    try:
        rows = _rows()
        anchor = tc.read_head_anchor(path)
        assert anchor["entries"] == len(rows) == 5       # marker + 3 decisions + 1 legacy
        assert anchor["head"] == rows[-1]["current_hash"]
        assert tc.verify_head_anchor(anchor, _KEY)[0] == att.STATUS_OK
        assert tc.check_head_anchor(rows, anchor, _KEY) is None
        assert ledger.verify_chain()["ok"]
        # rows appended after an anchor was sealed are expected, not a finding
        older = tc.build_head_anchor(rows[1]["current_hash"], 2, _KEY)
        assert tc.check_head_anchor(rows, older, _KEY) is None
    finally:
        ledger.close()


def test_e_deleting_the_last_three_rows_is_truncated(tmp_path, monkeypatch):
    """Acceptance (e): fewer rows than the anchor sealed, or a different head
    at the anchored position, is TRUNCATED, on the witness and the verifier."""
    ledger, path = _anchored_ledger(tmp_path, monkeypatch)
    try:
        export = _rows()
        anchor = tc.read_head_anchor(path)
        failing = tc.check_head_anchor(export[:-3], anchor, _KEY)
        assert failing is not None and failing[1] == tc.VIOLATION_TRUNCATED, failing
        # a rebuilt chain of the same length: the head differs
        rebuilt = [dict(r) for r in export]
        rebuilt[-1]["reason"] = "rebuilt"
        rebuilt[-1]["current_hash"] = tc.recompute_current_hash(rebuilt[-1])
        failing = tc.check_head_anchor(rebuilt, anchor, _KEY)
        assert failing is not None and failing[1] == tc.VIOLATION_TRUNCATED, failing
        # an anchor the verifier cannot trust vouches for nothing
        failing = tc.check_head_anchor(export, anchor, _OTHER_KEY)
        assert failing is not None and failing[1] == tc.VIOLATION_TRUNCATED

        _delete_last_rows(3)
        result = ledger.verify_chain(mode="audit")
        assert any("TRUNCATED" in v for v in result["violations"]), result["violations"]
    finally:
        ledger.close()


def test_anchor_location_is_proven_at_boot(tmp_path, monkeypatch):
    _enforcement_on(monkeypatch)
    monkeypatch.setenv(tc.HEAD_ANCHOR_ENV, str(tmp_path / "no-such-dir" / "ledger.anchor"))
    with pytest.raises(RuntimeError, match="not writable"):
        _fresh_ledger()
    monkeypatch.setenv(tc.HEAD_ANCHOR_ENV, str(tmp_path / "ledger.anchor"))
    _enforcement_off_no_key(monkeypatch)
    with pytest.raises(RuntimeError, match="unsigned anchor"):
        _fresh_ledger()


# ---------------------------------------------------------------------------
# Step 2.7: enforcement is the default; one loud dev-only override
# ---------------------------------------------------------------------------

def test_i_production_profile_without_a_key_refuses_to_start(monkeypatch, capfd):
    """Acceptance (i): no key, no override -> the ledger does not start. The
    dev override starts it, and says so loudly on stderr."""
    for var in (att.ENV_KEY, att.ENV_KEY_FILE, att.ENV_REQUIRE, att.ENV_DEV_OVERRIDE):
        monkeypatch.delenv(var, raising=False)
    assert att.enforcement_required() is True
    with pytest.raises(RuntimeError, match="enforced by default"):
        PostgreSQLLedger(**_PG)

    monkeypatch.setenv(att.ENV_DEV_OVERRIDE, "1")
    att._warned.discard("override")      # once per process; let this test see it
    ledger = _fresh_ledger()
    try:
        assert att.enforcement_required() is False
        assert _rows() == []             # unenforced: no marker is appended
    finally:
        ledger.close()
    err = capfd.readouterr().err
    assert "WARNING" in err and att.ENV_DEV_OVERRIDE in err and "unverifiable" in err


def test_a_falsy_require_flag_no_longer_switches_enforcement_off(monkeypatch, capfd):
    monkeypatch.delenv(att.ENV_DEV_OVERRIDE, raising=False)
    monkeypatch.setenv(att.ENV_REQUIRE, "false")
    att._warned.discard("require-falsy")
    assert att.enforcement_required() is True
    assert "ignored" in capfd.readouterr().err


# ---------------------------------------------------------------------------
# Steps 2.8 and 2.9: the offline verifier, on export files, no database
# ---------------------------------------------------------------------------

import subprocess
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOL = os.path.join(_REPO, "tools", "verify_receipts.py")
EXPORT_SCRIPT = os.path.join(_REPO, "sentinel_os", "scripts", "export_ledger.py")


def _export_to(path):
    run = subprocess.run([sys.executable, EXPORT_SCRIPT, "--out", str(path)],
                         capture_output=True, text=True, check=True)
    assert "wrote" in run.stdout
    with open(path) as fh:
        return json.load(fh)


def _verdict(tmp_path, rows, anchor_path, key_file, fps, columns=None):
    export = {"format": "sentinel_os.ledger_export.v1",
              "columns": columns or list(tc.SHIPPED_COLUMNS), "rows": rows}
    export_path = tmp_path / "export-under-test.json"
    with open(export_path, "w") as fh:
        json.dump(export, fh, default=str)
    # the command also reads the ledger's own key environment; scrub it so
    # these tests hand it exactly the key file and nothing else
    env = {k: v for k, v in os.environ.items() if not k.startswith("ICEBERG_LEDGER_ATTESTATION")}
    run = subprocess.run(
        [sys.executable, TOOL, "--export", str(export_path), "--anchor", str(anchor_path),
         "--trusted-fingerprints", ",".join(fps), "--key-file", str(key_file)],
        capture_output=True, text=True, env=env)
    first = (run.stdout.splitlines() or [""])[0]
    return first.split(" ")[0], run


def _rechain(rows, start):
    """What an attacker with the file does after an edit: recompute every
    later hash so the unkeyed chain is self-consistent again."""
    for i in range(start, len(rows)):
        if i > 0:
            rows[i]["previous_hash"] = rows[i - 1]["current_hash"]
        rows[i]["current_hash"] = tc.recompute_current_hash(rows[i])


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    """A real enforced ledger with an anchor: the marker, unsigned-claim-free
    decisions, two abv3-signed accountable decisions and a legacy row,
    exported through scripts/export_ledger.py."""
    ledger, anchor_path = _anchored_ledger(tmp_path, monkeypatch, appends=2)
    try:
        for _ in range(2):
            assert ledger.append_decision(
                _record(authorized_by="harness:production"), governance_params=_params())
    finally:
        ledger.close()
    export = _export_to(tmp_path / "export.json")
    key_file = tmp_path / "keys.txt"
    key_file.write_text(_KEY.decode() + "\n")
    return {"rows": export["rows"], "anchor": anchor_path, "key_file": key_file,
            "fps": [att.key_fingerprint(_KEY)], "tmp": tmp_path}


def test_2_8_a_clean_export_is_verified_with_exit_zero(scenario):
    verdict, run = _verdict(scenario["tmp"], scenario["rows"], scenario["anchor"],
                            scenario["key_file"], scenario["fps"])
    assert verdict == "VERIFIED" and run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "VERIFIED"


def test_a_signature_by_an_untrusted_key_is_unattested_and_no_key_is_refused(scenario):
    other_file = scenario["tmp"] / "other.txt"
    other_file.write_text(_OTHER_KEY.decode() + "\n")
    verdict, run = _verdict(scenario["tmp"], scenario["rows"], scenario["anchor"],
                            other_file, [att.key_fingerprint(_OTHER_KEY)])
    assert verdict == tc.VIOLATION_UNATTESTED and run.returncode == 1, run.stdout
    # trusting a fingerprint without holding its key is not verification
    verdict, run = _verdict(scenario["tmp"], scenario["rows"], scenario["anchor"],
                            other_file, scenario["fps"])
    assert run.returncode == 2 and "no trusted key material" in run.stderr


# ---------------------------------------------------------------------------
# Step 2.9: acceptance tests a to g, through the offline command
# ---------------------------------------------------------------------------

def test_a_editing_a_rows_input_data_is_tampered(scenario):
    rows = [dict(r) for r in scenario["rows"]]
    target = next(i for i, r in enumerate(rows) if r["record_kind"] == "governance_decision")
    rows[target]["input_data"] = dict(rows[target]["input_data"], score=0.99)
    verdict, run = _verdict(scenario["tmp"], rows, scenario["anchor"],
                            scenario["key_file"], scenario["fps"])
    assert verdict == tc.VIOLATION_TAMPERED and run.returncode == 1, run.stdout
    assert f"row={rows[target]['id']}" in run.stdout


def test_b_copying_another_rows_subject_digest_is_transplanted(scenario):
    rows = [dict(r) for r in scenario["rows"]]
    decisions = [i for i, r in enumerate(rows) if r["record_kind"] == "governance_decision"]
    first, second = decisions[0], decisions[1]
    rows[second]["subject_digest"] = rows[first]["subject_digest"]
    _rechain(rows, second)
    verdict, run = _verdict(scenario["tmp"], rows, scenario["anchor"],
                            scenario["key_file"], scenario["fps"])
    assert verdict == tc.VIOLATION_TRANSPLANTED and run.returncode == 1, run.stdout
    assert f"row={rows[second]['id']}" in run.stdout
    assert "anchor mismatch" in run.stdout


def test_c_a_seed_the_server_would_not_derive_is_seed_forged(scenario):
    rows = [dict(r) for r in scenario["rows"]]
    target = next(i for i, r in enumerate(rows) if r["record_kind"] == "governance_decision")
    rows[target]["shuffle_seed"] = "0" * 64
    _rechain(rows, target)
    verdict, run = _verdict(scenario["tmp"], rows, scenario["anchor"],
                            scenario["key_file"], scenario["fps"])
    assert verdict == tc.VIOLATION_SEED_FORGED and run.returncode == 1, run.stdout
    # the seed the server WOULD have derived passes the same check
    rows = [dict(r) for r in scenario["rows"]]
    rows[target]["shuffle_seed"] = att.derive_shuffle_seed(
        rows[target]["previous_hash"], rows[target]["record_kind"], _KEY)
    _rechain(rows, target)
    assert tc.verify_shuffle_seed_row(rows[target], _KEY)[0]
    verdict, run = _verdict(scenario["tmp"], rows, scenario["anchor"],
                            scenario["key_file"], scenario["fps"])
    # the seed check is satisfied; what objects now is the rebuilt tail (the
    # later abv3 signatures cover their previous_hash, and the anchor sealed
    # the original head), which is exactly what a rebuilt chain should trip
    assert verdict != tc.VIOLATION_SEED_FORGED, run.stdout
    assert verdict in (tc.VIOLATION_TAMPERED, tc.VIOLATION_TRUNCATED), run.stdout


def test_d_nulling_a_signature_after_the_marker_is_unattested(scenario):
    """The attack that passed before this change: strip a signature and
    recompute every later hash. The marker makes it UNATTESTED, and the
    anchor also reports the head mismatch."""
    rows = [dict(r) for r in scenario["rows"]]
    signed = next(i for i, r in enumerate(rows) if r.get("authorized_by_sig"))
    marker = next(i for i, r in enumerate(rows) if r["record_kind"] == "attestation_policy")
    assert marker < signed
    rows[signed]["authorized_by_sig"] = None
    _rechain(rows, signed)
    verdict, run = _verdict(scenario["tmp"], rows, scenario["anchor"],
                            scenario["key_file"], scenario["fps"])
    assert verdict == tc.VIOLATION_UNATTESTED and run.returncode == 1, run.stdout
    assert f"row={rows[signed]['id']}" in run.stdout
    assert "anchor mismatch" in run.stdout


def test_e_deleting_the_last_three_rows_is_truncated(scenario):
    rows = [dict(r) for r in scenario["rows"]][:-3]
    verdict, run = _verdict(scenario["tmp"], rows, scenario["anchor"],
                            scenario["key_file"], scenario["fps"])
    assert verdict == tc.VIOLATION_TRUNCATED and run.returncode == 1, run.stdout
    assert "missing from the tail" in run.stdout


def test_f_a_ledger_written_before_this_change_is_verified(tmp_path):
    """No marker row, none of the new fields, unsigned accountable claims
    before any policy, one abv2 signature: VERIFIED. Old rows kept their
    bytes and their standing."""
    rows = _fixture_rows()
    anchor_path = tmp_path / "fixture.anchor"
    tc.write_head_anchor(str(anchor_path), rows[-1]["current_hash"], len(rows), _FIXTURE_KEY)
    key_file = tmp_path / "keys.txt"
    key_file.write_text(_FIXTURE_KEY.decode() + "\n")
    verdict, run = _verdict(tmp_path, rows, anchor_path, key_file,
                            [att.key_fingerprint(_FIXTURE_KEY)])
    assert verdict == "VERIFIED" and run.returncode == 0, run.stdout + run.stderr


def test_g_200_rows_writer_hash_equals_witness_recompute(tmp_path, monkeypatch):
    """Acceptance (g): 200 generated decisions with and without each optional
    field, plus the marker and a legacy row. Every row the writer hashed,
    the witness recomputes byte for byte, and the primary verifier agrees."""
    ledger, _ = _anchored_ledger(tmp_path, monkeypatch, appends=0)
    try:
        params = _params()
        last_decision_hash = None
        for i in range(200):
            kw = {}
            if i % 2:
                kw["authorized_by"] = "harness:production"
            if i % 3 == 0:
                kw["model_identity"] = f"model-{i}"
            if i % 4 == 0:
                kw["outcome_obligation"] = "loan_performance@24mo"
            if i % 5 == 0:
                kw["ai_cost"] = {"model": "m", "input_tokens": i, "output_tokens": 1, "cost_usd": 0.0}
            if i % 6 == 0:
                kw["cassette_code_hash"] = "c" * 64
            if i % 7 == 0 and last_decision_hash:
                kw["replaces_hash"] = last_decision_hash
            if i % 8 == 0:
                kw["output"] = {"approved": True, "confidence": i / 200}
            assert ledger.append_decision(_record(**kw), governance_params=params)
            last_decision_hash = _rows("record_kind = 'governance_decision'")[-1]["current_hash"]
    finally:
        ledger.close()
    rows = _rows()
    assert len(rows) == 202
    seen_with = {f: False for f in ("subject_digest", "authorized_by_sig", "model_identity",
                                    "outcome_obligation", "ai_cost", "cassette_code_hash",
                                    "replaces_hash")}
    for row in rows:
        ok, detail = tc.deep_verify_row(row, keys=_KEY)
        assert ok, f"row {row['id']}: {detail}"
        for f in seen_with:
            seen_with[f] = seen_with[f] or bool(row.get(f))
    assert all(seen_with.values()), seen_with
    assert any(not r.get("subject_digest") for r in rows)        # the marker and the legacy row
    assert PostgreSQLLedger(**_PG).verify_chain()["ok"]
    assert tc.verify_rows(rows, keys=_KEY) is None
