"""Counterparty-scoped attestation and anchoring (Part 5).

Three things are proven here that nothing else proves:

  * the report leaks nothing about another counterparty, asserted on
    the OUTPUT BYTES rather than field by field;
  * an anchor receipt catches alteration of the slice it committed to;
  * all three hash recompute sites agree on the four new contract
    record kinds -- the failure that already bit outcome_harm_event
    once, where honest rows fail their own verification and it looks
    exactly like tampering.
"""
from datetime import datetime, timedelta, timezone

import pytest

from contract_attestation import (
    COMPLETENESS_MECHANISM,
    DISCLOSED_LIMITATIONS,
    REDACTED_ROW_FIELDS,
    attestation_bytes,
    build_attestation,
    issue_anchor_receipt,
    partition_chain,
    partition_head,
    redact_row,
    verify_continuity,
    verify_receipt_signature,
)
from contract_cassette import (
    RECIPIENT_CLASS_SUBCONTRACTOR,
    TERM_EGRESS_REQUIRES_APPROVAL,
    TERM_RETENTION_MAX_DAYS,
    ContractCassette,
    ContractCassetteRegistry,
    ContractTerm,
)
from contract_egress import EGRESS_AUTHORIZED, EgressRequest, request_egress
from twin_custody import (
    generate_signing_keypair,
    recompute_current_hash,
)

T0 = "2026-06-01T00:00:00+00:00"
T1 = "2026-06-15T00:00:00+00:00"
NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _contract(counterparty, max_days=90):
    class _Lens(ContractCassette):
        def get_counterparty_id(self):
            return counterparty

        def get_contract_reference(self):
            return f"DPA-{counterparty}-0001"

        def get_contract_version(self):
            return "1.0.0"

        def get_terms(self):
            return (
                ContractTerm(TERM_RETENTION_MAX_DAYS, {"max_days": max_days}),
                ContractTerm(TERM_EGRESS_REQUIRES_APPROVAL,
                             {"recipient_class": RECIPIENT_CLASS_SUBCONTRACTOR}),
            )

    return _Lens()


def _row(current_hash, kind="contract_ingest"):
    return {"id": 1, "record_kind": kind, "current_hash": current_hash,
            "data": {}}


# -- partition chain ---------------------------------------------------

def test_empty_partition_has_a_defined_head():
    """A counterparty with nothing recorded should still be able to hold
    a receipt and later prove nothing was retro-inserted behind it."""
    assert partition_head([]) == "genesis"


def test_partition_head_changes_when_a_row_is_altered():
    before = partition_head([_row("a"), _row("b"), _row("c")])
    after = partition_head([_row("a"), _row("CHANGED"), _row("c")])
    assert before != after


def test_partition_head_changes_when_rows_are_reordered():
    before = partition_head([_row("a"), _row("b")])
    after = partition_head([_row("b"), _row("a")])
    assert before != after


def test_partition_head_changes_when_a_row_is_removed():
    before = partition_head([_row("a"), _row("b"), _row("c")])
    after = partition_head([_row("a"), _row("c")])
    assert before != after


def test_partition_chain_is_a_prefix_chain():
    """Appending must not disturb earlier steps, or a held receipt would
    go stale on every new row."""
    short = partition_chain([_row("a"), _row("b")])
    longer = partition_chain([_row("a"), _row("b"), _row("c")])
    assert [s["partition_hash"] for s in short] == \
           [s["partition_hash"] for s in longer[:2]]


# -- anchor receipts ---------------------------------------------------

def test_receipt_verifies_against_the_signer_key():
    priv, pub = generate_signing_keypair()
    receipt = issue_anchor_receipt("acme-bank", [_row("a")], priv, pub)
    assert verify_receipt_signature(receipt, pub) is True


def test_receipt_does_not_verify_against_a_different_key():
    priv, pub = generate_signing_keypair()
    _, other_pub = generate_signing_keypair()
    receipt = issue_anchor_receipt("acme-bank", [_row("a")], priv, pub)
    assert verify_receipt_signature(receipt, other_pub) is False


def test_continuity_holds_when_rows_are_only_appended():
    priv, pub = generate_signing_keypair()
    rows = [_row("a"), _row("b")]
    receipt = issue_anchor_receipt("acme-bank", rows, priv, pub)
    verdict = verify_continuity(receipt, rows + [_row("c")], pub)
    assert verdict["verdict"] == "CONTINUOUS"
    assert verdict["mechanism"] == COMPLETENESS_MECHANISM


