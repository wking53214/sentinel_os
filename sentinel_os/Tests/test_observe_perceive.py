"""
Tests for OBSERVE and PERCEIVE Core Engines
"""

import numpy as np
import pytest
from observe_perceive_core import (
    ObserveCore, 
    PerceiveCore, 
    EmotionalState, 
    FrictionEvent, 
    CallOutcome, 
    CallPercept,
    synthesize_percept
)

def test_observe_friction_detection():
    print("\n[TEST 1] OBSERVE: Friction detection engine")
    observe = ObserveCore()
    
    e1 = observe.observe_transition("C001", "start", "root", 10.0)
    e2 = observe.observe_transition("C001", "root", "intent_menu", 25.0)
    e3 = observe.observe_transition("C001", "intent_menu", "intent_menu", 35.0)
    e4 = observe.observe_transition("C001", "intent_menu", "billing_queue", 90.0)
    
    friction = [e for e in [e1, e2, e3, e4] if e is not None]
    
    assert len(friction) >= 2, "Should detect at least repeat menu and wait friction"
    assert any(f.type == "repeat" for f in friction)
    assert any(f.type == "long_wait" for f in friction)

def test_perceive_emotional_state():
    print("\n[TEST 2] PERCEIVE: Emotional state transition model")
    observe = ObserveCore()
    
    friction = [
        FrictionEvent(node="intent_menu", type="repeat", severity=0.6, timestamp=35.0),
        FrictionEvent(node="billing_queue", type="long_wait", severity=0.8, timestamp=90.0)
    ]
    
    updated_emotion = observe.get_emotional_state("C001", friction, elapsed_time=90.0)
    
    # Production initializes from frustration=0.0, patience=1.0, trust=1.0
    assert updated_emotion.frustration > 0.0, "Frustration should increase"
    assert updated_emotion.patience < 1.0, "Patience should decay"
    assert updated_emotion.trust < 1.0, "Trust should degrade from baseline"

def test_perceive_outcome_inference():
    print("\n[TEST 3] PERCEIVE: Outcome inference")
    perceive = PerceiveCore()
    
    emotion_good = EmotionalState(frustration=0.2, patience=0.8, trust=0.9)
    journey_resolved = ["root", "intent_menu", "billing_queue", "agent_a"]
    outcome1 = perceive.infer_outcome(journey_resolved, emotion_good, "agent_a")
    assert outcome1 == CallOutcome.RESOLVED, f"Should be resolved, got {outcome1}"
    
    emotion_bad = EmotionalState(frustration=0.8, patience=0.1, trust=0.2)
    journey_abandoned = ["root", "intent_menu", "billing_queue"]
    outcome2 = perceive.infer_outcome(journey_abandoned, emotion_bad, "billing_queue")
    assert outcome2 == CallOutcome.IN_PROGRESS, f"Should be in progress, got {outcome2}"

def test_perceive_abandonment_risk():
    print("\n[TEST 4] PERCEIVE: Abandonment risk calculation")
    perceive = PerceiveCore()
    
    emotion_low_risk = EmotionalState(frustration=0.1, patience=0.9, trust=0.8)
    risk_low = perceive.predict_abandonment_risk(emotion_low_risk, wait_time_remaining=10.0)
    
    emotion_high_risk = EmotionalState(frustration=0.8, patience=0.1, trust=0.2)
    risk_high = perceive.predict_abandonment_risk(emotion_high_risk, wait_time_remaining=120.0)
    
    assert risk_low < 0.4, f"Low risk should be small, got {risk_low}"
    assert risk_high > 0.6, f"High risk should be elevated, got {risk_high}"

def test_perceive_next_action():
    print("\n[TEST 5] PERCEIVE: Next action distribution")
    perceive = PerceiveCore()
    
    emotion = EmotionalState(frustration=0.5, patience=0.4, trust=0.6)
    actions = perceive.predict_next_action(
        current_node="billing_queue",
        caller_intent="unknown",
        emotional_state=emotion,
        available_nodes=["billing_agent", "exit"]
    )
    
    assert "billing_agent" in actions
    assert "exit" in actions
    assert np.isclose(sum(actions.values()), 1.0), "Probabilities must sum to 1.0"

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
    assert percept.abandonment_risk > 0.2, "Should have abandonment risk"
    assert len(percept.next_action_distribution) > 0, "Should have next actions"
    assert percept.outcome == CallOutcome.IN_PROGRESS, "Should be in progress"
    
    print(f"  ✓ PASSED - Full percept synthesized")
    print(f"    Journey length: {len(percept.journey)}")
    print(f"    Abandonment risk: {percept.abandonment_risk:.2f}")
    print(f"    Inferred outcome: {percept.outcome.value}")

if __name__ == "__main__":
    test_observe_friction_detection()
    test_perceive_emotional_state()
    test_perceive_outcome_inference()
    test_perceive_abandonment_risk()
    test_perceive_next_action()
    test_full_percept_synthesis()
