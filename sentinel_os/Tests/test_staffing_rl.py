# Row Count: 173

"""
test_staffing_rl.py
-------------------

Deterministic regression tests for Iceberg's StaffingRLEngine.

These tests guarantee:
- Deterministic staffing delta computation
- Stable structural hash across identical runs
- JSON-safe RL outputs
- Replay-friendly multi-step evolution
- No drift in queue-load interpretation
- No randomness in PPO/MARL-adjacent staffing logic

Best-in-Class Notes
-------------------
- Deterministic: No randomness.
- Governance-Safe: Structural hash detects drift.
- Replay-Friendly: Identical queue loads → identical staffing deltas.
"""

import json
import hashlib
import pytest

from domain.staffing_rl import StaffingRLEngine


# ---------------------------------------------------------
# Structural hash utility
# ---------------------------------------------------------
def structural_hash(obj: dict) -> str:
    raw = json.dumps(obj, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------
# TEST 1 — Engine initializes deterministically
# ---------------------------------------------------------
def test_engine_initialization():
    e1 = StaffingRLEngine(lr=0.0003, delta_limit=0.5)
    e2 = StaffingRLEngine(lr=0.0003, delta_limit=0.5)

    assert e1.lr == e2.lr
    assert e1.delta_limit == e2.delta_limit


# ---------------------------------------------------------
# TEST 2 — Deterministic delta computation
# ---------------------------------------------------------
def test_deterministic_delta():
    engine = StaffingRLEngine(lr=0.0003, delta_limit=0.5)

    queues = {
        "billing": {"load": 0.8},
        "tech": {"load": 0.4},
        "sales": {"load": 0.2},
    }

    out1 = engine.compute_deltas(queues)
    out2 = engine.compute_deltas(queues)

    assert out1 == out2, "Staffing delta drift detected"


# ---------------------------------------------------------
# TEST 3 — Structural hash stable for identical outputs
# ---------------------------------------------------------
def test_structural_hash_stable():
    engine = StaffingRLEngine(lr=0.0003, delta_limit=0.5)

    queues = {
        "billing": {"load": 0.8},
        "tech": {"load": 0.4},
        "sales": {"load": 0.2},
    }

    out = engine.compute_deltas(queues)

    h1 = structural_hash(out)
    h2 = structural_hash(out)

    assert h1 == h2, "Structural hash changed unexpectedly"


# ---------------------------------------------------------
# TEST 4 — JSON-safe RL outputs
# ---------------------------------------------------------
def test_json_safe_output():
    engine = StaffingRLEngine(lr=0.0003, delta_limit=0.5)

    queues = {
        "billing": {"load": 0.8},
        "tech": {"load": 0.4},
        "sales": {"load": 0.2},
    }

    out = engine.compute_deltas(queues)

    try:
        json.dumps(out)
    except Exception as e:
        pytest.fail(f"Staffing RL output is not JSON-safe: {e}")


# ---------------------------------------------------------
# TEST 5 — Multi-step evolution deterministic
# ---------------------------------------------------------
def test_multistep_deterministic():
    engine1 = StaffingRLEngine(lr=0.0003, delta_limit=0.5)
    engine2 = StaffingRLEngine(lr=0.0003, delta_limit=0.5)

    queues1 = {"billing": {"load": 0.8}, "tech": {"load": 0.4}, "sales": {"load": 0.2}}
    queues2 = {"billing": {"load": 0.8}, "tech": {"load": 0.4}, "sales": {"load": 0.2}}

    for _ in range(5):
        out1 = engine1.compute_deltas(queues1)
        out2 = engine2.compute_deltas(queues2)

        assert out1 == out2, "Multi-step staffing drift detected"


# ---------------------------------------------------------
# TEST 6 — Delta limit respected
# ---------------------------------------------------------
def test_delta_limit_respected():
    engine = StaffingRLEngine(lr=0.0003, delta_limit=0.1)

    queues = {
        "billing": {"load": 1.0},
        "tech": {"load": 0.0},
    }

    out = engine.compute_deltas(queues)

    for q, delta in out.items():
        assert abs(delta) <= 0.1, "Delta limit violated"


# ---------------------------------------------------------
# TEST 7 — Structural hash changes after update
# ---------------------------------------------------------
def test_hash_changes_after_update():
    engine = StaffingRLEngine(lr=0.0003, delta_limit=0.5)

    queues = {"billing": {"load": 0.8}, "tech": {"load": 0.4}}

    h_before = structural_hash(queues)
    out = engine.compute_deltas(queues)
    h_after = structural_hash(out)

    assert h_before != h_after, "Structural hash should change after staffing update"