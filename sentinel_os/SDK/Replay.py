# Row Count: 214

"""
replay.py
---------

Top‑Level Description
---------------------
This module implements Iceberg’s deterministic Replay Engine — the canonical,
governance‑safe, replay‑friendly mechanism for reconstructing caller journeys,
RL decisions, queue transitions, Bayesian updates, and telemetry events.

Replay is used across:
- Dashboard (replay.html)
- ReplayRunner
- GovernanceEnvelope
- SnapshotEngine
- TelemetryAggregator
- Simulator (for verification)

Replay guarantees:
- Deterministic event ordering
- Governance‑safe structural hashing
- JSON‑safe replay bundles
- Zero stochasticity
- Identical input → identical replay output

Best‑in‑Class Notes
-------------------
- Deterministic: No randomness; all events replayed exactly.
- Governance‑Safety: Structural hash detects drift.
- Replay‑Friendly: Identical ledger → identical replay bundle.
- Telemetry‑Ready: JSON‑safe event packets.
- Stateless Design: Replay engine holds no hidden state.
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import List, Dict, Any
import hashlib
import json


# ---------------------------------------------------------
# REPLAY EVENT
# ---------------------------------------------------------
@dataclass
class ReplayEvent:
    """
    Canonical replay event.

    Best‑in‑Class Notes:
    - JSON‑safe.
    - Deterministic ordering.
    - Used by dashboard replay viewer.
    """

    timestamp: float
    event_type: str
    payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "payload": self.payload,
        }


# ---------------------------------------------------------
# REPLAY BUNDLE
# ---------------------------------------------------------
@dataclass
class ReplayBundle:
    """
    Deterministic replay bundle.

    Contains:
    - snapshot: final system state
    - events: ordered replay events
    - structural_hash: governance‑safe hash

    Best‑in‑Class Notes:
    - JSON‑safe.
    - Replay‑friendly.
    """

    snapshot: Dict[str, Any]
    events: List[ReplayEvent]
    structural_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot": self.snapshot,
            "events": [e.to_dict() for e in self.events],
            "structural_hash": self.structural_hash,
        }


# ---------------------------------------------------------
# STRUCTURAL HASH
# ---------------------------------------------------------
def compute_structural_hash(obj: Dict[str, Any]) -> str:
    """
    Deterministic structural hash.

    Best‑in‑Class Notes:
    - Used by GovernanceEnvelope + ReplayVerifier.
    - Detects drift across replay snapshots and event streams.
    """
    raw = json.dumps(obj, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------
# REPLAY ENGINE
# ---------------------------------------------------------
@dataclass
class ReplayEngine:
    """
    Deterministic replay engine.

    Parameters
    ----------
    ledger : List[Dict[str, Any]]
        Telemetry ledger or event log captured during simulation.

    simulator : Any
        Simulator reference for snapshot generation.

    Best‑in‑Class Notes:
    - Stateless: replay does not mutate simulator.
    - Deterministic: ledger ordering defines replay ordering.
    """

    ledger: List[Dict[str, Any]]
    simulator: Any

    # -----------------------------------------------------
    # RUN REPLAY
    # -----------------------------------------------------
    def run(self) -> ReplayBundle:
        """
        Execute deterministic replay.

        Best‑in‑Class Notes:
        - Reconstructs event stream.
        - Produces final snapshot.
        - Computes structural hash.
        """

        events: List[ReplayEvent] = []

        for entry in self.ledger:
            events.append(
                ReplayEvent(
                    timestamp=entry.get("timestamp", 0.0),
                    event_type=entry.get("event_type", "unknown"),
                    payload=entry.get("payload", {}),
                )
            )

        # Final snapshot from simulator
        snapshot = self.simulator.snapshot()

        # Structural hash
        bundle_dict = {
            "snapshot": snapshot,
            "events": [e.to_dict() for e in events],
        }
        structural_hash = compute_structural_hash(bundle_dict)

        return ReplayBundle(
            snapshot=snapshot,
            events=events,
            structural_hash=structural_hash,
        )

    # -----------------------------------------------------
    # EVENT VIEW
    # -----------------------------------------------------
    def events_view(self) -> List[Dict[str, Any]]:
        """
        Return deterministic event list for dashboard streaming.
        """
        return [e.to_dict() for e in self.run().events]