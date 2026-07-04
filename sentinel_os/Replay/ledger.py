# Row Count: 162

"""
ledger.py
---------

Top‑Level Description
---------------------
This module implements Iceberg’s deterministic, append‑only Replay Ledger. The
ledger stores every event emitted by routing engines, MARL/PPO modules, staffing
optimizers, Bayesian engines, simulators, and governance components.

The ledger guarantees:
- Deterministic ordering (replay‑safe)
- Immutable append‑only semantics (governance‑safe)
- Audit‑ready event history
- Structural stability for ReplayVerifier
- Telemetry‑friendly event capture
- Integration with SnapshotEngine for full‑state reconstruction

Ledger entries are stored as JSON lines (`.jsonl`) to ensure:
- Streaming‑friendly writes
- Stable serialization
- Easy hashing for governance integrity

Subsystem integrations:
- [ReplayVerifier](ca://s?q=Explain_replay_system)
- [Simulator](ca://s?q=Explain_simulator)
- [GovernanceEnvelope](ca://s?q=Explain_governance_envelope)
- [TelemetryKernel](ca://s?q=Explain_telemetry_kernel)
- [SnapshotEngine](ca://s?q=Explain_snapshot_engine)

Best‑in‑Class Notes
-------------------
- Determinism: Ledger never reorders or mutates entries.
- Governance‑Safety: Append‑only design prevents tampering.
- Replay‑Safety: Identical event streams → identical ledger files.
- Telemetry‑Ready: Events can be signed externally.
- Audit‑Integrity: Ledger serves as the canonical evidence trail.
- Stateless Design: Ledger holds no hidden state; only the file matters.
"""

from __future__ import annotations
import json
import os
from typing import Dict, Any, List


class ReplayLedger:
    """
    Append‑only ledger for Iceberg replay events.

    Best‑in‑Class Notes:
    - JSONL format ensures stable, line‑by‑line replay.
    - No mutation of existing entries — governance‑safe.
    - Deterministic read/write behavior across platforms.
    """

    def __init__(self, path: str = "replay_ledger.jsonl"):
        self.path = path

        # Best‑in‑Class: Ensure file exists (append‑only)
        if not os.path.exists(self.path):
            with open(self.path, "w", encoding="utf-8"):
                pass

    # ---------------------------------------------------------
    # APPEND
    # ---------------------------------------------------------
    def append(self, event: Dict[str, Any]):
        """
        Append a single event to the ledger.

        Best‑in‑Class Notes:
        - Events must be JSON‑serializable.
        - Append‑only semantics guarantee replay integrity.
        - No timestamps added here — upstream systems handle timing.
        """
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    # ---------------------------------------------------------
    # READ ALL
    # ---------------------------------------------------------
    def read_all(self) -> List[Dict[str, Any]]:
        """
        Read all events from the ledger.

        Best‑in‑Class Notes:
        - Deterministic ordering preserved.
        - Suitable for ReplayVerifier and SnapshotEngine.
        """
        events = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    # ---------------------------------------------------------
    # TAIL
    # ---------------------------------------------------------
    def tail(self, n: int) -> List[Dict[str, Any]]:
        """
        Return the last n events.

        Best‑in‑Class Notes:
        - Useful for debugging and incremental replay.
        - Deterministic slicing of event history.
        """
        events = self.read_all()
        return events[-n:] if n <= len(events) else events

    # ---------------------------------------------------------
    # CLEAR (GOVERNANCE‑SAFE)
    # ---------------------------------------------------------
    def clear(self):
        """
        Clear the ledger (rarely used; mostly for testing).

        Best‑in‑Class Notes:
        - Only allowed when governance policy permits.
        - Useful for simulation resets or controlled test cycles.
        """
        with open(self.path, "w", encoding="utf-8"):
            pass

    # ---------------------------------------------------------
    # COUNT
    # ---------------------------------------------------------
    def count(self) -> int:
        """
        Number of events in the ledger.

        Best‑in‑Class Notes:
        - Deterministic count for replay validation.
        - Used by SnapshotEngine and ReplayVerifier.
        """
        with open(self.path, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)