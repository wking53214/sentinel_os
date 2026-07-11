import array_ops as np
from typing import List
from dataclasses import dataclass

@dataclass
class Trajectory:
    state: np.ndarray
    action: int
    reward: float
    done: bool

class SimpleRLTrainer:
    '''Simplified RL trainer that doesn't require complex caller encoding'''
    
    def __init__(self, state_dim=10, action_dim=2, lr=0.001, seed=None):
        self.state_dim = state_dim
        self.action_dim = action_dim
        # One seeded generator initializes the weights and nothing
        # overwrites them afterward. The previous version re-assigned
        # both weight matrices from the UNSEEDED global RNG two lines
        # later, which silently made the seed parameter dead: passing
        # seed=42 twice gave two different policies.
        rng = np.random.default_rng(seed)
        self.policy_weights = rng.standard_normal((self.action_dim, self.state_dim)) * 0.01
        self.value_weights = rng.standard_normal((1, self.state_dim)) * 0.01
        self.lr = lr
        self.trajectories: List[Trajectory] = []
        
    def choose_action(self, state: np.ndarray):
        '''Softmax policy: choose action from state'''
        logits = self.policy_weights @ state
        logits = logits - np.max(logits)
        probs = np.exp(logits) / np.sum(np.exp(logits))
        action = np.argmax(probs)
        value = (self.value_weights @ state).item()
        return action, float(probs[action]), value
    
    def collect_trajectory(self, state: np.ndarray, action: int, reward: float, done: bool):
        '''Record one step'''
        traj = Trajectory(state=state, action=action, reward=reward, done=done)
        self.trajectories.append(traj)
    
    def compute_returns(self):
        '''Compute discounted returns'''
        if not self.trajectories:
            return []
        
        returns = []
        next_return = 0.0
        
        for traj in reversed(self.trajectories):
            next_return = traj.reward + 0.99 * next_return
            returns.insert(0, next_return)
        
        return np.array(returns)
    
    def update_weights(self):
        '''Update policy and value network'''
        if not self.trajectories:
            return 0.0
        
        returns = self.compute_returns()
        returns = (returns - returns.mean()) / (returns.std() + 1e-8)
        
        total_loss = 0.0
        for i, traj in enumerate(self.trajectories):
            # Policy gradient
            logits = self.policy_weights @ traj.state
            softmax = np.exp(logits) / np.sum(np.exp(logits))
            
            policy_loss = -np.log(softmax[traj.action] + 1e-8) * returns[i]
            
            # Value loss
            value = (self.value_weights @ traj.state).item()
            value_loss = (returns[i] - value) ** 2
            
            loss = policy_loss + 0.5 * value_loss
            total_loss += loss
            
            # Update weights (simple gradient descent)
            policy_grad = -returns[i] * traj.state
            self.policy_weights[traj.action] -= self.lr * policy_grad
        
        avg_loss = total_loss / len(self.trajectories)
        self.trajectories = []
        return float(avg_loss)
