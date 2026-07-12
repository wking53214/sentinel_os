"""
Banking Cassette - Domain-specific implementation for financial services

Same boom box, completely different rules for banking context
"""

import copy

from cassette_interface import Cassette, CassetteConfig, QualityResult
from typing import Dict, List

class BankingCassette(Cassette):
    """Banking/financial services cassette"""
    
    # THE declaration site for banking governance numbers (see
    # cassette_schema). Banking judges the same call differently by
    # design: longer wait tolerance (security verification), same
    # inclusive trigger.
    _GOVERNANCE_PARAMETERS = {
        "long_wait_threshold": {
            "value": 45.0,
            "type": "float",
            "min": 1.0,
            "max": 600.0,
            "unit": "seconds",
            "description": "A wait longer than this at any node counts as one friction event.",
            "metadata": {
                "approval_date": None,
                "justification": "Security verification steps legitimately hold banking callers longer than a standard IVR.",
                "last_reviewed": None,
            },
        },
        "governance_trigger": {
            "value": 2,
            "type": "int",
            "min": 0,
            "max": 100,
            "unit": "friction events",
            "description": "Calls with friction_count >= this value are routed to the governor (inclusive).",
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
            version="1.1.0",
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
    
    def infer_intent(self, queue_name: str, caller_data: Dict) -> str:
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
    
    def score_outcome_quality(self, resolved: bool, duration: float,
                             friction_count: int, emotion_data: Dict) -> QualityResult:
        """Score banking call quality (DIFFERENT from IVR).

        Deliberately keeps its own 0.80 "excellent" cutoff and its own
        weights -- banking judging the same call differently than IVR
        is the point of the cassette system, not a bug to normalize.

        NOTE (open decision, not silently changed): a *correct* fraud
        escalation to a human still scores as unresolved here, even
        though compute_reward already treats it as a win. Whether a
        proper escalation counts as a success needs an explicit call
        before this cassette grows an escalation carve-out.
        """
        
        score = 0.0
        
        # For banking: escalation to human is GOOD (not bad)
        # Fraud alerts MUST go to human (automatic escalation = success)
        if resolved:
            score += 0.7  # Higher baseline for resolution
        else:
            score += 0.2  # Escalation to human is acceptable
        
        # Duration: banking calls typically longer (compliance, security)
        # 3min is acceptable, 10min is concerning
        if duration < 180:
            score += 0.2
        elif duration < 600:
            score += 0.05
        
        # Friction: repeats are worse in banking (security risk)
        friction_penalty = min(friction_count * 0.25, 0.4)  # Stricter
        score -= friction_penalty
        
        # Emotion: frustration is acceptable if fraud is caught
        # Don't penalize as heavily
        frustration_penalty = emotion_data.get("frustration", 0) * 0.1
        score -= frustration_penalty
        
        score = max(0.0, min(1.0, score))
        
        if score > 0.80:
            tier = "excellent"
        elif score > 0.60:
            tier = "good"
        elif score > 0.35:
            tier = "poor"
        else:
            tier = "failed"
        
        return QualityResult(score=score, tier=tier)
    
    def diagnose_abandonment(self, journey: List[str], friction: List,
                            emotion: Dict, resolved: bool) -> Dict:
        """Diagnose banking abandonment (security-focused)"""
        
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
    
    def get_friction_thresholds(self) -> Dict[str, float]:
        """Banking-specific friction (MORE SENSITIVE to security)"""
        return {
            "long_wait_threshold": self._GOVERNANCE_PARAMETERS["long_wait_threshold"]["value"],
            "repeat_penalty": 0.3,  # Repeats worse in banking
            "denial_penalty": 0.4,  # Denials worse (security failures)
            "min_friction_for_governance": self._GOVERNANCE_PARAMETERS["governance_trigger"]["value"],
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
