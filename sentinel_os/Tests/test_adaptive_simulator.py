import sys
import os
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from integration_adaptive import AdaptiveSimulator
from governance.log_rotation_v1 import LogRotationManager, LocalDiskAdapter
from governance.drift_core_v1 import DriftPolicy

def test_adaptive_simulator_basic(tmp_path):
    ledger = LogRotationManager(LocalDiskAdapter(str(tmp_path)), seed="815")
    baseline = {"auth": 20.0, "menu": 15.0}
    
    # Mock simulator
    class MockSim:
        def step(self, caller, node):
            return {"node": "auth", "wait": 22.0}
    
    sim = MockSim()
    policy = DriftPolicy(metric_q=90.0, rel_threshold=0.40, min_samples=1)
    
    adaptive = AdaptiveSimulator(sim, ledger, baseline, policy)
    
    # Create mock caller
    caller = {"caller_id": "test"}
    
    # This should run without error
    results = adaptive.run_with_adaptation([caller], "root")
    assert len(results) == 1
    
    # Ledger should have recorded something
    report = ledger.verify(mode="strict")
    assert report["ok"], "ledger should verify"

if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    test_adaptive_simulator_basic(tmp)
    print("AdaptiveSimulator test passed!")
