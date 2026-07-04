import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from observe_perceive_core import (
    EmotionalState, CallOutcome, synthesize_percept, FrictionEvent
)

def test_full_percept_synthesis():
    print("\n[TEST 6] PERCEIVE: Full percept synthesis")
    
    emotion = EmotionalState(frustration=0.6, patience=0.5, trust=0.6)
    friction = [FrictionEvent(node="queue", type="long_wait", severity=0.7, timestamp=30.0)]
    
    percept = synthesize_percept(
        caller_id="C001",
        journey=["root", "intent_menu", "billing_queue", "agent_a"],
        friction_events=friction,
        emotional_state=emotion,
        final_node="agent_a",  # Now a resolution node
        wait_time_remaining=0.0,
        available_next=["exit"]
    )
    
    assert percept.caller_id == "C001"
    assert percept.abandonment_risk >= 0.0, "Should have abandonment risk"
    assert len(percept.next_action_distribution) > 0, "Should have next actions"
    assert percept.outcome == CallOutcome.RESOLVED, f"Should be resolved, got {percept.outcome}"
    
    print(f"  ✓ PASSED - Full percept synthesized")
    print(f"    Journey length: {len(percept.journey)}")
    print(f"    Abandonment risk: {percept.abandonment_risk:.2f}")
    print(f"    Outcome: {percept.outcome.value}")
    return True

test_full_percept_synthesis()
