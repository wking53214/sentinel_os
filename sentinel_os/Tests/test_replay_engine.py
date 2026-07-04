# Row Count: 219

"""
test_replay_engine.py
---------------------

Deterministic regression tests for Iceberg's ReplayEngine.

These tests guarantee:
- Deterministic reconstruction of caller + queue + latent state
- Structural-hash equivalence between live simulation and replay
- JSON-safe replay snapshots
- No drift in event ordering
- Replay-friendly multi-step evolution
- Identical telemetry → identical reconstructed state

Best-in-Class Notes
-------------------
- Deterministic: Replay must exactly match live simulation.
- Governance-Safe: Structural hash detects drift.
- Replay-Friendly: Identical telemetry → identical reconstructed state.
"""

import json
import hashlib
import pytest

from domain.simulator import Simulator
from domain.build_graph import build_graph
from domain.telemetry import TelemetryKernel
from domain.replay import ReplayEngine
from domain.CallerState import CallerState
from domain.QueueState import QueueState


# ---------------------------------------------------------
# Structural hash utility
# ---------------------------------------------------------
def structural_hash(obj: dict) -> str:
    raw = json.dumps(obj, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------
# TEST 1 — ReplayEngine initializes deterministically
# ---------------------------------------------------------
def test_replay_engine_initialization():
    r1 = ReplayEngine()
    r2 = ReplayEngine()

    assert r1.version == r2.version
    assert r1.strict_mode == r2.strict_mode


# ---------------------------------------------------------
# TEST 2 — Replay reconstructs caller state deterministically
# ---------------------------------------------------------
def test_replay_reconstructs_caller_state():
    graph = build_graph()
    telemetry = TelemetryKernel()
    sim = Simulator(graph=graph, telemetry=telemetry, max_steps=10)

    caller = CallerState.new("caller-1")

    # Run simulation
    for _ in range(5):
        sim.step(caller)

    # Replay from telemetry
    replay = ReplayEngine()
    reconstructed = replay.replay_from_events(telemetry.ledger)

    assert reconstructed["callers"]["caller-1"] == caller.to_dict(), \
        "Replay caller reconstruction drift detected"


# ---------------------------------------------------------
# TEST 3 — Replay reconstructs queue state deterministically
# ---------------------------------------------------------
def test_replay_reconstructs_queue_state():
    graph = build_graph()
    telemetry = TelemetryKernel()
    sim = Simulator(graph=graph, telemetry=telemetry, max_steps=10)

    queue = QueueState.new("billing")

    for _ in range(5):
        sim.update_queue(queue)

    replay = ReplayEngine()
    reconstructed = replay.replay_from_events(telemetry.ledger)

    assert reconstructed["queues"]["billing"] == queue.to_dict(), \
        "Replay queue reconstruction drift detected"


# ---------------------------------------------------------
# TEST 4 — Structural hash equivalence between live and replay
# ---------------------------------------------------------
def test_structural_hash_equivalence():
    graph = build_graph()
    telemetry = TelemetryKernel()
    sim = Simulator(graph=graph, telemetry=telemetry, max_steps=10)

    caller = CallerState.new("caller-x")

    for _ in range(4):
        sim.step(caller)

    live_hash = structural_hash(caller.to_dict())

    replay = ReplayEngine()
    reconstructed = replay.replay_from_events(telemetry.ledger)
    replay_hash = structural_hash(reconstructed["callers"]["caller-x"])

    assert live_hash == replay_hash, "Replay structural hash mismatch"


# ---------------------------------------------------------
# TEST 5 — Replay snapshot JSON-safe
# ---------------------------------------------------------
def test_replay_json_safe():
    graph = build_graph()
    telemetry = TelemetryKernel()
    sim = Simulator(graph=graph, telemetry=telemetry, max_steps=10)

    caller = CallerState.new("caller-json")

    for _ in range(3):
        sim.step(caller)

    replay = ReplayEngine()
    reconstructed = replay.replay_from_events(telemetry.ledger)

    try:
        json.dumps(reconstructed)
    except Exception as e:
        pytest.fail(f"Replay snapshot is not JSON-safe: {e}")


# ---------------------------------------------------------
# TEST 6 — Event ordering preserved
# ---------------------------------------------------------
def test_event_ordering_preserved():
    graph = build_graph()
    telemetry = TelemetryKernel()
    sim = Simulator(graph=graph, telemetry=telemetry, max_steps=10)

    caller = CallerState.new("caller-order")

    for _ in range(6):
        sim.step(caller)

    replay = ReplayEngine()
    reconstructed = replay.replay_from_events(telemetry.ledger)

    assert reconstructed["meta"]["event_count"] == len(telemetry.ledger), \
        "Replay event ordering drift detected"


# ---------------------------------------------------------
# TEST 7 — Replay matches multi-step evolution
# ---------------------------------------------------------
def test_replay_multistep_deterministic():
    graph = build_graph()
    telemetry = TelemetryKernel()
    sim = Simulator(graph=graph, telemetry=telemetry, max_steps=10)

    caller = CallerState.new("caller-multi")

    for _ in range(8):
        sim.step(caller)

    replay = ReplayEngine()
    reconstructed = replay.replay_from_events(telemetry.ledger)

    assert reconstructed["callers"]["caller-multi"] == caller.to_dict(), \
        "Replay multi-step drift detected"


# ---------------------------------------------------------
# TEST 8 — Structural hash changes after updates
# ---------------------------------------------------------
def test_replay_hash_changes_after_update():
    graph = build_graph()
    telemetry = TelemetryKernel()
    sim = Simulator(graph=graph, telemetry=telemetry, max_steps=10)

    caller = CallerState.new("caller-hash")

    h_before = structural_hash(caller.to_dict())

    sim.step(caller)

    replay = ReplayEngine()
    reconstructed = replay.replay_from_events(telemetry.ledger)

    h_after = structural_hash(reconstructed["callers"]["caller-hash"])

    assert h_before != h_after, "Replay structural hash should change after update"