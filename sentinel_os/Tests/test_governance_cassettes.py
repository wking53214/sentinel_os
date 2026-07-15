import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from governance.log_rotation_v1 import LogRotationManager, LocalDiskAdapter
from governance.drift_core_v1 import DriftPolicy, detect_drift

def test_log_rotation_basic(tmp_path):
    ledger = LogRotationManager(LocalDiskAdapter(str(tmp_path)), seed="815")
    head1 = ledger.flush([{"event": "test"}])
    assert head1, "flush should return a hash"
    report = ledger.verify(mode="strict")
    assert report["ok"], "clean ledger should verify"

def test_drift_detection():
    baseline = {"node_a": 10.0, "node_b": 15.0}
    current = {"node_a": [8.0, 9.0, 11.0] * 10, "node_b": [35.0] * 30}
    policy = DriftPolicy(metric_q=90.0, rel_threshold=0.40, min_samples=20)
    signals = detect_drift(baseline, current, policy)
    node_b_signal = [s for s in signals if s.node == "node_b"][0]
    assert node_b_signal.breached, "node_b should breach"
