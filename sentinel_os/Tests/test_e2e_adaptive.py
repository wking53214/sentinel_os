import sys
import os
import random
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from cassette_loader import CassetteLoader
from cassette_schema import validate_cassette

from governance.drift_core_v1 import DriftPolicy, detect_drift, baseline_from_holds
from governance.self_heal_v1 import heal, HealBand, InMemoryParameterStore
from governance.log_rotation_v1 import LogRotationManager, LocalDiskAdapter

def test_end_to_end_adaptive_pipeline():
    tmp_path = tempfile.mkdtemp()
    ledger = LogRotationManager(LocalDiskAdapter(tmp_path), seed="815")
    
    # 1. Create baseline holds (normal week)
    rng = random.Random(815)
    baseline_holds = {
        "auth_node": [rng.uniform(15, 25) for _ in range(100)],
        "menu_node": [rng.uniform(8, 12) for _ in range(100)],
    }
    baseline = baseline_from_holds(baseline_holds, DriftPolicy())
    print(f"Baseline: {baseline}")
    
    # 2. Simulate drift event (auth gets slower this week)
    current_holds = {
        "auth_node": [rng.uniform(40, 60) for _ in range(100)],  # 2-3x slower
        "menu_node": [rng.uniform(8, 12) for _ in range(100)],   # unchanged
    }
    
    # 3. Detect drift
    policy = DriftPolicy(metric_q=90.0, rel_threshold=0.40, min_samples=20)
    signals = detect_drift(baseline, current_holds, policy)
    breached = [s for s in signals if s.breached]
    
    print(f"\nDrift detected in {len(breached)} node(s):")
    for s in breached:
        print(f"  {s.human()}")
    
    # 4. Self-heal
    store = InMemoryParameterStore()
    lo, hi = validate_cassette(CassetteLoader().load_cassette("ivr")).range_value("expected_wait_bounds")
    band = HealBand(lo=lo, hi=hi)
    records = heal(breached, store, band, ledger, kind="expected_wait")
    
    print(f"\nHealed {len(records)} parameter(s):")
    for r in records:
        print(f"  {r.kind} {r.node}: {r.previous:.1f}s -> {r.applied:.1f}s")
    
    # 5. Verify ledger
    report = ledger.verify(mode="strict")
    print(f"\nLedger: {report['last_good_index'] + 1} action(s) sealed, "
          f"head={report['computed_head'][:12]}...")
    
    assert report["ok"], "ledger must verify"
    assert len(breached) > 0, "drift must be detected"
    assert len(records) > 0, "self-heal must apply"
    print("\n✓ End-to-end test PASSED")

test_end_to_end_adaptive_pipeline()
