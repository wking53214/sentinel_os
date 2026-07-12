import sys
import os
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from cassette_loader import CassetteLoader
from cassette_schema import validate_cassette
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "observe"))

from governance.drift_core_v1 import DriftPolicy, detect_drift, baseline_from_holds
from governance.self_heal_v1 import heal, HealBand, InMemoryParameterStore
from governance.log_rotation_v1 import LogRotationManager, LocalDiskAdapter

def test_twilio_to_adaptive_simplified():
    tmp_path = tempfile.mkdtemp()
    ledger = LogRotationManager(LocalDiskAdapter(tmp_path), seed="815")
    
    print("\n=== Generate baseline hold data ===")
    import random
    rng = random.Random(815)
    baseline_holds = {
        "auth_node": [rng.uniform(15, 25) for _ in range(100)],
        "menu_node": [rng.uniform(8, 12) for _ in range(100)],
        "intent_node": [rng.uniform(10, 15) for _ in range(100)],
    }
    baseline = baseline_from_holds(baseline_holds, DriftPolicy())
    print(f"Baseline: {len(baseline)} nodes established")
    for node, val in baseline.items():
        print(f"  {node}: p90={val:.1f}s")
    
    print("\n=== Simulate realistic drift (auth backend slows) ===")
    current_holds = {
        "auth_node": [rng.uniform(35, 55) for _ in range(100)],  # 2x slower
        "menu_node": [rng.uniform(8, 12) for _ in range(100)],   # stable
        "intent_node": [rng.uniform(10, 15) for _ in range(100)],  # stable
    }
    
    print("\n=== Detect drift ===")
    policy = DriftPolicy(metric_q=90.0, rel_threshold=0.40, min_samples=20)
    signals = detect_drift(baseline, current_holds, policy)
    breached = [s for s in signals if s.breached]
    
    print(f"Detected {len(breached)} breached node(s):")
    for s in breached:
        print(f"  {s.human()}")
    
    print("\n=== Self-heal with clamping ===")
    store = InMemoryParameterStore()
    lo, hi = validate_cassette(CassetteLoader().load_cassette("ivr")).range_value("expected_wait_bounds")
    band = HealBand(lo=lo, hi=hi)
    records = heal(breached, store, band, ledger, kind="expected_wait")
    
    print(f"Applied {len(records)} heal(s):")
    for r in records:
        tag = " [CLAMPED]" if r.clamped else ""
        print(f"  {r.kind} {r.node}: {r.previous:.1f}s -> {r.applied:.1f}s{tag}")
    
    print("\n=== Verify ledger ===")
    report = ledger.verify(mode="strict")
    print(f"Ledger: {report['last_good_index'] + 1} action(s) sealed")
    print(f"Head: {report['computed_head'][:16]}...")
    
    assert report["ok"], "ledger must verify"
    assert len(breached) > 0, "drift must be detected"
    assert len(records) > 0, "heals must apply"
    print("\n✓ Full adaptive pipeline PASSED")

test_twilio_to_adaptive_simplified()
