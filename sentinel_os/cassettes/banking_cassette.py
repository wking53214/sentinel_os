"""
Banking Cassette - Domain-specific implementation for financial services

Same boom box, completely different rules for banking context.

First cassette to use the kernel/capability split honestly: banking
enables routing_topology, rl, and self_healing -- and does NOT enable
telephony_ingest, because this domain has never had real banking call
data behind Twilio ingest thresholds. Under the old universal
parameter contract, that forced three explicitly-flagged PLACEHOLDER
twilio_* values into this file just to load; under capability-scoped
contracts, the honest declaration ("no telephony ingest yet") is
simply made, and the placeholders are gone. Judgment happens through
the kernel surface: judge/explain over episodes.
"""

import copy

from cassette_interface import Cassette, CassetteConfig, QualityResult
from cassette_capabilities import (
    CAPABILITY_RL,
    CAPABILITY_ROUTING_TOPOLOGY,
    CAPABILITY_SELF_HEALING,
    ReinforcementLearning,
    RoutingTopology,
    SelfHealing,
)
from episode import Episode
from typing import Any, Dict, List

class BankingCassette(Cassette, RoutingTopology, ReinforcementLearning,
                      SelfHealing):
    """Banking/financial services cassette"""

    # The manifest. telephony_ingest is deliberately ABSENT: enabling
    # it obligates real, reviewed values for long_wait_threshold and
    # the three twilio_* duration thresholds, and this domain has none
    # yet. When real banking call data exists, enable the capability
    # and declare reviewed values -- prior working intent, for the
    # record: long_wait_threshold 45.0s ("security verification steps
    # legitimately hold banking callers longer than a standard IVR",
    # ~1.5x the IVR tolerance), and the removed twilio_* placeholders
    # were 450/180/15, scaled by that same ratio, never reviewed
    # against real data. Validation now REJECTS declaring any of those
    # four parameters while the capability is off, so the placeholder
    # hack cannot quietly return.
    CAPABILITIES = (
        CAPABILITY_ROUTING_TOPOLOGY,
        CAPABILITY_RL,
        CAPABILITY_SELF_HEALING,
    )

    # THE declaration site for banking governance numbers (see
    # cassette_schema). Banking judges the same episode differently by
    # design; the kernel trigger and healing band are its only
    # parameter obligations under the enabled manifest.
    _GOVERNANCE_PARAMETERS = {
        "governance_trigger": {
            "value": 2,
            "type": "int",
            "min": 0,
            "max": 100,
            "unit": "friction events",
            "description": "Episodes with friction_count >= this value are routed to the governor (inclusive).",
            "metadata": {
                "approval_date": None,
                "justification": "Matches the prior min_friction_for_governance=2 declaration for this domain.",
                "last_reviewed": None,
            },
        },
        "expected_wait_bounds": {
            "value": [15.0, 300.0],
            "type": "range",
            "min": 0.0,
            "max": 3600.0,
            "unit": "seconds",
            "description": "Self-healing clamp band for the expected_wait parameter (stricter floor for verification).",
            "metadata": {
                "approval_date": None,
                "justification": "Healing below 15s would bypass mandatory verification pacing; above 300s is an outage, not a target.",
                "last_reviewed": None,
            },
        },
    }

    def get_config(self) -> CassetteConfig:
        return CassetteConfig(
            name="banking-v1",
            # 1.0.0 -> 1.1.0: governance parameters became typed schema
            # declarations (Item #3).
            # 1.1.0 -> 2.0.0: kernel/capability split. telephony_ingest
            # dropped from the manifest, and with it the three flagged
            # placeholder twilio_* thresholds and long_wait_threshold;
            # judgment moved to the kernel surface (judge/explain over
            # episodes) with the SAME scoring arithmetic and tier
            # cutoffs the old score_outcome_quality used. New code
            # hash => new version (binding enforcement).
            # 2.0.0 -> 2.0.1: regulatory-cassette framework modules
            # joined the shared governance code-hash surface (see
            # ivr_cassette for the identical note). Behavior unchanged;
            # moved code hash => new version under binding enforcement.
            version="2.0.1",
            description="Financial services & banking",
            domain="banking"
        )

    def get_governance_parameters(self) -> Dict[str, Dict]:
        """The typed governance declaration (see cassette_schema)."""
        return copy.deepcopy(self._GOVERNANCE_PARAMETERS)
    
    def get_queue_definitions(self) -> Dict[str, Dict]:
        """Banking-specific queues (completely different from IVR)"""
        return {
            "account_inquiry_queue": {"agents": 4, "priority": 2},
            "fraud_detection_queue": {"agents": 3, "priority": 1},  # HIGHEST priority
            "transaction_queue": {"agents": 5, "priority": 2},
            "dispute_resolution_queue": {"agents": 2, "priority": 1},
            "loan_queue": {"agents": 3, "priority": 3},
            "compliance_queue": {"agents": 1, "priority": 1},
        }
    
    def _infer_intent_to_label(self, queue_name: str, caller_data: Dict) -> str:
        """Map banking queue to financial intent"""
        mapping = {
            "account_inquiry_queue": "ACCOUNT_LOOKUP",
            "fraud_detection_queue": "FRAUD_ALERT",
            "transaction_queue": "TRANSACTION_ISSUE",
            "dispute_resolution_queue": "DISPUTE",
            "loan_queue": "LOAN_APPLICATION",
            "compliance_queue": "COMPLIANCE",
        }
        return mapping.get(queue_name, "UNKNOWN")

    # ---- Kernel judgment surface ----
    #
    # Banking's judgment arithmetic is the SAME as its old
    # score_outcome_quality -- same weights, same 0.80 excellent
    # cutoff -- relocated to the kernel surface. Banking judging the
    # same episode differently than IVR is the point of the cassette
    # system, not a bug to normalize.
    #
    # Field convention this cassette reads (fail-loud on absence):
    # episode.actual["resolved"] (an OUTCOME -- read from the observed
    # record, never from actor_report); episode.attributes "duration",
    # "friction_count", optional "emotion" (dict), and optional
    # "journey"/"friction_events" for explanation.

    def _episode_facts(self, episode: Episode):
        try:
            resolved = bool(episode.actual["resolved"])
        except KeyError:
            raise KeyError(
                "banking judgment requires episode.actual['resolved'] -- the "
                "OBSERVED resolution outcome (actor_report is never read)"
            )
        try:
            duration = float(episode.attributes["duration"])
            friction_count = int(episode.attributes["friction_count"])
        except KeyError as missing:
            raise KeyError(
                f"banking judgment requires episode.attributes[{missing.args[0]!r}]; "
                f"declared attributes: {sorted(episode.attributes)}"
            )
        emotion_data = dict(episode.attributes.get("emotion", {}))
        return resolved, duration, friction_count, emotion_data

    def _score_components(self, resolved: bool, duration: float,
                          friction_count: int, emotion_data: Dict):
        """THE banking scoring arithmetic, in one place (see judge).

        NOTE (open decision, not silently changed): a *correct* fraud
        escalation to a human still scores as unresolved here, even
        though compute_reward already treats it as a win. Whether a
        proper escalation counts as a success needs an explicit call
        before this cassette grows an escalation carve-out.
        """

        score = 0.0
        factors: List[Dict[str, Any]] = []

        # For banking: escalation to human is GOOD (not bad)
        # Fraud alerts MUST go to human (automatic escalation = success)
        if resolved:
            score += 0.7  # Higher baseline for resolution
            factors.append({"factor": "resolved", "value": True,
                            "contribution": +0.7,
                            "detail": "resolved without human escalation"})
        else:
            score += 0.2  # Escalation to human is acceptable
            factors.append({"factor": "resolved", "value": False,
                            "contribution": +0.2,
                            "detail": "not resolved in-system; escalation to a "
                                      "human is acceptable in banking"})

        # Duration: banking calls typically longer (compliance, security)
        # 3min is acceptable, 10min is concerning
        if duration < 180:
            score += 0.2
            factors.append({"factor": "duration", "value": duration,
                            "contribution": +0.2,
                            "detail": "within normal banking handling time (< 180s)"})
        elif duration < 600:
            score += 0.05
            factors.append({"factor": "duration", "value": duration,
                            "contribution": +0.05,
                            "detail": "long but tolerable for compliance/security (< 600s)"})
        else:
            factors.append({"factor": "duration", "value": duration,
                            "contribution": 0.0,
                            "detail": "concerning duration (>= 600s)"})

        # Friction: repeats are worse in banking (security risk)
        friction_penalty = min(friction_count * 0.25, 0.4)  # Stricter
        score -= friction_penalty
        factors.append({"factor": "friction", "value": friction_count,
                        "contribution": -friction_penalty,
                        "detail": "0.25 per friction event (stricter than IVR), capped at 0.4"})

        # Emotion: frustration is acceptable if fraud is caught
        # Don't penalize as heavily
        frustration_penalty = emotion_data.get("frustration", 0) * 0.1
        score -= frustration_penalty
        factors.append({"factor": "frustration",
                        "value": emotion_data.get("frustration", 0),
                        "contribution": -frustration_penalty,
                        "detail": "0.1 x measured frustration (lighter than IVR)"})

        score = max(0.0, min(1.0, score))

        if score > 0.80:
            tier = "excellent"
        elif score > 0.60:
            tier = "good"
        elif score > 0.35:
            tier = "poor"
        else:
            tier = "failed"

        return QualityResult(score=score, tier=tier), factors

    def judge(self, episode: Episode) -> QualityResult:
        """Judge one validated episode with banking's own rules.

        Deliberately keeps its own 0.80 "excellent" cutoff and its own
        weights (see _score_components).
        """
        resolved, duration, friction_count, emotion_data = \
            self._episode_facts(episode)
        result, _ = self._score_components(
            resolved, duration, friction_count, emotion_data
        )
        return result

    def explain(self, episode: Episode) -> List[Dict[str, Any]]:
        """Factor-level reasons in banking vocabulary. For unresolved
        episodes, the security-focused signals (the old abandonment
        diagnosis logic, kept as _security_signals) ride along. Kernel
        verification findings are prepended by episode.explain_episode."""
        resolved, duration, friction_count, emotion_data = \
            self._episode_facts(episode)
        _, factors = self._score_components(
            resolved, duration, friction_count, emotion_data
        )
        if not resolved:
            journey = list(episode.attributes.get("journey", []))
            friction_events = list(episode.attributes.get("friction_events", []))
            signals = self._security_signals(
                journey, friction_events, emotion_data, resolved
            )
            factors.append({"factor": "security_diagnosis", **signals})
        return factors

    def _security_signals(self, journey: List[str], friction: List,
                          emotion: Dict, resolved: bool) -> Dict:
        """Banking's security-focused read on an unresolved episode
        (formerly the diagnose_abandonment telephony surface; the
        logic is unchanged, it is simply no longer a contract method
        of a capability this domain doesn't enable)."""

        if resolved:
            return {"reason": "n/a", "confidence": 1.0}

        primary = "unknown"
        factors = []

        # In banking, fraud alerts MUST stay on line
        if any("fraud" in node.lower() for node in journey):
            if emotion.get("frustration", 0) > 0.5:
                primary = "fraud_frustration"
                factors.append("Caller frustrated during fraud alert")

        # Long hold = abandonment risk in banking
        if any(f.get("type") == "long_wait" for f in friction if isinstance(f, dict)):
            if primary == "unknown":
                primary = "security_hold_too_long"
            factors.append("Security verification took too long")

        # Repeats suggest account issues
        if any(node == "account_inquiry_queue" for node in journey):
            account_repeats = sum(1 for node in journey if "account" in node)
            if account_repeats > 1:
                primary = "account_issue"
                factors.append("Caller could not resolve account issue")

        return {
            "reason": primary,
            "factors": factors,
            "confidence": 0.85 if primary != "unknown" else 0.3
        }

    def get_healing_bounds(self) -> Dict[str, tuple]:
        """Banking-specific healing bounds (STRICTER)"""
        return {
            "expected_wait": tuple(self._GOVERNANCE_PARAMETERS["expected_wait_bounds"]["value"]),
            "staffing_agents": (2, 15),  # Minimum 2 for compliance
            "fraud_detection_threshold": (0.1, 0.9),  # Sensitivity range
            "escalation_rate": (0.2, 0.8),  # Expected fraud escalation
        }
    
    def compute_reward(self, outcome: Dict) -> float:
        """Banking reward signal (different from IVR)"""
        reward = 0.0
        
        # Resolution is good
        if outcome.get("resolved"):
            reward += 15.0  # Higher reward for banking
        
        # Escalation to human for fraud = GOOD
        if outcome.get("escalated_for_fraud"):
            reward += 5.0
        
        # But don't reward abandonment
        if outcome.get("abandoned"):
            reward -= 20.0
        
        # Time less critical (security > speed)
        reward -= outcome.get("wait_time", 0) / 20
        
        # Friction is worse
        reward -= outcome.get("friction_count", 0) * 3
        
        return reward
    
    def validate(self) -> bool:
        """Validate banking cassette"""
        return (
            self.get_config() is not None and
            len(self.get_queue_definitions()) >= 4 and  # At least 4 queues
            "fraud" in str(self.get_queue_definitions()).lower()  # Must have fraud handling
        )
