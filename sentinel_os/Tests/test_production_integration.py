import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from metrics_prometheus import PrometheusMetrics
from twilio_log_ingestion import TwilioLogParser, IcebergJourney

def test_prometheus_metrics():
    print("\n[TEST 1] Prometheus metrics export")
    metrics = PrometheusMetrics()
    
    # Simulate call activity
    metrics.record_call(wait_time=25.0, resolved=True, resolution_time=120.0)
    metrics.record_call(wait_time=45.0, resolved=False, resolution_time=300.0)
    metrics.record_drift_detection("billing_queue", 0.45)
    metrics.record_governance_action("heal")
    metrics.record_rl_loss(0.4532)
    metrics.record_queue_state("billing_queue", waiting=5, staffed=3)
    
    # Export
    prometheus_output = metrics.export_prometheus()
    
    assert "iceberg_calls_total 2" in prometheus_output
    assert "iceberg_calls_resolved 1" in prometheus_output
    assert "iceberg_calls_abandoned 1" in prometheus_output
    assert "iceberg_abandonment_rate" in prometheus_output
    assert "iceberg_drift_detections 1" in prometheus_output
    assert "iceberg_governance_actions 1" in prometheus_output
    
    summary = metrics.get_summary()
    assert summary["calls_total"] == 2
    assert summary["governance_actions"] == 1
    
    print(f"  ✓ PASSED - Metrics exported, abandonment_rate={summary['abandonment_rate']:.2f}")
    return True

def test_twilio_log_parsing():
    print("\n[TEST 2] Twilio log parsing")
    parser = TwilioLogParser()
    
    # Mock Twilio record
    twilio_record = {
        "sid": "CA1234567890abcdef",
        "to": "+16125551234",
        "from": "+16125555555",
        "start_time": 1688000000,
        "duration": 180,
        "status": "completed",
        "recording_url": "https://example.com/recording.wav",
        "price": 0.02
    }
    
    journey = parser.parse_call_log(twilio_record)
    
    assert journey is not None
    assert journey.resolved == True
    assert journey.caller_id.startswith("twilio_")
    assert "intent_menu" in journey.journey
    assert "agent_a" in journey.journey
    assert journey.friction_count >= 0
    
    print(f"  ✓ PASSED - Parsed Twilio call: journey={journey.journey}")
    return True

def test_twilio_abandonment_parsing():
    print("\n[TEST 3] Twilio abandonment detection")
    parser = TwilioLogParser()
    
    # Abandoned call
    twilio_record = {
        "sid": "CA9876543210fedcba",
        "to": "+16125551234",
        "from": "+16125555555",
        "start_time": 1688000100,
        "duration": 30,
        "status": "no-answer",
        "recording_url": None,
        "price": 0.00
    }
    
    journey = parser.parse_call_log(twilio_record)
    
    assert journey is not None
    assert journey.resolved == False
    assert journey.abandonment_reason == "no_answer"
    assert journey.friction_count > 0
    
    print(f"  ✓ PASSED - Detected abandonment: reason={journey.abandonment_reason}")
    return True

def test_full_production_integration():
    print("\n[TEST 4] Full production integration: metrics + Twilio + governance")
    
    metrics = PrometheusMetrics()
    parser = TwilioLogParser()
    
    # Simulate batch of real Twilio calls
    twilio_records = [
        {"sid": "CA001", "status": "completed", "duration": 120, "from": "+1111"},
        {"sid": "CA002", "status": "completed", "duration": 150, "from": "+2222"},
        {"sid": "CA003", "status": "no-answer", "duration": 30, "from": "+1111"},
        {"sid": "CA004", "status": "completed", "duration": 200, "from": "+3333"},
        {"sid": "CA005", "status": "failed", "duration": 10, "from": "+2222"},
    ]
    
    for record in twilio_records:
        journey = parser.parse_call_log(record)
        if journey:
            metrics.record_call(
                wait_time=journey.total_duration * 0.3,
                resolved=journey.resolved,
                resolution_time=journey.total_duration
            )
            if journey.friction_count > 0:
                metrics.record_governance_action("analyze")
    
    summary = metrics.get_summary()
    
    assert summary["calls_total"] == 5
    assert summary["calls_resolved"] == 3
    assert summary["calls_abandoned"] == 2
    assert summary["abandonment_rate"] == 0.4
    
    # Verify Prometheus export works
    prometheus_text = metrics.export_prometheus()
    assert len(prometheus_text) > 100
    assert "iceberg_calls_total" in prometheus_text
    
    print(f"  ✓ PASSED - Full integration: 5 calls processed")
    print(f"             Resolution: {summary['calls_resolved']}/5")
    print(f"             Abandonment: {summary['abandonment_rate']*100:.0f}%")
    return True

def main():
    print("\n" + "="*70)
    print("PRODUCTION INTEGRATION TESTS - PostgreSQL + Prometheus + Twilio + Claude")
    print("="*70)
    
    tests = [
        ("Prometheus metrics", test_prometheus_metrics),
        ("Twilio log parsing", test_twilio_log_parsing),
        ("Twilio abandonment", test_twilio_abandonment_parsing),
        ("Full production integration", test_full_production_integration),
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
    print(f"PRODUCTION INTEGRATION RESULTS: {passed}/{total} tests passed")
    print("="*70 + "\n")
    
    return all(results)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
