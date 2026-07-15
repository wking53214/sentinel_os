import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from queue_staffing_bayes_integration import (
    QueueState, StaffingCoordinator, BayesianIntentEngine, integrate_all_three
)

def test_queue_dynamics():
    print("\n[TEST 1] Queue dynamics: Erlang C and staffing")
    coord = StaffingCoordinator()
    
    # Light traffic
    wait1 = coord.queue_dynamics.predict_wait_time(agents=5, traffic_intensity=3.0, avg_handle_time=5.0)
    assert wait1 < 20.0, f"Light traffic should have short wait, got {wait1}"
    
    # Heavy traffic
    wait2 = coord.queue_dynamics.predict_wait_time(agents=5, traffic_intensity=4.5, avg_handle_time=5.0)
    assert wait2 > wait1, "Heavy traffic should have longer wait"
    
    # Recommend agents for target
    agents_needed = coord.queue_dynamics.recommended_agents(traffic_intensity=4.5, target_wait=30.0, avg_handle_time=5.0)
    assert agents_needed >= 5, f"Should recommend more agents, got {agents_needed}"
    
    print(f"  ✓ PASSED - Light wait: {wait1:.1f}s, Heavy wait: {wait2:.1f}s, Agents needed: {agents_needed}")
    return True

def test_staffing_adjustment():
    print("\n[TEST 2] Staffing adjustment from governance")
    coord = StaffingCoordinator()
    
    queue = QueueState(
        queue_name="billing_queue",
        waiting_count=20,
        current_wait_p90=45.0,
        staffed_agents=5,
        abandonment_rate=0.15
    )
    
    # Governance signal: heal wait to 20s
    gov_signal = {"healed_expected_wait": 20.0}
    
    adjustment = coord.propose_adjustment(queue, gov_signal)
    
    assert adjustment is not None, "Should propose adjustment"
    assert adjustment.recommended_agents > queue.staffed_agents, "Should recommend more staff"
    assert adjustment.expected_wait_reduction > 0, "Should reduce wait"
    
    print(f"  ✓ PASSED - Recommend: {adjustment.current_agents} → {adjustment.recommended_agents} agents")
    print(f"             Expected wait reduction: {adjustment.expected_wait_reduction:.1f}s")
    return True

def test_bayes_intent():
    print("\n[TEST 3] Bayesian intent learning")
    bayes = BayesianIntentEngine()
    
    # Simulate 20 billing calls: 15 resolved, 5 not
    for i in range(15):
        bayes.observe_outcome("billing", True, 4.0)
    for i in range(5):
        bayes.observe_outcome("billing", False, 6.0)
    
    posterior = bayes.get_posterior("billing")
    
    assert posterior.success_rate == 0.75, f"Should be 75% success, got {posterior.success_rate}"
    assert posterior.confidence > 0.15, "Should have some confidence"
    assert posterior.avg_handling_time < 5.0, "Avg should decrease with more successes"
    
    print(f"  ✓ PASSED - Billing success: {posterior.success_rate*100:.1f}%, Confidence: {posterior.confidence:.2f}")
    return True

def test_full_integration():
    print("\n[TEST 4] Full Queue + Staffing + Bayes integration")
    
    queue_states = [
        QueueState("billing_queue", 15, 40.0, 4, 0.12),
        QueueState("tech_queue", 25, 65.0, 3, 0.18),
    ]
    
    gov_signals = {
        "billing_queue": {"healed_expected_wait": 20.0},
        "tech_queue": {"healed_expected_wait": 30.0},
    }
    
    call_outcomes = [
        {"intent": "billing", "resolved": True, "handle_time": 4.0},
        {"intent": "billing", "resolved": True, "handle_time": 5.0},
        {"intent": "technical", "resolved": False, "handle_time": 10.0},
        {"intent": "technical", "resolved": True, "handle_time": 8.0},
    ]
    
    result = integrate_all_three(queue_states, gov_signals, call_outcomes)
    
    assert len(result["staffing_adjustments"]) > 0, "Should recommend staffing changes"
    assert len(result["bayesian_posteriors"]) > 0, "Should have posterior beliefs"
    assert result["queue_count"] == 2
    
    print(f"  ✓ PASSED - Staffing changes: {len(result['staffing_adjustments'])}")
    print(f"             Intents tracked: {len(result['bayesian_posteriors'])}")
    return True

def main():
    print("\n" + "="*70)
    print("QUEUE/STAFFING/BAYES INTEGRATION TESTS")
    print("="*70)
    
    tests = [
        ("Queue dynamics", test_queue_dynamics),
        ("Staffing adjustment", test_staffing_adjustment),
        ("Bayes intent learning", test_bayes_intent),
        ("Full integration", test_full_integration),
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
    print(f"QUEUE/STAFFING/BAYES RESULTS: {passed}/{total} tests passed")
    print("="*70 + "\n")
    
    return all(results)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
