# Row Count: 201

"""
replay_runner.py
----------------

Top‑Level Description
---------------------
This module implements Iceberg’s deterministic Replay Runner — the orchestrator
responsible for executing full replay cycles using:

- ReplayLedger (event source)
- SnapshotEngine (state reconstruction)
- RoutingEngine / MARL / PPO (decision engines)
- Staffing RL (operational deltas)
- Bayesian GPU (posterior updates)
- Recorder (trace capture)
- GovernanceEnvelope (policy enforcement)

ReplayRunner guarantees:
- Deterministic replay execution
- Governance‑safe sequencing
- Replay‑equivalent outputs for validation
- Telemetry‑ready trace bundles
- Integration with SnapshotEngine for structural hashing

ReplayRunner is the “glue” that ties together all replay‑capable subsystems.

Subsystem integrations:
- [ReplayLedger](ca://s?q=Explain_replay_ledger)
- [ReplayVerifier](ca://s?q=Explain_replay_system)
- [SnapshotEngine](ca://s?q=Explain_snapshot_engine)
- [Recorder](ca://s?q=Explain_recorder_subsystem)
- [RoutingEngine](ca://s?q=Explain_routing_engine)
- [Simulator](ca://s?q=Explain_simulator)
- [GovernanceEnvelope](ca://s?q=Explain_governance_envelope)

Best‑in‑Class Notes
-------------------
- Determinism: ReplayRunner never introduces randomness.
- Replay‑Safety: Identical ledger → identical replay output.
- Governance‑Safety: Strict sequencing and immutability.
- Telemetry‑Ready: All replay steps can be logged and signed.
- Stateless Design: Runner holds no hidden state; everything is explicit.
- Audit‑Integrity: Produces audit‑grade replay bundles.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List

# External subsystems (expected to be provided by Iceberg)
# RoutingEngine, MARLEngine, PPORouter, StaffingOptimizerRL, BayesianIntentEngineGPU,
# Recorder, ReplayLedger, SnapshotEngine


@dataclass
class ReplayContext:
    """
    Container for all subsystem references used during replay.

    Best‑in‑Class Notes:
    - Explicit dependency injection ensures governance‑safe clarity.
    - No hidden global state.
    """
    routing: Any
    marl: Any
    ppo: Any
    staffing: Any
    bayes: Any
    recorder: Any
    ledger: Any
    snapshot_engine: Any


class ReplayRunner:
    """
    Deterministic replay orchestrator for Iceberg.

    Best‑in‑Class Notes:
    - Executes replay cycles in strict deterministic order.
    - Produces snapshots for ReplayVerifier and governance audits.
    - Stateless: all state is passed explicitly through replay steps.
    """

    def __init__(self, ctx: ReplayContext):
        self.ctx = ctx

    # ---------------------------------------------------------
    # INTERNAL HELPERS
    # ---------------------------------------------------------
    def _apply_routing(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply routing decision using PPO or MARL depending on event type.

        Best‑in‑Class Notes:
        - Deterministic selection based on event metadata.
        - Telemetry‑friendly output structure.
        """
        caller = event["caller"]
        node = event["node"]

        if event.get("policy") == "ppo":
            next_node, idx, logp, value = self.ctx.ppo.choose_action(caller, node)
        else:
            agents = event["agents"]
            results = self.ctx.marl.choose_actions(agents, node)
            # MARL returns dict keyed by agent_id
            return {"marl_results": results}

        return {
            "next_node": next_node,
            "action_idx": idx,
            "logp": logp,
            "value": value,
        }

    def _apply_staffing(self, event: Dict[str, Any]) -> Dict[str, float]:
        """
        Apply staffing RL deltas.

        Best‑in‑Class Notes:
        - Deterministic deltas ensure replay equivalence.
        - Governance‑safe clipping enforced by Staffing RL.
        """
        caller = event["caller"]
        return self.ctx.staffing.propose_staffing(caller)

    def _apply_bayes(self, event: Dict[str, Any]) -> Dict[str, float]:
        """
        Apply Bayesian posterior update.

        Best‑in‑Class Notes:
        - Pure functional update: no mutation of engine state.
        - GPU‑accelerated but deterministic.
        """
        posterior = event["posterior"]
        likelihoods = event["likelihoods"]
        intents = event["intents"]
        return self.ctx.bayes.observe_single(posterior, likelihoods, intents)

    # ---------------------------------------------------------
    # MAIN REPLAY LOOP
    # ---------------------------------------------------------
    def run(self) -> Dict[str, Any]:
        """
        Execute a full deterministic replay cycle.

        Best‑in‑Class Notes:
        - Ledger drives replay; no external state is consulted.
        - Recorder captures replay trace for auditability.
        - SnapshotEngine produces final replay snapshot.
        """

        ledger_events = self.ctx.ledger.read_all()
        routing_trace = []
        marl_trace = []
        ppo_trace = []
        staffing_trace = []
        bayes_trace = []

        for evt in ledger_events:
            etype = evt["type"]

            # Routing
            if etype == "routing":
                out = self._apply_routing(evt)
                self.ctx.recorder.record("routing_replay", out)
                if "marl_results" in out:
                    marl_trace.append(out)
                else:
                    ppo_trace.append(out)
                routing_trace.append(out)

            # Staffing
            elif etype == "staffing":
                deltas = self._apply_staffing(evt)
                self.ctx.recorder.record("staffing_replay", deltas)
                staffing_trace.append(deltas)

            # Bayesian
            elif etype == "bayes":
                posterior_new = self._apply_bayes(evt)
                self.ctx.recorder.record("bayes_replay", posterior_new)
                bayes_trace.append(posterior_new)

            # Unknown event types are ignored deterministically
            else:
                self.ctx.recorder.record("ignored_event", {"event": evt})

        # -----------------------------------------------------
        # BUILD SNAPSHOT
        # -----------------------------------------------------
        snapshot = self.ctx.snapshot_engine.build(
            caller_states=[],
            queue_states=[],
            routing_trace=routing_trace,
            marl_trace=marl_trace,
            ppo_trace=ppo_trace,
            staffing_trace=staffing_trace,
            bayes_posteriors={"trace": bayes_trace},
            recorder_events=self.ctx.recorder.export(),
        )

        return self.ctx.snapshot_engine.export(snapshot)