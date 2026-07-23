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
import inspect
from typing import Any, Dict


# Item 3: modules whose source is included in the cassette CODE hash beyond the
# concrete cassette module itself. These hold the decision logic a cassette
# inherits or shares (base scoring, intent-label mapping, the parameter schema
# that constrains behavior). This is a DECLARED allowlist, deliberately NOT the
# full transitive import closure: hashing every transitive dependency (stdlib,
# json, typing, ...) would make the hash brittle -- an unrelated edit anywhere
# in the dependency graph would change every cassette's code hash and force a
# re-binding for no governance reason. The tradeoff is explicit and documented:
# the hash covers the cassette's own code and the shared governance code it runs
# on, not code outside this boundary. COMPLIANCE.md states this scope honestly.
_GOVERNANCE_CODE_MODULES = (
    "cassette_interface",      # kernel Cassette ABC: judge/explain, identity, manifest
    "cassette_schema",         # parameter bounds/validation that constrain decisions
    "cassette_capabilities",   # capability contracts: which surfaces exist and what they require
    "episode",                 # the ground-truth record + its integrity invariants
)


def _module_source_or_marker(module_name: str) -> str:
    """Return a module's source, or an explicit unavailable-marker string.

    Never raises: if a module can't be located or read, the marker (which
    includes the module name) is hashed instead. That makes an unreadable
    dependency a VISIBLE, deterministic change in the code hash rather than a
    crash in the decision path -- fail-closed for the hash, since a decision
    whose code can't be hashed must not silently hash as if the code were fine.
    """
    try:
        import importlib
        module = importlib.import_module(module_name)
        return inspect.getsource(module)
    except Exception as exc:  # noqa: BLE001 -- any failure becomes a marker
        return f"<<UNAVAILABLE_MODULE:{module_name}:{type(exc).__name__}>>"


def compute_cassette_code_hash(cassette_obj: Any) -> str:
    """Deterministic SHA-256 over the cassette's DECISION CODE.

    cassette_hash covers parameter VALUES; this covers the CODE that runs on
    them (score_outcome_quality, _infer_intent_to_label, evaluate/validate, and
    the shared governance modules above). Two cassettes with identical
    parameters but different scoring logic hash IDENTICALLY under cassette_hash
    and DIFFERENTLY here -- which is the whole point of Item 3 / hole F-H.

    Runtime-stable: hashes source text, so the same checkout on two machines
    (and the same object loaded twice in one process) produces the same hash.
    Order is fixed and the concrete module is labeled, so the digest is
    reproducible and diff-attributable.

    Fail-closed: unreadable source becomes an explicit marker in the hashed
    text (see _module_source_or_marker), never an exception in the caller.
    """
    parts = []

    # The concrete cassette module (e.g. ivr_cassette). Prefer the module that
    # actually defines the object's class, so a subclass hashes its own file.
    try:
        concrete_module = type(cassette_obj).__module__
        concrete_src = inspect.getsource(inspect.getmodule(cassette_obj))
    except Exception as exc:  # noqa: BLE001
        concrete_module = getattr(type(cassette_obj), "__module__", "<unknown>")
        concrete_src = f"<<UNAVAILABLE_CASSETTE_SOURCE:{concrete_module}:{type(exc).__name__}>>"
    parts.append(f"# module: {concrete_module}\n{concrete_src}")

    # The declared shared governance code, in fixed order.
    for mod in _GOVERNANCE_CODE_MODULES:
        parts.append(f"# module: {mod}\n{_module_source_or_marker(mod)}")

    combined = "\n\n".join(parts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


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
    # One source of truth: GovernanceParameters.snapshot() IS the
    # policy snapshot shape. This function used to hand-build the same
    # dict field-by-field -- a second serializer that silently drifted
    # the moment snapshot() gained a field (the 2.0.0 capability
    # manifest), which is exactly the two-places-that-can-quietly-
    # disagree pattern the cassette system exists to end. Pre-existing
    # duplication, fixed here rather than patched around.
    return governance_params.snapshot()


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
