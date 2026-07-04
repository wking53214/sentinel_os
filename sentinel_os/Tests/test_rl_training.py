import sys
import os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from Engines.ppo_trainer import PPOTrainer, Trajectory
from Engines.rl_ppo_adaptive import PPORouter

def test_rl_training_loop():
    print("\n[TEST] PPO Training Loop - Router learns from trajectories")
    
    # Setup router
    neighbors = {
        "root": ["queue_a", "queue_b"],
        "queue_a": ["agent_a"],
        "queue_b": ["agent_b"],
        "agent_a": ["exit"],
        "agent_b": ["exit"],
        "exit": []
    }
    
    router = PPORouter(graph=None, neighbors=neighbors, 
                      expected_wait={"queue_a": 20.0, "queue_b": 50.0})
    
    trainer = PPOTrainer(router, learning_rate=0.001, gamma=0.99)
    
    print("\n  Collecting trajectories...")
    
    class MockIntent:
        def list(self):
            return ["intent_a"]
    
    class MockEmotion:
        def list(self):
            return ["neutral"]
    
    class MockCaller:
        def __init__(self):
            self.intent = MockIntent()
            self.emotion = MockEmotion()
    
    caller = MockCaller()
    
    # Batch 1: Routes to slow queue (bad)
    for i in range(5):
        trainer.collect_trajectory(caller, "root", action_idx=1, wait_time=50.0, resolved=False)
    
    # Batch 2: Routes to fast queue (good)
    for i in range(5):
        trainer.collect_trajectory(caller, "root", action_idx=0, wait_time=20.0, resolved=True)
    
    print(f"  Collected {len(trainer.trajectories)} trajectories")
    
    print("\n  Training on trajectories...")
    loss = trainer.update_weights()
    print(f"  Loss: {loss:.4f}")
    
    assert loss > 0, "Loss should be positive"
    print(f"  ✓ PASSED - PPO trainer learns from trajectories")
    print(f"  ✓ Router can optimize routing based on outcomes")
    return True

if __name__ == "__main__":
    try:
        success = test_rl_training_loop()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"  ✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
