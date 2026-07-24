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
            # 2.0.1 -> 2.0.2: fraud-escalation top-tier carve-out (see
            # _score_components). A genuine behavior change -- a
            # qualifying fraud escalation now scores "excellent"
            # instead of capping at "poor" -- so this is a real version
            # bump, not just a moved-code-hash formality.
            version="2.0.2",
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
    # "friction_count", optional "emotion" (dict), optional
    # "journey"/"friction_events" for explanation, and optional
    # "customer_stated_fraud" (bool) for the fraud-escalation carve-out
    # below.

    # The one existing, fixed parameter that names banking's
    # system-identified fraud signal: the queue this cassette already
    # declares in get_queue_definitions() and already reads structurally
    # in _security_signals' own fraud lookup. Reused here verbatim --
    # not a new detection heuristic -- so a call only qualifies as
    # "system identified" if it was actually routed through this exact,
    # already-declared queue.
    _FRAUD_DETECTION_QUEUE = "fraud_detection_queue"

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
        customer_stated_fraud = bool(episode.attributes.get("customer_stated_fraud",
                                                             False))
        journey = list(episode.attributes.get("journey", []))
        return (resolved, duration, friction_count, emotion_data,
                customer_stated_fraud, journey)

    def _fraud_escalation_path(self, resolved: bool, customer_stated_fraud: bool,
                               journey: List[str]):
        """Which of the two legitimate fraud-escalation paths (if any)
        this episode qualifies under, for the top-tier carve-out in
        _score_components. Returns None when neither applies -- normal
        scoring then resumes unchanged, so a non-fraud escalation
        (agent gives up, unresolved billing dispute, IVR loop-out) is
        completely unaffected.

        An escalation is by definition NOT resolved in-system -- a
        resolved call already competes for excellent/good on the
        ordinary arithmetic and needs no carve-out -- so this only
        ever fires for resolved=False.

        1. customer_stated: episode.attributes["customer_stated_fraud"]
           is truthy -- the customer directly indicated they believe
           it's fraud. Recorded as-is; not re-verified (see the class
           docstring note on why no feedback loop is needed here).
        2. system_identified: the journey routed through
           _FRAUD_DETECTION_QUEUE, the one fixed, already-declared
           parameter for this signal -- not open-ended model judgment,
           and not loosened or extended beyond what this cassette
           already declares.
        """
        if resolved:
            return None
        if customer_stated_fraud:
            return "customer_stated"
        if self._FRAUD_DETECTION_QUEUE in journey:
            return f"system_identified:{self._FRAUD_DETECTION_QUEUE}"
        return None

    def _score_components(self, resolved: bool, duration: float,
                          friction_count: int, emotion_data: Dict,
                          customer_stated_fraud: bool = False,
                          journey: List[str] = ()):
        """THE banking scoring arithmetic, in one place (see judge).

        FRAUD-ESCALATION TOP-TIER DECISION (resolves the note that used
        to live here): a fraud escalation is now scored as this
        cassette's best possible outcome -- score 1.0, tier
        "excellent", the top of the existing four-tier hierarchy --
        under exactly two legitimate paths, both checked in
        _fraud_escalation_path: the customer directly states they
        believe it's fraud, or the call was routed through this
        cassette's own already-declared fraud-detection queue. Neither
        path is a discretionary AI judgment call: a customer's own
        direct statement isn't a model inference, and routing through a
        specific named queue is a fixed structural fact, not a tunable
        score. That is why no verification of "was it real fraud" is
        required or possible here, and why it isn't a gaming risk --
        there is no feedback loop for either signal to game, and
        attempts to game outcome scoring are assumed to be derivatives
        of already-known tactics (misrouting, false claims), not novel
        discretionary failures a detector would need to catch after the
        fact. The fix closes discretion at the source instead. The
        override is unconditional (bypasses the duration/friction/
        frustration arithmetic below entirely) and always carries a
        "fraud_escalation_top_tier" factor naming exactly which path
        fired and, for system-identified, which parameter matched --
        the audit trail Requirement 6 requires for the classification
        to apply at all. Non-fraud escalations (agent gives up,
        unresolved billing dispute, IVR loop-out, etc.) take neither
        path and are scored exactly as before, still capped at "poor".
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

        fraud_path = self._fraud_escalation_path(
            resolved, customer_stated_fraud, list(journey)
        )
        if fraud_path is not None:
            matched_parameter = (self._FRAUD_DETECTION_QUEUE
                                 if fraud_path.startswith("system_identified")
                                 else None)
            factors.append({
                "factor": "fraud_escalation_top_tier",
                "value": True,
                "contribution": None,
                "escalation_path": fraud_path,
                "matched_parameter": matched_parameter,
                "detail": "fraud escalation classified as the best possible "
                          "outcome (score 1.0, tier excellent); overrides the "
                          "duration/friction/frustration factors above -- see "
                          "escalation_path for which of the two legitimate "
                          "paths triggered this and matched_parameter for the "
                          "exact system-identified parameter, when applicable",
            })
            return QualityResult(score=1.0, tier="excellent"), factors

        return QualityResult(score=score, tier=tier), factors

    def judge(self, episode: Episode) -> QualityResult:
        """Judge one validated episode with banking's own rules.

        Deliberately keeps its own 0.80 "excellent" cutoff and its own
        weights (see _score_components), including the fraud-escalation
        top-tier carve-out documented there.
        """
        (resolved, duration, friction_count, emotion_data,
         customer_stated_fraud, journey) = self._episode_facts(episode)
        result, _ = self._score_components(
            resolved, duration, friction_count, emotion_data,
            customer_stated_fraud, journey,
        )
        return result

    def explain(self, episode: Episode) -> List[Dict[str, Any]]:
        """Factor-level reasons in banking vocabulary. For unresolved
        episodes, the security-focused signals (the old abandonment
        diagnosis logic, kept as _security_signals) ride along. Kernel
        verification findings are prepended by episode.explain_episode."""
        (resolved, duration, friction_count, emotion_data,
         customer_stated_fraud, journey) = self._episode_facts(episode)
        _, factors = self._score_components(
            resolved, duration, friction_count, emotion_data,
            customer_stated_fraud, journey,
        )
        if not resolved:
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
