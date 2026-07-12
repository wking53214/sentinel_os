"""
Operational Resilience - Error handling, logging, circuit breakers, retries

Hardens production system against failures
"""

import logging
import json
import time
from functools import wraps
from typing import Callable, Any, Optional
from enum import Enum
from datetime import datetime, timedelta, timezone

# JSON structured logging
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_obj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        
        if hasattr(record, "extra_data"):
            log_obj.update(record.extra_data)
        
        return json.dumps(log_obj)

def setup_logging(name: str, level=logging.INFO):
    """Setup structured JSON logging"""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    handler = logging.StreamHandler()
    formatter = JSONFormatter()
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger

class CircuitBreakerState(Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery

class CircuitBreaker:
    """Circuit breaker pattern: fail fast when service is down"""
    
    def __init__(self, failure_threshold: int = 5, timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitBreakerState.CLOSED
        self.logger = setup_logging("CircuitBreaker")
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection"""
        
        if self.state == CircuitBreakerState.OPEN:
            if self._should_attempt_reset():
                self.state = CircuitBreakerState.HALF_OPEN
                self.logger.info("Circuit breaker entering HALF_OPEN", extra={"extra_data": {"action": "reset_attempt"}})
            else:
                raise Exception("Circuit breaker is OPEN")
        
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise
    
    def _on_success(self):
        self.failure_count = 0
        if self.state == CircuitBreakerState.HALF_OPEN:
            self.state = CircuitBreakerState.CLOSED
            self.logger.info("Circuit breaker recovered to CLOSED")
    
    def _on_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitBreakerState.OPEN
            self.logger.error(f"Circuit breaker opened after {self.failure_count} failures", 
                            extra={"extra_data": {"failures": self.failure_count}})
    
    def _should_attempt_reset(self) -> bool:
        return (self.last_failure_time and 
                time.time() - self.last_failure_time >= self.timeout)

def retry_with_backoff(max_attempts: int = 3, backoff_factor: float = 2.0, max_wait: int = 60):
    """Decorator: retry with exponential backoff"""
    
    def decorator(func: Callable) -> Callable:
        logger = setup_logging(func.__module__)
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            last_exception = None
            
            while attempt < max_attempts:
                try:
                    result = func(*args, **kwargs)
                    if attempt > 0:
                        logger.info(f"Retry successful after {attempt} attempts", 
                                  extra={"extra_data": {"function": func.__name__, "attempts": attempt}})
                    return result
                except Exception as e:
                    last_exception = e
                    attempt += 1
                    
                    if attempt < max_attempts:
                        wait_time = min(backoff_factor ** (attempt - 1), max_wait)
                        logger.warning(f"Attempt {attempt} failed, retrying in {wait_time:.1f}s: {e}",
                                     extra={"extra_data": {"function": func.__name__, "attempt": attempt, "wait": wait_time}})
                        time.sleep(wait_time)
                    else:
                        logger.error(f"All {max_attempts} attempts failed: {e}",
                                   extra={"extra_data": {"function": func.__name__, "attempts": max_attempts}})
            
            raise last_exception
        
        return wrapper
    return decorator

class HealthChecker:
    """Detailed health checks with component status"""
    
    def __init__(self):
        self.components = {}
        self.logger = setup_logging("HealthChecker")
    
    def register_component(self, name: str, check_fn: Callable):
        """Register a health check function"""
        self.components[name] = check_fn
    
    def check_all(self) -> dict:
        """Run all health checks"""
        status = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "overall": "healthy",
            "components": {}
        }
        
        for name, check_fn in self.components.items():
            try:
                is_healthy = check_fn()
                status["components"][name] = {
                    "status": "healthy" if is_healthy else "unhealthy",
                    "checked_at": datetime.now(timezone.utc).isoformat()
                }
                
                if not is_healthy:
                    status["overall"] = "degraded"
            except Exception as e:
                status["components"][name] = {
                    "status": "error",
                    "error": str(e),
                    "checked_at": datetime.now(timezone.utc).isoformat()
                }
                status["overall"] = "unhealthy"
                self.logger.error(f"Health check failed for {name}: {e}")
        
        return status

class GracefulDegradation:
    """Fallback strategies when services fail"""
    
    @staticmethod
    def fallback_to_simulation(original_fn: Callable) -> Callable:
        """Fallback to simulation mode if real system unavailable"""
        
        logger = setup_logging("GracefulDegradation")
        
        @wraps(original_fn)
        def wrapper(*args, **kwargs):
            try:
                return original_fn(*args, **kwargs)
            except Exception as e:
                logger.warning(f"Real system failed, falling back to simulation: {e}",
                             extra={"extra_data": {"function": original_fn.__name__}})
                # Return sensible default instead of crashing
                return {"status": "fallback", "error": str(e)}
        
        return wrapper

# Alert rules for Prometheus
ALERT_RULES = """
groups:
- name: iceberg_alerts
  interval: 30s
  rules:
  - alert: HighAbandonmentRate
    expr: iceberg_abandonment_rate > 0.25
    for: 5m
    annotations:
      summary: "High call abandonment rate ({{ $value | humanizePercentage }})"
  
  - alert: HighDriftDetections
    expr: rate(iceberg_drift_detections[5m]) > 0.1
    for: 5m
    annotations:
      summary: "Frequent drift detections ({{ $value | humanize }}/sec)"
  
  - alert: HighRL_Loss
    expr: iceberg_rl_loss > 1.0
    for: 5m
    annotations:
      summary: "High RL training loss ({{ $value }})"
  
  - alert: GovernanceActionBacklog
    expr: iceberg_governance_actions > 100
    for: 5m
    annotations:
      summary: "Large backlog of governance actions ({{ $value }})"
"""

def export_alert_rules() -> str:
    """Export Prometheus alert rules"""
    return ALERT_RULES
