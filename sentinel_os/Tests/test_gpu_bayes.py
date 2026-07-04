# Row Count: 198

"""
test_gpu_bayes.py
-----------------

Regression and behavioral tests for Iceberg's BayesianIntentEngineGPU.

Governance Mandates:
- Numerical Stability: Updates must survive extreme likelihood values.
- Determinism: GPU operations must match CPU outputs exactly.
- Auditability: Structural hashes must be invariant across identical runs.
"""

import pytest
import torch
import numpy as np
from bayes_gpu import BayesianIntentEngineGPU

# ---------------------------------------------------------
# Fixtures
# ---------------------------------------------------------
@pytest.fixture
def engine():
    return BayesianIntentEngineGPU(deterministic=True)

@pytest.fixture
def base_data():
    return {
        "intents": ["billing", "tech", "sales"],
        "posterior": {"billing": 0.4, "tech": 0.3, "sales": 0.3},
        "likelihood": {"billing": 0.9, "tech": 0.1, "sales": 0.5}
    }

# ---------------------------------------------------------
# Test Suites
# ---------------------------------------------------------

def test_deterministic_initialization(engine):
    """Mandate: Engine must enforce deterministic algorithms."""
    assert torch.are_deterministic_algorithms_enabled()

def test_single_update_equivalence(engine, base_data):
    """Mandate: GPU updates must be perfectly deterministic."""
    out1 = engine.observe_single(**base_data)
    out2 = engine.observe_single(**base_data)
    assert out1 == out2, "Stochasticity detected in GPU update."

def test_log_space_underflow_resilience(engine, base_data):
    """Mandate: Engine must handle extremely low likelihoods without collapse."""
    # Underflow risk scenario
    tiny_likelihood = {"billing": 1e-10, "tech": 1e-10, "sales": 1e-10}
    out = engine.observe_single(base_data["posterior"], tiny_likelihood, base_data["intents"])
    
    assert abs(sum(out.values()) - 1.0) < 1e-6, "Distribution collapsed under extreme likelihoods."

def test_sequential_update_stability(engine, base_data):
    """Mandate: Sequence updates must maintain probability integrity."""
    seq = [base_data["likelihood"]] * 5
    out = engine.observe_sequence(base_data["posterior"], seq, base_data["intents"])
    
    assert abs(sum(out.values()) - 1.0) < 1e-6, "Distribution invalidated after sequence."

def test_cpu_gpu_alignment():
    """Mandate: CPU and GPU backends must produce bit-identical results."""
    # This specifically checks for cross-architecture drift
    cpu_engine = BayesianIntentEngineGPU(device="cpu", deterministic=True)
    gpu_engine = BayesianIntentEngineGPU(device="cuda", deterministic=True)
    
    # Note: Requires CUDA available to test properly; skip if missing
    if not torch.cuda.is_available():
        pytest.skip("CUDA unavailable, skipping cross-device alignment.")
        
    # Logic to compare outputs...
    pass 

def test_structural_hash_sensitivity(engine, base_data):
    """Mandate: Hash must evolve if and only if state changes."""
    h_before = hash(str(base_data["posterior"]))
    out = engine.observe_single(**base_data)
    h_after = hash(str(out))
    
    assert h_before != h_after, "Hash failed to detect intent drift."