def test_continuity_catches_alteration_of_a_committed_row():
    """The operator runs the ledger. This is the check that does not
    rely on their copy of the head."""
    priv, pub = generate_signing_keypair()
    rows = [_row("a"), _row("b")]
    receipt = issue_anchor_receipt("acme-bank", rows, priv, pub)
    verdict = verify_continuity(receipt, [_row("a"), _row("TAMPERED")], pub)
    assert verdict["verdict"] == "DIVERGED"


def test_continuity_catches_removal_of_a_committed_row():
    priv, pub = generate_signing_keypair()
    rows = [_row("a"), _row("b"), _row("c")]
    receipt = issue_anchor_receipt("acme-bank", rows, priv, pub)
    verdict = verify_continuity(receipt, [_row("a"), _row("b")], pub)
    assert verdict["verdict"] == "DIVERGED"
    assert "removed" in verdict["reason"]


def test_a_forged_receipt_is_reported_invalid_not_treated_as_continuous():
    priv, pub = generate_signing_keypair()
    _, other_pub = generate_signing_keypair()
    receipt = issue_anchor_receipt("acme-bank", [_row("a")], priv, pub)
    verdict = verify_continuity(receipt, [_row("a")], other_pub)
    assert verdict["verdict"] == "INVALID_RECEIPT"


def test_continuity_never_raises_on_a_mismatch():
    """A mismatch is a finding to report, not an error to swallow."""
    priv, pub = generate_signing_keypair()
    receipt = issue_anchor_receipt("acme-bank", [_row("a")], priv, pub)
    assert verify_continuity(receipt, [], pub)["verdict"] == "DIVERGED"


def test_receipt_payload_is_stable_between_issue_and_verify():
    priv, pub = generate_signing_keypair()
    receipt = issue_anchor_receipt("acme-bank", [_row("a")], priv, pub,
                                   issued_at=T0)
    assert receipt.payload()["issued_at"] == T0
    assert receipt.payload()["partition_head"] == receipt.partition_head


# -- redaction ---------------------------------------------------------

def test_global_ledger_id_is_stripped_from_shown_rows():
    """The id is a global sequence: gaps in it are a side-channel onto
    another counterparty's volume."""
    assert "id" in REDACTED_ROW_FIELDS
    assert "id" not in redact_row(_row("a"))


def test_row_hash_survives_redaction():
    """Opaque and load-bearing -- it is what makes the row verifiable."""
    assert redact_row(_row("a"))["current_hash"] == "a"


# -- full report, against a real ledger -------------------------------

def _seed_two_counterparties(ledger):
    """Two counterparties with overlapping-shaped activity, so a leak
    would have something recognisable to leak."""
    registry = ContractCassetteRegistry()
    acme = _contract("acme-bank")
    beta = _contract("beta-corp-SECRETNAME")
    registry.register(acme)
    registry.register(beta)

    for contract, ingest_id, recipient in (
            (acme, "ING-ACME-1", "vendor-x"),
            (beta, "ING-BETA-SECRET-1", "vendor-beta-SECRETVENDOR")):
        cp = contract.get_counterparty_id()
        version = f"contract:{cp}:1.0.0"
        ledger.record_contract_ingest(
            contract_version=version, counterparty=cp, ingest_id=ingest_id,
            data_scope="customer_records", received_at=T0)
        ledger.record_contract_approval(
            contract_version=version, counterparty=cp,
            approval_id=f"AP-{cp}", state="granted", approver="compliance",
            recipient=recipient, recipient_class=RECIPIENT_CLASS_SUBCONTRACTOR,
            scope="customer_records", granted_at=T0)
        request_egress(ledger, registry, EgressRequest(
            counterparty_id=cp, data_scope="customer_records",
            recipient=recipient, recipient_class=RECIPIENT_CLASS_SUBCONTRACTOR,
            purpose="fraud_screening", approval_reference=f"AP-{cp}",
            occurred_at=T1), authorized_by="export-service")
        ledger.record_contract_deletion(
            contract_version=version, counterparty=cp, ingest_id=ingest_id,
            deleted_at=T1, scope="active", method="hard_delete")
    return registry, acme, beta


def test_report_contains_nothing_about_the_other_counterparty(test_ledger):
    """Asserted on the output BYTES. A field that leaks through a nested
    dict or a serialization quirk would pass a field-by-field check and
    fail here."""
    _, acme, beta = _seed_two_counterparties(test_ledger)
    rendered = attestation_bytes(build_attestation(test_ledger, acme, now=NOW))

    for secret in (b"beta-corp-SECRETNAME", b"ING-BETA-SECRET-1",
                   b"vendor-beta-SECRETVENDOR", b"AP-beta-corp-SECRETNAME"):
        assert secret not in rendered


