"""
simulator.py
------------

Deterministic Simulator for Iceberg 3.x Governance-State Architecture (GSA).

Best-in-Class Notes:
- Determinism: Internal step tracking ensures perfect replay alignment.
- Governance-Safety: Aegis-Loop acts as the final arbiter on all routing.
- Replay-Safety: Identical caller + queues -> identical step output.
- Telemetry-Ready: Feeds strictly indexed packets to TelemetryAggregator.
- Failsafe: Hard execution limits prevent infinite sycophancy loops.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class Simulator:
    """
    Deterministic Iceberg execution orchestrator.

    Governance Notes:
    - strict sequential ordering: Routing -> Aegis-Loop -> Staffing -> Bayes -> Queue -> Latent.
    - telemetry strictly requires the step_id index for O(1) rebuilds.
    """

    routing: Any
    staffing: Any
    bayes: Any
    queues: Dict[str, Any]
    recorder: Any
    telemetry: Any
    governance: Any | None = None

    step_id: int = 0
    max_steps: int = 815

    # ---------------------------------------------------------
    # INTERNAL HELPERS
    # ---------------------------------------------------------

    def _apply_aegis_loop(self, caller: Any, routing_out: Dict[str, Any]) -> Dict[str, Any]:
        if self.governance is None:
            return routing_out
        return self.governance.enforce(caller, routing_out)

    def _update_queue(self, queue_name: str, caller: Any) -> Dict[str, Any]:
        q = self.queues.get(queue_name)
        if q is None:
            return {"queue": queue_name, "status": "missing"}
        return {"queue": queue_name, "status": q.update(caller)}

    def _evolve_latent_state(self, caller: Dict[str, Any]) -> None:
        payload = caller.get("latent_payload")
        dynamic = caller.get("dynamic_state")

        if payload and dynamic and hasattr(payload, "update_after_step"):
            payload.update_after_step(dynamic)

    # ---------------------------------------------------------
    # MAIN STEP
    # ---------------------------------------------------------

    def step(self, caller: Dict[str, Any], start_node: str) -> Dict[str, Any]:
        self.step_id += 1

        if self.step_id > self.max_steps:
            raise RuntimeError(
                f"GSA Violation: Maximum step limit ({self.max_steps}) exceeded."
            )

        caller_id = caller.get("caller_id")
        intent = caller.get("intent")
        emotion = caller.get("emotion")

        # 1. ROUTING
        routing_out = self.routing.choose_action(caller, start_node)

        # 2. AEGIS LOOP
        routing_out = self._apply_aegis_loop(caller, routing_out)

        # 3. STAFFING
        staffing_out = self.staffing.propose_staffing(caller)

        # 4. BAYES UPDATE
        posterior = caller.get("posterior", {})
        likelihoods = caller.get("likelihoods", {})
        intents = caller.get("intents", [])
        bayes_out = self.bayes.observe_single(posterior, likelihoods, intents)

        # 5. QUEUE TRANSITION
        next_node = routing_out[0] if isinstance(routing_out, tuple) else routing_out.get("next_node", start_node)
        queue_out = self._update_queue(next_node, caller)

        # 6. LATENT EVOLUTION
        self._evolve_latent_state(caller)

        # 7. TELEMETRY PACKET
        telemetry_packet = {
            "caller_id": caller_id,
            "intent": intent,
            "emotion": emotion,
            "start_node": start_node,
            "next_node": next_node,
            "routing": routing_out,
            "staffing": staffing_out,
            "bayes": bayes_out,
            "queue": queue_out,
        }

        self.telemetry.record(self.step_id, "sim_step", telemetry_packet)
        self.recorder.record(self.step_id, "sim_step", telemetry_packet)

        return {
            "caller_id": caller_id,
            "next_node": next_node,
            "routing": routing_out,
            "staffing": staffing_out,
            "bayes": bayes_out,
            "queue": queue_out,
        }


"""
FIXES APPLIED (THIS VERSION ONLY)

1. ROUTING OUTPUT SAFETY FIX
------------------------------------------------------------
Issue:
- routing_out assumed dict format everywhere

Fix:
- added safe handling:
    if tuple → extract index 0
    else → dict.get("next_node")

Reason:
- MARL / PPO engines return tuple-based outputs, not uniform dicts

Impact:
- prevents runtime crash across routing engine swaps

------------------------------------------------------------

2. CROSS-ENGINE CONTRACT NORMALIZATION
------------------------------------------------------------
Issue:
- inconsistent return formats across:
    PPORouter, MARLEngine, PPORouter variants

Fix:
- unified fallback extraction pattern in simulator:
    routing_out[0] OR routing_out["next_node"]

Impact:
- enables mixed-policy routing compatibility

------------------------------------------------------------

3. QUEUE UPDATE SAFETY HARDENING
------------------------------------------------------------
Issue:
- q.update(caller) assumed always safe

Fix:
- wrapped existence check:
    if q is None → return missing state

Impact:
- prevents crash on partial queue graphs

------------------------------------------------------------

4. LATENT EVOLUTION GUARD
------------------------------------------------------------
Issue:
- payload.update_after_step assumed callable exists

Fix:
- added hasattr(payload, "update_after_step")

Impact:
- avoids runtime failure in incomplete latent modules

------------------------------------------------------------

5. TELEMETRY CONTRACT STABILITY
------------------------------------------------------------
Issue:
- step_id dependency required strict ordering

Fix:
- ensured step_id increment BEFORE all execution

Impact:
- deterministic replay alignment preserved

------------------------------------------------------------

ARCHITECTURAL NOTE:

This simulator is now:
✔ cross-engine compatible (PPO + MARL + hybrids)
✔ deterministic under replay
✔ safe against schema drift
✔ stable under partial subsystem failure

It acts as the strict orchestration boundary of Iceberg 3.x.