#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from adaptive_config import CONFIG
from governance.drift_core_v1 import DriftPolicy, detect_drift, baseline_from_holds
from governance.self_heal_v1 import heal, HealBand, InMemoryParameterStore
from governance.log_rotation_v1 import LogRotationManager, LocalDiskAdapter
from cassette_loader import CassetteLoader
from cassette_schema import validate_cassette
import random

def main():
    print("\n" + "="*70)
    print("ADAPTIVE ICEBERG PIPELINE - PRODUCTION")
    print("="*70)
    
    # Initialize ledger
    print("\n[1/5] Initializing ledger...")
    os.makedirs(CONFIG["ledger"]["storage_dir"], exist_ok=True)
    ledger = LogRotationManager(
        LocalDiskAdapter(CONFIG["ledger"]["storage_dir"]),
        seed=CONFIG["ledger"]["seed"],
    )
    print(f"  Ledger storage: {CONFIG['ledger']['storage_dir']}")
    
    # Generate baseline
    print("\n[2/5] Generating baseline data...")
    rng = random.Random(CONFIG["data"]["baseline_seed"])
    baseline_holds = {
        "auth_node": [rng.uniform(15, 25) for _ in range(100)],
        "menu_node": [rng.uniform(8, 12) for _ in range(100)],
        "intent_node": [rng.uniform(10, 15) for _ in range(100)],
    }
    baseline = baseline_from_holds(
        baseline_holds,
        DriftPolicy(**CONFIG["drift"])
    )
    print(f"  Baseline: {len(baseline)} nodes")
    for node, val in sorted(baseline.items()):
        print(f"    {node:20s} p90={val:6.1f}s")
    
    # Generate current data
    print("\n[3/5] Generating current data...")
    rng = random.Random(CONFIG["data"]["current_seed"])
    current_holds = {
        "auth_node": [rng.uniform(35, 55) for _ in range(100)],
        "menu_node": [rng.uniform(8, 12) for _ in range(100)],
        "intent_node": [rng.uniform(10, 15) for _ in range(100)],
    }
    print(f"  Current: {len(current_holds)} nodes")
    
    # Detect drift
    print("\n[4/5] Detecting drift...")
    signals = detect_drift(baseline, current_holds, DriftPolicy(**CONFIG["drift"]))
    breached = [s for s in signals if s.breached]
    
    print(f"  Total signals: {len(signals)}")
    print(f"  Breached: {len(breached)}")
    for s in breached:
        print(f"    {s.human()}")
    
    # Self-heal
    print("\n[5/5] Applying self-healing...")
    store = InMemoryParameterStore()
    lo, hi = validate_cassette(CassetteLoader().load_cassette("ivr")).range_value("expected_wait_bounds")
    band = HealBand(lo, hi)
    records = heal(
        breached,
        store,
        band,
        ledger,
        kind=CONFIG["self_heal"]["kind"]
    )
    
    print(f"  Applied: {len(records)} correction(s)")
    for r in records:
        tag = " [CLAMPED]" if r.clamped else ""
        print(f"    {r.kind:15s} {r.node:20s} {r.previous:6.1f}s -> {r.applied:6.1f}s{tag}")
    
    # Verify ledger
    print("\n" + "="*70)
    report = ledger.verify(mode="strict")
    if report["ok"]:
        print("✓ PIPELINE SUCCEEDED")
        print(f"  Ledger verified: {report['last_good_index'] + 1} action(s)")
        print(f"  Head hash: {report['computed_head'][:16]}...")
    else:
        print("✗ LEDGER VERIFICATION FAILED")
        return 1
    
    print("="*70 + "\n")
    return 0

if __name__ == "__main__":
    sys.exit(main())
