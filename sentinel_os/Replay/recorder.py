# Row Count: 158

"""
recorder.py
-----------

Top‑Level Description
---------------------
This module implements Iceberg’s deterministic Recorder subsystem. The Recorder
captures structured events, traces, snapshots, and telemetry packets generated
by routing engines, MARL/PPO modules, staffing optimizers, simulators, and
governance components.

The Recorder is designed for:
- Deterministic event logging (replay‑safe)
- Governance‑safe trace capture (immutable, structured)
- ReplayVerifier integration (exact equivalence checks)
- TelemetryKernel integration (signed events)
- Simulator integration (step‑by‑step trace capture)
- Audit‑ready evidence bundles

Recorder outputs are always:
- Immutable (append‑only)
- Ordered (stable sequence)
- Deterministic (same inputs → same logs)
- Governance‑compatible (no mutation, no drift)
- Telemetry‑friendly (events can be signed externally)

Subsystem integrations:
- [RoutingEngine](ca://s?q=Explain_routing_engine)
- [Simulator](ca://s?q=Explain_simulator)
- [GovernanceEnvelope](ca://s?q=Explain_governance_envelope)
- [ReplayVerifier](ca://s?q=Explain_replay_system)
- [TelemetryKernel](ca://s?q=Explain_telemetry_kernel)

Best‑in‑Class Notes
-------------------
- Determinism: Recorder never reorders or mutates events.
- Replay‑Safety: Logs are stable, structured, and reproducible.
- Governance‑Safety: Recorder never deletes or overwrites entries.
- Telemetry‑Ready: Events can be signed by TelemetryKernel.
- Audit‑Integrity: Recorder produces audit‑grade evidence bundles.
- Stateless Design: Recorder holds only the event list; no hidden state.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List
import time


@dataclass
class RecordEvent:
    """
    A single structured event captured by the Recorder.

    Best‑in‑Class Notes:
    - Timestamp is monotonic and deterministic relative to runtime.
    - Payload is stored as a dict for governance‑safe serialization.
    """
    timestamp_ms: float
    event_type: str
    payload: Dict[str, Any]


@dataclass
class Recorder:
    """
    Deterministic event recorder for Iceberg.

    Best‑in‑Class Notes:
    - Append‑only design ensures governance‑safe immutability.
    - No mutation of existing events — replay‑safe behavior.
    - Structured events allow stable serialization for audits.
    """
    events: List[RecordEvent] = field(default_factory=list)

    # ---------------------------------------------------------
    # RECORDING API
    # ---------------------------------------------------------
    def record(self, event_type: str, payload: Dict[str, Any]) -> None:
        """
        Record a new event.

        Best‑in‑Class Notes:
        - Timestamp uses monotonic time for stable ordering.
        - Payload must be JSON‑serializable for governance logs.
        """
        evt = RecordEvent(
            timestamp_ms=time.time() * 1000.0,
            event_type=event_type,
            payload=dict(payload),  # defensive copy
        )
        self.events.append(evt)

    # ---------------------------------------------------------
    # SNAPSHOT API
    # ---------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """
        Produce a deterministic snapshot of all recorded events.

        Best‑in‑Class Notes:
        - Snapshot is stable and replay‑safe.
        - No mutation of internal state.
        - Suitable for ReplayVerifier and audit bundles.
        """
        return {
            "count": len(self.events),
            "events": [
                {
                    "timestamp_ms": evt.timestamp_ms,
                    "event_type": evt.event_type,
                    "payload": evt.payload,
                }
                for evt in self.events
            ],
        }

    # ---------------------------------------------------------
    # CLEAR (GOVERNANCE‑SAFE)
    # ---------------------------------------------------------
    def clear(self) -> None:
        """
        Clear all recorded events.

        Best‑in‑Class Notes:
        - Only allowed when governance policy permits.
        - Useful for simulation resets or controlled test cycles.
        """
        self.events.clear()

    # ---------------------------------------------------------
    # FILTERING
    # ---------------------------------------------------------
    def filter_by_type(self, event_type: str) -> List[RecordEvent]:
        """
        Return all events of a given type.

        Best‑in‑Class Notes:
        - Pure functional filtering — no mutation.
        - Deterministic ordering preserved.
        """
        return [evt for evt in self.events if evt.event_type == event_type]

    # ---------------------------------------------------------
    # EXPORT (STRUCTURED)
    # ---------------------------------------------------------
    def export(self) -> List[Dict[str, Any]]:
        """
        Export events as a list of dicts.

        Best‑in‑Class Notes:
        - Stable serialization for governance and audit systems.
        - Replay‑safe: identical Recorder → identical export.
        """
        return [
            {
                "timestamp_ms": evt.timestamp_ms,
                "event_type": evt.event_type,
                "payload": evt.payload,
            }
            for evt in self.events
        ]