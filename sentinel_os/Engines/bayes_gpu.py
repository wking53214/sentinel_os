"""
bayes_gpu.py
------------

Deterministic, GPU-accelerated Bayesian intent updater for Iceberg 3.x.

Best-in-Class Notes:
- Deterministic: Enforces torch deterministic algorithms.
- Stability: Uses log-space multiplication to prevent underflow.
- Governance: Normalization ensures valid distributions (sum = 1.0).
- Stateless: Pure functional updates.
"""

from __future__ import annotations
from typing import Dict, List, Any
import torch


class BayesianIntentEngineGPU:
    def __init__(self, device: str = "cuda", deterministic: bool = True):
        self.device = torch.device(device if torch.cuda.is_available() else "cpu")
        if deterministic:
            torch.use_deterministic_algorithms(True)

    def _to_tensor(self, data: Dict[str, float], intents: List[str]) -> torch.Tensor:
        """Converts dict to tensor in deterministic order."""
        return torch.tensor([data[i] for i in intents], dtype=torch.float32, device=self.device)

    def _normalize_log(self, log_probs: torch.Tensor) -> torch.Tensor:
        """Normalize using log-sum-exp to maintain stability."""
        return torch.softmax(log_probs, dim=0)

    def observe_single(self, posterior: Dict[str, float], likelihoods: Dict[str, float], intents: List[str]) -> Dict[str, float]:
        """Performs Bayesian update in log-space."""
        p = self._to_tensor(posterior, intents).log()
        l = self._to_tensor(likelihoods, intents).log()
        
        # Log-space: multiplication becomes addition
        return {i: float(v) for i, v in zip(intents, self._normalize_log(p + l).tolist())}

    def observe_sequence(self, posterior: Dict[str, float], sequence_likelihoods: List[Dict[str, float]], intents: List[str]) -> Dict[str, float]:
        """Performs sequential updates in log-space."""
        p = self._to_tensor(posterior, intents).log()
        
        for lk in sequence_likelihoods:
            l = self._to_tensor(lk, intents).log()
            p = p + l
            
        return {i: float(v) for i, v in zip(intents, self._normalize_log(p).tolist())}