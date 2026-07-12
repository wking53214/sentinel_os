"""
Cassette Harness - Boom box that accepts any cassette

Routes all domain logic through loaded cassette
"""

import os
from typing import Dict, Optional
from cassette_loader import CassetteLoader
from cassette_interface import Cassette
from cassette_schema import validate_cassette
from governance.friction_core import compute_friction
from resilient_harness import ResilientHarness
from operational_resilience import setup_logging

logger = setup_logging("CassetteHarness")

class CassetteHarness:
    """Universal harness: same boom box, different cassettes"""
    
    def __init__(self, cassette_domain: str, config: Dict):
        """
        Initialize harness with specific cassette
        
        Args:
            cassette_domain: "ivr", "banking", "healthcare", etc.
            config: Standard configuration (postgres, claude, etc.)
        """
        
        self.cassette_domain = cassette_domain
        self.config = config
        
        # Load cassettes (with auto-discovery)
        loader = CassetteLoader()
        loader.load_all_cassettes()
        
        # Get specific cassette
        try:
            self.cassette = loader.get_cassette_for_domain(cassette_domain)
            logger.info(f"Loaded cassette for domain: {cassette_domain}")
        except Exception as e:
            logger.error(f"Failed to load cassette: {e}")
            raise
        
        # Initialize resilient harness (boom box)
        self.harness = ResilientHarness(config)
        
        # Store cassette metadata
        self.cassette_config = self.cassette.get_config()
        logger.info(f"Cassette: {self.cassette_config.name} v{self.cassette_config.version}")
    
    def process_call(self, call_data: Dict) -> Dict:
        """
        Process call using loaded cassette
        
        Same boom box logic, cassette-specific domain rules
        """
        
        if not self.harness.harness:
            raise Exception("Harness not initialized")
        
        try:
            # 1. Parse call (generic)
            caller_id = call_data.get("sid", "unknown")
            
            # 2. Extract journey (generic)
            journey = self._extract_journey(call_data)
            
            # 3. CASSETTE: Infer intent (domain-specific)
            intent = self.cassette.infer_intent(journey[1] if len(journey) > 1 else "", call_data)
            
            # 4. CASSETTE: Score quality (domain-specific)
            resolved = call_data.get("status") == "completed"
            duration = float(call_data.get("duration", 0))
            friction_count = self._count_friction(call_data, journey)
            emotion = {"frustration": 0.3}  # Placeholder
            
            quality = self.cassette.score_outcome_quality(
                resolved, duration, friction_count, emotion
            )
            
            # 5. CASSETTE: Diagnose abandonment (domain-specific)
            if not resolved:
                diagnosis = self.cassette.diagnose_abandonment(
                    journey, [], emotion, resolved
                )
            else:
                diagnosis = None
            
            # 6. Record metrics (boom box)
            self.harness.process_call(call_data)
            
            # 7. CASSETTE: Compute reward (domain-specific)
            reward = self.cassette.compute_reward({
                "resolved": resolved,
                "wait_time": duration,
                "friction_count": friction_count
            })
            
            return {
                "caller_id": caller_id,
                "domain": self.cassette_domain,
                "intent": intent,
                "quality_tier": quality.tier,
                "quality_score": quality.score,
                "diagnosis": diagnosis,
                "reward": reward,
                "resolved": resolved,
                "cassette": self.cassette_config.name
            }
        
        except Exception as e:
            logger.error(f"Call processing failed: {e}")
            raise
    
    def process_batch(self, calls: list) -> Dict:
        """Process batch using cassette"""
        
        results = []
        for call in calls:
            try:
                result = self.process_call(call)
                results.append(result)
            except Exception as e:
                logger.error(f"Call {call.get('sid')} failed: {e}")
                results.append({"error": str(e)})
        
        return {
            "domain": self.cassette_domain,
            "cassette": self.cassette_config.name,
            "calls_processed": len(results),
            "results": results,
            "metrics": self.harness.harness.metrics.get_summary() if self.harness.harness else {}
        }
    
    def get_cassette_info(self) -> Dict:
        """Get loaded cassette information"""
        return {
            "name": self.cassette_config.name,
            "version": self.cassette_config.version,
            "domain": self.cassette_config.domain,
            "description": self.cassette_config.description,
            "queues": list(self.cassette.get_queue_definitions().keys()),
            "friction_thresholds": self.cassette.get_friction_thresholds(),
            "healing_bounds": self.cassette.get_healing_bounds(),
        }
    
    def get_metrics(self) -> str:
        """Export metrics"""
        return self.harness.export_metrics()
    
    def get_health(self) -> Dict:
        """Get health status"""
        return self.harness.get_health()
    
    def _extract_journey(self, call_data: Dict) -> list:
        """Extract call journey from data"""
        return [
            "root",
            "intent_menu",
            next((q for q in self.cassette.get_queue_definitions().keys() 
                 if q in call_data.get("to", "").lower()), "unknown"),
            "exit"
        ]
    
    def _count_friction(self, call_data: Dict, journey: list) -> int:
        """Count friction events through the unified rule.

        The threshold is the cassette's declared long_wait_threshold --
        read via schema validation, with NO literal fallback. A cassette
        that cannot state its threshold is a halt, not a silent 30.
        """
        duration = float(call_data.get("duration", 0))
        long_wait = validate_cassette(self.cassette).float_value("long_wait_threshold")
        return compute_friction(duration, long_wait)
    
    def shutdown(self):
        """Cleanup"""
        if self.harness:
            self.harness.shutdown()
