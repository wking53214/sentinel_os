"""
S.A.G.E.-K. ("Streaming Adaptive Governance & Ensemble Kernel") simulation
kernel, reconstructed from artifact_2.py (the newer of the two copies; it is
a superset of artifact_1.py and additionally implements execute_governance_logic
so a Fortress instance can be plugged into a GsaUniversalAdapter).

What this actually is, plainly stated: a single scalar KPI is nudged toward a
moving target over 60 simulated steps by one of three fixed-gain linear
controllers ("agents"), selected by a softmax policy over an 8-dimensional
encoding of the KPI. Guardrails (IntegrityLayer, InvariantMonitor,
DriftMonitor, MandateLayer) clamp the action and can freeze learning if the
state misbehaves. Class names like WorldModel, Policy, and the module-level
docstring's references to "Echo State Network reservoirs" and "Lyapunov
Stability Engines" describe aspirations, not what the code implements: there
is no ESN reservoir and no Lyapunov exponent calculation anywhere below,
just tanh-based linear layers and a rolling standard deviation used as a
volatility proxy. Treat the docstrings in the original artifacts as
naming/marketing, not a spec.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import random
import statistics
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List

from .gsa_adapter import GsaContextEnvelope

__all__ = ["Payload", "Fortress", "set_global_seed", "safe_stdev", "audit_append"]


# ---------------------------------------------------------------------------
# Seeding & math utilities
# ---------------------------------------------------------------------------

def set_global_seed(seed: int | None) -> None:
    """Seeds stdlib random (and numpy if installed); records a run id."""
    if seed is None:
        return
    random.seed(seed)
    try:
        import numpy as np
        np.random.seed(seed)
    except ImportError:
        pass
    os.environ["FORTRESS_RUN_ID"] = hashlib.sha256(f"fortress-seed-{seed}".encode()).hexdigest()


def safe_stdev(sequence_input: "deque | List[float]", default_value: float = 0.0) -> float:
    """Population stdev, or default_value if fewer than 2 samples."""
    return statistics.stdev(sequence_input) if len(sequence_input) > 1 else default_value


# ---------------------------------------------------------------------------
# Audit log (HMAC-signed JSON lines, not encrypted, integrity/tamper-evidence
# only)
# ---------------------------------------------------------------------------

_AUDIT_LOG_PATH: str = os.getenv("FORTRESS_AUDIT_LOG", "fortress_audit.log")
_AUDIT_KEY: str = os.getenv("FORTRESS_AUDIT_KEY", "development-key")

if _AUDIT_KEY == "development-key" and os.getenv("FORTRESS_ENV") == "production":
    raise RuntimeError("Security Exception: Production deployments require unique cryptographic keys.")


def _compute_hmac_signature(key_bytes: bytes, message_bytes: bytes) -> str:
    return hmac.new(key_bytes, message_bytes, hashlib.sha256).hexdigest()


def audit_append(event_type: str, data_payload: Dict[str, Any]) -> None:
    """Appends an HMAC-signed JSON line to the audit log. Fails silently on IOError."""
    record_structure = {
        "ts": int(time.time()),
        "run_id": os.getenv("FORTRESS_RUN_ID", "none"),
        "event": event_type,
        "data": data_payload,
    }
    serialized_bytes = json.dumps(record_structure, separators=(",", ":"), sort_keys=True).encode()
    record_structure["hmac"] = _compute_hmac_signature(_AUDIT_KEY.encode(), serialized_bytes)
    try:
        with open(_AUDIT_LOG_PATH, "a") as append_file:
            append_file.write(json.dumps(record_structure) + "\n")
    except IOError:
        pass


# ---------------------------------------------------------------------------
# Core data struct
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Payload:
    """Immutable envelope: a text tag, a scalar KPI, and free-form metadata."""
    body: str
    kpi: float
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Guardrail / monitoring components
# ---------------------------------------------------------------------------

class IntegrityLayer:
    """Tracks rolling error volatility and flags a few hardcoded word-pair contradictions in Payload.body."""

    def __init__(self) -> None:
        self.error_history_window: deque[float] = deque(maxlen=8)

    def analyze(self, input_payload: Payload, current_error_value: float) -> Dict[str, Any]:
        if math.isnan(current_error_value) or math.isinf(current_error_value):
            raise ValueError("Numeric Safety Exception: Input error value is invalid (NaN/Inf).")

        self.error_history_window.append(abs(current_error_value))
        calculated_volatility = safe_stdev(self.error_history_window)

        normalized_text_tokens = set((input_payload.body or "").lower().split())
        calculated_semantic_risk = 0.0
        tracked_contradictions = [("stable", "broken"), ("safe", "failure"), ("healthy", "critical")]
        for active_state, failing_state in tracked_contradictions:
            if active_state in normalized_text_tokens and failing_state in normalized_text_tokens:
                calculated_semantic_risk += 0.3

        aggregated_distortion = min(0.98, (calculated_volatility * 0.05) + calculated_semantic_risk)
        return {"distortion": aggregated_distortion, "compromised": aggregated_distortion > 0.45}


class RegimeEngine:
    """Buckets (volatility, distortion) into STABLE / UNSTABLE / CRITICAL via fixed thresholds."""

    @staticmethod
    def classify(volatility_metric: float, distortion_metric: float) -> str:
        if distortion_metric > 0.65 or volatility_metric > 20.0:
            return "CRITICAL"
        if distortion_metric > 0.35 or volatility_metric > 10.0:
            return "UNSTABLE"
        return "STABLE"


class InvariantMonitor:
    """Hard bounds checks on state/distortion/error/volatility."""

    @staticmethod
    def check(current_state: float, distortion: float, model_error: float, volatility: float) -> List[str]:
        detected_violations: List[str] = []
        if abs(current_state) > 250.0:
            detected_violations.append("STATE_DIVERGENCE")
        if distortion > 0.85:
            detected_violations.append("DISTORTION_OVERFLOW")
        if model_error > 20.0:
            detected_violations.append("WORLD_MODEL_FAILURE")
        if volatility > 40.0:
            detected_violations.append("VOLATILITY_SPIKE")
        return detected_violations


class DriftMonitor:
    """Flags when the mean |weight| of the policy matrix jumps more than 0.12 from its recent rolling mean."""

    def __init__(self) -> None:
        self.magnitude_history_window: deque[float] = deque(maxlen=12)

    def check(self, operational_weights: List[List[float]]) -> tuple[bool, float]:
        flattened_weights = [w for row in operational_weights for w in row]
        if not flattened_weights:
            return False, 0.0
        current_mean_magnitude = sum(abs(w) for w in flattened_weights) / len(flattened_weights)
        self.magnitude_history_window.append(current_mean_magnitude)
        if len(self.magnitude_history_window) < 4:
            return False, 0.0
        calculated_drift = abs(current_mean_magnitude - statistics.mean(self.magnitude_history_window))
        return calculated_drift > 0.12, calculated_drift


class MandateLayer:
    """Clamps a proposed action delta by distance-to-target and volatility, and by absolute KPI bounds."""

    @staticmethod
    def enforce(proposed_action: Dict[str, float], current_kpi: float, target_kpi: float, volatility_index: float) -> Dict[str, float]:
        linear_distance = target_kpi - current_kpi
        speed_velocity_limit = (abs(linear_distance) * 0.35) / (1.0 + (volatility_index * 0.15))
        speed_velocity_limit = max(1.2, min(28.0, speed_velocity_limit))

        extracted_delta = proposed_action.get("delta", 0.0)
        proposed_action["delta"] = max(min(extracted_delta, speed_velocity_limit), -speed_velocity_limit)

        projected_kpi_state = current_kpi + proposed_action["delta"]
        if projected_kpi_state > (target_kpi + 15.0):
            proposed_action["delta"] = (target_kpi + 15.0) - current_kpi
        elif projected_kpi_state < (target_kpi - 75.0):
            proposed_action["delta"] = (target_kpi - 75.0) - current_kpi

        return proposed_action


# ---------------------------------------------------------------------------
# Learned components (plain tanh linear layers, not ESN/RL in the formal sense)
# ---------------------------------------------------------------------------

class WorldModel:
    """8-dim tanh encoding of the KPI, with a hand-rolled gradient-free weight nudge on update()."""

    def __init__(self, state_space_dimension: int = 8) -> None:
        self.dimension: int = state_space_dimension
        self.weight_state_matrix: List[float] = [random.uniform(-0.04, 0.04) for _ in range(state_space_dimension)]
        self.weight_action_matrix: List[float] = [random.uniform(-0.04, 0.04) for _ in range(state_space_dimension)]

    def encode(self, input_payload: Payload) -> List[float]:
        normalized_scalar = (input_payload.kpi - 100.0) / 75.0
        return [math.tanh(normalized_scalar * w) for w in self.weight_state_matrix]

    def update(self, initial_payload: Payload, executed_action: Dict[str, float], result_payload: Payload, learning_rate: float) -> float:
        if learning_rate <= 0.0:
            return 0.0
        latent = self.encode(initial_payload)
        projected_output = sum(
            math.tanh(cell + (executed_action["delta"] / 100.0) * w)
            for cell, w in zip(latent, self.weight_action_matrix)
        ) * 25.0
        predictive_error = max(min(result_payload.kpi - projected_output, 12.0), -12.0)
        for i in range(self.dimension):
            self.weight_state_matrix[i] += learning_rate * predictive_error * 0.0012
            self.weight_action_matrix[i] += learning_rate * predictive_error * (executed_action["delta"] * 0.00012)
        return abs(predictive_error)


class Policy:
    """Softmax agent-selection policy with an attention-style lookback over a 16-step memory buffer."""

    def __init__(self, state_space_dimension: int, active_agent_count: int) -> None:
        self.dimension: int = state_space_dimension
        self.agent_count: int = active_agent_count
        self.sequential_memory_buffer: deque[List[float]] = deque(maxlen=16)
        self.weight_policy_matrix: List[List[float]] = [
            [random.uniform(-0.01, 0.01) for _ in range(active_agent_count)]
            for _ in range(state_space_dimension)
        ]
        self.base_learning_rate: float = 0.025

    def select(self, latent_vector: List[float], beta_exploration_rate: float) -> tuple[int, List[float], List[float]]:
        attended_context_vector = list(latent_vector)

        if self.sequential_memory_buffer:
            normalization_scale = 1.0 / math.sqrt(self.dimension)
            buffer_length = len(self.sequential_memory_buffer)
            for step_idx, historical_memory in enumerate(self.sequential_memory_buffer):
                exponential_decay = math.exp((step_idx - buffer_length) / 5.0)
                matching_score = sum(c * m for c, m in zip(latent_vector, historical_memory)) * normalization_scale
                composite_weight = (math.exp(min(matching_score, 5.0)) * exponential_decay) / buffer_length
                for i in range(self.dimension):
                    attended_context_vector[i] += composite_weight * historical_memory[i]

        self.sequential_memory_buffer.append(latent_vector)

        probability_logits = [
            sum(attended_context_vector[i] * self.weight_policy_matrix[i][a] for i in range(self.dimension))
            for a in range(self.agent_count)
        ]
        max_logit = max(probability_logits)
        exp_probs = [math.exp(v - max_logit) for v in probability_logits]
        prob_sum = sum(exp_probs)
        normalized_probabilities = [p / prob_sum for p in exp_probs]

        if beta_exploration_rate <= 0.0:
            return normalized_probabilities.index(max(normalized_probabilities)), attended_context_vector, normalized_probabilities

        random_threshold = random.random()
        cumulative = 0.0
        for agent_index, agent_probability in enumerate(normalized_probabilities):
            cumulative += agent_probability
            if random_threshold <= cumulative:
                return agent_index, attended_context_vector, normalized_probabilities
        return 0, attended_context_vector, normalized_probabilities

    def update(self, attended_context: List[float], probabilities: List[float], selected_idx: int, feedback_advantage: float, modifier_learning_rate: float) -> None:
        if modifier_learning_rate <= 0.0:
            return
        for agent_idx in range(self.agent_count):
            gradient_step = (1.0 if agent_idx == selected_idx else 0.0) - probabilities[agent_idx]
            for cell_idx in range(self.dimension):
                self.weight_policy_matrix[cell_idx][agent_idx] += (
                    self.base_learning_rate * modifier_learning_rate * gradient_step * feedback_advantage * attended_context[cell_idx]
                )


# ---------------------------------------------------------------------------
# Agents: three fixed-gain linear controllers
# ---------------------------------------------------------------------------

class ConservativeAgent:
    @staticmethod
    def tick(observation_data: Dict[str, float], target_value: float) -> Dict[str, float]:
        return {"delta": (target_value - observation_data["kpi"]) * 0.05}


class AggressiveAgent:
    @staticmethod
    def tick(observation_data: Dict[str, float], target_value: float) -> Dict[str, float]:
        return {"delta": (target_value - observation_data["kpi"]) * 0.35}


class ReactiveAgent:
    @staticmethod
    def tick(observation_data: Dict[str, float], target_value: float) -> Dict[str, float]:
        target_deviation = abs(target_value - observation_data["kpi"])
        scaling_coefficient = 0.18 if target_deviation > 15.0 else 0.08
        return {"delta": (target_value - observation_data["kpi"]) * scaling_coefficient}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Fortress:
    """
    Runs a 60-step simulation nudging a KPI from 60.0 toward a target that
    steps from 100.0 to 140.0 halfway through, via a guardrailed multi-agent
    policy. `execute_governance_logic` lets an instance be wrapped by
    GsaUniversalAdapter (see gsa_adapter.py).
    """

    def __init__(self, operational_seed: int | None = None) -> None:
        set_global_seed(operational_seed)
        self.integrity_subsystem = IntegrityLayer()
        self.classification_engine = RegimeEngine()
        self.boundary_monitor = InvariantMonitor()
        self.divergence_monitor = DriftMonitor()
        self.predictive_world_model = WorldModel()
        self.policy_coordinator = Policy(state_space_dimension=8, active_agent_count=3)
        self.available_agents: List[Any] = [ConservativeAgent(), AggressiveAgent(), ReactiveAgent()]
        self.freeze_timer: int = 0

    def _evaluate_runtime_safety(self, current_state: float, world_error_value: float, distortion_metric: float, volatility_index: float) -> tuple[float, float, str]:
        systemic_regime = self.classification_engine.classify(volatility_index, distortion_metric)
        active_learning_modifier = 1.0 - distortion_metric
        exploration_entropy_rate = 0.05 * active_learning_modifier

        active_violations = self.boundary_monitor.check(current_state, distortion_metric, world_error_value, volatility_index)
        if active_violations:
            active_learning_modifier *= 0.1
            exploration_entropy_rate = 0.0
        if len(active_violations) >= 2:
            self.freeze_timer = 8

        return active_learning_modifier, exploration_entropy_rate, systemic_regime

    def _execute_agent_action(self, state_representation: List[float], current_state: float, target_value: float, exploration_rate: float, learning_modifier: float) -> tuple[int, List[float], List[float], Dict[str, float]]:
        if self.freeze_timer > 0:
            assigned_agent_idx = 0
            context_vector = state_representation
            selection_probabilities = [1.0, 0.0, 0.0]
            self.freeze_timer -= 1
        else:
            assigned_agent_idx, context_vector, selection_probabilities = self.policy_coordinator.select(state_representation, exploration_rate)

        calculated_action = self.available_agents[assigned_agent_idx].tick({"kpi": current_state}, target_value)
        return assigned_agent_idx, context_vector, selection_probabilities, calculated_action

    def run_cycle(self, noise_scale_coefficient: float = 4.0) -> Dict[str, Any]:
        current_state_variable = 60.0
        target_kpi_goal = 100.0
        running_predictive_error = 0.0
        historical_state_tracking: deque[float] = deque([current_state_variable], maxlen=10)
        current_active_regime = "STABLE"
        last_calculated_distortion = 0.0

        for cycle_step in range(60):
            if cycle_step == 30:
                target_kpi_goal = 140.0

            current_data_packet = Payload("System Functional", current_state_variable)
            contextual_quality_metrics = self.integrity_subsystem.analyze(current_data_packet, running_predictive_error)
            current_rolling_volatility = safe_stdev(historical_state_tracking)
            last_calculated_distortion = contextual_quality_metrics["distortion"]

            learning_modifier, exploration_rate, current_active_regime = self._evaluate_runtime_safety(
                current_state_variable, running_predictive_error, last_calculated_distortion, current_rolling_volatility
            )

            latent_state_mapping = self.predictive_world_model.encode(current_data_packet)
            agent_index, context_outputs, strategy_probabilities, raw_action_delta = self._execute_agent_action(
                latent_state_mapping, current_state_variable, target_kpi_goal, exploration_rate, learning_modifier
            )

            governed_action_delta = MandateLayer.enforce(raw_action_delta, current_state_variable, target_kpi_goal, current_rolling_volatility)

            audit_append("action_enforced", {
                "delta": governed_action_delta["delta"],
                "state": current_state_variable,
                "target": target_kpi_goal,
                "regime": current_active_regime,
            })

            applied_environmental_noise = random.uniform(-noise_scale_coefficient, noise_scale_coefficient)
            current_state_variable = (current_state_variable + governed_action_delta["delta"] + applied_environmental_noise) * 0.99
            historical_state_tracking.append(current_state_variable)

            subsequent_data_packet = Payload("step", current_state_variable)
            running_predictive_error = self.predictive_world_model.update(
                current_data_packet, governed_action_delta, subsequent_data_packet, 0.02 * learning_modifier
            )

            structural_drift_alert, _ = self.divergence_monitor.check(self.policy_coordinator.weight_policy_matrix)
            if structural_drift_alert:
                learning_modifier *= 0.25
                for row in self.policy_coordinator.weight_policy_matrix:
                    for i in range(len(row)):
                        row[i] *= 0.995

            step_feedback_reward = -abs(target_kpi_goal - current_state_variable)
            statistical_advantage_factor = step_feedback_reward / 100.0

            if self.freeze_timer == 0:
                self.policy_coordinator.update(
                    context_outputs, strategy_probabilities, agent_index, statistical_advantage_factor, learning_modifier
                )

        return {
            "final_state": current_state_variable,
            "regime": current_active_regime,
            "distortion": last_calculated_distortion,
        }

    async def execute_governance_logic(self, envelope: GsaContextEnvelope) -> GsaContextEnvelope:
        """Runs one run_cycle and reports the result back through a GSA envelope."""
        loop_results = self.run_cycle(noise_scale_coefficient=5.0)
        updated_payload = {
            "kernel_final_state": loop_results["final_state"],
            "kernel_regime": loop_results["regime"],
            "kernel_distortion": loop_results["distortion"],
        }
        return replace(
            envelope,
            payload_data=updated_payload,
            status_string=f"SAGE_KERNEL_COMPUTATION_SUCCESSFUL_REGIME_{loop_results['regime']}",
        )
