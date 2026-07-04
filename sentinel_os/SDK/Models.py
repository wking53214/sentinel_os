# Row Count: 201

"""
models.py
---------

Top‑Level Description
---------------------
This module defines Iceberg’s canonical model surface — a unified import layer
that exposes all deterministic domain models used across:

- Simulator
- RoutingEngine (PPO / MARL)
- StaffingOptimizerRL
- BayesianIntentEngineGPU
- ReplayRunner
- TelemetryAggregator
- Dashboard Server
- Client Libraries

This file does NOT contain business logic.
It simply aggregates stable, governance‑safe, replay‑friendly data models.

Best‑in‑Class Notes
-------------------
- Deterministic: No mutation, no randomness.
- Governance‑Safe: Centralized import surface prevents drift.
- Replay‑Friendly: Identical model surface → identical replay behavior.
- Telemetry‑Ready: All models JSON‑safe.
- Stateless Design: Pure data definitions.
"""

from __future__ import annotations

# Domain enums
from domain.Intent import Intent
from domain.Emotion import Emotion

# Domain states
from domain.CallerState import CallerState, DynamicState
from domain.QueueState import QueueState
from domain.LatentPayload import LatentPayload

# Graph
from domain.build_graph import RoutingGraph, GraphNode, build_graph

# Telemetry
from domain.telemetry_kernel import TelemetryEvent, TelemetryKernel

# Replay
from domain.replay import ReplayBundle, ReplayEvent

# RL
from domain.rl_ppo import PPOAction
from domain.rl_marl import MARLJointAction
from domain.staffing_rl import StaffingDelta


# ---------------------------------------------------------
# EXPORT SURFACE
# ---------------------------------------------------------
__all__ = [
    # Enums
    "Intent",
    "Emotion",

    # Caller + Queue
    "CallerState",
    "DynamicState",
    "QueueState",

    # Latent
    "LatentPayload",

    # Graph
    "RoutingGraph",
    "GraphNode",
    "build_graph",

    # Telemetry
    "TelemetryEvent",
    "TelemetryKernel",

    # Replay
    "ReplayBundle",
    "ReplayEvent",

    # RL
    "PPOAction",
    "MARLJointAction",
    "StaffingDelta",
]


# ---------------------------------------------------------
# SNAPSHOT UTILITIES
# ---------------------------------------------------------
def snapshot_all(queues, callers, telemetry, governance=None) -> dict:
    """
    Deterministic snapshot aggregator.

    Best‑in‑Class Notes:
    - Used by dashboard_server.py
    - Governance‑safe, JSON‑safe
    - Replay‑friendly
    """

    return {
        "queues": {name: q.snapshot() for name, q in queues.items()},
        "callers": {cid: c.snapshot() for cid, c in callers.items()},
        "telemetry": telemetry.snapshot(),
        "governance": governance.snapshot() if governance else None,
    }


# ---------------------------------------------------------
# STRUCTURAL HASH
# ---------------------------------------------------------
import hashlib
import json

def structural_hash(obj: dict) -> str:
    """
    Compute deterministic structural hash for any JSON‑safe object.

    Best‑in‑Class Notes:
    - Used by GovernanceEnvelope + ReplayVerifier.
    - Detects drift across callers, queues, RL outputs, telemetry.
    """

    raw = json.dumps(obj, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()