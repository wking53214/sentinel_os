"""
Cassette Interface - Abstract base for domain-specific implementations

Allows any industry to plug in their own cassette without touching boom box
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional
from enum import Enum

@dataclass
class CassetteConfig:
    """Configuration for a cassette"""
    name: str
    version: str
    description: str
    domain: str  # "ivr", "banking", "healthcare", etc.

@dataclass(frozen=True)
class QualityResult:
    """A cassette's domain judgment of one outcome: score AND tier.

    The cassette owns both halves -- the score arithmetic and the
    cutoffs that turn a score into a tier label. Two domains may judge
    the same call differently by design (banking's "excellent" bar is
    not IVR's bar). Consumers that need the core's OutcomeQuality enum
    translate the tier label; they never re-derive a tier from the
    score with their own cutoffs, because then two places would own
    the same judgment and could quietly disagree.
    """
    score: float  # 0.0-1.0, cassette-computed
    tier: str  # "excellent" | "good" | "poor" | "failed"

class Cassette(ABC):
    """Abstract base: all cassettes implement this"""
    
    @abstractmethod
    def get_config(self) -> CassetteConfig:
        """Return cassette metadata"""
        pass
    
    @abstractmethod
    def get_queue_definitions(self) -> Dict[str, Dict]:
        """Return queue names and properties"""
        pass
    
    @abstractmethod
    def infer_intent(self, queue_name: str, caller_data: Dict) -> str:
        """Map queue choice to caller intent"""
        pass
    
    @abstractmethod
    def score_outcome_quality(self, resolved: bool, duration: float, 
                             friction_count: int, emotion_data: Dict) -> QualityResult:
        """Score the outcome with this domain's own rules.

        Returns QualityResult(score, tier). The cassette owns its tier
        cutoffs; callers read .tier and .score, never re-bucket .score.
        """
        pass
    
    @abstractmethod
    def diagnose_abandonment(self, journey: List[str], friction: List, 
                            emotion: Dict, resolved: bool) -> Dict:
        """Diagnose why call abandoned"""
        pass
    
    @abstractmethod
    def get_friction_thresholds(self) -> Dict[str, float]:
        """Domain-specific friction detection thresholds"""
        pass

    @abstractmethod
    def get_governance_parameters(self) -> Dict[str, Dict]:
        """The typed governance declaration this domain runs under.

        Shape and required parameters are defined by cassette_schema
        (SCHEMA_VERSION). Every value the engine reads on the
        governance path comes from here -- validated on load, read at
        decision time. See cassette_schema.validate_cassette.
        """
        pass
    
    @abstractmethod
    def get_healing_bounds(self) -> Dict[str, tuple]:
        """Domain-specific parameter bounds for self-healing"""
        pass
    
    @abstractmethod
    def compute_reward(self, outcome: Dict) -> float:
        """RL reward signal for this domain"""
        pass
    
    @abstractmethod
    def validate(self) -> bool:
        """Verify cassette is valid and complete"""
        pass

class CassetteRegistry:
    """Load and manage multiple cassettes"""
    
    def __init__(self):
        self.cassettes = {}
    
    def register(self, cassette: Cassette):
        """Register a cassette (fail-loud).

        Full schema validation runs here, not just the cassette's own
        self-check: an invalid cassette raises CassetteValidationError
        carrying the complete violation list. Registration is a load
        path, and no load path admits an unvalidated cassette.
        """
        from cassette_schema import validate_cassette

        config = cassette.get_config()
        key = f"{config.domain}:{config.name}"

        validate_cassette(cassette)

        self.cassettes[key] = cassette
    
    def get(self, domain: str) -> Cassette:
        """Get cassette by domain"""
        for key, cassette in self.cassettes.items():
            if key.startswith(domain):
                return cassette
        raise KeyError(f"No cassette found for domain: {domain}")
    
    def list_all(self) -> Dict:
        """List all registered cassettes"""
        return {
            key: cassette.get_config() for key, cassette in self.cassettes.items()
        }
