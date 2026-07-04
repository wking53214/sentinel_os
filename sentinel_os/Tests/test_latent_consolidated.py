# Row Count: 204

"""
test_latent_consolidated.py
---------------------------

Unified regression and functional test suite for LatentPayload.

Governance Mandates:
- Deterministic behavior: Multi-step evolution must be invariant.
- Drift detection: Structural hash must be immutable for fixed states.
- Boundary enforcement: Clamping must be verified across extremes.
"""

import pytest
import json
from domain.LatentPayload import LatentPayload

# ---------------------------------------------------------
# Mock Utilities
# ---------------------------------------------------------
class DummyCallerDynamic:
    def __init__(self, frustration: float = 0.0):
        self.frustration = frustration

# ---------------------------------------------------------
# Test Suites
# ---------------------------------------------------------

def test_initialization_defaults():
    """Mandate: LatentPayload defaults must remain invariant."""
    lp1, lp2 = LatentPayload(), LatentPayload()
    assert lp1.to_dict() == lp2.to_dict(), "Default state drift detected."

def test_json_serialization_integrity():
    """Mandate: Payload must be JSON-safe at all lifecycle stages."""
    lp = LatentPayload()
    for _ in range(10):
        lp.update_after_step(DummyCallerDynamic())
        assert json.dumps(lp.to_dict()), "Snapshot serialization failed."

@pytest.mark.parametrize("steps", [1, 5, 20])
def test_evolution_determinism(steps):
    """Mandate: Identical inputs must yield identical state evolution."""
    lp1, lp2 = LatentPayload(), LatentPayload()
    cd1, cd2 = DummyCallerDynamic(0.1), DummyCallerDynamic(0.1)
    
    for _ in range(steps):
        lp1.update_after_step(cd1)
        lp2.update_after_step(cd2)
        
    assert lp1.to_dict() == lp2.to_dict(), "Non-deterministic state drift."
    assert lp1.structural_hash() == lp2.structural_hash(), "Hash drift detected."

def test_structural_hash_drift_sensitivity():
    """Mandate: Structural hash must detect latent state mutation."""
    lp = LatentPayload()
    h_before = lp.structural_hash()
    
    lp.update_after_step(DummyCallerDynamic(0.2))
    assert h_before != lp.structural_hash(), "Hash failed to detect state change."

def test_governance_clamping_invariants():
    """Mandate: Emotional and trust variables must remain within [0.0, 1.0]."""
    # Initialize with edge cases
    lp = LatentPayload(patience=0.0, trust_scalar=0.01)
    cd = DummyCallerDynamic(frustration=5.0) # Extreme frustration
    
    lp.update_after_step(cd)
    
    state = lp.to_dict()
    assert 0.0 <= state["trust_scalar"] <= 1.0, "trust_scalar clamped violation."
    assert 0.0 <= state["volatility"] <= 1.0, "volatility clamped violation."
    assert state["memory_flag"] <= 1.0, "memory_flag ceiling violation."

def test_monotonic_emotional_drift():
    """Mandate: Behavioral evolution must adhere to defined monotonicity."""
    lp = LatentPayload()
    cd = DummyCallerDynamic(frustration=0.2)
    
    before = lp.to_dict()
    lp.update_after_step(cd)
    after = lp.to_dict()
    
    assert after["trust_scalar"] <= before["trust_scalar"], "Trust invariant violation."
    assert after["volatility"] >= before["volatility"], "Volatility invariant violation."
    assert after["memory_flag"] >= before["memory_flag"], "Memory invariant violation."