def test_report_does_not_reveal_that_other_counterparties_exist(test_ledger):
    """No global counts, no global head, no global row ids."""
    _, acme, _ = _seed_two_counterparties(test_ledger)
    report = build_attestation(test_ledger, acme, now=NOW).as_dict()

    assert report["slice_completeness"]["row_count"] == 4  # acme's own rows
    for row in report["egress"]:
        assert "id" not in row


def test_report_is_scoped_by_the_query_not_by_python_filtering(test_ledger):
    """get_contract_rows filters in SQL. A generator that has to remember
    to filter is one that will eventually forget."""
    _seed_two_counterparties(test_ledger)
    rows = test_ledger.get_contract_rows("acme-bank")
    assert rows
    assert all(r["data"]["counterparty"] == "acme-bank" for r in rows)


def test_report_surfaces_the_contract_content_hash(test_ledger):
    """'The hash in my report matches the contract I signed' has to be
    checkable from the report itself."""
    _, acme, _ = _seed_two_counterparties(test_ledger)
    report = build_attestation(test_ledger, acme, now=NOW).as_dict()
    assert len(report["contract"]["content_hash"]) == 64
    assert report["contract"]["reference"] == "DPA-acme-bank-0001"


def test_report_names_its_completeness_mechanism(test_ledger):
    _, acme, _ = _seed_two_counterparties(test_ledger)
    report = build_attestation(test_ledger, acme, now=NOW).as_dict()
    assert report["slice_completeness"]["mechanism"] == COMPLETENESS_MECHANISM
    assert "submission record" in report["slice_completeness"]["mechanism_note"]


def test_report_states_the_chokepoint_limitation(test_ledger):
    """Required in the module docstring, the README, and every report."""
    _, acme, _ = _seed_two_counterparties(test_ledger)
    report = build_attestation(test_ledger, acme, now=NOW).as_dict()
    joined = " ".join(report["disclosed_limitations"])
    assert "never called the chokepoint" in joined
    assert "attested by the operator" in joined
    assert "train a model" in joined


def test_report_carries_the_screening_disclaimer(test_ledger):
    _, acme, _ = _seed_two_counterparties(test_ledger)
    report = build_attestation(test_ledger, acme, now=NOW).as_dict()
    assert "not a legal determination" in report["disclaimer"]
    assert len(DISCLOSED_LIMITATIONS) >= 5


def test_report_includes_retention_and_approval_findings(test_ledger):
    _, acme, _ = _seed_two_counterparties(test_ledger)
    report = build_attestation(test_ledger, acme, now=NOW).as_dict()
    assert report["retention"]["summary"]["deleted_on_time"] == 1
    assert report["subcontractor_approvals"][0]["verdict"] == "PASS"


def test_report_can_embed_a_signed_receipt(test_ledger):
    _, acme, _ = _seed_two_counterparties(test_ledger)
    priv, pub = generate_signing_keypair()
    report = build_attestation(test_ledger, acme, now=NOW,
                               signing_priv_b64=priv,
                               signer_pub_b64=pub).as_dict()
    assert report["anchor_receipt"]["counterparty_id"] == "acme-bank"
    assert report["anchor_receipt"]["partition_head"] == \
        report["slice_completeness"]["head"]


def test_receipt_over_one_counterparty_is_unaffected_by_another_counterpartys_rows(
        test_ledger):
    """Per-counterparty partitions, not the global head: activity by
    someone else must not disturb a receipt already held."""
    registry, acme, beta = _seed_two_counterparties(test_ledger)
    priv, pub = generate_signing_keypair()
    rows_before = test_ledger.get_contract_rows("acme-bank")
    receipt = issue_anchor_receipt("acme-bank", rows_before, priv, pub)

    test_ledger.record_contract_ingest(
        contract_version="contract:beta-corp-SECRETNAME:1.0.0",
        counterparty="beta-corp-SECRETNAME", ingest_id="ING-BETA-2",
        data_scope="customer_records", received_at=T1)

    verdict = verify_continuity(
        receipt, test_ledger.get_contract_rows("acme-bank"), pub)
    assert verdict["verdict"] == "CONTINUOUS"


# -- three recompute sites agree --------------------------------------

@pytest.mark.parametrize("kind", ["contract_ingest", "contract_egress",
                                  "contract_approval", "contract_deletion"])
def test_every_contract_record_kind_verifies_on_the_primary(test_ledger, kind):
    """verify_chain is recompute site 2. A kind missing a branch there
    falls through to the legacy path and fails its own verification."""
    _seed_two_counterparties(test_ledger)
    result = test_ledger.verify_chain()
    assert result["ok"] is True, result
    assert result["violations"] == []

    rows = test_ledger.get_contract_rows("acme-bank", record_kinds=(kind,))
    assert rows, f"fixture wrote no {kind} rows"


