import sys
import os
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from governance.recommend_v1 import recommend
from governance.drift_core_v1 import DriftPolicy, detect_drift, DriftSignal
from governance.log_rotation_v1 import LogRotationManager, LocalDiskAdapter

def test_recommendation_engine():
    print("\n[TEST 5] Recommendation engine generates proposals")
    
    signals = [
        DriftSignal(node="auth::auth", baseline_value=20.0, current_value=50.0,
                   rel_change=1.5, breached=True, n_current=100, reason="auth spike"),
        DriftSignal(node="menu::main", baseline_value=12.0, current_value=13.0,
                   rel_change=0.08, breached=False, n_current=100, reason="stable"),
    ]
    
    tmp = tempfile.mkdtemp()
    ledger = LogRotationManager(LocalDiskAdapter(tmp), seed="815")
    
    recommendations = recommend(signals, ledger)
    
    assert len(recommendations) == 1, f"Should have 1 recommendation (only breached), got {len(recommendations)}"
    assert recommendations[0].status == "pending", "Recommendation should be pending"
    assert "auth" in recommendations[0].role, "Should identify auth node"
    
    print(f"  ✓ PASSED - Generated {len(recommendations)} recommendation(s)")
    return True

def test_multi_node_drift():
    print("\n[TEST 6] Multi-node drift in one journey")
    import random
    
    baseline = {
        "step1::menu": 10.0,
        "step2::auth": 20.0,
        "step3::verify": 15.0,
    }
    
    rng = random.Random(99)
    current = {
        "step1::menu": [9.5, 10.0, 10.5] * 10,  # stable
        "step2::auth": [rng.uniform(40, 60) for _ in range(100)],  # 2x drift
        "step3::verify": [rng.uniform(30, 45) for _ in range(100)],  # 2.5x drift
    }
    
    policy = DriftPolicy(metric_q=90.0, rel_threshold=0.40, min_samples=20)
    signals = detect_drift(baseline, current, policy)
    breached = [s for s in signals if s.breached]
    
    assert len(breached) == 2, f"Should detect 2 drifts, got {len(breached)}"
    nodes = {s.node for s in breached}
    assert "step2::auth" in nodes and "step3::verify" in nodes
    
    print(f"  ✓ PASSED - Detected selective drift across {len(breached)} nodes in journey")
    return True

def test_ledger_recovery():
    print("\n[TEST 7] Ledger recovery from tail corruption")
    tmp = tempfile.mkdtemp()
    ledger = LogRotationManager(LocalDiskAdapter(tmp), seed="815")
    
    # Write clean data
    ledger.flush([{"action": "clean_1"}])
    ledger.flush([{"action": "clean_2"}])
    
    # Corrupt the last chunk's content
    import json
    last_chunk = max(ledger.adapter.list_chunks())
    with open(f"{tmp}/chunk_{last_chunk:06d}.json", "w") as f:
        f.write(json.dumps({"corrupted": True}))
    
    # Try tolerant verify
    report = ledger.verify(mode="strict")
    
    # Strict should fail on corrupted chunk
    if not report["ok"]:
        print(f"  ✓ PASSED - Strict mode correctly rejected corrupted tail")
        return True
    else:
        print(f"  ✗ FAILED - Should have detected corruption")
        return False

def test_parameter_persistence():
    print("\n[TEST 8] Parameter persistence across batches")
    from governance.self_heal_v1 import InMemoryParameterStore
    
    store = InMemoryParameterStore()
    
    # Batch 1: Set parameters
    store.set("expected_wait", "node_a", 25.0)
    store.set("expected_wait", "node_b", 15.0)
    
    snap1 = store.snapshot()
    
    # Batch 2: Retrieve and verify
    assert store.get("expected_wait", "node_a") == 25.0
    assert store.get("expected_wait", "node_b") == 15.0
    
    snap2 = store.snapshot()
    
    assert snap1 == snap2, "Snapshots should match"
    print(f"  ✓ PASSED - Parameters persisted: {len(snap2)} entries retained")
    return True

def main():
    print("\n" + "="*70)
    print("REMAINING EDGE CASE & INTEGRATION TESTS")
    print("="*70)
    
    results = []
    tests = [
        ("Recommendation engine", test_recommendation_engine),
        ("Multi-node drift", test_multi_node_drift),
        ("Ledger recovery", test_ledger_recovery),
        ("Parameter persistence", test_parameter_persistence),
    ]
    
    for name, test_fn in tests:
        try:
            results.append(test_fn())
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
