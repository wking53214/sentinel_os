import sys
import os
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from iceberg_final_orchestrator import IcebergFinalOrchestrator
from governance.log_rotation_v1 import LogRotationManager, LocalDiskAdapter

class MockPPORouter:
    def __init__(self):
        self.expected_wait = {}

class MockSimulator:
    def __init__(self):
        self.step_count = 0
    
    def step(self, caller, start_node):
        self.step_count += 1
        node = caller.get("intent_node", "auth_node")
        return {"caller_id": caller["caller_id"], "next_node": node}

def test_complete_iceberg_integration():
    print("\n" + "="*70)
    print("COMPLETE ICEBERG INTEGRATION TEST")
    print("="*70)
    
    # Setup
    tmp = tempfile.mkdtemp()
    ledger = LogRotationManager(LocalDiskAdapter(tmp), seed="815")
    sim = MockSimulator()
    router = MockPPORouter()
    
    baseline_holds = {
        "auth_node": [20.0] * 50,
        "menu_node": [12.0] * 50,
    }
    
    orchestrator = IcebergFinalOrchestrator(sim, router, ledger, baseline_holds)
    
    print("\n>>> BATCH 1: Baseline (auth at 20s)")
    callers_1 = [
        {"caller_id": f"C{i:04d}", "intent_node": "auth_node", "simulated_wait": 20.0}
        for i in range(50)
    ]
    orchestrator.run_batch(callers_1)
    print(f"  Router expected_wait after batch 1: {orchestrator.ppo_router.expected_wait}")
    
    print("\n>>> BATCH 2: Drift detected (auth at 50s)")
    callers_2 = [
        {"caller_id": f"C{i+100:04d}", "intent_node": "auth_node", "simulated_wait": 50.0}
        for i in range(50)
    ]
    orchestrator.run_batch(callers_2)
    print(f"  Router expected_wait after batch 2: {orchestrator.ppo_router.expected_wait}")
    
    print("\n>>> BATCH 3: Recovery (auth at 22s)")
    callers_3 = [
        {"caller_id": f"C{i+200:04d}", "intent_node": "auth_node", "simulated_wait": 22.0}
        for i in range(50)
    ]
    orchestrator.run_batch(callers_3)
    print(f"  Router expected_wait after batch 3: {orchestrator.ppo_router.expected_wait}")
    
    # Verify
    report = ledger.verify(mode="strict")
    print("\n" + "="*70)
    print("INTEGRATION RESULTS")
    print(f"  Total steps: {sim.step_count}")
    print(f"  Ledger actions: {report['last_good_index'] + 1}")
    print(f"  PPORouter expected_wait updated: {'auth_node' in orchestrator.ppo_router.expected_wait}")
    print(f"  Ledger verified: {'✓ YES' if report['ok'] else '✗ NO'}")
    print("="*70 + "\n")
    
    assert report["ok"], "ledger must verify"
    assert 'auth_node' in orchestrator.ppo_router.expected_wait, "router must have expected_wait"
    print("✓ COMPLETE ICEBERG INTEGRATION VERIFIED")

test_complete_iceberg_integration()
