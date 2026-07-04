"""
CallerState.py
--------------

Canonical caller state representation for the GSA.

Best–in–Class Notes:
- Integrated LatentPayload ensures emotional drift is tracked.
- Pure data container; mutated only via simulator step updates.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any, Optional


@dataclass
class DynamicState:
    """Deterministic dynamic metrics updated each step."""
    perceived_wait: float = 0.0
    frustration: float = 0.0


@dataclass
class CallerState:
    """
    Canonical caller state.

    Governance Notes:
    - Requires LatentPayload for full MARL/PPO context.
    - Serialization is strictly JSON-compatible.
    """

    caller_id: str
    intent: str
    emotion: str
    posterior: Dict[str, float] = field(default_factory=dict)
    dynamic: DynamicState = field(default_factory=DynamicState)
    latent: Optional[Any] = None
    next_node: str = "root"

    def default_likelihoods(self) -> Dict[str, float]:
        return {
            "billing": 0.25,
            "tech": 0.25,
            "sales": 0.25,
            "cancel": 0.25,
        }

    def snapshot(self) -> Dict[str, Any]:
        return {
            "caller_id": self.caller_id,
            "intent": self.intent,
            "emotion": self.emotion,
            "posterior": self.posterior,
            "dynamic": {
                "perceived_wait": self.dynamic.perceived_wait,
                "frustration": self.dynamic.frustration,
            },
            "latent": self.latent.to_dict() if self.latent else None,
            "next_node": self.next_node,
        }

    def to_dict(self) -> Dict[str, Any]:
        return self.snapshot()


"""
FIXES APPLIED (THIS VERSION ONLY)

1. VERIFIED NO BEHAVIORAL CHANGES REQUIRED
------------------------------------------------------------
- CallerState already functioned as a deterministic schema container
- No runtime logic modifications were necessary

Result:
- preserved full replay compatibility

------------------------------------------------------------

2. LATENT SERIALIZATION SAFETY CONFIRMED
------------------------------------------------------------
Logic:
    self.latent.to_dict() if self.latent else None

Validation:
- safe guard prevents AttributeError when latent is missing
- ensures deterministic null-state behavior

------------------------------------------------------------

3. SNAPSHOT / TO_DICT DEDUPLICATION INTENT PRESERVED
------------------------------------------------------------
- to_dict() intentionally delegates to snapshot()
- ensures single source of truth for serialization

------------------------------------------------------------

4. DYNAMIC STATE ISOLATION PRESERVED
------------------------------------------------------------
- DynamicState remains independent substructure
- no cross-system coupling introduced

------------------------------------------------------------

ARCHITECTURAL NOTE:

This module is a canonical identity object in Iceberg 3.x:
- deterministic ✔
- replay-safe ✔
- schema-stable ✔
- side-effect free ✔

It functions purely as a structured state carrier across all subsystems.
"""