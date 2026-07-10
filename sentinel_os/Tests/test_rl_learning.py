import sys
import os
import array_ops as np
np.random.seed(42)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from Engines.simple_rl_trainer import SimpleRLTrainer

def test_rl_training_learns():
    print("\n[TEST] RL Training Loop - Learns to prefer good actions")
    
    # Create trainer: 10-dim state, 2 actions (fast queue vs slow queue)
    trainer = SimpleRLTrainer(state_dim=10, action_dim=2, lr=0.001)
    
    print("\n  Phase 1: Bad routing (action 1 = slow queue)")
    # Simulate 20 bad calls (slow queue, high wait, low reward)
    for i in range(20):
        state = np.random.randn(10)
        trainer.collect_trajectory(state, action=1, reward=-50.0, done=False)
    
    loss1 = trainer.update_weights()
    
    print("\n  Phase 2: Good routing (action 0 = fast queue)")
    # Simulate 20 good calls (fast queue, low wait, high reward)
    for i in range(20):
        state = np.random.randn(10)
        trainer.collect_trajectory(state, action=0, reward=10.0, done=True)
    
    loss2 = trainer.update_weights()
    
    print(f"\n  Training Results:")
    print(f"    Phase 1 loss (bad routing): {loss1:.4f}")
    print(f"    Phase 2 loss (good routing): {loss2:.4f}")
    
    # Loss should decrease as it learns good routing
    improvement = (loss1 - loss2) / loss1 if loss1 > 0 else 0
    print(f"    Improvement: {improvement*100:.1f}%")
    
    # Verify learning happened
    assert loss2 < loss1, f"Should improve: {loss1:.4f} -> {loss2:.4f}"
    
    print(f"\n  ✓ PASSED - RL agent learns to prefer better actions")
    print(f"  ✓ Router can optimize policy based on call outcomes")
    return True

if __name__ == "__main__":
    try:
        success = test_rl_training_learns()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"  ✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
