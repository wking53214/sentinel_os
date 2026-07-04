"""
integration_adaptive.py -- wire drift + self-heal into the Simulator.

After a batch of callers completes, extract timing data, detect drift,
apply self-healing, and log everything to the ledger.
"""

from governance.drift_core_v1 import (
    DriftPolicy, detect_drift, baseline_from_holds
)
from governance.self_heal_v1 import (
    heal, HealBand, InMemoryParameterStore, HEALABLE
)
from governance.log_rotation_v1 import LogRotationManager


class AdaptiveSimulator:
    """Wraps a Simulator with drift detection and self-healing."""
    
    def __init__(self, simulator, ledger: LogRotationManager, 
                 baseline: dict, policy: DriftPolicy = None):
        self.sim = simulator
        self.ledger = ledger
        self.baseline = baseline
        self.policy = policy or DriftPolicy()
        self.store = InMemoryParameterStore()
        self.heal_band = HealBand(lo=4.0, hi=120.0)
        
    def run_with_adaptation(self, callers, start_node):
        """Run callers through simulator and adapt on drift."""
        results = []
        holds_by_node = {}
        
        for caller in callers:
            result = self.sim.step(caller, start_node)
            results.append(result)
            
            # Collect timing data
            if "node" in result and "wait" in result:
                node = result["node"]
                wait = result["wait"]
                holds_by_node.setdefault(node, []).append(wait)
        
        # Detect drift
        if holds_by_node:
            signals = detect_drift(self.baseline, holds_by_node, self.policy)
            breached = [s for s in signals if s.breached]
            
            if breached:
                # Apply self-healing
                recs = heal(breached, self.store, self.heal_band, 
                           self.ledger, kind="expected_wait")
                print(f"Drift detected in {len(breached)} nodes; "
                      f"healed {len(recs)}")
        
        return results