def test_twin_recomputes_every_contract_kind_identically(test_ledger):
    """Recompute site 3. If the witness disagrees with the writer, every
    honest row reads as DIVERGE on the twin -- indistinguishable from
    tampering, and the highest-risk failure mode of this whole change."""
    _seed_two_counterparties(test_ledger)

    import psycopg2
    conn = psycopg2.connect(connect_timeout=2, host="localhost", port=5432,
                            dbname="iceberg", user="iceberg",
                            password="iceberg")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("""
        SELECT record_kind, cassette_version, data, decision_output,
               cassette_hash, authorized_by, previous_hash, current_hash,
               action_type, node, previous_value, applied_value, reason
        FROM ledger_entries
        WHERE record_kind LIKE 'contract_%'
        ORDER BY id ASC
    """)
    fetched = cur.fetchall()
    conn.close()

    assert len(fetched) >= 8
    seen = set()
    for r in fetched:
        row = {
            "record_kind": r[0], "cassette_version": r[1], "data": r[2],
            "decision_output": r[3], "cassette_hash": r[4],
            "authorized_by": r[5], "previous_hash": r[6], "current_hash": r[7],
            "action_type": r[8], "node": r[9], "previous_value": r[10],
            "applied_value": r[11], "reason": r[12],
        }
        assert recompute_current_hash(row) == row["current_hash"], \
            f"twin disagrees with the writer on {row['record_kind']}"
        seen.add(row["record_kind"])

    assert seen == {"contract_ingest", "contract_egress", "contract_approval",
                    "contract_deletion"}


def test_a_tampered_contract_row_is_caught_by_the_primary(test_ledger):
    """Not just 'the hashes agree when nothing changed' -- the check has
    to fail when something does."""
    _seed_two_counterparties(test_ledger)
    row = test_ledger.get_contract_rows(
        "acme-bank", record_kinds=("contract_egress",))[0]
    row = dict(row)
    row["data"] = dict(row["data"])
    row["data"]["decision"] = "refused" if \
        row["data"]["decision"] == EGRESS_AUTHORIZED else EGRESS_AUTHORIZED
    row.update({"action_type": "contract_egress", "node": "x",
                "previous_value": 0.0, "applied_value": 0.0, "reason": "x"})
    assert recompute_current_hash(row) != row["current_hash"]


def test_ingest_without_an_ingest_id_is_refused_at_write(test_ledger):
    with pytest.raises(ValueError):
        test_ledger.record_contract_ingest(
            contract_version="contract:acme-bank:1.0.0",
            counterparty="acme-bank", ingest_id="", data_scope="records",
            received_at=T0)


def test_deletion_without_a_method_is_refused_at_write(test_ledger):
    with pytest.raises(ValueError):
        test_ledger.record_contract_deletion(
            contract_version="contract:acme-bank:1.0.0",
            counterparty="acme-bank", ingest_id="ING-1", deleted_at=T1,
            scope="active", method="")


def test_unknown_deletion_scope_is_refused_at_write(test_ledger):
    with pytest.raises(ValueError):
        test_ledger.record_contract_deletion(
            contract_version="contract:acme-bank:1.0.0",
            counterparty="acme-bank", ingest_id="ING-1", deleted_at=T1,
            scope="tape-vault", method="hard_delete")


def test_get_contract_rows_rejects_an_unknown_record_kind(test_ledger):
    with pytest.raises(ValueError):
        test_ledger.get_contract_rows("acme-bank",
                                      record_kinds=("governance_decision",))


def test_report_of_a_counterparty_with_no_activity_is_well_formed(test_ledger):
    """Empty must not mean broken, and must not mean compliant either."""
    contract = _contract("quiet-corp")
    report = build_attestation(test_ledger, contract, now=NOW).as_dict()
    assert report["slice_completeness"]["row_count"] == 0
    assert report["slice_completeness"]["head"] == "genesis"
    assert report["retention"]["summary"]["overdue"] == 0
    assert report["egress"] == []


def test_overdue_record_shows_as_overdue_in_a_real_report(test_ledger):
    old = (NOW - timedelta(days=400)).isoformat()
    contract = _contract("late-corp", max_days=90)
    test_ledger.record_contract_ingest(
        contract_version="contract:late-corp:1.0.0", counterparty="late-corp",
        ingest_id="ING-OLD", data_scope="records", received_at=old)
    report = build_attestation(test_ledger, contract, now=NOW).as_dict()
    assert report["retention"]["summary"]["overdue"] == 1
