"""Counterparty-scoped attestation -- what one customer may see, and prove.

WHAT THIS PRODUCES
------------------
A report scoped to exactly one counterparty, containing their contract
terms and its content hash, their egress history, their retention
status, their subcontractor-approval findings, and an anchor receipt
over their own partition of the chain. It reveals nothing about any
other counterparty, including that any other counterparty exists.

THE LEAK SURFACE IS NOT ONLY IDENTIFIERS
----------------------------------------
Filtering rows by counterparty is the obvious half. The half that is
easy to miss is metadata that is not "about" anyone by name and still
leaks:

  * `ledger_entries.id` is a GLOBAL sequence. Handing a counterparty
    their rows with ids 41, 87, 214 tells them 170-odd other rows were
    written in between, by someone. Volume, cadence, and the bare fact
    that other customers exist, all from a column nobody would call
    identifying. The id is stripped, and REDACTED_ROW_FIELDS is the
    single list saying so.
  * A GLOBAL chain head would leak the same way over time: two
    receipts a month apart, differing, say nothing on their own, but a
    global head that a counterparty could compare against their own
    row count tells them how much else moved. This is why the anchor
    receipt below covers the counterparty's OWN partition, never the
    global head.

`previous_hash` is kept: it is an opaque digest, it is what makes the
row verifiable, and it names no one.

SLICE COMPLETENESS -- HOW A COUNTERPARTY KNOWS THEY SAW EVERYTHING
-------------------------------------------------------------------
Two layers, and the report states which is which because each closes
what the other cannot.

1. PARTITION CHAIN (in-chain completeness). Their rows are folded, in
   ledger order, into a partition chain: each step hashes the previous
   partition hash together with the row's own current_hash. Removing,
   reordering or altering any row in their slice changes the partition
   head. Combined with an anchor receipt they already hold, a later
   verification catches it. This is the mechanism named in the report.

2. INDEPENDENT COMPLETENESS CROSS-CHECK (pre-chain completeness). A
   partition chain cannot show a row that was dropped BEFORE it was
   ever chained -- there is nothing in the chain to be missing. That
   gap is closed by twin_attestation_spec_v1.md §6.4's ICC, which
   anchors completeness in the counterparty's OWN submission record.
   That mechanism is reused here rather than reinvented, and it fits
   this case better than the one it was built for: here the
   counterparty IS the sender, so they naturally hold the record it
   needs. The corresponding duty is theirs and the report says so
   plainly -- Sentinel cannot supply a log that exists precisely
   because Sentinel does not control it.

ANCHOR RECEIPTS
---------------
A signed, timestamped statement of the partition head at a point in
time, published to the counterparty on a schedule. The counterparty
keeps it. Later verification checks continuity against the receipt
THEY hold, not against anything the operator stores.

Without this, tamper-evidence is close to worthless in exactly the
situation it is for: the operator runs the ledger, and a counterparty
with no subpoena power comparing the operator's chain against the
operator's own copy of the head is checking the operator's arithmetic,
not the operator's honesty.

Signing reuses twin_custody's Ed25519 primitives and canonical_json.
No new crypto.

WHAT THIS REPORT CANNOT SAY
---------------------------
Declared in DISCLOSED_LIMITATIONS and reproduced in every report:
completeness only relative to the chokepoint, deletion claims
attested rather than verified, and nothing at all about what a
recipient did with data after it left.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import cassette_forensics
from contract_cassette import ContractCassette, contract_cassette_version_of
from contract_egress import (
    EGRESS_SCOPE_NOTE,
    check_subcontractor_approvals,
)
from contract_retention import assess_counterparty, summarize
from regulatory_cassette_interface import SCREENING_DISCLAIMER
from twin_custody import canonical_json, fingerprint, sign, verify_signature

ATTESTATION_SCHEMA_VERSION = "1.0.0"

# Row fields stripped before a row is shown to a counterparty. See the
# module docstring: `id` is a global sequence and its gaps are a
# side-channel onto other counterparties' volume.
REDACTED_ROW_FIELDS: Tuple[str, ...] = ("id",)

# The mechanism the report names for slice completeness.
COMPLETENESS_MECHANISM = "per_counterparty_partition_chain_plus_icc"

DISCLOSED_LIMITATIONS: Tuple[str, ...] = (
    EGRESS_SCOPE_NOTE,
    "Deletion events are attested by the operator, not verified by Sentinel. "
    "Sentinel does not delete data and does not observe deletion; it records "
    "what the operator states and, more importantly, refuses to treat silence "
    "as compliance -- a record past its retention horizon with no deletion "
    "attested is reported overdue, never within term.",
    "This report cannot show whether data was used to train a model after it "
    "left the boundary. That is not provable from inside this boundary. The "
    "egress log states what left and to whom, and nothing about downstream use.",
    "This report cannot show whether a recipient resold or re-shared data "
    "beyond what the egress log records.",
    "The partition chain proves your slice has not been altered or reordered "
    "since a receipt you hold. Proving that a record about you was never "
    "chained in the first place requires the Independent Completeness "
    "Cross-check, which compares against your own submission record. That log "
    "is yours to keep; Sentinel cannot supply it, and that is the point of it.",
    "Findings are mechanical screening for human review, not a legal "
    "determination of contract compliance.",
)


def redact_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """One ledger row as a counterparty may see it."""
    return {k: v for k, v in row.items() if k not in REDACTED_ROW_FIELDS}


def partition_chain(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fold one counterparty's rows into their own hash chain.

    step_hash(n) = sha256(step_hash(n-1) || row.current_hash)

    Independent of the global chain's shape, so it leaks nothing about
    it, while still being pinned to it: each step commits to a
    current_hash that the global chain also commits to. Altering a row
    breaks both.
    """
    import hashlib

    chain: List[Dict[str, Any]] = []
    running = "genesis"
    for seq, row in enumerate(rows):
        row_hash = str(row.get("current_hash") or "")
        running = hashlib.sha256(
            f"{running}{row_hash}".encode("utf-8")).hexdigest()
        chain.append({
            "seq": seq,
            "record_kind": row.get("record_kind"),
            "row_hash": row_hash,
            "partition_hash": running,
        })
    return chain


