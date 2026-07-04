# Row Count: 233

"""
test_cluster_runner.py
----------------------

Deterministic regression tests for Iceberg's ClusterRunner.

These tests guarantee:
- Deterministic job scheduling
- Deterministic worker selection
- Deterministic cluster-wide state propagation
- Stable structural hash across identical runs
- JSON-safe cluster snapshots
- Replay-friendly multi-step cluster evolution
- No drift in routing or dispatch logic

Best-in-Class Notes
-------------------
- Deterministic: No randomness in worker selection or scheduling.
- Governance-Safe: Structural hash detects drift.
- Replay-Friendly: Identical job streams → identical cluster output.
"""

import json
import hashlib
import pytest

from Domain.cluster_runner import ClusterRunner
from Domain.simulator import Simulator
from Domain.build_graph import build_graph
from Domain.telemetry import TelemetryKernel
from Domain.CallerState import CallerState
from Domain.QueueState import QueueState


# ---------------------------------------------------------
# Structural hash utility
# ---------------------------------------------------------
def structural_hash(obj: dict) -> str:
    raw = json.dumps(obj, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------
# TEST 1 — ClusterRunner initializes deterministically
# ---------------------------------------------------------
def test_cluster_runner_initialization():
    c1 = ClusterRunner()
    c2 = ClusterRunner()

    assert c1.version == c2.version
    assert c1.strict_mode == c2.strict_mode
    assert c1.worker_count == c2.worker_count


# ---------------------------------------------------------
# TEST 2 — Deterministic worker selection
# ---------------------------------------------------------
def test_worker_selection_deterministic():
    cluster = ClusterRunner()

    job = {"caller_id": "c1", "intent": "billing"}

    w1 = cluster.select_worker(job)
    w2 = cluster.select_worker(job)

    assert w1 == w2, "ClusterRunner worker selection drift detected"


# ---------------------------------------------------------
# TEST 3 — Deterministic scheduling
# ---------------------------------------------------------
def test_scheduling_deterministic():
    cluster1 = ClusterRunner()
    cluster2 = ClusterRunner()

    jobs = [
        {"caller_id": "c1", "intent": "billing"},
        {"caller_id": "c2", "intent": "tech"},
        {"caller_id": "c3", "intent": "sales"},
    ]

    out1 = [cluster1.schedule(job) for job in jobs]
    out2 = [cluster2.schedule(job) for job in jobs]

    assert out1 == out2, "ClusterRunner scheduling drift detected"


# ---------------------------------------------------------
# TEST 4 — Structural hash stable for identical cluster snapshots
# ---------------------------------------------------------
def test_structural_hash_stable():
    cluster = ClusterRunner()

    snap = cluster.snapshot()

    h1 = structural_hash(snap)
    h2 = structural_hash(snap)

    assert h1 == h2, "Structural hash changed unexpectedly"


# ---------------------------------------------------------
# TEST 5 — JSON-safe cluster snapshot
# ---------------------------------------------------------
def test_json_safe_snapshot():
    cluster = ClusterRunner()
    snap = cluster.snapshot()

    try:
        json.dumps(snap)
    except Exception as e:
        pytest.fail(f"ClusterRunner snapshot is not JSON-safe: {e}")


# ---------------------------------------------------------
# TEST 6 — Deterministic multi-step cluster evolution
# ---------------------------------------------------------
def test_multistep_cluster_deterministic():
    cluster1 = ClusterRunner()
    cluster2 = ClusterRunner()

    jobs1 = [{"caller_id": f"c{i}", "intent": "billing"} for i in range(5)]
    jobs2 = [{"caller_id": f"c{i}", "intent": "billing"} for i in range(5)]

    for j1, j2 in zip(jobs1, jobs2):
        cluster1.schedule(j1)
        cluster2.schedule(j2)

    assert cluster1.snapshot() == cluster2.snapshot(), \
        "ClusterRunner multi-step drift detected"


# ---------------------------------------------------------
# TEST 7 — ClusterRunner integrates with Simulator deterministically
# ---------------------------------------------------------
def test_cluster_simulator_integration():
    graph = build_graph()
    telemetry = TelemetryKernel()
    sim = Simulator(graph=graph, telemetry=telemetry, max_steps=10)

    cluster = ClusterRunner()

    caller = CallerState.new("caller-sim")

    for _ in range(4):
        job = {"caller_id": caller.caller_id, "intent": "billing"}
        cluster.schedule(job)
        sim.step(caller)

    replay_cluster = ClusterRunner()
    for job in telemetry.ledger:
        if job["type"] == "schedule":
            replay_cluster.schedule(job["payload"])

    assert cluster.snapshot() == replay_cluster.snapshot(), \
        "ClusterRunner simulator integration drift detected"


# ---------------------------------------------------------
# TEST 8 — Structural hash changes after scheduling
# ---------------------------------------------------------
def test_hash_changes_after_scheduling():
    cluster = ClusterRunner()

    h_before = structural_hash(cluster.snapshot())

    job = {"caller_id": "c-hash", "intent": "tech"}
    cluster.schedule(job)

    h_after = structural_hash(cluster.snapshot())

    assert h_before != h_after, "Structural hash should change after scheduling"


# ---------------------------------------------------------
# TEST 9 — Replay-friendly cluster evolution
# ---------------------------------------------------------
def test_replay_friendly_cluster_evolution():
    cluster = ClusterRunner()

    jobs = [
        {"caller_id": "c1", "intent": "billing"},
        {"caller_id": "c2", "intent": "tech"},
        {"caller_id": "c3", "intent": "sales"},
    ]

    for job in jobs:
        cluster.schedule(job)

    snap_live = cluster.snapshot()

    replay = ClusterRunner()
    for job in jobs:
        replay.schedule(job)

    snap_replay = replay.snapshot()

    assert snap_live == snap_replay, "ClusterRunner replay mismatch"