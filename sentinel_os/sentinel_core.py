"""
Sentinel - IVR Analytics & Diagnostics Layer

Infers caller intent, scores quality, diagnoses abandonment, prescribes fixes
"""

from dataclasses import dataclass
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
    # The cassette's domain-native intent label (e.g. "BILLING",
    # "FRAUD_ALERT"). Previously the cassette classified the intent and
    # the result was used only to pick a confidence number, then thrown
    # away -- the one field this signal exists to carry never rode in it.
    classification: str = "UNKNOWN"

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
        # Injection is a load path too: a cassette handed straight to
        # the core gets the same fail-loud schema validation as one
        # that came through the loader. (Import is local to keep this
        # module importable in isolation.)
        from cassette_schema import validate_cassette
        validate_cassette(cassette)
        # This engine is a call-analytics pipeline: it reads the
        # routing surface (_infer_intent_to_label) and the telephony
        # judgment surface (score_outcome_quality, diagnose_abandonment).
        # A cassette without those capabilities is refused HERE, with a
        # legible error, instead of failing mid-call -- fail-closed at
        # the door. Kernel-only domains are judged through
        # episode.judge_episode / explain_episode, not this class.
        from cassette_capabilities import (
            CAPABILITY_ROUTING_TOPOLOGY,
            CAPABILITY_TELEPHONY_INGEST,
            require_capabilities,
        )
        require_capabilities(
            cassette,
            (CAPABILITY_TELEPHONY_INGEST, CAPABILITY_ROUTING_TOPOLOGY),
            consumer="SentinelCore",
        )
        self.cassette = cassette

    def infer_intent(self, journey: List[str], first_queue_chosen: str) -> IntentSignal:
        """Infer caller intent from first queue choice, via the cassette"""
        
        raw_intent = self.cassette._infer_intent_to_label(first_queue_chosen, {})
        confidence = 0.85 if raw_intent != "UNKNOWN" else 0.3
        
        reasoning = f"Caller routed to {first_queue_chosen}"
        if len(journey) > 2:
            if journey[1] == first_queue_chosen:
                reasoning += " (direct match)"
                confidence = 0.95
        
        return IntentSignal(
            queue_chosen=first_queue_chosen,
            confidence=confidence,
            reasoning=reasoning,
            classification=raw_intent,
        )
    
    # Translation from cassette-native tier labels to the core's
    # structural enum. The cassette owns the cutoffs that PRODUCE a
    # label; the core only translates the label it is handed. An
    # unknown label is a contract violation between cassette and core
    # and fails loudly rather than being rounded to a nearby tier.
    _TIER_MAP = {
        "excellent": OutcomeQuality.EXCELLENT,
        "good": OutcomeQuality.GOOD,
        "poor": OutcomeQuality.POOR,
        "failed": OutcomeQuality.FAILED,
    }

    @staticmethod
    def _emotion_as_dict(emotional_state) -> Dict:
        """Normalize at the boundary: the pipeline speaks objects
        (EmotionalState), cassettes speak primitives (dicts)."""
        if emotional_state is None:
            return {}
        if isinstance(emotional_state, dict):
            return emotional_state
        out = {}
        for key in ("frustration", "patience", "trust"):
            value = getattr(emotional_state, key, None)
            if value is not None:
                out[key] = float(value)
        return out

    @staticmethod
    def _friction_as_dicts(friction_events) -> List[Dict]:
        """Normalize friction events (objects or dicts) to plain dicts
        so cassettes never need to know the pipeline's event classes."""
        out = []
        for event in friction_events or []:
            if isinstance(event, dict):
                out.append(event)
                continue
            entry = {}
            for key in ("type", "node", "severity"):
                value = getattr(event, key, None)
                if value is not None:
                    entry[key] = value
            out.append(entry)
        return out

    def score_outcome_quality(self, resolved: bool, resolution_time: float,
                             friction_count: int, emotional_state) -> QualityScore:
        """Score quality of outcome (Path A: the cassette owns the judgment).

        The cassette computes the score AND picks the tier with its own
        domain cutoffs. The core normalizes inputs on the way down,
        translates the tier label into OutcomeQuality on the way back,
        and wraps the verdict with the structural fields callers rely
        on. There is deliberately no scoring arithmetic left in this
        class -- a second scorer here is exactly the two-places-that-
        can-quietly-disagree problem the cassette system exists to end.
        """
        emotion_data = self._emotion_as_dict(emotional_state)
        result = self.cassette.score_outcome_quality(
            resolved, resolution_time, friction_count, emotion_data
        )
        try:
            tier = self._TIER_MAP[result.tier]
        except KeyError:
            raise ValueError(
                f"Cassette returned unknown quality tier {result.tier!r}; "
                f"expected one of {sorted(self._TIER_MAP)}"
            )
        return QualityScore(
            resolution_time=resolution_time,
            friction_count=friction_count,
            emotional_deterioration=float(emotion_data.get("frustration", 0.0)),
            overall_score=float(result.score),
            quality_tier=tier,
        )
    
    # Structural interventions the core can point at WITHOUT domain
    # knowledge: a wait-shaped cause implicates where the caller stood
    # (last node); a repeat-shaped cause implicates where they were
    # sent from (previous node). Causes a cassette names in its own
    # vocabulary get no structural guess -- None, not a wrong answer.
    _INTERVENTION_AT_LAST_NODE = {"long_wait"}
    _INTERVENTION_AT_PREVIOUS_NODE = {"repeat_routing"}

    def diagnose_abandonment(self, journey: List[str], friction_events: List,
                            emotional_state, resolved: bool) -> AbandonmentDiagnosis:
        """Diagnose why abandonment occurred (Path A: cassette owns the why).

        The cassette names the cause in its own domain vocabulary; the
        core wraps that verdict with the structural fields -- which
        node in the journey to look at -- that require no domain
        knowledge. The abandonment rules themselves no longer live here.
        """
        verdict = self.cassette.diagnose_abandonment(
            journey,
            self._friction_as_dicts(friction_events),
            self._emotion_as_dict(emotional_state),
            resolved,
        )

        primary = verdict.get("reason", "unknown")

        intervention = None
        if primary in self._INTERVENTION_AT_LAST_NODE and journey:
            intervention = journey[-1]
        elif primary in self._INTERVENTION_AT_PREVIOUS_NODE and len(journey) > 1:
            intervention = journey[-2]

        return AbandonmentDiagnosis(
            primary_cause=primary,
            contributing_factors=list(verdict.get("factors", [])),
            intervention_point=intervention,
            confidence=float(verdict.get("confidence", 0.0)),
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
