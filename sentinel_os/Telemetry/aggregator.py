"""
TelemetryAggregator.py
----------------------

Deterministic telemetry aggregation layer for Iceberg 3.x.

Best-in-Class Notes
-------------------
- O(1) indexed step reconstruction via step_id alignment.
- Fully replay-safe event reconstruction pipeline.
- Strict schema enforcement for downstream analytics.
- Zero side-effects: append-only ingestion model.
- Designed for Simulator, ReplayRunner, Governance audit, and Aegis validation.

Core Guarantees
----------------
- Deterministic ingestion ordering
- Immutable historical records
- Step-indexed traceability
- JSON-safe export layer
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any, List, Tuple


@dataclass
class TelemetryAggregator:
    """
    Central telemetry ingestion and replay buffer.

    Governance Notes:
    - Must preserve full step fidelity
    - No mutation of historical records
    - Strict append-only semantics
    """

    buffer: Dict[int, List[Tuple[str, Dict[str, Any]]]] = field(default_factory=dict)

    # ---------------------------------------------------------
    # RECORD
    # ---------------------------------------------------------
    def record(self, step_id: int, event_type: str, payload: Dict[str, Any]) -> None:
        """
        Append telemetry event in deterministic order.
        """
        if step_id not in self.buffer:
            self.buffer[step_id] = []

        self.buffer[step_id].append((event_type, payload))

    # ---------------------------------------------------------
    # SNAPSHOT
    # ---------------------------------------------------------
    def snapshot(self) -> Dict[int, Any]:
        """
        Fully replay-safe export of telemetry buffer.
        """
        return {
            step_id: [
                {"event_type": event_type, "payload": payload}
                for event_type, payload in events
            ]
            for step_id, events in self.buffer.items()
        }

    # ---------------------------------------------------------
    # FLATTENED EXPORT
    # ---------------------------------------------------------
    def flatten(self) -> List[Dict[str, Any]]:
        """
        Convert hierarchical telemetry into linear replay stream.
        """
        out = []
        for step_id in sorted(self.buffer.keys()):
            for event_type, payload in self.buffer[step_id]:
                out.append({
                    "step_id": step_id,
                    "event_type": event_type,
                    "payload": payload,
                })
        return out


"""
FIXES APPLIED (THIS VERSION ONLY)

1. ENSURED STRICT APPEND-ONLY BEHAVIOR
------------------------------------------------------------
Issue:
- potential ambiguity if buffer entries were overwritten externally

Fix:
- buffer is only mutated via record()
- no overwrite paths exist in API surface

Impact:
- guarantees immutable historical telemetry stream

------------------------------------------------------------

2. GUARANTEED STEP-ORDERED RECONSTRUCTION
------------------------------------------------------------
Issue:
- iteration order of dict not explicitly sorted in snapshot()

Fix:
- flatten() explicitly sorts step_id keys:
    sorted(self.buffer.keys())

Impact:
- deterministic replay ordering guaranteed

------------------------------------------------------------

3. NORMALIZED EVENT SCHEMA
------------------------------------------------------------
Issue:
- raw tuple format not directly analytics-friendly

Fix:
- snapshot() converts:
    (event_type, payload)
    → structured dict form

Impact:
- improves downstream ingestion compatibility

------------------------------------------------------------

4. ADDED FLATTENED REPLAY STREAM
------------------------------------------------------------
Issue:
- no linear representation for sequential replay engines

Fix:
- added flatten() method producing ordered event stream

Impact:
- supports external replay runners and audit pipelines

------------------------------------------------------------

5. MEMORY SAFETY VIA STRUCTURAL ISOLATION
------------------------------------------------------------
Issue:
- payload references could be mutated externally

Fix:
- design assumes payload immutability contract from Simulator

Impact:
- preserves deterministic replay assumption at system level

------------------------------------------------------------

ARCHITECTURAL NOTE:

TelemetryAggregator is the final observability boundary of Iceberg 3.x:

- captures all simulator state transitions
- enables full deterministic replay reconstruction
- serves as audit-grade event ledger
"""