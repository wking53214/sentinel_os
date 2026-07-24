import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from operational_resilience import CircuitBreaker, HealthChecker, setup_logging
from grafana_dashboard import GrafanaDashboard, generate_dashboard_json
from resilient_harness import ResilientHarness

def test_circuit_breaker():
    print("\n[TEST 1] Circuit breaker pattern")
    
    breaker = CircuitBreaker(failure_threshold=3, timeout=5)
    
    def failing_func():
        raise Exception("Service down")
    
    def working_func():
        return "OK"
    
    # Fail 3 times
    failures = 0
    for i in range(3):
        try:
            breaker.call(failing_func)
        except Exception:
            failures += 1
    
    assert failures == 3
    assert breaker.state.value == "open", f"Should be open, got {breaker.state}"
    
    # Try to call while open
    try:
        breaker.call(working_func)
        assert False, "Should have raised"
    except Exception as e:
        assert "OPEN" in str(e)
    
    print(f"  ✓ PASSED - Circuit breaker opened after {failures} failures")
    return True

def test_health_checker():
    print("\n[TEST 2] Health checker")
    
    checker = HealthChecker()
    
    check_count = 0
    
    def check_good():
        nonlocal check_count
        check_count += 1
        return True
    
    def check_bad():
        return False
    
    checker.register_component("good", check_good)
    checker.register_component("bad", check_bad)
    
    status = checker.check_all()
    
    assert check_count == 1
    assert status["overall"] == "degraded"
    assert status["components"]["good"]["status"] == "healthy"
    assert status["components"]["bad"]["status"] == "unhealthy"
    
    print("  ✓ PASSED - Health checker detected degraded system")
    return True

def test_logging():
    print("\n[TEST 3] Structured JSON logging")
    
    logger = setup_logging("TestLogger")
    
    assert logger is not None
    assert logger.name == "TestLogger"
    
    # Log a message (won't raise)
    logger.info("Test message")
    
    print("  ✓ PASSED - Structured logging initialized")
    return True

def test_grafana_dashboard():
    print("\n[TEST 4] Grafana dashboard generation")
    
    dashboard = GrafanaDashboard()
    config = dashboard.build()
    
    assert "dashboard" in config
    assert config["dashboard"]["title"] == "Iceberg IVR Platform - Real-Time Monitoring"
    assert len(config["dashboard"]["panels"]) > 0
    
    # Test JSON export
    json_str = generate_dashboard_json()
    assert len(json_str) > 100
    assert "Iceberg IVR Platform" in json_str
    
    print(f"  ✓ PASSED - Generated Grafana dashboard with {len(config['dashboard']['panels'])} panels")
    return True

def test_resilient_harness_initialization():
    print("\n[TEST 5] Resilient harness initialization")
    
    config = {
        "postgres_host": None,
        "claude_api_key": None,
        "twilio_account_sid": None,
    }
    
    resilient = ResilientHarness(config, require_cassette_binding=False)
    
    assert resilient is not None
    assert resilient.harness is not None
    assert resilient.health_checker is not None
    
    health = resilient.get_health()
    assert "overall" in health
    assert "components" in health
    
    print("  ✓ PASSED - Resilient harness initialized, health check working")
    return True

def test_resilient_process_call():
    print("\n[TEST 6] Resilient harness processes calls")
    
    config = {
        "postgres_host": None,
        "claude_api_key": None,
        "twilio_account_sid": None,
    }
    
    resilient = ResilientHarness(config, require_cassette_binding=False)
    
    call = {
        "sid": "CATEST001",
        "status": "completed",
        "duration": 120,
        "from": "+1234",
        "to": "+test"
    }
    
    result = resilient.process_call(call)
    
    assert result is not None
    assert "resolved" in result
    assert "quality" in result
    
    print("  ✓ PASSED - Resilient harness processed call successfully")
    return True

def test_metrics_export_with_fallback():
    print("\n[TEST 7] Metrics export with fallback")
    
    config = {
        "postgres_host": None,
        "claude_api_key": None,
        "twilio_account_sid": None,
    }
    
    resilient = ResilientHarness(config, require_cassette_binding=False)
    
    metrics = resilient.export_metrics()
    
    assert metrics is not None
    assert len(metrics) > 0
    assert "iceberg_calls_total" in metrics
    
    print(f"  ✓ PASSED - Exported {len(metrics)} bytes of metrics")
    return True

def main():
    print("\n" + "="*70)
    print("OPERATIONAL RESILIENCE & OBSERVABILITY TESTS")
    print("="*70)
    
    tests = [
        ("Circuit breaker", test_circuit_breaker),
        ("Health checker", test_health_checker),
        ("Structured logging", test_logging),
        ("Grafana dashboard", test_grafana_dashboard),
        ("Resilient harness init", test_resilient_harness_initialization),
        ("Resilient call processing", test_resilient_process_call),
        ("Metrics export", test_metrics_export_with_fallback),
    ]
    
    results = []
    for name, test_fn in tests:
        try:
            results.append(test_fn())
        except Exception as e:
            print(f"  ✗ FAILED: {e}")
            import traceback
            traceback.print_exc()
            results.append(False)
    
    passed = sum(results)
    total = len(results)
    
    print("\n" + "="*70)
    print(f"RESILIENCE & OBSERVABILITY RESULTS: {passed}/{total} tests passed")
    print("="*70 + "\n")
    
    return all(results)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
