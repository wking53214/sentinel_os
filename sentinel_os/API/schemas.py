# Row Count: 165

"""
staffing_rl.py
--------------

Deterministic Staffing Reinforcement Learning engine.

Best–in–Class Notes:
- Operational Integrity: Aggregates queue metrics for stable behavior.
- Stateless Design: No internal drift; pure functional decision engine.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any
import numpy as np

@dataclass
class StaffingConfig:
    lr: float = 3e-4
    delta_limit: float = 0.5 
    hidden: int = 16
    seed: int = 815

class StaffingOptimizerRL:
    def __init__(self, graph, queues: Dict[str, Any], latent=None, priors=None, config: StaffingConfig | None = None):
        self.graph = graph
        self.queues = queues
        self.latent = latent or {}
        self.priors = priors or {}
        self.cfg = config or StaffingConfig()
        self._weight_cache: Dict[Tuple[int, int], np.ndarray] = {}

    def _get_weights(self, rows: int, cols: int) -> np.ndarray:
        cache_key = (rows, cols)
        if cache_key not in self._weight_cache:
            rng = np.random.RandomState(self.cfg.seed)
            self._weight_cache[cache_key] = rng.randn(rows, cols) * 0.01
        return self._weight_cache[cache_key]

    def encode_state(self, caller: Any) -> np.ndarray:
        dyn = np.array([caller.dynamic.perceived_wait, caller.dynamic.frustration])
        
        lat = np.array([
            getattr(self.latent, "trust", 0.0),
            getattr(self.latent, "volatility", 0.0),
            getattr(self.latent, "frustration_memory", 0.0),
            getattr(self.latent, "drift", 0.0),
        ])

        if self.queues:
            q_vec = np.array([
                np.mean([q.staffing for q in self.queues.values()]),
                np.mean([q.target_service_level for q in self.queues.values()]),
                np.mean([q.abandonment_rate for q in self.queues.values()])
            ])
        else:
            q_vec = np.zeros(3)

        return np.concatenate([dyn, lat, q_vec])

    def propose_staffing(self, caller: Any) -> Dict[str, float]:
        names = list(self.queues.keys())
        if not names:
            return {}

        state = self.encode_state(caller)
        W = self._get_weights(len(names), state.shape[0])
        
        raw_deltas = W @ state
        clipped = np.clip(raw_deltas, -self.cfg.delta_limit, self.cfg.delta_limit)

        return {name: float(delta) for name, delta in zip(names, clipped)}

    def apply_staffing(self, caller: Any) -> Dict[str, float]:
        deltas = self.propose_staffing(caller)
        for name, delta in deltas.items():
            if name in self.queues:
                self.queues[name].apply_delta(delta)
        return deltas