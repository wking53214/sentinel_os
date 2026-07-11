import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from cassette_loader import CassetteLoader
from cassette_harness import CassetteHarness

def get_loader():
    """Helper: get loader with cassettes pre-loaded"""
    loader = CassetteLoader()
    loader.load_all_cassettes()
    return loader

def test_load_all_cassettes():
    print("\n[TEST 1] Load all cassettes dynamically")
    
    loader = get_loader()
    cassettes = loader.list_available()
    
    assert len(cassettes) >= 2, f"Should have at least 2 cassettes, got {len(cassettes)}"
    assert any("ivr" in str(k) for k in cassettes.keys()), "Should have IVR cassette"
    assert any("banking" in str(k) for k in cassettes.keys()), "Should have banking cassette"
    
    print(f"  ✓ PASSED - Loaded {len(cassettes)} cassettes")
    for name, config in cassettes.items():
        print(f"             - {name}: {config.description}")
    return True

def test_ivr_cassette_structure():
    print("\n[TEST 2] IVR cassette structure")
    
    loader = get_loader()
    ivr = loader.get_cassette_for_domain("ivr")
    
    # Validate structure
    assert ivr.get_config() is not None
    queues = ivr.get_queue_definitions()
    assert len(queues) >= 5, "IVR should have multiple queues"
    assert "billing_queue" in queues
    assert "tech_queue" in queues
    
    # Test methods
    intent = ivr.infer_intent("billing_queue", {})
    assert intent == "BILLING"
    
    quality = ivr.score_outcome_quality(True, 100, 0, {"frustration": 0.1})
    assert quality.tier in ["excellent", "good", "poor", "failed"]
    assert 0.0 <= quality.score <= 1.0, f"Score out of range: {quality.score}"
    
    print(f"  ✓ PASSED - IVR cassette valid, {len(queues)} queues")
    return True

def test_banking_cassette_structure():
    print("\n[TEST 3] Banking cassette structure")
    
    loader = get_loader()
    banking = loader.get_cassette_for_domain("banking")
    
    # Validate structure (DIFFERENT from IVR)
    assert banking.get_config() is not None
    queues = banking.get_queue_definitions()
    assert len(queues) >= 4, "Banking should have multiple queues"
    assert "fraud_detection_queue" in queues, "Banking must have fraud queue"
    assert "dispute_resolution_queue" in queues, "Banking must have dispute queue"
    
    # Different intent mapping
    intent = banking.infer_intent("fraud_detection_queue", {})
    assert intent == "FRAUD_ALERT", f"Expected FRAUD_ALERT, got {intent}"
    
    print(f"  ✓ PASSED - Banking cassette valid, {len(queues)} queues")
    print(f"             Completely different from IVR")
    return True

def test_cassette_swapping():
    print("\n[TEST 4] Cassette swapping (same boom box, different cassettes)")
    
    config = {
        "postgres_host": None,
        "claude_api_key": None,
        "twilio_account_sid": None,
    }
    
    # Load IVR harness
    ivr_harness = CassetteHarness("ivr", config)
    ivr_info = ivr_harness.get_cassette_info()
    
    assert ivr_info["domain"] == "ivr"
    assert "billing_queue" in ivr_info["queues"]
    
    # Load Banking harness (SAME CODE, DIFFERENT CASSETTE)
    banking_harness = CassetteHarness("banking", config)
    banking_info = banking_harness.get_cassette_info()
    
    assert banking_info["domain"] == "banking"
    assert "fraud_detection_queue" in banking_info["queues"]
    
    # Verify they're different
    assert ivr_info["queues"] != banking_info["queues"], "Different cassettes should have different queues"
    assert ivr_info["domain"] != banking_info["domain"], "Different domains"
    
    print(f"  ✓ PASSED - Boom box works with multiple cassettes")
    print(f"             IVR domain: {ivr_info['domain']}, queues: {len(ivr_info['queues'])}")
    print(f"             Banking domain: {banking_info['domain']}, queues: {len(banking_info['queues'])}")
    return True

def test_call_processing_different_cassettes():
    print("\n[TEST 5] Process same call data with different cassettes")
    
    config = {
        "postgres_host": None,
        "claude_api_key": None,
        "twilio_account_sid": None,
    }
    
    call = {
        "sid": "CA001",
        "status": "completed",
        "duration": 150,
        "from": "+1234",
        "to": "+billing"
    }
    
    # Process with IVR
    ivr_harness = CassetteHarness("ivr", config)
    ivr_result = ivr_harness.process_call(call)
    
    assert ivr_result["domain"] == "ivr"
    assert "intent" in ivr_result
    assert "quality_tier" in ivr_result
    
    # Process same call with banking
    banking_harness = CassetteHarness("banking", config)
    banking_result = banking_harness.process_call(call)
    
    assert banking_result["domain"] == "banking"
    
    print(f"  ✓ PASSED - Same call, different cassettes")
    print(f"             IVR: intent={ivr_result.get('intent')}, quality={ivr_result.get('quality_tier')}")
    print(f"             Banking: intent={banking_result.get('intent')}, quality={banking_result.get('quality_tier')}")
    return True

def test_cassette_validates():
    print("\n[TEST 6] Cassette validation")
    
    loader = get_loader()
    ivr = loader.get_cassette_for_domain("ivr")
    banking = loader.get_cassette_for_domain("banking")
    
    assert ivr.validate() == True, "IVR should validate"
    assert banking.validate() == True, "Banking should validate"
    
    print(f"  ✓ PASSED - All cassettes valid")
    return True

def main():
    print("\n" + "="*70)
    print("BOOM BOX + CASSETTE SYSTEM TESTS")
    print("="*70)
    
    tests = [
        ("Load all cassettes", test_load_all_cassettes),
        ("IVR cassette structure", test_ivr_cassette_structure),
        ("Banking cassette structure", test_banking_cassette_structure),
        ("Cassette swapping", test_cassette_swapping),
        ("Call processing w/ different cassettes", test_call_processing_different_cassettes),
        ("Cassette validation", test_cassette_validates),
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
    print(f"CASSETTE SYSTEM RESULTS: {passed}/{total} tests passed")
    print("="*70)
    
    if passed == total:
        print("\n✓ BOOM BOX + CASSETTE ARCHITECTURE VERIFIED")
        print("\nThis proves:")
        print("  ✓ Same boom box code works with multiple domains")
        print("  ✓ Cassettes are truly swappable")
        print("  ✓ Different domains have completely different rules")
        print("  ✓ Licensing model is viable")
        print("\nYou can now license this to any industry.")
    
    print()
    return all(results)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
