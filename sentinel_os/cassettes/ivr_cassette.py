"""
IVR Cassette - Reference implementation for call center IVR

Domain-specific rules for traditional voice IVR
"""

import copy

from cassette_interface import Cassette, CassetteConfig, QualityResult
from typing import Dict, List

class IvrCassette(Cassette):
    """Call center IVR cassette - what we've been building"""
    
    # THE declaration site. Every governance number the engine reads
    # about this domain lives in this one dict -- typed, bounded,
    # documented, with forensic metadata slots. The engine reads it
    # through cassette_schema validation; nothing downstream may
    # restate these values as literals.
    _GOVERNANCE_PARAMETERS = {
        "long_wait_threshold": {
            "value": 30.0,
            "type": "float",
            "min": 1.0,
            "max": 600.0,
            "unit": "seconds",
            "description": "A wait longer than this at any node counts as one friction event.",
            "metadata": {
                "approval_date": None,
                "justification": "Callers audibly disengage past ~30s of dead air in a standard voice IVR.",
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
                "justification": "Two measured friction events on one call indicates a systemic path problem, not caller noise.",
                "last_reviewed": None,
            },
        },
        "expected_wait_bounds": {
            "value": [4.0, 120.0],
            "type": "range",
            "min": 0.0,
            "max": 3600.0,
            "unit": "seconds",
            "description": "Self-healing clamp band for the expected_wait parameter.",
            "metadata": {
                "approval_date": None,
                "justification": "Below 4s the sensor chases noise; above 120s the heal target itself is the outage.",
                "last_reviewed": None,
            },
        },
        "twilio_long_duration_threshold": {
            "value": 300,
            "type": "int",
            "min": 1,
            "max": 3600,
            "unit": "seconds",
            "description": "Twilio ingest: calls longer than this duration contribute 2 friction points.",
            "metadata": {
                "approval_date": None,
                "justification": "5 minutes (300s) is the standard call-center threshold for 'long call' in IVR contexts.",
                "last_reviewed": None,
            },
        },
        "twilio_medium_duration_threshold": {
            "value": 120,
            "type": "int",
            "min": 1,
            "max": 3600,
            "unit": "seconds",
            "description": "Twilio ingest: calls longer than this duration contribute 1 friction point.",
            "metadata": {
                "approval_date": None,
                "justification": "2 minutes (120s) is the threshold for 'medium length' in IVR contexts.",
                "last_reviewed": None,
            },
        },
        "twilio_short_duration_threshold": {
            "value": 10,
            "type": "int",
            "min": 1,
            "max": 60,
            "unit": "seconds",
            "description": "Twilio ingest: calls shorter than this duration with non-completed status indicate dropped calls (1 friction point).",
            "metadata": {
                "approval_date": None,
                "justification": "10 seconds is insufficient for an IVR to play a prompt and capture input; shorter non-completed calls suggest early hang-ups.",
                "last_reviewed": None,
            },
        },
    }

    def get_config(self) -> CassetteConfig:
        return CassetteConfig(
            name="standard-ivr",
            # 1.0.0 -> 1.1.0: governance parameters became typed schema
            # declarations (Item #3); governance_trigger declared at 2
            # with inclusive (>=) semantics.
            version="1.1.0",
            description="Traditional call center IVR",
            domain="ivr"
        )

    def get_governance_parameters(self) -> Dict[str, Dict]:
        """The typed governance declaration (see cassette_schema)."""
        return copy.deepcopy(self._GOVERNANCE_PARAMETERS)
    
    def get_queue_definitions(self) -> Dict[str, Dict]:
        """IVR-specific queues"""
        return {
            "billing_queue": {"agents": 5, "priority": 1},
            "tech_queue": {"agents": 3, "priority": 2},
            "sales_queue": {"agents": 4, "priority": 1},
            "cancel_queue": {"agents": 2, "priority": 3},
            "upgrade_queue": {"agents": 3, "priority": 2},
            "complaint_queue": {"agents": 2, "priority": 3},
            "general_queue": {"agents": 2, "priority": 2},
        }
    
    def infer_intent(self, queue_name: str, caller_data: Dict) -> str:
        """Map IVR queue to caller intent"""
        mapping = {
            "billing_queue": "BILLING",
            "tech_queue": "TECHNICAL",
            "sales_queue": "SALES",
            "cancel_queue": "CANCEL",
            "upgrade_queue": "UPGRADE",
            "complaint_queue": "COMPLAINT",
            "general_queue": "GENERAL",
        }
        return mapping.get(queue_name, "UNKNOWN")
    
    def score_outcome_quality(self, resolved: bool, duration: float,
                             friction_count: int, emotion_data: Dict) -> QualityResult:
        """Score call quality for IVR.

        Returns QualityResult: this cassette owns both the score
        arithmetic and the tier cutoffs below.
        """
        
        score = 0.0
        
        if resolved:
            # 0.7, not 0.6: with the old baseline a flawless call
            # (resolved, fast, zero friction, calm caller) maxed out at
            # 0.6 + 0.2 = 0.8, which sits under the 0.85 "excellent"
            # cutoff -- perfection was mathematically capped at "good".
            # At 0.7 a flawless call scores 0.9 and can actually reach
            # the top tier.
            score += 0.7
        else:
            score += 0.1
        
        if duration < 120:
            score += 0.2
        elif duration < 300:
            score += 0.1
        
        friction_penalty = min(friction_count * 0.15, 0.3)
        score -= friction_penalty
        
        frustration_penalty = emotion_data.get("frustration", 0) * 0.2
        score -= frustration_penalty
        
        score = max(0.0, min(1.0, score))
        
        if score > 0.85:
            tier = "excellent"
        elif score > 0.65:
            tier = "good"
        elif score > 0.35:
            tier = "poor"
        else:
            tier = "failed"
        
        return QualityResult(score=score, tier=tier)
    
    def diagnose_abandonment(self, journey: List[str], friction: List,
                            emotion: Dict, resolved: bool) -> Dict:
        """Diagnose IVR abandonment"""
        
        if resolved:
            return {"reason": "n/a", "confidence": 1.0}
        
        primary = "unknown"
        factors = []
        
        queue_visits = sum(1 for node in journey if "queue" in node)
        if queue_visits > 1:
            primary = "repeat_routing"
            factors.append("Caller repeated same queue")
        
        if any(f.get("type") == "long_wait" for f in friction if isinstance(f, dict)):
            if primary == "unknown":
                primary = "long_wait"
            factors.append("Long wait time")
        
        if emotion.get("frustration", 0) > 0.7:
            if primary == "unknown":
                primary = "emotional_decline"
            factors.append("High frustration")
        
        return {
            "reason": primary,
            "factors": factors,
            "confidence": 0.8 if primary != "unknown" else 0.3
        }
    
    def get_friction_thresholds(self) -> Dict[str, float]:
        """IVR-specific friction thresholds.

        Governance-relevant values are NOT restated here; they are
        derived from the single declaration above so there is exactly
        one place this domain's judgment lives.
        """
        return {
            "long_wait_threshold": self._GOVERNANCE_PARAMETERS["long_wait_threshold"]["value"],
            "repeat_penalty": 0.2,
            "denial_penalty": 0.3,
            "min_friction_for_governance": self._GOVERNANCE_PARAMETERS["governance_trigger"]["value"],
        }
    
    def get_healing_bounds(self) -> Dict[str, tuple]:
        """IVR-specific healing bounds (expected_wait derives from the
        governance declaration above -- one source of truth)."""
        return {
            "expected_wait": tuple(self._GOVERNANCE_PARAMETERS["expected_wait_bounds"]["value"]),
            "staffing_agents": (1, 20),
            "menu_size": (3, 10),
        }
    
    def compute_reward(self, outcome: Dict) -> float:
        """IVR reward signal"""
        reward = 0.0
        
        if outcome.get("resolved"):
            reward += 10.0
        
        reward -= outcome.get("wait_time", 0) / 10
        reward -= outcome.get("friction_count", 0) * 2
        
        return reward
    
    def validate(self) -> bool:
        """Validate cassette is complete"""
        return (
            self.get_config() is not None and
            self.get_queue_definitions() is not None and
            len(self.get_queue_definitions()) > 0 and
            self.get_friction_thresholds() is not None and
            self.get_healing_bounds() is not None
        )
