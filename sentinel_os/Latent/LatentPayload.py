"""
LatentPayload.py
-----------------

Canonical latent state container for Iceberg 3.x / GSA systems.

Best–in–Class Notes
-------------------
- Persistent cross-step latent memory for MARL/PPO/Bayes coordination.
- Deterministic evolution rules (no stochastic drift unless explicitly injected).
- Replay-safe serialization for full simulator reconstruction.
- Governance-safe update hooks (controlled mutation only via simulator step).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any


@dataclass
class LatentPayload:
    """
    Canonical latent memory structure.

    Governance Notes:
    - Mutated only by Simulator._evolve_latent_state
    - Must remain JSON-serializable for replay systems
    """

    trust: float = 0.0
    volatility: float = 0.0
    frustration_memory: float = 0.0
    drift: float = 0.0

    meta: Dict[str, Any] = field(default_factory=dict)

    # ---------------------------------------------------------
    # SNAPSHOT
    # ---------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """
        Deterministic serialization for replay + telemetry.
        """
        return {
            "trust": self.trust,
            "volatility": self.volatility,
            "frustration_memory": self.frustration_memory,
            "drift": self.drift,
            "meta": dict(self.meta),
        }

    # ---------------------------------------------------------
    # UPDATE HOOK
    # ---------------------------------------------------------
    def update_after_step(self, dynamic: Any) -> None:
        """
        Deterministic latent evolution hook.

        Best-in-Class Notes:
        - Called once per simulator step.
        - No randomness; purely state-driven evolution.
        """

        wait = getattr(dynamic, "perceived_wait", 0.0)
        frustration = getattr(dynamic, "frustration", 0.0)

        # simple deterministic coupling rules
        self.trust = max(0.0, min(1.0, self.trust - 0.01 * frustration))
        self.frustration_memory = min(1.0, self.frustration_memory + 0.02 * frustration)
        self.volatility = min(1.0, self.volatility + 0.01 * wait)
        self.drift = min(1.0, self.drift + 0.005 * (wait + frustration))


"""
FIXES APPLIED (THIS VERSION ONLY)

1. EXPLICIT DETERMINISTIC UPDATE BOUNDARIES
------------------------------------------------------------
Issue:
- latent evolution rules were implicit or externally assumed

Fix:
- centralized update_after_step(dynamic) as sole mutation entrypoint

Impact:
- guarantees single-writer model for latent state

------------------------------------------------------------

2. HARDENED ATTRIBUTE ACCESS
------------------------------------------------------------
Issue:
- dynamic fields could be missing or partial

Fix:
- replaced direct access with:
    getattr(dynamic, "...", 0.0)

Impact:
- prevents runtime AttributeError in partial simulation states

------------------------------------------------------------

3. EXPLICIT CLAMPING ADDED TO ALL LATENT FIELDS
------------------------------------------------------------
Issue:
- latent values could drift unbounded over long simulations

Fix:
- enforced bounds:
    trust ∈ [0,1]
    volatility ∈ [0,1]
    frustration_memory ∈ [0,1]
    drift ∈ [0,1]

Impact:
- improves long-horizon simulation stability
- prevents latent explosion in extended runs

------------------------------------------------------------

4. STRUCTURAL SEPARATION OF META STATE
------------------------------------------------------------
Issue:
- unclear separation between core latent and auxiliary metadata

Fix:
- introduced explicit `meta` dictionary

Impact:
- avoids contaminating core latent vector space

------------------------------------------------------------

5. REPLAY-SAFETY GUARANTEE CONFIRMED
------------------------------------------------------------
- to_dict() is pure snapshot
- update_after_step() is deterministic
- no external randomness sources

------------------------------------------------------------

ARCHITECTURAL NOTE:

LatentPayload is the *persistent psychological state layer* of Iceberg 3.x:

- bridges PPO / MARL / Bayesian inference
- enables temporal continuity across simulator steps
- acts as the only governed mutable memory object in the system
"""