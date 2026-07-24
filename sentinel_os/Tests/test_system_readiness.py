import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def test_complete_system_ready():
    print("\n[TEST 1] Complete system components present")
    
    # Check all production files exist
    files = [
        "production_harness.py",
        "api_server.py",
        "requirements.txt",
        "docker-compose-prod.yml",
        "start_production.sh",
        "smoke_test.sh",
        "load_test_live.py",
    ]
    
    missing = []
    for f in files:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), f)
        if not os.path.exists(path):
            missing.append(f)
    
    assert len(missing) == 0, f"Missing files: {missing}"
    print(f"  ✓ PASSED - All {len(files)} production files present")
    return True

def test_imports():
    print("\n[TEST 2] All imports available")
    
    try:
        # These 6 imports exist to prove the modules import cleanly -- that
        # IS this test's job (test_imports), not incidental. F401 (unused
        # import) is a false positive here, not dead code -- noqa'd rather
        # than deleted, which would silently turn this into a no-op test.
        from production_harness import IcebergProductionHarness  # noqa: F401
        from twilio_log_ingestion import TwilioLogParser  # noqa: F401
        from metrics_prometheus import PrometheusMetrics  # noqa: F401
        from claude_governance_api import ClaudeGovernanceDecider  # noqa: F401
        from observe_perceive_core import ObserveCore  # noqa: F401
        from sentinel_core import SentinelCore  # noqa: F401
        print("  ✓ PASSED - All imports successful")
        return True
    except ImportError as e:
        print(f"  ✗ FAILED - Import error: {e}")
        return False

def test_harness_initialization():
    print("\n[TEST 3] Production harness initializes")
    
    try:
        from production_harness import IcebergProductionHarness
        
        config = {
            "postgres_host": None,
            "claude_api_key": None,
            "twilio_account_sid": None,
        }
        
        harness = IcebergProductionHarness(config, require_cassette_binding=False)
        
        assert harness.metrics is not None
        assert harness.observer is not None
        assert harness.sentinel is not None
        
        print("  ✓ PASSED - Harness initialized successfully")
        return True
    except Exception as e:
        print(f"  ✗ FAILED - {e}")
        return False

def test_e2e_call_processing():
    print("\n[TEST 4] End-to-end call processing")
    
    try:
        from production_harness import IcebergProductionHarness
        
        config = {
            "postgres_host": None,
            "claude_api_key": None,
            "twilio_account_sid": None,
        }
        
        harness = IcebergProductionHarness(config, require_cassette_binding=False)
        
        # Process test call
        call = {
            "sid": "CATEST001",
            "status": "completed",
            "duration": 150,
            "from": "+16125555555",
            "to": "+billing"
        }
        
        result = harness.process_call(call)
        
        assert result.get("resolved") is not None
        assert result.get("quality") is not None
        assert "caller_id" in result
        
        print(f"  ✓ PASSED - Call processed: {result['quality']} quality")
        return True
    except Exception as e:
        print(f"  ✗ FAILED - {e}")
        import traceback
        traceback.print_exc()
        return False

def test_batch_processing():
    print("\n[TEST 5] Batch call processing")
    
    try:
        from production_harness import IcebergProductionHarness
        
        config = {
            "postgres_host": None,
            "claude_api_key": None,
            "twilio_account_sid": None,
        }
        
        harness = IcebergProductionHarness(config, require_cassette_binding=False)
        
        calls = [
            {"sid": "CAB001", "status": "completed", "duration": 120, "from": "+1111", "to": "+billing"},
            {"sid": "CAB002", "status": "no-answer", "duration": 30, "from": "+2222", "to": "+tech"},
            {"sid": "CAB003", "status": "completed", "duration": 180, "from": "+3333", "to": "+sales"},
        ]
        
        summary = harness.process_batch(calls)
        
        assert summary["calls_processed"] == 3
        assert summary["calls_resolved"] == 2
        assert summary["calls_abandoned"] == 1
        
        print(f"  ✓ PASSED - Batch processed: 3 calls, {summary['abandonment_rate']*100:.0f}% abandonment")
        return True
    except Exception as e:
        print(f"  ✗ FAILED - {e}")
        import traceback
        traceback.print_exc()
        return False

def test_prometheus_export():
    print("\n[TEST 6] Prometheus metrics export")
    
    try:
        from production_harness import IcebergProductionHarness
        
        config = {
            "postgres_host": None,
            "claude_api_key": None,
            "twilio_account_sid": None,
        }
        
        harness = IcebergProductionHarness(config, require_cassette_binding=False)
        
        # Process some calls
        harness.process_call({"sid": "CAP001", "status": "completed", "duration": 120, "from": "+1111", "to": "+test"})
        harness.process_call({"sid": "CAP002", "status": "failed", "duration": 10, "from": "+2222", "to": "+test"})
        
        metrics_text = harness.export_metrics()
        
        assert "iceberg_calls_total" in metrics_text
        assert "iceberg_calls_resolved" in metrics_text
        assert "iceberg_abandonment_rate" in metrics_text
        assert len(metrics_text) > 500
        
        print(f"  ✓ PASSED - Exported {len(metrics_text)} bytes of Prometheus metrics")
        return True
    except Exception as e:
        print(f"  ✗ FAILED - {e}")
        return False

def main():
    print("\n" + "="*70)
    print("COMPLETE SYSTEM READINESS TESTS")
    print("="*70)
    
    tests = [
        ("System files present", test_complete_system_ready),
        ("Imports available", test_imports),
        ("Harness initialization", test_harness_initialization),
        ("End-to-end call", test_e2e_call_processing),
        ("Batch processing", test_batch_processing),
        ("Prometheus export", test_prometheus_export),
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
    print(f"SYSTEM READINESS: {passed}/{total} tests passed")
    print("="*70)
    
    if passed == total:
        print("\n✓ SYSTEM IS PRODUCTION READY")
        print("\nTo start production:")
        print("  ./start_production.sh")
        print("\nTo run smoke tests:")
        print("  ./smoke_test.sh")
        print("\nTo run load test:")
        print("  python3 load_test_live.py")
    
    print()
    return all(results)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
