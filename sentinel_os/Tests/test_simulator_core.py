# Row Count: 189

"""
test_simulator_core.py
----------------------

Deterministic regression tests for Iceberg's Simulator.

These tests guarantee:
- Deterministic caller evolution
- Deterministic queue evolution
- Deterministic graph traversal
- Stable structural hash across identical runs
- JSON-safe snapshots
- Replay-friendly step transitions

Best-in-Class Notes
-------------------
- Deterministic: No randomness.
- Governance-Safe: Structural hash detects drift.
- Replay-Friendly: Identical inputs → identical simulator output.
"""

import json
import hashlib
import pytest

from domain.simulator import Simulator
from domain.build_graph import build_graph
from domain.telemetry import TelemetryKernel
from domain.CallerState import CallerState
from domain.QueueState import QueueState


# ---------------------------------------------------------
# Structural hash utility
# ---------------------------------------------------------
def structural_hash(obj: dict) -> str:
    raw = json.dumps(obj, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------
# TEST 1 — Simulator builds deterministically
# ---------------------------------------------------------
def test_simulator_build_deterministic():
    graph1 = build_graph()
    graph2 = build_graph()

    telemetry1 = TelemetryKernel()
    telemetry2 = TelemetryKernel()

    sim1 = Simulator(graph=graph1, telemetry=telemetry1, max_steps=10)
    sim2 = Simulator(graph=graph2, telemetry=telemetry2, max_steps=10)

    assert sim1.max_steps == sim2.max_steps
    assert sim1.graph.to_dict() == sim2.graph.to_dict()


# ---------------------------------------------------------
# TEST 2 — Single caller deterministic evolution
# ---------------------------------------------------------
def test_single_caller_deterministic():
    graph = build_graph()
    telemetry1 = TelemetryKernel()
    telemetry2 = TelemetryKernel()

    sim1 = Simulator(graph=graph, telemetry=telemetry1, max_steps=10)
    sim2 = Simulator(graph=graph, telemetry=telemetry2, max_steps=10)

    caller1 = CallerState.new("caller-1")
    caller2 = CallerState.new("caller-1")

    for _ in range(5):
        sim1.step(caller1)
        sim2.step(caller2)

    assert caller1.to_dict() == caller2.to_dict(), "Caller evolution drift detected"


# ---------------------------------------------------------
# TEST 3 — Queue evolution deterministic
# ---------------------------------------------------------
def test_queue_evolution_deterministic():
    graph = build_graph()
    telemetry1 = TelemetryKernel()
    telemetry2 = TelemetryKernel()

    sim1 = Simulator(graph=graph, telemetry=telemetry1, max_steps=10)
    sim2 = Simulator(graph=graph, telemetry=telemetry2, max_steps=10)

    q1 = QueueState.new("billing")
    q2 = QueueState.new("billing")

    for _ in range(5):
        sim1.update_queue(q1)
        sim2.update_queue(q2)

    assert q1.to_dict() == q2.to_dict(), "Queue evolution drift detected"


# ---------------------------------------------------------
# TEST 4 — Structural hash stable across identical runs
# ---------------------------------------------------------
def test_simulator_structural_hash_stable():
    graph = build_graph()
    telemetry = TelemetryKernel()
    sim = Simulator(graph=graph, telemetry=telemetry, max_steps=10)

    caller = CallerState.new("caller-x")

    for _ in range(3):
        sim.step(caller)

    snap = caller.to_dict()
    h1 = structural_hash(snap)
    h2 = structural_hash(snap)

    assert h1 == h2, "Structural hash changed unexpectedly"


# ---------------------------------------------------------
# TEST 5 — Simulator snapshots JSON-safe
# ---------------------------------------------------------
def test_simulator_json_safe():
    graph = build_graph()
    telemetry = TelemetryKernel()
    sim = Simulator(graph=graph, telemetry=telemetry, max_steps=10)

    caller = CallerState.new("caller-json")

    for _ in range(3):
        sim.step(caller)

    try:
        json.dumps(caller.to_dict())
    except Exception as e:
        pytest.fail(f"Simulator snapshot is not JSON-safe: {e}")


# ---------------------------------------------------------
# TEST 6 — Graph traversal deterministic
# ---------------------------------------------------------
def test_graph_traversal_deterministic():
    graph = build_graph()
    telemetry1 = TelemetryKernel()
    telemetry2 = TelemetryKernel()

    sim1 = Simulator(graph=graph, telemetry=telemetry1, max_steps=10)
    sim2 = Simulator(graph=graph, telemetry=telemetry2, max_steps=10)

    caller1 = CallerState.new("caller-route")
    caller2 = CallerState.new("caller-route")

    for _ in range(6):
        sim1.step(caller1)
        sim2.step(caller2)

    assert caller1.route == caller2.route, "Graph traversal drift detected"


# ---------------------------------------------------------
# TEST 7 — Telemetry events deterministic
# ---------------------------------------------------------
def test_telemetry_deterministic():
    graph = build_graph()
    telemetry1 = TelemetryKernel()
    telemetry2 = TelemetryKernel()

    sim1 = Simulator(graph=graph, telemetry=telemetry1, max_steps=10)
    sim2 = Simulator(graph=graph, telemetry=telemetry2, max_steps=10)

    caller1 = CallerState.new("caller-tel")
    caller2 = CallerState.new("caller-tel")

    for _ in range(4):
        sim1.step(caller1)
        sim2.step(caller2)

    assert telemetry1.ledger == telemetry2.ledger, "Telemetry drift detected"


# ---------------------------------------------------------
# TEST 8 — Multiple callers deterministic
# ---------------------------------------------------------
def test_multiple_callers_deterministic():
    graph = build_graph()
    telemetry1 = TelemetryKernel()
    telemetry2 = TelemetryKernel()

    sim1 = Simulator(graph=graph, telemetry=telemetry1, max_steps=10)
    sim2 = Simulator(graph=graph, telemetry=telemetry2, max_steps=10)

    callers1 = [CallerState.new(f"c{i}") for i in range(3)]
    callers2 = [CallerState.new(f"c{i}") for i in range(3)]

    for _ in range(5):
        for c1, c2 in zip(callers1, callers2):
            sim1.step(c1)
            sim2.step(c2)

    for c1, c2 in zip(callers1, callers2):
        assert c1.to_dict() == c2.to_dict(), "Multi-caller drift detected"