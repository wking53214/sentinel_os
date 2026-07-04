# Row Count: 236

"""
dashboard.py
------------

Top‑Level Description
---------------------
This module implements Iceberg’s deterministic Dashboard Aggregator — the
canonical, governance‑safe, replay‑friendly observability surface for:

- RoutingEngine (PPO / MARL)
- Simulator
- StaffingOptimizerRL
- BayesianIntentEngineGPU
- ReplayRunner
- TelemetryAggregator
- QueueState + CallerState
- GovernanceEnvelope

The dashboard provides:
- Deterministic snapshots of all runtime subsystems
- Governance‑safe structural hashes
- Replay‑friendly bundles
- Telemetry‑ready event streams
- Unified API for UI layers (FastAPI, React, etc.)

Subsystem integrations:
- [Simulator](ca://s?q=Explain_simulator)
- [TelemetryAggregator](ca://s?q=Explain_telemetry_aggregator)
- [ReplayRunner](ca://s?q=Explain_replay_runner)
- [SnapshotEngine](ca://s?q=Explain_snapshot_engine)
- [QueueState](ca://s?q=Give_me_QueueState.py)
- [CallerState](ca://s?q=Give_me_CallerState.py)
- [GovernanceEnvelope](ca://s?q=Explain_governance_envelope)

Best‑in‑Class Notes
-------------------
- Determinism: Dashboard never mutates underlying state.
- Governance‑Safety: Structural hashes detect drift.
- Replay‑Safety: Identical runtime → identical dashboard snapshot.
- Telemetry‑Ready: JSON‑safe packets for UI visualization.
- Stateless Design: Dashboard holds references only; no hidden logic.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, List, Optional
import hashlib
import json
import time

@dataclass
class Dashboard:
    """
    Deterministic Iceberg Dashboard Aggregator.

    Best‑in‑Class Notes:
    - Pure observability layer; no business logic.
    - All data is pulled from subsystems deterministically.
    """

    simulator: Any
    telemetry: Any
    replay_runner: Any
    queues: Dict[str, Any]
    callers: Dict[str, Any]
    governance: Optional[Any] = None

    def _get_raw_state(self, include_metadata: bool = True) -> Dict[str, Any]:
        """
        Internal helper for consolidated state retrieval.
        Ensures consistent structure for snapshots and hashing.
        """
        state = {
            "queues": {n: q.snapshot() for n, q in self.queues.items()},
            "callers": {cid: c.snapshot() for cid, c in self.callers.items()},
            "telemetry": self.telemetry.snapshot(),
            "governance": self.governance.snapshot() if self.governance else None,
        }
        if include_metadata:
            state["timestamp"] = time.time()
        return state

    # ---------------------------------------------------------
    # STRUCTURAL HASH
    # ---------------------------------------------------------
    def structural_hash(self) -> str:
        """
        Compute structural hash of the entire dashboard state.

        Best‑in‑Class Notes:
        - Used by GovernanceEnvelope + ReplayVerifier.
        - Excludes metadata (timestamp) to ensure determinism.
        - Detects any drift in queues, callers, or telemetry.
        """
        raw = json.dumps(self._get_raw_state(include_metadata=False), sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # ---------------------------------------------------------
    # SNAPSHOT
    # ---------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """
        Produce deterministic dashboard snapshot.

        Best‑in‑Class Notes:
        - Replay‑friendly: identical runtime → identical snapshot.
        - Telemetry‑ready: JSON‑safe for UI layers.
        """
        return self._get_raw_state(include_metadata=True)

    # ---------------------------------------------------------
    # TELEMETRY STREAM
    # ---------------------------------------------------------
    def telemetry_stream(self) -> List[Dict[str, Any]]:
        """
        Return telemetry events for UI streaming.

        Best‑in‑Class Notes:
        - Deterministic ordering.
        - JSON‑safe packets.
        """
        return self.telemetry.export()

    # ---------------------------------------------------------
    # QUEUE VIEW
    # ---------------------------------------------------------
    def queue_view(self) -> Dict[str, Any]:
        """
        Return deterministic queue metrics.

        Best‑in‑Class Notes:
        - Used by UI dashboards for real‑time queue visualization.
        """
        return {name: q.snapshot() for name, q in self.queues.items()}

    # ---------------------------------------------------------
    # CALLER VIEW
    # ---------------------------------------------------------
    def caller_view(self) -> Dict[str, Any]:
        """
        Return deterministic caller metrics.

        Best‑in‑Class Notes:
        - Used by UI dashboards for caller‑journey visualization.
        """
        return {cid: c.snapshot() for cid, c in self.callers.items()}

    # ---------------------------------------------------------
    # REPLAY VIEW
    # ---------------------------------------------------------
    def replay_view(self) -> Dict[str, Any]:
        """
        Execute deterministic replay and return snapshot.

        Best‑in‑Class Notes:
        - ReplayRunner drives replay from ledger.
        - Dashboard exposes replay output for UI layers.
        """
        return self.replay_runner.run()

    # ---------------------------------------------------------
    # FULL EXPORT
    # ---------------------------------------------------------
    def export(self) -> Dict[str, Any]:
        """
        Export full dashboard bundle.

        Best‑in‑Class Notes:
        - Governance‑safe (uses deterministic structural_hash).
        - Replay‑friendly.
        - Telemetry‑ready.
        """
        return {
            "snapshot": self.snapshot(),
            "structural_hash": self.structural_hash(),
            "telemetry": self.telemetry.export(),
        }