def partition_head(rows: List[Dict[str, Any]]) -> str:
    """The current head of a counterparty's partition, or the genesis
    marker when they have no rows yet. An empty partition has a
    well-defined head on purpose: a counterparty with nothing recorded
    should still be able to hold a receipt and later prove that
    nothing was retro-inserted behind it."""
    chain = partition_chain(rows)
    return chain[-1]["partition_hash"] if chain else "genesis"


@dataclass(frozen=True)
class AnchorReceipt:
    """A signed, timestamped commitment to a counterparty's partition head."""

    counterparty_id: str
    partition_head: str
    row_count: int
    issued_at: str
    signer_fingerprint: str
    signature: str
    schema_version: str = ATTESTATION_SCHEMA_VERSION

    def payload(self) -> Dict[str, Any]:
        """Exactly the bytes that were signed. Kept as its own method so
        issuing and verifying cannot drift into signing different
        shapes of the same facts."""
        return receipt_payload(self.counterparty_id, self.partition_head,
                               self.row_count, self.issued_at,
                               self.schema_version)

    def as_dict(self) -> Dict[str, Any]:
        return {**self.payload(),
                "signer_fingerprint": self.signer_fingerprint,
                "signature": self.signature}


def receipt_payload(counterparty_id: str, head: str, row_count: int,
                    issued_at: str,
                    schema_version: str = ATTESTATION_SCHEMA_VERSION
                    ) -> Dict[str, Any]:
    return {
        "schema_version": schema_version,
        "kind": "contract_anchor_receipt",
        "counterparty_id": counterparty_id,
        "partition_head": head,
        "row_count": int(row_count),
        "issued_at": issued_at,
    }


def issue_anchor_receipt(counterparty_id: str, rows: List[Dict[str, Any]],
                         signing_priv_b64: str, signer_pub_b64: str,
                         issued_at: Optional[str] = None) -> AnchorReceipt:
    """Publish the counterparty's partition head, signed and timestamped.

    Reuses twin_custody's Ed25519 signing and canonical_json rather
    than introducing a second signing scheme -- the repo already has
    exactly one, and DAP already commits to it.
    """
    issued_at = issued_at or datetime.now(timezone.utc).isoformat()
    head = partition_head(rows)
    payload = receipt_payload(counterparty_id, head, len(rows), issued_at)
    return AnchorReceipt(
        counterparty_id=counterparty_id,
        partition_head=head,
        row_count=len(rows),
        issued_at=issued_at,
        signer_fingerprint=fingerprint(signer_pub_b64),
        signature=sign(payload, signing_priv_b64),
    )


def verify_receipt_signature(receipt: AnchorReceipt,
                             signer_pub_b64: str) -> bool:
    return verify_signature(receipt.payload(), receipt.signature, signer_pub_b64)


def verify_continuity(receipt: AnchorReceipt, rows: List[Dict[str, Any]],
                      signer_pub_b64: str) -> Dict[str, Any]:
    """Check current rows against a receipt the counterparty holds.

    The receipt commits to a head over the first `row_count` rows. If
    the operator later removed, altered or reordered anything in that
    prefix, recomputing the prefix head will not reproduce the
    receipt's head. Growth beyond the prefix is expected and fine.

    Returns a verdict dict; never raises on a mismatch, because a
    mismatch is a finding to report, not an error to swallow.
    """
    if not verify_receipt_signature(receipt, signer_pub_b64):
        return {"verdict": "INVALID_RECEIPT",
                "reason": "receipt signature did not verify against the "
                          "provided signer key",
                "counterparty_id": receipt.counterparty_id}

    if len(rows) < receipt.row_count:
        return {"verdict": "DIVERGED",
                "reason": "fewer rows present now than the receipt committed "
                          "to; rows have been removed",
                "counterparty_id": receipt.counterparty_id,
                "rows_now": len(rows),
                "rows_at_receipt": receipt.row_count}

    recomputed = partition_head(rows[:receipt.row_count])
    if recomputed != receipt.partition_head:
        return {"verdict": "DIVERGED",
                "reason": "the prefix committed to by this receipt no longer "
                          "recomputes to the same head; a row in your slice "
                          "was altered or reordered",
                "counterparty_id": receipt.counterparty_id,
                "expected_head": receipt.partition_head,
                "recomputed_head": recomputed}

    return {"verdict": "CONTINUOUS",
            "counterparty_id": receipt.counterparty_id,
            "rows_at_receipt": receipt.row_count,
            "rows_now": len(rows),
            "current_head": partition_head(rows),
            "mechanism": COMPLETENESS_MECHANISM}


