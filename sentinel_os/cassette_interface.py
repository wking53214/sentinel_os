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
                             friction_count: int, emotion_data: Dict) -> str:
        """Return quality tier: excellent/good/poor/failed"""
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
        """Register a cassette"""
        config = cassette.get_config()
        key = f"{config.domain}:{config.name}"
        
        if not cassette.validate():
            raise Exception(f"Cassette {key} validation failed")
        
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
