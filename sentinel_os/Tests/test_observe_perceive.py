import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from observe_perceive_core import (
    ObserveCore, PerceiveCore, EmotionalState, CallOutcome,
    synthesize_percept, FrictionEvent
)

def test_observe_friction_detection():
    print("\n[TEST 1] OBSERVE: Friction detection")
    observe = ObserveCore()
    
    # Test 1a: Detect repeat
    event1 = observe.observe_transition("C001", "root", "billing_queue", 15.0)
    event2 = observe.observe_transition("C001", "billing_queue", "billing_agent", 10.0)
    event3 = observe.observe_transition("C001", "billing_agent", "billing_queue", 5.0)  # repeat!
    
    assert event3 is not None, "Should detect repeat"
    assert event3.type == "repeat", f"Should be repeat, got {event3.type}"
    assert event3.severity > 0, "Repeat should have severity"
    
    # Test 1b: Detect long wait
    event4 = observe.observe_transition("C002", "root", "tech_queue", 45.0)  # > 30s
    assert event4 is not None, "Should detect long wait"
    assert event4.type == "long_wait", f"Should be long_wait, got {event4.type}"
    
    print(f"  ✓ PASSED - Detected {2} friction types")
    return True

def test_perceive_emotional_state():
    print("\n[TEST 2] PERCEIVE: Emotional state inference")
    observe = ObserveCore()
    
    friction_events = [
        FrictionEvent(node="queue_a", type="repeat", severity=0.5, timestamp=10.0),
        FrictionEvent(node="queue_b", type="long_wait", severity=0.8, timestamp=20.0),
    ]
    
    emotion = observe.get_emotional_state("C001", friction_events, elapsed_time=60.0)
    
    assert emotion.frustration > 0.3, f"Should be frustrated, got {emotion.frustration}"
    assert emotion.patience < 0.8, f"Patience should decrease, got {emotion.patience}"
    assert emotion.trust < 1.0, f"Trust should decrease, got {emotion.trust}"
    
    print(f"  ✓ PASSED - Emotional state: frustration={emotion.frustration:.2f}, "
          f"patience={emotion.patience:.2f}, trust={emotion.trust:.2f}")
    return True

def test_perceive_outcome_inference():
    print("\n[TEST 3] PERCEIVE: Outcome inference")
    perceive = PerceiveCore()
    
    # Test resolved
    emotion_good = EmotionalState(frustration=0.2, patience=0.8, trust=0.9)
    journey_resolved = ["root", "intent_menu", "billing_queue", "agent_a"]
    outcome1 = perceive.infer_outcome(journey_resolved, emotion_good, "agent_a")
    assert outcome1 == CallOutcome.RESOLVED, f"Should be resolved, got {outcome1}"
    
    # Test abandoned
    emotion_bad = EmotionalState(frustration=0.9, patience=0.1, trust=0.3)
    journey_abandoned = ["root", "intent_menu", "billing_queue", "billing_queue"]
    outcome2 = perceive.infer_outcome(journey_abandoned, emotion_bad, "exit")
    assert outcome2 == CallOutcome.ABANDONED, f"Should be abandoned, got {outcome2}"
    
    print(f"  ✓ PASSED - Correctly inferred {2} outcome types")
    return True

def test_perceive_abandonment_risk():
    print("\n[TEST 4] PERCEIVE: Abandonment risk prediction")
    perceive = PerceiveCore()
    
    # High frustration + low patience = high risk
    emotion_high_risk = EmotionalState(frustration=0.9, patience=0.1, trust=0.3)
    risk_high = perceive.predict_abandonment_risk(emotion_high_risk, wait_time_remaining=120.0)
    assert risk_high > 0.6, f"Should be high risk, got {risk_high}"
    
    # Low frustration + high patience = low risk
    emotion_low_risk = EmotionalState(frustration=0.1, patience=0.9, trust=0.9)
    risk_low = perceive.predict_abandonment_risk(emotion_low_risk, wait_time_remaining=5.0)
    assert risk_low < 0.3, f"Should be low risk, got {risk_low}"
    
    print(f"  ✓ PASSED - High risk: {risk_high:.2f}, Low risk: {risk_low:.2f}")
    return True

def test_perceive_next_action():
    print("\n[TEST 5] PERCEIVE: Next action prediction")
    perceive = PerceiveCore()
    
    emotion = EmotionalState(frustration=0.8, patience=0.2, trust=0.4)
    available = ["billing_queue", "tech_queue", "exit"]
    
    dist = perceive.predict_next_action("intent_menu", "billing", emotion, available)
    
    assert "exit" in dist, "Exit should be in distribution"
    assert dist["exit"] > dist["billing_queue"], "Exit should be more likely when frustrated"
    assert sum(dist.values()) > 0.99, "Distribution should sum to ~1.0"
    
    print(f"  ✓ PASSED - Exit prob: {dist['exit']:.2f}, Queue prob: {dist.get('billing_queue', 0):.2f}")
    return True

def test_full_percept_synthesis():
    print("\n[TEST 6] PERCEIVE: Full percept synthesis")
    
    emotion = EmotionalState(frustration=0.6, patience=0.5, trust=0.6)
    friction = [FrictionEvent(node="queue", type="long_wait", severity=0.7, timestamp=30.0)]
    
    percept = synthesize_percept(
        caller_id="C001",
        journey=["root", "intent_menu", "billing_queue"],
        friction_events=friction,
        emotional_state=emotion,
        final_node="billing_queue",
        wait_time_remaining=45.0,
        available_next=["billing_agent", "exit"]
    )
    
    assert percept.caller_id == "C001"
    assert percept.abandonment_risk > 0.3, "Should have abandonment risk"
    assert len(percept.next_action_distribution) > 0, "Should have next actions"
    assert percept.outcome == CallOutcome.RESOLVED, "Should be resolved"
    
    print(f"  ✓ PASSED - Full percept synthesized")
    print(f"    Journey length: {len(percept.journey)}")
    print(f"    Abandonment risk: {percept.abandonment_risk:.2f}")
    print(f"    Outcome: {percept.outcome.value}")
    return True

def main():
    print("\n" + "="*70)
    print("OBSERVE/PERCEIVE CORE - PERCEPTION LAYER TESTS")
    print("="*70)
    
    tests = [
        ("Friction detection", test_observe_friction_detection),
        ("Emotional state", test_perceive_emotional_state),
        ("Outcome inference", test_perceive_outcome_inference),
        ("Abandonment risk", test_perceive_abandonment_risk),
        ("Next action prediction", test_perceive_next_action),
        ("Full percept synthesis", test_full_percept_synthesis),
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
    print(f"OBSERVE/PERCEIVE RESULTS: {passed}/{total} tests passed")
    print("="*70 + "\n")
    
    return all(results)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
