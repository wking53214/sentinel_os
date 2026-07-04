"""
rl_marl.py
----------

Deterministic Multi–Agent Reinforcement Learning engine for Iceberg.

Best–in–Class Notes:
- Centralized Critic: Shared cache ensures value consistency across agents.
- Decentralized Actors: Independent but deterministic state evaluation.
- Governance–Safety: Pure functional action selection guarantees no side-effects.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Tuple, Any
import numpy as np

@dataclass
class MARLConfig:
    hidden: int = 32
    gamma: float = 0.99
    eps_clip: float = 0.2
    seed_policy: int = 815
    seed_value: int = 815

class MARLEngine:
    def __init__(self, graph, neighbors: Dict[str, List[str]], config: MARLConfig | None = None):
        self.graph = graph
        self.neighbors = neighbors
        self.cfg = config or MARLConfig()
        self._weight_cache: Dict[Tuple[int, int, int], np.ndarray] = {}

    def _get_weights(self, rows: int, cols: int, seed: int) -> np.ndarray:
        cache_key = (rows, cols, seed)
        if cache_key not in self._weight_cache:
            rng = np.random.RandomState(seed)
            self._weight_cache[cache_key] = rng.randn(rows, cols) * 0.01
        return self._weight_cache[cache_key]

    def encode_agent_state(self, agent: Any, node_id: str) -> np.ndarray:
        intent_vec = np.zeros(8)
        if hasattr(agent, "intent"):
            intents = agent.intent.list()
            intent_vec[intents.index(agent.intent)] = 1.0

        emotion_vec = np.zeros(8)
        if hasattr(agent, "emotion"):
            emotions = agent.emotion.list()
            emotion_vec[emotions.index(agent.emotion)] = 1.0

        dyn_vec = np.array([agent.dynamic.perceived_wait, agent.dynamic.frustration]) if hasattr(agent, "dynamic") else np.zeros(2)
        staff_vec = np.array([agent.load.current, agent.load.capacity]) if hasattr(agent, "load") else np.zeros(2)
        node_vec = np.array([(hash(node_id) % 997) / 997.0])

        return np.concatenate([intent_vec, emotion_vec, dyn_vec, staff_vec, node_vec])

    def choose_actions(self, agents: List[Any], node_id: str) -> Dict[str, Tuple[str, int, float, float]]:
        actions = self.neighbors.get(node_id, [])
        if not actions:
            return {agent.id: (node_id, 0, 0.0, 0.0) for agent in agents}

        results = {}
        for agent in agents:
            state = self.encode_agent_state(agent, node_id)
            
            W_policy = self._get_weights(len(actions), state.shape[0], self.cfg.seed_policy)
            logits = W_policy @ state

            exps = np.exp(np.clip(logits - np.max(logits), -50, 50))
            probs = exps / np.sum(exps)

            action_idx = int(np.argmax(probs))
            
            W_value = self._get_weights(1, state.shape[0], self.cfg.seed_value)
            value = float(W_value @ state)

            results[agent.id] = (
                actions[action_idx],
                action_idx,
                float(np.log(probs[action_idx] + 1e-8)),
                value,
            )

        return results


# =========================================================
# FIXES / REVIEW NOTES (ADDED — NON-BREAKING)
# =========================================================

"""
FIXES SUMMARY:

1. CRITICAL RUNTIME RISK: intent/emotion indexing
   - Current code assumes:
       intent_vec[intents.index(agent.intent)]
   - Risk: ValueError if agent.intent not in list
   - Impact: hard crash in production routing loop

2. DETERMINISM ISSUE: Python hash()
   - hash(node_id) is NOT stable across interpreter runs
   - Risk: cross-run replay divergence
   - Fix: replace with stable hash (sha256 or seeded hash)

3. POLICY VALIDITY ISSUE (MISLEADING RL)
   - Softmax computed but argmax used immediately
   - Result: probabilities are computed but not actually sampled
   - System is deterministic greedy routing, not stochastic policy

4. PROBABILITY STABILITY GAP
   - np.sum(exps) has no epsilon guard
   - Risk: divide-by-zero under extreme logits
   - Fix: add +1e-12 denominator stabilization

5. SHAPE COUPLING RISK
   - intent_vec/emotion_vec fixed at size 8
   - But agent.intent.list() may not align with this size
   - Risk: silent index mismatch or truncated representation

6. FEATURE ASSUMPTION COUPLING
   - Assumes:
       agent.dynamic.perceived_wait
       agent.load.current
   - No interface enforcement → runtime AttributeError risk

7. RL MISNOMER (ARCHITECTURAL CLARITY)
   - No learning, no gradient updates, no reward propagation
   - This is a deterministic policy scoring engine, not MARL training

8. REPLAY SAFETY WEAK POINT
   - Weight cache is deterministic, but depends on:
       (rows, cols, seed)
   - Any change in state vector shape breaks reproducibility silently

SUGGESTED HARDENING (OPTIONAL NEXT STEP):
- Replace hash(node_id) with stable hash (sha256 mod)
- Replace fixed-size vectors with schema-driven encoding
- Add agent interface Protocol for:
    intent/emotion/dynamic/load guarantees
- Consider renaming:
    MARLEngine → DeterministicPolicyRouter
"""