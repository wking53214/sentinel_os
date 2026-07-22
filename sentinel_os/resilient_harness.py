"""
Resilient Harness - Production harness with error handling, logging, health checks

Wraps production_harness.py with operational hardening
"""

from typing import Dict
from operational_resilience import (
    setup_logging, CircuitBreaker, retry_with_backoff,
    HealthChecker
)
from production_harness import IcebergProductionHarness

logger = setup_logging("ResilientHarness")

class ResilientHarness:
    """Production harness with operational resilience"""
    
    def __init__(self, config: Dict, require_cassette_binding: bool = True):
        self.config = config
        self.require_cassette_binding = require_cassette_binding
        self.harness = None
        self.circuit_breaker = CircuitBreaker(failure_threshold=5, timeout=60)
        self.health_checker = HealthChecker()
        
        # Setup health checks
        self.health_checker.register_component("harness", self._check_harness)
        self.health_checker.register_component("metrics", self._check_metrics)
        self.health_checker.register_component("ledger", self._check_ledger)
        
        self._initialize_harness()
    
    def _initialize_harness(self):
        """Initialize harness with error handling"""
        try:
            self.harness = IcebergProductionHarness(
                self.config,
                require_cassette_binding=self.require_cassette_binding,
            )
            logger.info("Harness initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize harness: {e}")
            self.harness = None
    
    def _check_harness(self) -> bool:
        return self.harness is not None
    
    def _check_metrics(self) -> bool:
        return self.harness is not None and self.harness.metrics is not None
    
    def _check_ledger(self) -> bool:
        return self.harness is not None and self.harness.ledger is not None
    
    @retry_with_backoff(max_attempts=3, backoff_factor=2.0)
    def process_call(self, call: Dict) -> Dict:
        """Process call with retry and fallback"""
        if self.harness is None:
            raise Exception("Harness not initialized")
        
        return self.circuit_breaker.call(self.harness.process_call, call)
    
    @retry_with_backoff(max_attempts=3, backoff_factor=2.0)
    def process_batch(self, calls: list) -> Dict:
        """Process batch with retry and fallback"""
        if self.harness is None:
            raise Exception("Harness not initialized")
        
        return self.circuit_breaker.call(self.harness.process_batch, calls)
    
    def export_metrics(self) -> str:
        """Export metrics with fallback"""
        if self.harness is None:
            return "# No metrics available - harness not initialized\n"
        
        return self.harness.export_metrics()
    
    def get_health(self) -> Dict:
        """Get detailed health status"""
        return self.health_checker.check_all()
    
    def verify_ledger(self) -> Dict:
        """Verify ledger with fallback"""
        if self.harness is None or not self.harness.ledger:
            return {"available": False, "reason": "Ledger not connected"}
        
        try:
            return self.harness.verify_ledger()
        except Exception as e:
            logger.error(f"Ledger verification failed: {e}")
            return {"available": False, "error": str(e)}
    
    def shutdown(self):
        """Graceful shutdown"""
        if self.harness:
            try:
                self.harness.shutdown()
                logger.info("Harness shutdown complete")
            except Exception as e:
                logger.error(f"Error during shutdown: {e}")
