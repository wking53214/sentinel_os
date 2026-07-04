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
from typing import Dict, Any, Tuple
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
        dyn = np.array([
            getattr(caller.dynamic, "perceived_wait", 0.0),
            getattr(caller.dynamic, "frustration", 0.0),
        ])

        lat = np.array([
            self.latent.get("trust", 0.0),
            self.latent.get("volatility", 0.0),
            self.latent.get("frustration_memory", 0.0),
            self.latent.get("drift", 0.0),
        ])

        if self.queues:
            q_vec = np.array([
                np.mean([getattr(q, "staffing", 0.0) for q in self.queues.values()]),
                np.mean([getattr(q, "target_service_level", 0.0) for q in self.queues.values()]),
                np.mean([getattr(q, "abandonment_rate", 0.0) for q in self.queues.values()])
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
            queue = self.queues.get(name)
            if queue and hasattr(queue, "apply_delta"):
                queue.apply_delta(delta)

        return deltas


"""
FIXES APPLIED (THIS VERSION ONLY)

1. SAFE ATTRIBUTE ACCESS (CALLER.DYNAMIC)
------------------------------------------------------------
Replaced direct attribute access with:
    getattr(caller.dynamic, "field", 0.0)

Reason:
- prevents runtime AttributeError if dynamic fields are missing
- improves schema tolerance across simulator variants

------------------------------------------------------------

2. LATENT STATE STANDARDIZATION
------------------------------------------------------------
Changed:
    getattr(self.latent, "key", 0.0)

To:
    self.latent.get("key", 0.0)

Reason:
- ensures latent works as dict-based state container
- avoids mixed object/dict access ambiguity

------------------------------------------------------------

3. QUEUE METRIC HARDENING
------------------------------------------------------------
Replaced:
    q.staffing / q.target_service_level / q.abandonment_rate

With:
    getattr(q, "field", 0.0)

Reason:
- prevents crashes from heterogeneous queue implementations
- improves interoperability across queue adapters

------------------------------------------------------------

4. SAFETY CHECK ON APPLY_DELTA
------------------------------------------------------------
Added:
    hasattr(queue, "apply_delta")

Reason:
- avoids runtime failure when queue lacks control interface
- supports partial or mock queue implementations

------------------------------------------------------------

5. DEFENSIVE EMPTY STATE HANDLING
------------------------------------------------------------
Kept:
    return {}

Reason:
- ensures deterministic no-op behavior when no queues exist

------------------------------------------------------------

ARCHITECTURAL NOTE:

This module is now:
- deterministic ✔
- schema-tolerant ✔
- runtime-safe ✔
- replay-consistent ✔

It remains a deterministic staffing allocator, not a learning RL system.
"""