@dataclass(frozen=True)
class Attestation:
    """One counterparty's complete, scoped report."""

    counterparty_id: str
    contract_reference: str
    contract_version: str
    contract_hash: str
    generated_at: str
    terms: List[Dict[str, Any]] = field(default_factory=list)
    egress: List[Dict[str, Any]] = field(default_factory=list)
    retention: List[Dict[str, Any]] = field(default_factory=list)
    retention_summary: Dict[str, int] = field(default_factory=dict)
    approval_findings: List[Dict[str, Any]] = field(default_factory=list)
    partition: Dict[str, Any] = field(default_factory=dict)
    receipt: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": ATTESTATION_SCHEMA_VERSION,
            "counterparty_id": self.counterparty_id,
            "contract": {
                "reference": self.contract_reference,
                "version": self.contract_version,
                # The hash a counterparty checks against the contract
                # they signed: bound at load time by
                # bind_cassette_version, surfaced here.
                "content_hash": self.contract_hash,
                "terms": self.terms,
            },
            "generated_at": self.generated_at,
            "egress": self.egress,
            "retention": {
                "summary": self.retention_summary,
                "findings": self.retention,
            },
            "subcontractor_approvals": self.approval_findings,
            "slice_completeness": self.partition,
            "anchor_receipt": self.receipt,
            "disclaimer": SCREENING_DISCLAIMER,
            "disclosed_limitations": list(DISCLOSED_LIMITATIONS),
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True)


def build_attestation(ledger, contract: ContractCassette,
                      now: Optional[datetime] = None,
                      signing_priv_b64: Optional[str] = None,
                      signer_pub_b64: Optional[str] = None,
                      issued_at: Optional[str] = None) -> Attestation:
    """Generate the scoped report for ONE counterparty.

    The scoping is done by the ledger query itself
    (get_contract_rows filters on data->>'counterparty' in SQL), not by
    filtering a global result set in Python afterwards. A report
    generator that has to remember to filter is one that will
    eventually forget, and the failure mode is silent disclosure.
    """
    now = now or datetime.now(timezone.utc)
    counterparty_id = contract.get_counterparty_id()

    rows = ledger.get_contract_rows(counterparty_id)
    by_kind: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_kind.setdefault(str(row.get("record_kind")), []).append(row)

    retention_findings = assess_counterparty(
        contract,
        by_kind.get("contract_ingest", []),
        by_kind.get("contract_deletion", []),
        now=now,
    )
    approval_findings = check_subcontractor_approvals(
        by_kind.get("contract_egress", []),
        by_kind.get("contract_approval", []),
    )

    chain = partition_chain(rows)
    partition: Dict[str, Any] = {
        "mechanism": COMPLETENESS_MECHANISM,
        "mechanism_note": (
            "Your rows are folded into a partition chain of their own: each "
            "step hashes the previous step together with that row's ledger "
            "hash. Removing, altering or reordering any row in your slice "
            "changes the head. Compare the head below against the anchor "
            "receipt you already hold. Records dropped before ever being "
            "chained are outside what this can show; that is what the "
            "Independent Completeness Cross-check against your own "
            "submission record is for."),
        "row_count": len(rows),
        "head": partition_head(rows),
        "chain": chain,
    }

    receipt_dict: Optional[Dict[str, Any]] = None
    if signing_priv_b64 and signer_pub_b64:
        receipt_dict = issue_anchor_receipt(
            counterparty_id, rows, signing_priv_b64, signer_pub_b64,
            issued_at=issued_at).as_dict()

    return Attestation(
        counterparty_id=counterparty_id,
        contract_reference=contract.get_contract_reference(),
        contract_version=contract_cassette_version_of(contract),
        contract_hash=cassette_forensics.compute_cassette_hash(
            contract.snapshot()),
        generated_at=now.isoformat(),
        terms=[t.as_dict() for t in contract.get_terms()],
        egress=[redact_row(r) for r in by_kind.get("contract_egress", [])],
        retention=[f.as_dict() for f in retention_findings],
        retention_summary=summarize(retention_findings),
        approval_findings=approval_findings,
        partition=partition,
        receipt=receipt_dict,
    )


def attestation_bytes(attestation: Attestation) -> bytes:
    """The exact bytes a counterparty receives. Used by the leak test,
    which asserts on the rendered output rather than on the object --
    a field that leaks through __repr__, a nested dict, or a
    serialization quirk would pass a field-by-field assertion and fail
    here."""
    return canonical_json(attestation.as_dict())
