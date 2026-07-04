#!/usr/bin/env python3
import sys
import os
import time
sys.path.insert(0, os.path.dirname(__file__))

from load_test_config import LOAD_TEST_CONFIG
from governance.drift_core_v1 import DriftPolicy, detect_drift, baseline_from_holds
from governance.self_heal_v1 import heal, HealBand, InMemoryParameterStore
from governance.log_rotation_v1 import LogRotationManager, LocalDiskAdapter
import random
import tempfile

def run_load_test(scale_name: str):
    config = LOAD_TEST_CONFIG[scale_name]
    baseline_n = config["baseline_n_calls"]
    current_n = config["current_n_calls"]
    
    print(f"\n{'='*70}")
    print(f"LOAD TEST: {config['description']}")
    print(f"{'='*70}")
    
    # Initialize ledger
    tmp = tempfile.mkdtemp()
    ledger = LogRotationManager(LocalDiskAdapter(tmp), seed="815")
    
    # Stage 1: Generate baseline
    print(f"\n[1/4] Generating baseline ({baseline_n} calls)...", end="", flush=True)
    t0 = time.time()
    rng = random.Random(815)
    baseline_holds = {}
    for i in range(baseline_n):
        node = ["auth_node", "menu_node", "intent_node"][i % 3]
        hold = rng.uniform(10, 30)
        baseline_holds.setdefault(node, []).append(hold)
    t1 = time.time()
    baseline = baseline_from_holds(baseline_holds, DriftPolicy())
    t_baseline = t1 - t0
    print(f" {t_baseline:.2f}s")
    
    # Stage 2: Generate current data
    print(f"[2/4] Generating current data ({current_n} calls)...", end="", flush=True)
    t0 = time.time()
    rng = random.Random(816)
    current_holds = {}
    for i in range(current_n):
        node = ["auth_node", "menu_node", "intent_node"][i % 3]
        mult = 2.0 if node == "auth_node" else 1.0  # auth drifts 2x
        hold = rng.uniform(10 * mult, 30 * mult)
        current_holds.setdefault(node, []).append(hold)
    t1 = time.time()
    t_current = t1 - t0
    print(f" {t_current:.2f}s")
    
    # Stage 3: Detect drift
    print(f"[3/4] Detecting drift...", end="", flush=True)
    t0 = time.time()
    signals = detect_drift(baseline, current_holds, DriftPolicy())
    breached = [s for s in signals if s.breached]
    t1 = time.time()
    t_detect = t1 - t0
    print(f" {t_detect:.4f}s ({len(breached)} breached)")
    
    # Stage 4: Self-heal
    print(f"[4/4] Applying self-healing...", end="", flush=True)
    t0 = time.time()
    store = InMemoryParameterStore()
    band = HealBand(4.0, 120.0)
    records = heal(breached, store, band, ledger, kind="expected_wait")
    t1 = time.time()
    t_heal = t1 - t0
    print(f" {t_heal:.4f}s ({len(records)} healed)")
    
    # Verify ledger
    t0 = time.time()
    report = ledger.verify(mode="strict")
    t1 = time.time()
    t_verify = t1 - t0
    
    # Results
    t_total = t_baseline + t_current + t_detect + t_heal + t_verify
    print(f"\n{'RESULTS':-^70}")
    print(f"  Baseline generation:  {t_baseline:8.3f}s")
    print(f"  Current generation:   {t_current:8.3f}s")
    print(f"  Drift detection:      {t_detect:8.4f}s")
    print(f"  Self-healing:         {t_heal:8.4f}s")
    print(f"  Ledger verify:        {t_verify:8.4f}s")
    print(f"  {'TOTAL':27s}: {t_total:8.3f}s")
    print(f"\n  Throughput: {(baseline_n + current_n) / t_total:.0f} calls/sec")
    print(f"  Ledger status: {'✓ OK' if report['ok'] else '✗ FAILED'}")
    print(f"{'='*70}\n")
    
    return {
        "scale": scale_name,
        "total_calls": baseline_n + current_n,
        "t_baseline": t_baseline,
        "t_current": t_current,
        "t_detect": t_detect,
        "t_heal": t_heal,
        "t_verify": t_verify,
        "t_total": t_total,
        "throughput": (baseline_n + current_n) / t_total,
        "ledger_ok": report["ok"],
    }

if __name__ == "__main__":
    print("\n" + "="*70)
    print("ADAPTIVE PIPELINE - LOAD TEST SUITE")
    print("="*70)
    
    results = []
    for scale in ["small", "medium", "large"]:  # skip xlarge for now (takes longer)
        results.append(run_load_test(scale))
    
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    print(f"{'Scale':<15} {'Calls':<10} {'Total(s)':<12} {'Throughput':<15} {'Status':<10}")
    print("-"*70)
    for r in results:
        status = "✓ PASS" if r["ledger_ok"] else "✗ FAIL"
        print(f"{r['scale']:<15} {r['total_calls']:<10} {r['t_total']:<12.3f} "
              f"{r['throughput']:<15.0f} {status:<10}")
    print("="*70 + "\n")
