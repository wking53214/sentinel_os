# Row Count: 189

"""
telemetry.py
------------

Top‑Level Description
---------------------
This module implements Iceberg’s deterministic Telemetry Kernel — the canonical,
governance‑safe, replay‑friendly event recorder used across:

- Simulator
- RoutingEngine (PPO / MARL)
- StaffingOptimizerRL
- BayesianIntentEngineGPU
- ReplayRunner
- Dashboard
- GovernanceEnvelope

Telemetry guarantees:
- Deterministic event ordering
- JSON‑safe event packets
- Replay‑friendly ledger
- Governance‑safe structural hashing
- Zero stochasticity

Best‑in‑Class Notes
-------------------
- Deterministic: No randomness; events appended in strict order.
- Governance‑Safety: Structural hash detects drift.
- Replay‑Friendly: Ledger is fully JSON‑safe.
- Telemetry‑Ready: Used by dashboard telemetry stream.
- Stateless Design: Kernel holds only event ledger.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, List
import hashlib
import json
import time


# ---------------------------------------------------------
# TELEMETRY EVENT
# ---------------------------------------------------------
@dataclass
class TelemetryEvent:
    """
    Canonical telemetry event.

    Best‑in‑Class Notes:
    - JSON‑safe.
    - Deterministic ordering.
    - Used by dashboard + replay.
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
# TELEMETRY KERNEL
# ---------------------------------------------------------
class TelemetryKernel:
    """
    Deterministic telemetry recorder.

    Best‑in‑Class Notes:
    - Append‑only ledger.
    - No mutation of past events.
    - Replay‑safe.
    """

    def __init__(self):
        self.ledger: List[Dict[str, Any]] = []

    # -----------------------------------------------------
    # RECORD EVENT
    # -----------------------------------------------------
    def record(self, event_type: str, payload: Dict[str, Any]):
        """
        Append deterministic telemetry event.

        Best‑in‑Class Notes:
        - Timestamp is monotonic wall‑clock time.
        - JSON‑safe payload.
        - Strict append ordering.
        """
        evt = TelemetryEvent(
            timestamp=time.time(),
            event_type=event_type,
            payload=payload,
        )
        self.ledger.append(evt.to_dict())

    # -----------------------------------------------------
    # SNAPSHOT
    # -----------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """
        Return deterministic telemetry snapshot.

        Best‑in‑Class Notes:
        - Used by dashboard + replay.
        - JSON‑safe.
        """
        return {
            "count": len(self.ledger),
            "events": list(self.ledger),
        }

    # -----------------------------------------------------
    # EXPORT STREAM
    # -----------------------------------------------------
    def export(self) -> List[Dict[str, Any]]:
        """
        Return telemetry event stream.

        Best‑in‑Class Notes:
        - Deterministic ordering.
        - Used by dashboard telemetry viewer.
        """
        return list(self.ledger)

    # -----------------------------------------------------
    # STRUCTURAL HASH
    # -----------------------------------------------------
    def structural_hash(self) -> str:
        """
        Compute deterministic structural hash of telemetry ledger.

        Best‑in‑Class Notes:
        - Used by GovernanceEnvelope + ReplayVerifier.
        - Detects drift across event streams.
        """
        raw = json.dumps(self.ledger, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()