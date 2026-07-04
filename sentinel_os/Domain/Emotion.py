# Row Count: 154

"""
emotion.py
----------

Top‑Level Description
---------------------
This module defines Iceberg’s deterministic Emotion Taxonomy — the canonical,
governance‑safe, replay‑friendly enumeration of caller emotional states used
across:

- RoutingEngine (PPO / MARL)
- BayesianIntentEngineGPU
- StaffingOptimizerRL
- Simulator
- ReplayRunner
- TelemetryAggregator
- GovernanceEnvelope

The emotion taxonomy guarantees:
- Deterministic ordering
- Immutable canonical list
- Governance‑safe serialization
- Replay‑friendly equivalence
- Telemetry‑ready labels
- Zero drift across versions

Subsystem integrations:
- [RoutingEngine](ca://s?q=Explain_routing_engine)
- [Simulator](ca://s?q=Explain_simulator)
- [BayesianIntentEngineGPU](ca://s?q=Explain_bayes_gpu)
- [StaffingOptimizerRL](ca://s?q=Explain_staffing_rl)
- [ReplayRunner](ca://s?q=Explain_replay_runner)
- [TelemetryAggregator](ca://s?q=Explain_telemetry_aggregator)
- [GovernanceEnvelope](ca://s?q=Explain_governance_envelope)

Best‑in‑Class Notes
-------------------
- Determinism: Emotion ordering is fixed and never changes.
- Governance‑Safety: Enum values are immutable and JSON‑safe.
- Replay‑Safety: Identical emotion → identical routing + Bayesian behavior.
- Telemetry‑Ready: Emotion labels are stable across versions.
- Stateless Design: Pure enumeration; no mutation or logic.
"""

from __future__ import annotations
from enum import Enum
from typing import List


class Emotion(Enum):
    """
    Deterministic emotion enumeration.

    Best‑in‑Class Notes:
    - Values are stable integers for governance‑safe serialization.
    - Names are stable strings for telemetry and audit logs.
    - Ordering is fixed and must never change.
    """

    NEUTRAL = 0
    IMPATIENT = 1
    FRUSTRATED = 2
    ANGRY = 3

    # ---------------------------------------------------------
    # CANONICAL LIST
    # ---------------------------------------------------------
    @classmethod
    def list(cls) -> List[str]:
        """
        Return canonical emotion list in deterministic order.

        Best‑in‑Class Notes:
        - Used by PPO, MARL, Bayesian GPU, and Simulator.
        - Replay‑safe: ordering never changes.
        """
        return [e.name for e in cls]

    # ---------------------------------------------------------
    # INDEX LOOKUP
    # ---------------------------------------------------------
    @classmethod
    def index(cls, emotion: "Emotion") -> int:
        """
        Deterministic index lookup.

        Best‑in‑Class Notes:
        - Used by PPO/MARL one‑hot encoders.
        - Governance‑safe: no dynamic ordering.
        """
        return emotion.value

    # ---------------------------------------------------------
    # FROM STRING
    # ---------------------------------------------------------
    @classmethod
    def from_string(cls, name: str) -> "Emotion":
        """
        Convert string → Emotion enum.

        Best‑in‑Class Notes:
        - Strict mapping; no fuzzy matching.
        - Governance‑safe: invalid names raise KeyError.
        """
        return cls[name.upper()]

    # ---------------------------------------------------------
    # TO JSON‑SAFE
    # ---------------------------------------------------------
    def to_json(self) -> str:
        """
        JSON‑safe serialization.

        Best‑in‑Class Notes:
        - Used by Recorder, TelemetryAggregator, SnapshotEngine.
        """
        return self.name

    # ---------------------------------------------------------
    # FROM JSON‑SAFE
    # ---------------------------------------------------------
    @classmethod
    def from_json(cls, value: str) -> "Emotion":
        """
        JSON‑safe deserialization.

        Best‑in‑Class Notes:
        - Replay‑safe: strict mapping ensures equivalence.
        """
        return cls[value]