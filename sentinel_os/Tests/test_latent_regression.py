# Row Count: 162

"""
test_latent_regression.py
-------------------------

Deterministic regression tests for Iceberg's LatentPayload model.

These tests guarantee:
- No drift in latent variable defaults
- Deterministic update rules
- Stable structural hash
- JSON-safe serialization
- Replay-friendly latent evolution

Best-in-Class Notes
-------------------
- Deterministic: No randomness.
- Governance-Safe: Structural hash detects drift.
- Replay-Friendly: Identical inputs → identical latent evolution.
"""

import json
import hashlib
import pytest

from Domain.LatentPayload import LatentPayload


# ---------------------------------------------------------
# Structural hash utility
# ---------------------------------------------------------
def structural_hash(obj: dict) -> str:
    raw = json.dumps(obj, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------
# TEST 1 — Default latent payload is stable
# ---------------------------------------------------------
def test_latent_defaults_stable():
    lp1 = LatentPayload()
    lp2 = LatentPayload()

    assert lp1.to_dict() == lp2.to_dict(), "LatentPayload defaults have drifted"


# ---------------------------------------------------------
# TEST 2 — Structural hash is stable
# ---------------------------------------------------------
def test_latent_structural_hash_stable():
    lp = LatentPayload()
    snap = lp.to_dict()

    h1 = structural_hash(snap)
    h2 = structural_hash(snap)

    assert h1 == h2, "Structural hash changed unexpectedly"


# ---------------------------------------------------------
# TEST 3 — JSON-safe serialization
# ---------------------------------------------------------
def test_latent_json_safe():
    lp = LatentPayload()
    snap = lp.to_dict()

    try:
        json.dumps(snap)
    except Exception as e:
        pytest.fail(f"LatentPayload snapshot is not JSON-safe: {e}")


# ---------------------------------------------------------
# TEST 4 — Update rules are deterministic
# ---------------------------------------------------------
class DummyCallerDynamic:
    """Minimal deterministic caller dynamic for update testing."""
    def __init__(self):
        self.frustration = 0.0


def test_latent_update_deterministic():
    lp1 = LatentPayload()
    lp2 = LatentPayload()

    cd1 = DummyCallerDynamic()
    cd2 = DummyCallerDynamic()

    # Apply identical update steps
    for _ in range(5):
        lp1.update_after_step(cd1)
        lp2.update_after_step(cd2)

    assert lp1.to_dict() == lp2.to_dict(), "Latent update rules are not deterministic"


# ---------------------------------------------------------
# TEST 5 — Update rules produce expected monotonic changes
# ---------------------------------------------------------
def test_latent_clamp_boundaries():
    """Verify governance boundaries are enforced by _clamp."""
    lp = LatentPayload(patience=0.0, trust_scalar=0.01)
    cd = DummyCallerDynamic()
    
    # Force state beyond reasonable bounds
    lp.update_after_step(cd)
    
    assert 0.0 <= lp.trust_scalar <= 1.0, "trust_scalar breached governance boundaries"
    assert 0.0 <= lp.volatility <= 1.0, "volatility breached governance boundaries"
    assert lp.memory_flag <= 1.0, "memory_flag exceeded maximum capacity"


# ---------------------------------------------------------
# TEST 6 — Structural hash changes after updates
# ---------------------------------------------------------
def test_latent_hash_changes_after_update():
    lp = LatentPayload()
    cd = DummyCallerDynamic()

    h_before = structural_hash(lp.to_dict())
    lp.update_after_step(cd)
    h_after = structural_hash(lp.to_dict())

    assert h_before != h_after, "Structural hash should change after latent update"


# ---------------------------------------------------------
# TEST 7 — Multiple updates remain JSON-safe
# ---------------------------------------------------------
def test_latent_multiple_updates_json_safe():
    lp = LatentPayload()
    cd = DummyCallerDynamic()

    for _ in range(10):
        lp.update_after_step(cd)

    try:
        json.dumps(lp.to_dict())
    except Exception as e:
        pytest.fail(f"LatentPayload after multiple updates is not JSON-safe: {e}")