import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from telemetry_pipeline import RealTelemetryCollector, GovernanceReactor, end_to_end_telemetry_flow

def test_telemetry_collection():
    print("\n[TEST 1] Real telemetry collection")
    collector = RealTelemetryCollector()
    
    # Record 10 calls
    for i in range(10):
        collector.record_call(
            caller_id=f"C{i}",
            queue="billing_queue",
            wait_time=20.0 + i * 2,
            resolved=i < 8,  # 80% resolution
            friction_count=0 if i < 8 else 2,
            frustration=0.2 if i < 8 else 0.8
        )
    
    assert len(collector.metrics) == 10
    assert collector.metrics[0].caller_id == "C0"
    
    print(f"  ✓ PASSED - Collected {len(collector.metrics)} call metrics")
    return True

def test_telemetry_snapshot():
    print("\n[TEST 2] Telemetry snapshot generation")
    collector = RealTelemetryCollector()
    
    # Record mixed outcomes
    for i in range(20):
        collector.record_call(
            caller_id=f"C{i}",
            queue="billing_queue" if i < 10 else "tech_queue",
            wait_time=20.0 + (i % 5) * 5,
            resolved=(i % 3 != 0),
            friction_count=0 if (i % 3 != 0) else 1,
            frustration=0.3 if (i % 3 != 0) else 0.7
        )
    
    snapshot = collector.get_snapshot()
    
    assert snapshot.metrics_count == 20
    assert "billing_queue" in snapshot.waits_by_queue
    assert "tech_queue" in snapshot.waits_by_queue
    assert snapshot.avg_frustration > 0.3
    assert len(snapshot.resolution_rates) > 0
    
    print(f"  ✓ PASSED - Snapshot: {snapshot.metrics_count} metrics, "
          f"{len(snapshot.waits_by_queue)} queues, frustration={snapshot.avg_frustration:.2f}")
    return True

def test_governance_reaction():
    print("\n[TEST 3] Governance reaction to telemetry")
    collector = RealTelemetryCollector()
    reactor = GovernanceReactor()
    
    # Create drift: billing queue wait increases 2.5x
    for i in range(20):
        collector.record_call(
            caller_id=f"C{i}",
            queue="billing_queue",
            wait_time=50.0 + i * 2,  # Up to 70s
            resolved=i < 15,
            friction_count=0 if i < 15 else 2,
            frustration=0.3 if i < 15 else 0.8
        )
    
    snapshot = collector.get_snapshot()
    baseline = {"billing_queue": 20.0}
    
    reactions = reactor.react_to_snapshot(snapshot, baseline)
    
    assert len(reactions) > 0, "Should trigger reactions"
    assert any("DRIFT" in r for r in reactions), "Should detect drift"
    
    print(f"  ✓ PASSED - Triggered {len(reactions)} governance reaction(s)")
    for r in reactions:
        print(f"             - {r}")
    return True

def test_end_to_end_flow():
    print("\n[TEST 4] End-to-end telemetry → governance flow")
    
    calls = [
        {"caller_id": f"C{i}", "queue": "billing_queue", "wait_time": 20.0 + i,
         "resolved": i < 8, "friction_count": 0 if i < 8 else 1, "frustration": 0.2 if i < 8 else 0.7}
        for i in range(10)
    ]
    
    baseline = {"billing_queue": 20.0}
    result = end_to_end_telemetry_flow(calls, baseline)
    
    assert result["metrics_collected"] == 10
    assert result["avg_resolution_rate"] == 0.8
    assert result["avg_frustration"] > 0.2
    assert result["reaction_count"] >= 0
    
    print("  ✓ PASSED - E2E flow:")
    print(f"             Metrics: {result['metrics_collected']}")
    print(f"             Resolution rate: {result['avg_resolution_rate']*100:.1f}%")
    print(f"             Frustration: {result['avg_frustration']:.2f}")
    print(f"             Governance reactions: {result['reaction_count']}")
    return True

def main():
    print("\n" + "="*70)
    print("REAL TELEMETRY PIPELINE TESTS")
    print("="*70)
    
    tests = [
        ("Telemetry collection", test_telemetry_collection),
        ("Snapshot generation", test_telemetry_snapshot),
        ("Governance reaction", test_governance_reaction),
        ("End-to-end flow", test_end_to_end_flow),
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
    print(f"TELEMETRY PIPELINE RESULTS: {passed}/{total} tests passed")
    print("="*70 + "\n")
    
    return all(results)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
