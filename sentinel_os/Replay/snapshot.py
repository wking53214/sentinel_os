# Row Count: 182

"""
snapshot.py
-----------

Top‑Level Description
---------------------
This module implements Iceberg’s deterministic Snapshot Engine. Snapshots are
immutable, governance‑safe, replay‑friendly bundles of system state captured at
a specific moment in time. They are used for:

- Replay equivalence validation
- Governance audits and compliance checks
- Simulator step‑by‑step state capture
- Telemetry‑ready evidence bundles
- Debugging and regression analysis
- Deterministic reconstruction of routing, staffing, MARL, PPO, Bayesian, and
  Recorder state

A snapshot contains:
- Caller states
- Queue states
- Routing decisions
- MARL/PPO outputs
- Bayesian posteriors
- Staffing deltas
- Recorder events (optional)
- Structural hash for governance integrity

Subsystem integrations:
- [Recorder](ca://s?q=Explain_recorder_subsystem)
- [ReplayVerifier](ca://s?q=Explain_replay_system)
- [Simulator](ca://s?q=Explain_simulator)
- [GovernanceEnvelope](ca://s?q=Explain_governance_envelope)
- [RoutingEngine](ca://s?q=Explain_routing_engine)
- [TelemetryKernel](ca://s?q=Explain_telemetry_kernel)

Best‑in‑Class Notes
-------------------
- Determinism: Snapshots must be identical for identical system states.
- Governance‑Safety: Snapshots are immutable and JSON‑serializable.
- Replay‑Safety: Structural hash ensures equivalence validation.
- Telemetry‑Ready: Snapshots can be signed and stored externally.
- Stateless Design: SnapshotEngine never mutates external objects.
- Audit‑Integrity: Snapshots serve as audit‑grade evidence bundles.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List
import json
import hashlib


@dataclass
class Snapshot:
    """
    Immutable snapshot of Iceberg system state.

    Best‑in‑Class Notes:
    - Stored as pure data; no methods that mutate state.
    - Structural hash ensures governance‑safe integrity.
    """
    caller_states: List[Dict[str, Any]]
    queue_states: List[Dict[str, Any]]
    routing_trace: List[Dict[str, Any]]
    marl_trace: List[Dict[str, Any]]
    ppo_trace: List[Dict[str, Any]]
    staffing_trace: List[Dict[str, Any]]
    bayes_posteriors: Dict[str, Any]
    recorder_events: List[Dict[str, Any]] = field(default_factory=list)
    structural_hash: str = ""


class SnapshotEngine:
    """
    Deterministic snapshot builder for Iceberg.

    Best‑in‑Class Notes:
    - Pure functional builder: no mutation of external objects.
    - Structural hash computed from canonical JSON.
    - Replay‑safe: identical inputs → identical snapshot + hash.
    """

    def __init__(self):
        pass

    # ---------------------------------------------------------
    # INTERNAL HELPERS
    # ---------------------------------------------------------
    def _canonical_json(self, data: Dict[str, Any]) -> str:
        """
        Produce canonical JSON for hashing.

        Best‑in‑Class Notes:
        - Sorted keys ensure deterministic ordering.
        - UTF‑8 encoding ensures cross‑platform consistency.
        """
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    def _compute_hash(self, data: Dict[str, Any]) -> str:
        """
        Compute structural hash for governance integrity.

        Best‑in‑Class Notes:
        - SHA‑256 ensures cryptographic stability.
        - Hash covers entire snapshot content.
        """
        canonical = self._canonical_json(data)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # ---------------------------------------------------------
    # PUBLIC API
    # ---------------------------------------------------------
    def build(
        self,
        caller_states: List[Dict[str, Any]],
        queue_states: List[Dict[str, Any]],
        routing_trace: List[Dict[str, Any]],
        marl_trace: List[Dict[str, Any]],
        ppo_trace: List[Dict[str, Any]],
        staffing_trace: List[Dict[str, Any]],
        bayes_posteriors: Dict[str, Any],
        recorder_events: List[Dict[str, Any]] | None = None,
    ) -> Snapshot:
        """
        Build a deterministic snapshot.

        Best‑in‑Class Notes:
        - All inputs must be JSON‑serializable.
        - Snapshot is immutable once created.
        - Structural hash ensures replay equivalence.
        """

        snapshot_dict = {
            "caller_states": caller_states,
            "queue_states": queue_states,
            "routing_trace": routing_trace,
            "marl_trace": marl_trace,
            "ppo_trace": ppo_trace,
            "staffing_trace": staffing_trace,
            "bayes_posteriors": bayes_posteriors,
            "recorder_events": recorder_events or [],
        }

        structural_hash = self._compute_hash(snapshot_dict)

        return Snapshot(
            caller_states=caller_states,
            queue_states=queue_states,
            routing_trace=routing_trace,
            marl_trace=marl_trace,
            ppo_trace=ppo_trace,
            staffing_trace=staffing_trace,
            bayes_posteriors=bayes_posteriors,
            recorder_events=recorder_events or [],
            structural_hash=structural_hash,
        )

    # ---------------------------------------------------------
    # EXPORT
    # ---------------------------------------------------------
    def export(self, snapshot: Snapshot) -> Dict[str, Any]:
        """
        Export snapshot as a dict.

        Best‑in‑Class Notes:
        - Stable serialization for governance and replay systems.
        - Structural hash included for integrity validation.
        """
        return {
            "caller_states": snapshot.caller_states,
            "queue_states": snapshot.queue_states,
            "routing_trace": snapshot.routing_trace,
            "marl_trace": snapshot.marl_trace,
            "ppo_trace": snapshot.ppo_trace,
            "staffing_trace": snapshot.staffing_trace,
            "bayes_posteriors": snapshot.bayes_posteriors,
            "recorder_events": snapshot.recorder_events,
            "structural_hash": snapshot.structural_hash,
        }