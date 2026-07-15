import sys
import os
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from cassette_loader import CassetteLoader
from cassette_schema import validate_cassette

from governance.drift_core_v1 import DriftPolicy, detect_drift, DriftSignal
from governance.self_heal_v1 import heal, HealBand, InMemoryParameterStore
from governance.log_rotation_v1 import LogRotationManager, LocalDiskAdapter

def test_ppo_router_expected_wait():
    print("\n[TEST 1] PPORouter accepts expected_wait parameter")
    from Engines.rl_ppo_adaptive import PPORouter
    
    neighbors = {"node_a": ["fast_node", "slow_node"]}
    
    router1 = PPORouter(graph=None, neighbors=neighbors)
    assert router1.expected_wait == {}, "Default expected_wait should be empty"
    
    expected_wait_map = {"slow_node": 100.0, "fast_node": 5.0}
    router2 = PPORouter(graph=None, neighbors=neighbors, expected_wait=expected_wait_map)
    assert router2.expected_wait == expected_wait_map, "expected_wait not stored"
    
    print(f"  Router1 expected_wait: {router1.expected_wait}")
    print(f"  Router2 expected_wait: {router2.expected_wait}")
    print("  ✓ PASSED")
    return True

def test_zero_drift():
    print("\n[TEST 2] Zero drift (stable conditions)")
    baseline = {"auth": 20.0, "menu": 12.0}
    current = {"auth": [19.8, 20.1, 20.2] * 10, "menu": [11.9, 12.0, 12.1] * 10}
    
    policy = DriftPolicy(metric_q=90.0, rel_threshold=0.40, min_samples=20)
    signals = detect_drift(baseline, current, policy)
    breached = [s for s in signals if s.breached]
    
    assert len(breached) == 0, "Should detect no drift in stable conditions"
    print("  ✓ PASSED - No false positives")
    return True

def test_multiple_drifts():
    print("\n[TEST 3] Multiple simultaneous drifts")
    import random
    baseline = {"auth": 20.0, "menu": 12.0, "intent": 15.0}
    rng = random.Random(42)
    current = {
        "auth": [rng.uniform(40, 60) for _ in range(100)],
        "menu": [rng.uniform(24, 36) for _ in range(100)],
        "intent": [rng.uniform(30, 45) for _ in range(100)],
    }
    
    policy = DriftPolicy(metric_q=90.0, rel_threshold=0.40, min_samples=20)
    signals = detect_drift(baseline, current, policy)
    breached = [s for s in signals if s.breached]
    
    assert len(breached) == 3, f"Should detect 3 drifts, got {len(breached)}"
    print("  ✓ PASSED - All 3 simultaneous drifts detected")
    return True

def test_clamping_boundaries():
    print("\n[TEST 4] Self-healing respects clamp boundaries")
    tmp = tempfile.mkdtemp()
    ledger = LogRotationManager(LocalDiskAdapter(tmp), seed="815")
    store = InMemoryParameterStore()
    lo, hi = validate_cassette(CassetteLoader().load_cassette("ivr")).range_value("expected_wait_bounds")
    band = HealBand(lo=lo, hi=hi)
    
    signal = DriftSignal(
        node="test", baseline_value=20.0, current_value=500.0,
        rel_change=24.0, breached=True, n_current=100, reason=""
    )
    
    records = heal([signal], store, band, ledger, kind="expected_wait")
    
    # Clamp ceiling is the cassette's expected_wait_bounds hi -- assert
    # against the cassette, not a literal, so this test tracks the
    # source of truth instead of duplicating it.
    assert records[0].applied == hi, f"Should clamp to {hi}, got {records[0].applied}"
    print(f"  ✓ PASSED - Extreme value (500s) clamped to ceiling ({hi}s)")
    return True

def main():
    print("\n" + "="*70)
    print("CRITICAL INTEGRATION & EDGE CASE TESTS")
    print("="*70)
    
    results = []
    try:
        results.append(test_ppo_router_expected_wait())
    except Exception as e:
        print(f"  ✗ FAILED: {e}")
        results.append(False)
    
    try:
        results.append(test_zero_drift())
    except Exception as e:
        print(f"  ✗ FAILED: {e}")
        results.append(False)
    
    try:
        results.append(test_multiple_drifts())
    except Exception as e:
        print(f"  ✗ FAILED: {e}")
        results.append(False)
    
    try:
        results.append(test_clamping_boundaries())
    except Exception as e:
        print(f"  ✗ FAILED: {e}")
        results.append(False)
    
    passed = sum(results)
    total = len(results)
    print("\n" + "="*70)
    print(f"RESULTS: {passed}/{total} tests passed")
    print("="*70 + "\n")
    
    return all(results)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
