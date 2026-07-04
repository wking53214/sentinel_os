"""
IVR Cassette - Reference implementation for call center IVR

Domain-specific rules for traditional voice IVR
"""

from cassette_interface import Cassette, CassetteConfig
from typing import Dict, List

class IvrCassette(Cassette):
    """Call center IVR cassette - what we've been building"""
    
    def get_config(self) -> CassetteConfig:
        return CassetteConfig(
            name="standard-ivr",
            version="1.0.0",
            description="Traditional call center IVR",
            domain="ivr"
        )
    
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
                             friction_count: int, emotion_data: Dict) -> str:
        """Score call quality for IVR"""
        
        score = 0.0
        
        if resolved:
            score += 0.6
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
            return "excellent"
        elif score > 0.65:
            return "good"
        elif score > 0.35:
            return "poor"
        else:
            return "failed"
    
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
        """IVR-specific friction thresholds"""
        return {
            "long_wait_threshold": 30.0,
            "repeat_penalty": 0.2,
            "denial_penalty": 0.3,
            "min_friction_for_governance": 1,
        }
    
    def get_healing_bounds(self) -> Dict[str, tuple]:
        """IVR-specific healing bounds"""
        return {
            "expected_wait": (4.0, 120.0),
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
