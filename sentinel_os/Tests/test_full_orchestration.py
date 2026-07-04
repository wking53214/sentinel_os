import sys
import os
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from iceberg_orchestrator import IcebergOrchestrator
from governance.log_rotation_v1 import LogRotationManager, LocalDiskAdapter
import random

class MockSimulator:
    def __init__(self):
        self.step_count = 0
        self.expected_wait = {}
    
    def step(self, caller, start_node):
        self.step_count += 1
        node = caller.get("intent_node", "auth_node")
        return {"caller_id": caller["caller_id"], "next_node": node}

def test_full_iceberg_orchestration():
    print("\n" + "="*70)
    print("FULL ICEBERG END-TO-END ORCHESTRATION")
    print("="*70)
    
    # Setup
    tmp = tempfile.mkdtemp()
    ledger = LogRotationManager(LocalDiskAdapter(tmp), seed="815")
    sim = MockSimulator()
    
    baseline_holds = {
        "auth_node": [20.0] * 50,
        "menu_node": [12.0] * 50,
    }
    
    orchestrator = IcebergOrchestrator(sim, ledger, baseline_holds)
    
    # Batch 1: Normal
    print("\n>>> BATCH 1 (Normal conditions)")
    callers_1 = [
        {"caller_id": f"C{i:04d}", "intent_node": "auth_node", "simulated_wait": 20.0}
        for i in range(50)
    ]
    orchestrator.run_batch(callers_1)
    
    # Batch 2: Auth slows (drift)
    print("\n>>> BATCH 2 (Auth backend slows 2.5x)")
    callers_2 = [
        {"caller_id": f"C{i+100:04d}", "intent_node": "auth_node", "simulated_wait": 50.0}
        for i in range(50)
    ]
    orchestrator.run_batch(callers_2)
    
    # Batch 3: Recovery
    print("\n>>> BATCH 3 (Auth recovers to 25s)")
    callers_3 = [
        {"caller_id": f"C{i+200:04d}", "intent_node": "auth_node", "simulated_wait": 25.0}
        for i in range(50)
    ]
    orchestrator.run_batch(callers_3)
    
    # Verify full ledger
    report = ledger.verify(mode="strict")
    print("\n" + "="*70)
    print(f"FULL ORCHESTRATION SUMMARY")
    print(f"  Batches run: 3")
    print(f"  Total steps: {sim.step_count}")
    print(f"  Ledger actions: {report['last_good_index'] + 1}")
    print(f"  Ledger status: {'✓ VERIFIED' if report['ok'] else '✗ FAILED'}")
    print("="*70 + "\n")
    
    assert report["ok"], "ledger must verify"
    print("✓ FULL ICEBERG ORCHESTRATION PASSED")

test_full_iceberg_orchestration()
