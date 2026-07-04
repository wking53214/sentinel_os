# Row Count: 189

"""
verifier.py
-----------

Top‑Level Description
---------------------
This module implements Iceberg’s deterministic Replay Verifier. The verifier
compares two replay outputs — typically two snapshots or two ledger‑driven
replay bundles — and determines whether they are *replay‑equivalent*.

Replay equivalence means:
- Identical structural hash
- Identical routing trace
- Identical MARL/PPO traces
- Identical staffing deltas
- Identical Bayesian posterior evolution
- Identical recorder event stream
- Identical caller + queue state bundles

The verifier guarantees:
- Deterministic comparison
- Governance‑safe validation
- Telemetry‑ready equivalence reports
- Audit‑grade evidence bundles

Subsystem integrations:
- [ReplayRunner](ca://s?q=Explain_replay_runner)
- [ReplayLedger](ca://s?q=Explain_replay_ledger)
- [SnapshotEngine](ca://s?q=Explain_snapshot_engine)
- [Recorder](ca://s?q=Explain_recorder_subsystem)
- [GovernanceEnvelope](ca://s?q=Explain_governance_envelope)

Best‑in‑Class Notes
-------------------
- Determinism: Verifier never introduces randomness.
- Governance‑Safety: Strict structural comparison; no heuristics.
- Replay‑Safety: Identical inputs → identical verdict.
- Telemetry‑Ready: Reports can be signed and stored externally.
- Stateless Design: Pure functional comparison; no hidden state.
- Audit‑Integrity: Produces audit‑grade equivalence reports.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class VerificationResult:
    """
    Result of a replay equivalence check.

    Best‑in‑Class Notes:
    - Pure data container; no mutation.
    - Designed for telemetry and governance export.
    """
    equivalent: bool
    mismatches: Dict[str, Any]


class ReplayVerifier:
    """
    Deterministic replay equivalence verifier for Iceberg.

    Best‑in‑Class Notes:
    - Compares snapshots field‑by‑field.
    - Structural hash is the primary equivalence indicator.
    - Detailed mismatch reporting for governance audits.
    """

    def __init__(self):
        pass

    # ---------------------------------------------------------
    # INTERNAL HELPERS
    # ---------------------------------------------------------
    def _compare_field(self, name: str, a: Any, b: Any, mismatches: Dict[str, Any]):
        """
        Compare two fields deterministically.

        Best‑in‑Class Notes:
        - Strict equality: no fuzzy matching.
        - Mismatches recorded with full detail.
        """
        if a != b:
            mismatches[name] = {
                "expected": a,
                "actual": b,
            }

    def _compare_list(self, name: str, a: List[Any], b: List[Any], mismatches: Dict[str, Any]):
        """
        Compare two lists deterministically.

        Best‑in‑Class Notes:
        - Order matters — replay equivalence requires identical ordering.
        - Full diff recorded for governance audits.
        """
        if len(a) != len(b):
            mismatches[name] = {
                "expected_length": len(a),
                "actual_length": len(b),
            }
            return

        for idx, (x, y) in enumerate(zip(a, b)):
            if x != y:
                mismatches.setdefault(name, {})
                mismatches[name][idx] = {
                    "expected": x,
                    "actual": y,
                }

    # ---------------------------------------------------------
    # PUBLIC API
    # ---------------------------------------------------------
    def verify(self, snap_a: Dict[str, Any], snap_b: Dict[str, Any]) -> VerificationResult:
        """
        Verify replay equivalence between two snapshots.

        Best‑in‑Class Notes:
        - Structural hash is the strongest equivalence signal.
        - Field‑by‑field comparison ensures full governance transparency.
        """

        mismatches: Dict[str, Any] = {}

        # Structural hash
        self._compare_field("structural_hash", snap_a.get("structural_hash"), snap_b.get("structural_hash"), mismatches)

        # Caller states
        self._compare_list("caller_states", snap_a.get("caller_states", []), snap_b.get("caller_states", []), mismatches)

        # Queue states
        self._compare_list("queue_states", snap_a.get("queue_states", []), snap_b.get("queue_states", []), mismatches)

        # Routing trace
        self._compare_list("routing_trace", snap_a.get("routing_trace", []), snap_b.get("routing_trace", []), mismatches)

        # MARL trace
        self._compare_list("marl_trace", snap_a.get("marl_trace", []), snap_b.get("marl_trace", []), mismatches)

        # PPO trace
        self._compare_list("ppo_trace", snap_a.get("ppo_trace", []), snap_b.get("ppo_trace", []), mismatches)

        # Staffing trace
        self._compare_list("staffing_trace", snap_a.get("staffing_trace", []), snap_b.get("staffing_trace", []), mismatches)

        # Bayesian posteriors
        self._compare_field("bayes_posteriors", snap_a.get("bayes_posteriors"), snap_b.get("bayes_posteriors"), mismatches)

        # Recorder events
        self._compare_list("recorder_events", snap_a.get("recorder_events", []), snap_b.get("recorder_events", []), mismatches)

        equivalent = len(mismatches) == 0

        return VerificationResult(
            equivalent=equivalent,
            mismatches=mismatches,
        )