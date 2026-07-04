import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from governance.drift_core_v1 import DriftPolicy, detect_drift, baseline_from_holds
from governance.self_heal_v1 import heal, HealBand, InMemoryParameterStore
from governance.log_rotation_v1 import LogRotationManager, LocalDiskAdapter

class IcebergFinalOrchestrator:
    """Complete integration: real Simulator + drift detection + adaptive PPORouter"""
    
    def __init__(self, simulator, ppo_router, ledger, baseline_holds):
        self.simulator = simulator
        self.ppo_router = ppo_router
        self.ledger = ledger
        self.baseline = baseline_from_holds(baseline_holds, DriftPolicy())
        self.store = InMemoryParameterStore()
        self.band = HealBand(4.0, 120.0)
        self.policy = DriftPolicy()
        self.batch_num = 0
        
    def run_batch(self, callers, start_node="root"):
        """Run batch through real Simulator, detect drift, heal, update PPORouter"""
        self.batch_num += 1
        print(f"\n[BATCH {self.batch_num}] Running {len(callers)} callers through real Simulator...")
        
        # 1. Run through Simulator
        results = []
        holds_by_node = {}
        for caller in callers:
            result = self.simulator.step(caller, start_node)
            results.append(result)
            
            # Extract timing from telemetry
            node = result.get("next_node", start_node)
            wait = caller.get("simulated_wait", 0)
            if wait > 0:
                holds_by_node.setdefault(node, []).append(wait)
        
        print(f"  Completed {len(results)} steps")
        
        # 2. Detect drift
        if holds_by_node:
            signals = detect_drift(self.baseline, holds_by_node, self.policy)
            breached = [s for s in signals if s.breached]
            
            if breached:
                # 3. Self-heal
                recs = heal(breached, self.store, self.band, self.ledger, kind="expected_wait")
                print(f"  Drift detected: {len(breached)} node(s)")
                
                # 4. UPDATE PPORouter with healed values
                for r in recs:
                    self.ppo_router.expected_wait[r.node] = r.applied
                    print(f"    Updated router: {r.node} expected_wait = {r.applied:.1f}s")
            else:
                print(f"  No drift detected")
        
        # 5. Verify ledger
        report = self.ledger.verify(mode="strict")
        assert report["ok"], "ledger verify failed"
        
        return results

if __name__ == "__main__":
    print("IcebergFinalOrchestrator ready for integration")
