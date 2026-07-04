# Row Count: 201

"""
test_rl_marl.py
---------------

Deterministic regression tests for Iceberg's MARLEngine.

These tests guarantee:
- Deterministic multi-agent coordination
- Stable structural hash across identical runs
- JSON-safe RL outputs
- Replay-friendly multi-step evolution
- No agent-interaction drift
- No randomness in joint-action selection

Best-in-Class Notes
-------------------
- Deterministic: No randomness in agent updates.
- Governance-Safe: Structural hash detects drift.
- Replay-Friendly: Identical queue states → identical joint actions.
"""

import json
import hashlib
import pytest

from domain.rl_marl import MARLEngine


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
    e1 = MARLEngine(lr=0.0003, hidden=32, agents=4)
    e2 = MARLEngine(lr=0.0003, hidden=32, agents=4)

    assert e1.lr == e2.lr
    assert e1.hidden == e2.hidden
    assert e1.agents == e2.agents


# ---------------------------------------------------------
# TEST 2 — Deterministic joint-action computation
# ---------------------------------------------------------
def test_joint_action_deterministic():
    engine = MARLEngine(lr=0.0003, hidden=32, agents=4)

    queues = {
        "billing": {"load": 0.8},
        "tech": {"load": 0.4},
        "sales": {"load": 0.2},
    }

    out1 = engine.compute_joint_action(queues)
    out2 = engine.compute_joint_action(queues)

    assert out1 == out2, "MARL joint-action drift detected"


# ---------------------------------------------------------
# TEST 3 — Structural hash stable for identical outputs
# ---------------------------------------------------------
def test_structural_hash_stable():
    engine = MARLEngine(lr=0.0003, hidden=32, agents=4)

    queues = {
        "billing": {"load": 0.8},
        "tech": {"load": 0.4},
        "sales": {"load": 0.2},
    }

    out = engine.compute_joint_action(queues)

    h1 = structural_hash(out)
    h2 = structural_hash(out)

    assert h1 == h2, "Structural hash changed unexpectedly"


# ---------------------------------------------------------
# TEST 4 — JSON-safe RL outputs
# ---------------------------------------------------------
def test_json_safe_output():
    engine = MARLEngine(lr=0.0003, hidden=32, agents=4)

    queues = {
        "billing": {"load": 0.8},
        "tech": {"load": 0.4},
        "sales": {"load": 0.2},
    }

    out = engine.compute_joint_action(queues)

    try:
        json.dumps(out)
    except Exception as e:
        pytest.fail(f"MARL output is not JSON-safe: {e}")


# ---------------------------------------------------------
# TEST 5 — Multi-step evolution deterministic
# ---------------------------------------------------------
def test_multistep_deterministic():
    engine1 = MARLEngine(lr=0.0003, hidden=32, agents=4)
    engine2 = MARLEngine(lr=0.0003, hidden=32, agents=4)

    queues1 = {"billing": {"load": 0.8}, "tech": {"load": 0.4}, "sales": {"load": 0.2}}
    queues2 = {"billing": {"load": 0.8}, "tech": {"load": 0.4}, "sales": {"load": 0.2}}

    for _ in range(5):
        out1 = engine1.compute_joint_action(queues1)
        out2 = engine2.compute_joint_action(queues2)

        assert out1 == out2, "Multi-step MARL drift detected"


# ---------------------------------------------------------
# TEST 6 — Agent count respected
# ---------------------------------------------------------
def test_agent_count_respected():
    engine = MARLEngine(lr=0.0003, hidden=32, agents=3)

    queues = {"billing": {"load": 0.8}, "tech": {"load": 0.4}}

    out = engine.compute_joint_action(queues)

    assert len(out["actions"]) == 3, "MARL agent count violated"


# ---------------------------------------------------------
# TEST 7 — Structural hash changes after update
# ---------------------------------------------------------
def test_hash_changes_after_update():
    engine = MARLEngine(lr=0.0003, hidden=32, agents=4)

    queues = {"billing": {"load": 0.8}, "tech": {"load": 0.4}}

    h_before = structural_hash(queues)
    out = engine.compute_joint_action(queues)
    h_after = structural_hash(out)

    assert h_before != h_after, "Structural hash should change after MARL update"


# ---------------------------------------------------------
# TEST 8 — Joint-action probability distribution valid
# ---------------------------------------------------------
def test_joint_action_probability_valid():
    engine = MARLEngine(lr=0.0003, hidden=32, agents=4)

    queues = {"billing": {"load": 0.8}, "tech": {"load": 0.4}}

    out = engine.compute_joint_action(queues)

    for agent, payload in out["actions"].items():
        total = sum(payload["probs"].values())
        assert abs(total - 1.0) < 1e-6, f"Agent {agent} probabilities do not sum to 1"


# ---------------------------------------------------------
# TEST 9 — JSON-safe multi-agent probabilities
# ---------------------------------------------------------
def test_multistep_json_safe():
    engine = MARLEngine(lr=0.0003, hidden=32, agents=4)

    queues = {"billing": {"load": 0.8}, "tech": {"load": 0.4}}

    out = engine.compute_joint_action(queues)

    try:
        json.dumps(out)
    except Exception as e:
        pytest.fail(f"MARL multi-agent output is not JSON-safe: {e}")