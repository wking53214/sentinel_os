"""
Forensic cassette serialization for ledger reconstruction.

When a decision is made, we capture the ENTIRE cassette configuration
at that moment (not just the version string). This lets regulators ask:
"Did you change the cassette after this decision was made?" and proves
the answer in the ledger.

Every cassette snapshot is:
- Serialized deterministically (sort_keys=True)
- Hashed with SHA-256
- Stored in the ledger alongside the decision
- Linked via the hash chain (tampering breaks the chain)

Regulators can then call reconstruct_cassette_for_decision(decision_id)
to pull the exact policy from the ledger and cryptographically verify
it matches the cassette hash stored in the chain.
"""

import json
import hashlib
from typing import Any, Dict


def serialize_cassette_for_ledger(governance_params: Any) -> Dict[str, Any]:
    """
    Serialize the cassette snapshot for storage in the ledger.

    Takes a GovernanceParameters object (from cassette_schema.py) and
    returns a JSON-safe dict that captures:
    - Schema version (self-describing: records survive schema evolution)
    - Cassette version (domain:name:version)
    - Every parameter with bounds, type, value, metadata

    This snapshot becomes part of the ledger record and is
    cryptographically linked via the hash chain. It answers:
    "What policy governed this decision?"
    """
    # governance_params is a GovernanceParameters from cassette_schema.py
    # It has cassette_version and _parameters attributes
    snapshot = {
        "schema_version": governance_params.snapshot()["schema_version"],
        "cassette_version": governance_params.cassette_version,
        "parameters": {
            name: spec.as_snapshot()
            for name, spec in sorted(governance_params._parameters.items())
        },
    }
    return snapshot


def compute_cassette_hash(cassette_snapshot: Dict[str, Any]) -> str:
    """
    Deterministic SHA-256 hash of the cassette snapshot.

    Use this to prove the cassette at decision time matches the one
    you have now. If the hashes don't match, the cassette was changed
    after the decision was made (tampering detected).

    The hash is deterministic (sort_keys=True) so the same cassette
    always produces the same hash.
    """
    canonical = json.dumps(cassette_snapshot, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def reconstruct_cassette_for_decision(ledger_decision: Dict[str, Any]) -> Dict[str, Any]:
    """
    Given a decision record from the ledger, reconstruct the cassette
    that governed it.

    This is the "show me your proof" endpoint for regulators.

    Returns:
    {
        "decision_id": <id>,
        "cassette_snapshot": <full cassette config>,
        "cassette_hash": <SHA-256>,
        "cassette_version": <domain:name:version>,
        "timestamp": <ISO 8601>,
        "integrity_verified": True/False (hash matches)
    }

    Raises ValueError if:
    - No cassette snapshot in the decision (pre-migration record)
    - Cassette snapshot is corrupted (hash mismatch)
    """
    cassette_snapshot = ledger_decision.get("cassette_snapshot")
    cassette_hash = ledger_decision.get("cassette_hash")

    if not cassette_snapshot:
        raise ValueError(
            f"Decision {ledger_decision.get('id')} has no cassette snapshot. "
            "It was recorded before cassette snapshots were stored in the ledger "
            "(pre-forensic-upgrade record). Cannot reconstruct cassette."
        )

    if not cassette_hash:
        raise ValueError(
            f"Decision {ledger_decision.get('id')}: cassette_hash is missing. "
            "Ledger record is incomplete."
        )

    # Verify integrity: recompute hash and compare
    computed_hash = compute_cassette_hash(cassette_snapshot)
    integrity_ok = computed_hash == cassette_hash

    if not integrity_ok:
        raise ValueError(
            f"Decision {ledger_decision.get('id')}: cassette snapshot is CORRUPTED. "
            f"Stored hash: {cassette_hash}, recomputed: {computed_hash}. "
            f"Ledger entry may have been tampered with."
        )

    return {
        "decision_id": ledger_decision.get("id"),
        "cassette_snapshot": cassette_snapshot,
        "cassette_hash": cassette_hash,
        "cassette_version": ledger_decision.get("cassette_version"),
        "timestamp": ledger_decision.get("timestamp"),
        "integrity_verified": True,
    }


def validate_cassette_snapshot_chain(ledger_decisions: list) -> Dict[str, Any]:
    """
    Audit multiple decisions to prove all cassette snapshots are
    consistent and uncorrupted.

    Returns:
    {
        "total_decisions": N,
        "snapshots_verified": M,
        "corrupted": [],
        "pre_migration": [],
        "all_ok": True/False
    }

    Used for regulatory audits: "Prove your cassette snapshots are real."
    """
    result = {
        "total_decisions": len(ledger_decisions),
        "snapshots_verified": 0,
        "corrupted": [],
        "pre_migration": [],
        "all_ok": True,
    }

    for decision in ledger_decisions:
        decision_id = decision.get("id")

        if not decision.get("cassette_snapshot"):
            result["pre_migration"].append(decision_id)
            continue

        try:
            reconstruct_cassette_for_decision(decision)
            result["snapshots_verified"] += 1
        except ValueError as e:
            result["corrupted"].append(
                {"decision_id": decision_id, "error": str(e)}
            )
            result["all_ok"] = False

    return result
