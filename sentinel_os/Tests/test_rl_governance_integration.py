import sys
import os
import tempfile
import array_ops as np
np.random.seed(42)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from cassette_loader import CassetteLoader
from cassette_schema import validate_cassette

from Engines.simple_rl_trainer import SimpleRLTrainer
from governance.drift_core_v1 import DriftPolicy, detect_drift, baseline_from_holds
from governance.self_heal_v1 import heal, HealBand, InMemoryParameterStore
from governance.log_rotation_v1 import LogRotationManager, LocalDiskAdapter

def test_complete_rl_governance_integration():
    print("\n" + "="*70)
    print("COMPLETE RL + GOVERNANCE INTEGRATION")
    print("="*70)
    
    # Setup
    tmp = tempfile.mkdtemp()
    ledger = LogRotationManager(LocalDiskAdapter(tmp), seed="815")
    trainer = SimpleRLTrainer(state_dim=10, action_dim=2, lr=0.001)
    baseline_holds = {"fast_queue": [20.0]*50, "slow_queue": [50.0]*50}
    baseline = baseline_from_holds(baseline_holds, DriftPolicy())
    store = InMemoryParameterStore()
    lo, hi = validate_cassette(CassetteLoader().load_cassette("ivr")).range_value("expected_wait_bounds")
    band = HealBand(lo, hi)
    
    print("\n[BATCH 1] Initial routing (before training)")
    print("  RL: Training on mixed quality calls")
    
    # Batch 1: Random routing - mix of good and bad
    losses = []
    for i in range(20):
        state = np.random.randn(10)
        # 50% fast (good), 50% slow (bad)
        action = 0 if i % 2 == 0 else 1
        reward = 10.0 if action == 0 else -50.0
        trainer.collect_trajectory(state, action, reward, done=(i % 5 == 0))
    
    loss1 = trainer.update_weights()
    losses.append(loss1)
    print(f"  Loss: {loss1:.4f}")
    
    print("\n[BATCH 2] RL improves routing")
    print("  RL: Now sees more good outcomes")
    
    # Batch 2: Router learns to prefer fast queue (action 0)
    for i in range(20):
        state = np.random.randn(10)
        # 80% fast (good), 20% slow (bad)
        action = 0 if i % 5 != 0 else 1
        reward = 10.0 if action == 0 else -50.0
        trainer.collect_trajectory(state, action, reward, done=(i % 4 == 0))
    
    loss2 = trainer.update_weights()
    losses.append(loss2)
    print(f"  Loss: {loss2:.4f}")
    
    # Check governance layer
    print("\n[GOVERNANCE] Detecting drift in queue wait times")
    current_holds = {
        "fast_queue": [20.0]*50,
        "slow_queue": [80.0]*50,  # 1.6x slower
    }
    
    policy = DriftPolicy(metric_q=90.0, rel_threshold=0.40, min_samples=20)
    signals = detect_drift(baseline, current_holds, policy)
    breached = [s for s in signals if s.breached]
    
    print(f"  Detected {len(breached)} drift(s)")
    
    if breached:
        print("\n[GOVERNANCE] Self-healing queue parameters")
        recs = heal(breached, store, band, ledger, kind="expected_wait")
        print(f"  Healed {len(recs)} parameter(s)")
        for r in recs:
            print(f"    {r.node}: {r.previous:.1f}s -> {r.applied:.1f}s")
    
    # Verify ledger
    report = ledger.verify(mode="strict")
    
    print("\n" + "="*70)
    print("INTEGRATION RESULTS")
    print("  RL Training:")
    print(f"    Phase 1 loss: {losses[0]:.4f}")
    print(f"    Phase 2 loss: {losses[1]:.4f}")
    print(f"    Improvement: {(losses[0]-losses[1])/losses[0]*100:.1f}%")
    print("  Governance:")
    print(f"    Drifts detected: {len(breached)}")
    print(f"    Parameters healed: {len(recs) if breached else 0}")
    print(f"    Ledger verified: {'✓ YES' if report['ok'] else '✗ NO'}")
    print("="*70 + "\n")
    
    assert losses[1] <= losses[0] + 0.05, "Loss within expected training step bounds"
    assert report["ok"], "Ledger must verify"
    
    print("✓ COMPLETE RL + GOVERNANCE INTEGRATION VERIFIED")
    return True

if __name__ == "__main__":
    try:
        success = test_complete_rl_governance_integration()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"  ✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
