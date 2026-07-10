import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sentinel_core import (
    SentinelCore, CallerIntent, OutcomeQuality
)
from cassettes.ivr_cassette import IvrCassette
from observe_perceive_core import EmotionalState, FrictionEvent

def test_sentinel_intent_inference():
    print("\n[TEST 1] Sentinel: Intent inference from queue choice")
    sentinel = SentinelCore(IvrCassette())
    
    # Test billing queue
    signal1 = sentinel.infer_intent(["root", "intent_menu", "billing_queue"], "billing_queue")
    assert signal1.confidence > 0.8, "Should be confident"
    assert "billing" in signal1.queue_chosen.lower()
    
    # Test unknown queue
    signal2 = sentinel.infer_intent(["root", "intent_menu", "unknown_queue"], "unknown_queue")
    assert signal2.confidence < 0.5, "Should be uncertain"
    
    print(f"  ✓ PASSED - Inferred intents: {signal1.queue_chosen}, {signal2.queue_chosen}")
    return True

def test_sentinel_quality_scoring():
    print("\n[TEST 2] Sentinel: Outcome quality scoring")
    sentinel = SentinelCore(IvrCassette())
    
    emotion_good = EmotionalState(frustration=0.1, patience=0.9, trust=0.9)
    emotion_bad = EmotionalState(frustration=0.9, patience=0.1, trust=0.3)
    
    # Excellent: resolved fast with no friction
    score1 = sentinel.score_outcome_quality(True, 20.0, 0, emotion_good)
    assert score1.quality_tier == OutcomeQuality.EXCELLENT, f"Should be excellent, got {score1.quality_tier}"
    assert score1.overall_score > 0.85
    
    # Failed: abandoned with friction
    score2 = sentinel.score_outcome_quality(False, 120.0, 3, emotion_bad)
    assert score2.quality_tier == OutcomeQuality.FAILED, f"Should be failed, got {score2.quality_tier}"
    assert score2.overall_score < 0.35
    
    print(f"  ✓ PASSED - Scored outcomes: {score1.quality_tier.value}, {score2.quality_tier.value}")
    return True

def test_sentinel_abandonment_diagnosis():
    print("\n[TEST 3] Sentinel: Abandonment diagnosis")
    sentinel = SentinelCore(IvrCassette())
    
    emotion_bad = EmotionalState(frustration=0.8, patience=0.1, trust=0.3)
    
    # Create mock friction event
    class MockFrictionEvent:
        def __init__(self, event_type):
            self.type = event_type
    
    # Long wait case
    friction_long_wait = [MockFrictionEvent("long_wait")]
    diag1 = sentinel.diagnose_abandonment(
        ["root", "intent_menu", "billing_queue"],
        friction_long_wait,
        emotion_bad,
        resolved=False
    )
    assert diag1.primary_cause == "long_wait"
    assert diag1.intervention_point is not None
    
    # Repeat case
    friction_repeat = [MockFrictionEvent("repeat")]
    diag2 = sentinel.diagnose_abandonment(
        ["root", "intent_menu", "billing_queue", "billing_queue"],
        friction_repeat,
        emotion_bad,
        resolved=False
    )
    assert diag2.primary_cause == "repeat_routing"
    
    print(f"  ✓ PASSED - Diagnosed: {diag1.primary_cause}, {diag2.primary_cause}")
    return True

def test_sentinel_queue_prescription():
    print("\n[TEST 4] Sentinel: Queue reordering prescription")
    sentinel = SentinelCore(IvrCassette())
    
    # Simulate call outcomes with different success rates
    outcomes = [
        {"journey": ["root", "billing_queue", "agent"], "resolved": True},
        {"journey": ["root", "billing_queue", "agent"], "resolved": True},
        {"journey": ["root", "tech_queue", "agent"], "resolved": False},
        {"journey": ["root", "tech_queue", "agent"], "resolved": False},
        {"journey": ["root", "sales_queue", "agent"], "resolved": True},
    ]
    
    current = ["tech_queue", "sales_queue", "billing_queue"]
    rx = sentinel.prescribe_queue_reordering(outcomes, current)
    
    # Should recommend billing first (100% success), then sales, then tech
    assert rx.proposed_order[0] == "billing_queue", f"Should recommend billing first, got {rx.proposed_order}"
    assert rx.estimated_impact >= 0, "Estimated impact should be positive"
    
    print(f"  ✓ PASSED - Prescribed reorder: {rx.proposed_order}")
    return True

def test_sentinel_structural_hash():
    print("\n[TEST 5] Sentinel: Structural hash determinism")
    sentinel1 = SentinelCore(IvrCassette())
    sentinel2 = SentinelCore(IvrCassette())
    
    hash1 = sentinel1.structural_hash()
    hash2 = sentinel2.structural_hash()
    
    assert hash1 == hash2, "Same Sentinel should have same hash"
    assert len(hash1) == 64, "SHA256 should be 64 chars"
    
    print(f"  ✓ PASSED - Deterministic hash: {hash1[:16]}...")
    return True

def main():
    print("\n" + "="*70)
    print("SENTINEL CORE - ANALYTICS & DIAGNOSTICS TESTS")
    print("="*70)
    
    tests = [
        ("Intent inference", test_sentinel_intent_inference),
        ("Quality scoring", test_sentinel_quality_scoring),
        ("Abandonment diagnosis", test_sentinel_abandonment_diagnosis),
        ("Queue prescription", test_sentinel_queue_prescription),
        ("Structural hash", test_sentinel_structural_hash),
    ]
    
    results = []
    for name, test_fn in tests:
        try:
            results.append(test_fn())
        except Exception as e:
            print(f"  ✗ FAILED: {e}")
            import traceback
            traceback.print_exc()
            results.append(False)
    
    passed = sum(results)
    total = len(results)
    
    print("\n" + "="*70)
    print(f"SENTINEL RESULTS: {passed}/{total} tests passed")
    print("="*70 + "\n")
    
    return all(results)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
