"""
Sentinel - IVR Analytics & Diagnostics Layer

Infers caller intent, scores quality, diagnoses abandonment, prescribes fixes
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum
import hashlib
import json

class CallerIntent(Enum):
    BILLING = "billing"
    TECHNICAL = "technical"
    SALES = "sales"
    CANCEL = "cancel"
    UPGRADE = "upgrade"
    COMPLAINT = "complaint"
    GENERAL = "general"
    UNKNOWN = "unknown"

class OutcomeQuality(Enum):
    EXCELLENT = "excellent"  # Resolved quickly
    GOOD = "good"  # Resolved with some friction
    POOR = "poor"  # Abandoned or long resolution
    FAILED = "failed"  # Abandoned early

@dataclass
class IntentSignal:
    """Raw signal of caller intent from routing choice"""
    queue_chosen: str
    confidence: float  # 0-1
    reasoning: str

@dataclass
class QualityScore:
    """Outcome quality assessment"""
    resolution_time: float  # seconds
    friction_count: int
    emotional_deterioration: float  # 0-1
    overall_score: float  # 0-1, higher is better
    quality_tier: OutcomeQuality

@dataclass
class AbandonmentDiagnosis:
    """Why did the caller abandon?"""
    primary_cause: str  # "long_wait", "repeat_routing", "emotional_decline", "unknown"
    contributing_factors: List[str]
    intervention_point: Optional[str]  # Which queue/node to improve
    confidence: float  # 0-1

@dataclass
class QueuePrescription:
    """Recommended change to queue ordering/structure"""
    current_order: List[str]
    proposed_order: List[str]
    rationale: str
    estimated_impact: float  # Expected improvement 0-1
    test_cohort_size: int

class SentinelCore:
    """Canonical Sentinel analytics engine

    Domain truth (queue-to-intent mapping, and eventually scoring/
    diagnosis rules) lives in the loaded cassette, not in this class.
    A cassette is mandatory: there is no built-in fallback map, by
    design, so there is exactly one place a given domain's rules can
    live rather than two that could quietly disagree.
    """

    def __init__(self, cassette):
        if cassette is None:
            raise ValueError(
                "SentinelCore requires a cassette; there is no built-in "
                "fallback. Load one first, e.g. "
                "CassetteLoader().load_cassette('ivr')."
            )
        self.cassette = cassette

    def infer_intent(self, journey: List[str], first_queue_chosen: str) -> IntentSignal:
        """Infer caller intent from first queue choice, via the cassette"""
        
        raw_intent = self.cassette.infer_intent(first_queue_chosen, {})
        confidence = 0.85 if raw_intent != "UNKNOWN" else 0.3
        
        reasoning = f"Caller routed to {first_queue_chosen}"
        if len(journey) > 2:
            if journey[1] == first_queue_chosen:
                reasoning += " (direct match)"
                confidence = 0.95
        
        return IntentSignal(
            queue_chosen=first_queue_chosen,
            confidence=confidence,
            reasoning=reasoning
        )
    
    def score_outcome_quality(self, resolved: bool, resolution_time: float,
                             friction_count: int, emotional_state) -> QualityScore:
        """Score quality of outcome"""
        
        # Base score from resolution
        base_score = 1.0 if resolved else 0.2
        
        # Time penalty: ideal < 30s, acceptable < 60s, poor > 120s
        if resolution_time < 30:
            time_score = 1.0
        elif resolution_time < 60:
            time_score = 0.8
        elif resolution_time < 120:
            time_score = 0.5
        else:
            time_score = 0.2
        
        # Friction penalty
        friction_score = max(0.0, 1.0 - friction_count * 0.15)
        
        # Emotional penalty
        emotional_penalty = emotional_state.frustration * 0.3
        emotional_score = max(0.0, 1.0 - emotional_penalty)
        
        # Composite
        overall = (base_score * 0.4 + time_score * 0.3 + 
                  friction_score * 0.2 + emotional_score * 0.1)
        
        if overall > 0.85:
            tier = OutcomeQuality.EXCELLENT
        elif overall > 0.65:
            tier = OutcomeQuality.GOOD
        elif overall > 0.35:
            tier = OutcomeQuality.POOR
        else:
            tier = OutcomeQuality.FAILED
        
        return QualityScore(
            resolution_time=resolution_time,
            friction_count=friction_count,
            emotional_deterioration=emotional_state.frustration,
            overall_score=overall,
            quality_tier=tier
        )
    
    def diagnose_abandonment(self, journey: List[str], friction_events: List,
                            emotional_state, resolved: bool) -> AbandonmentDiagnosis:
        """Diagnose why abandonment occurred"""
        
        if resolved:
            return AbandonmentDiagnosis(
                primary_cause="n/a",
                contributing_factors=[],
                intervention_point=None,
                confidence=1.0
            )
        
        primary = "unknown"
        factors = []
        intervention = None
        
        # Check for long wait
        if any(e.type == "long_wait" for e in friction_events):
            primary = "long_wait"
            intervention = journey[-1] if journey else None
            factors.append("Queue wait exceeded tolerance")
        
        # Check for repeats
        if any(e.type == "repeat" for e in friction_events):
            if not primary or primary == "unknown":
                primary = "repeat_routing"
            factors.append("Caller repeated same queue")
            intervention = journey[-2] if len(journey) > 1 else None
        
        # Check emotional deterioration
        if emotional_state.frustration > 0.7:
            if not primary or primary == "unknown":
                primary = "emotional_decline"
            factors.append("High frustration detected")
        
        confidence = 0.8 if primary != "unknown" else 0.3
        
        return AbandonmentDiagnosis(
            primary_cause=primary,
            contributing_factors=factors,
            intervention_point=intervention,
            confidence=confidence
        )
    
    def prescribe_queue_reordering(self, call_outcomes: List[Dict],
                                  current_order: List[str]) -> QueuePrescription:
        """Recommend queue reordering based on outcomes"""
        
        # Count successful outcomes by queue
        queue_success = {}
        for outcome in call_outcomes:
            first_queue = next((q for q in outcome["journey"] if "queue" in q), None)
            if first_queue:
                if first_queue not in queue_success:
                    queue_success[first_queue] = {"success": 0, "total": 0}
                queue_success[first_queue]["total"] += 1
                if outcome["resolved"]:
                    queue_success[first_queue]["success"] += 1
        
        # Calculate success rates
        success_rates = {}
        for queue, counts in queue_success.items():
            if counts["total"] > 0:
                success_rates[queue] = counts["success"] / counts["total"]
        
        # Propose new order: higher success rates first
        proposed_order = sorted(success_rates.keys(), 
                               key=lambda q: success_rates[q], 
                               reverse=True)
        
        # Estimate impact
        avg_current = sum(success_rates.values()) / len(success_rates) if success_rates else 0.5
        avg_proposed = (sum(success_rates.get(q, 0.5) for q in proposed_order) / 
                       len(proposed_order) if proposed_order else 0.5)
        estimated_impact = max(0.0, avg_proposed - avg_current)
        
        return QueuePrescription(
            current_order=current_order,
            proposed_order=proposed_order,
            rationale="Reorder queues by success rate to match caller intent distribution",
            estimated_impact=estimated_impact,
            test_cohort_size=100
        )
    
    def structural_hash(self) -> str:
        """Deterministic hash of Sentinel state, including which cassette
        is active -- so the hash actually changes if the domain rules
        change, instead of staying identical across two different
        cassettes the way a hash of core-only state would."""
        cassette_config = self.cassette.get_config()
        state = {
            "version": "sentinel_canonical_v2",
            "cassette": {
                "name": cassette_config.name,
                "version": cassette_config.version,
                "domain": cassette_config.domain,
            },
        }
        return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
