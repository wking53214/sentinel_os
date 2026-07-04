import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from governance.drift_core_v1 import DriftPolicy, detect_drift, baseline_from_holds
from governance.self_heal_v1 import heal, HealBand, InMemoryParameterStore
from governance.log_rotation_v1 import LogRotationManager, LocalDiskAdapter

class IcebergOrchestrator:
    """Full end-to-end: baseline -> runs -> drift -> heal -> adapt"""
    
    def __init__(self, simulator, ledger, baseline_holds):
        self.simulator = simulator
        self.ledger = ledger
        self.baseline = baseline_from_holds(baseline_holds, DriftPolicy())
        self.store = InMemoryParameterStore()
        self.band = HealBand(4.0, 120.0)
        self.policy = DriftPolicy()
        self.batch_num = 0
        
    def run_batch(self, callers, start_node="root"):
        self.batch_num += 1
        print(f"\n[BATCH {self.batch_num}] Running {len(callers)} callers...")
        
        results = []
        holds_by_node = {}
        for caller in callers:
            result = self.simulator.step(caller, start_node)
            results.append(result)
            node = result.get("next_node", start_node)
            wait = caller.get("simulated_wait", 0)
            if wait > 0:
                holds_by_node.setdefault(node, []).append(wait)
        
        print(f"  Completed {len(results)} steps")
        
        if holds_by_node:
            signals = detect_drift(self.baseline, holds_by_node, self.policy)
            breached = [s for s in signals if s.breached]
            
            if breached:
                recs = heal(breached, self.store, self.band, self.ledger, kind="expected_wait")
                print(f"  Drift detected: healed {len(recs)} node(s)")
            else:
                print(f"  No drift detected")
        
        report = self.ledger.verify(mode="strict")
        assert report["ok"], "ledger verify failed"
        return results
