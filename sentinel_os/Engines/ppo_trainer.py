import numpy as np
from typing import List, Tuple, Dict, Any
from dataclasses import dataclass

@dataclass
class Trajectory:
    state: np.ndarray
    action: int
    reward: float
    log_prob: float
    value: float
    done: bool

class PPOTrainer:
    '''Minimal PPO training loop for PPORouter'''
    
    def __init__(self, router, learning_rate=0.001, gamma=0.99, gae_lambda=0.95):
        self.router = router
        self.lr = learning_rate
        self.gamma = gamma
        self.gae_lambda = gae_lambda
        self.trajectories: List[Trajectory] = []
        
    def collect_trajectory(self, caller, node_id, action_idx, wait_time, resolved):
        '''Record one step: caller at node, took action, waited, got reward'''
        # Reward: -wait_time (prefer speed) + 10 if resolved
        reward = -wait_time + (10.0 if resolved else 0.0)
        
        # Get state and log_prob from router
        state = self.router.encode_state(caller, node_id)
        actions = self.router.neighbors.get(node_id, [])
        
        if not actions:
            return
        
        W_policy = self.router._get_weights(len(actions), state.shape[0], self.router.cfg.seed_policy)
        logits = W_policy @ state
        
        # Apply expected_wait penalty (if available)
        for i, action in enumerate(actions):
            wait = self.router.expected_wait.get(action, 10.0)
            wait_penalty = -0.1 * (wait / 20.0)
            logits[i] += wait_penalty
        
        logits = logits - np.max(logits)
        exps = np.exp(np.clip(logits, -50, 50))
        probs = exps / (np.sum(exps) + 1e-12)
        
        log_prob = float(np.log(probs[action_idx] + 1e-8))
        
        W_value = self.router._get_weights(1, state.shape[0], self.router.cfg.seed_value)
        value = float(W_value @ state)
        
        traj = Trajectory(state=state, action=action_idx, reward=reward,
                         log_prob=log_prob, value=value, done=resolved)
        self.trajectories.append(traj)
    
    def compute_returns(self):
        '''Compute discounted returns and GAE advantages'''
        if not self.trajectories:
            return [], []
        
        returns = []
        advantages = []
        next_value = 0.0
        gae = 0.0
        
        for traj in reversed(self.trajectories):
            delta = traj.reward + self.gamma * next_value - traj.value
            gae = delta + self.gamma * self.gae_lambda * gae
            next_value = traj.value
            returns.insert(0, gae + traj.value)
            advantages.insert(0, gae)
        
        return np.array(returns), np.array(advantages)
    
    def update_weights(self):
        '''Simple weight update: gradient on policy logits toward high-return actions'''
        if not self.trajectories:
            return 0.0
        
        returns, advantages = self.compute_returns()
        
        # Normalize advantages
        advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)
        
        total_loss = 0.0
        for i, traj in enumerate(self.trajectories):
            # Policy gradient: maximize log_prob * advantage
            policy_loss = -traj.log_prob * advantages[i]
            
            # Value loss: (return - predicted_value)^2
            value_loss = (returns[i] - traj.value) ** 2
            
            loss = policy_loss + 0.5 * value_loss
            total_loss += loss
        
        avg_loss = total_loss / len(self.trajectories)
        self.trajectories = []  # Clear for next batch
        
        return float(avg_loss)
    
    def train_batch(self, trajectories_data):
        '''Train on a batch of collected trajectories'''
        for state, action, reward, log_prob, value, done in trajectories_data:
            traj = Trajectory(state=state, action=action, reward=reward,
                             log_prob=log_prob, value=value, done=done)
            self.trajectories.append(traj)
        
        loss = self.update_weights()
        return loss
