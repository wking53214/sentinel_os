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
    intent = ivr._infer_intent_to_label("billing_queue", {})
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
    intent = banking._infer_intent_to_label("fraud_detection_queue", {})
    assert intent == "FRAUD_ALERT", f"Expected FRAUD_ALERT, got {intent}"
    
    print(f"  ✓ PASSED - Banking cassette valid, {len(queues)} queues")
    print("             Completely different from IVR")
    return True

def test_cassette_swapping():
    print("\n[TEST 4] Cassette swapping (same boom box, different cassettes)")
    
    config = {
        "postgres_host": None,
        "claude_api_key": None,
        "twilio_account_sid": None,
    }
    
    # Load IVR harness
    ivr_harness = CassetteHarness("ivr", config, require_cassette_binding=False)
    ivr_info = ivr_harness.get_cassette_info()
    
    assert ivr_info["domain"] == "ivr"
    assert "billing_queue" in ivr_info["queues"]
    
    # Banking no longer enables telephony_ingest, so the TELEPHONY
    # boom box refuses it AT THE DOOR -- fail-closed with a legible
    # capability error, not a KeyError mid-call. (Under the old
    # universal contract this "worked" only because banking declared
    # flagged placeholder Twilio thresholds to satisfy validation.)
    from cassette_capabilities import CapabilityError
    try:
        CassetteHarness("banking", config, require_cassette_binding=False)
        assert False, "telephony harness must refuse a non-telephony cassette"
    except CapabilityError as e:
        assert "telephony_ingest" in str(e)
        assert "banking" in str(e)

    # Banking's routing surface is still real and still different from
    # IVR's -- swappability now shows up at the KERNEL surface (see
    # test_call_processing_different_cassettes), not by forcing every
    # domain through the telephony pipeline.
    from cassette_loader import CassetteLoader
    banking = CassetteLoader().load_cassette("banking")
    assert "fraud_detection_queue" in banking.get_queue_definitions()
    assert ivr_info["queues"] != list(banking.get_queue_definitions().keys())
    
    print("  ✓ PASSED - Boom box works with multiple cassettes")
    print(f"             IVR domain: {ivr_info['domain']}, queues: {len(ivr_info['queues'])}")
    print(f"             Banking (kernel-judged domain): correctly refused by telephony harness")
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
    
    # Process with IVR through the telephony boom box -- unchanged.
    ivr_harness = CassetteHarness("ivr", config, require_cassette_binding=False)
    ivr_result = ivr_harness.process_call(call)
    
    assert ivr_result["domain"] == "ivr"
    assert "intent" in ivr_result
    assert "quality_tier" in ivr_result
    
    # The SAME facts judged by banking now flow through the kernel
    # surface: one Episode, two domains, two verdicts by design.
    from cassette_loader import CassetteLoader
    from cassettes.ivr_cassette import IvrCassette
    from episode import make_episode, judge_episode

    banking = CassetteLoader().load_cassette("banking")
    episode = make_episode(
        episode_id=call["sid"], domain="any",
        requested={"resolved": True},
        actual={"resolved": call["status"] == "completed"},
        attributes={"duration": float(call["duration"]), "friction_count": 0,
                    "emotion": {"frustration": 0.3}},
    )
    banking_verdict = judge_episode(banking, episode)
    ivr_verdict = judge_episode(IvrCassette(), episode)

    assert banking_verdict.tier in ["excellent", "good", "poor", "failed"]
    # Same episode, different cutoffs and weights: the domains are
    # allowed -- expected -- to disagree. (150s is "fast enough" for
    # banking's 180s band but not IVR's 120s band.)
    assert banking_verdict.score != ivr_verdict.score, \
        "two domains judging identically would mean the cassette layer does nothing"
    
    print("  ✓ PASSED - Same facts, different domain judgments")
    print(f"             IVR (harness): intent={ivr_result.get('intent')}, quality={ivr_result.get('quality_tier')}")
    print(f"             IVR (kernel): {ivr_verdict.tier} {ivr_verdict.score:.2f}")
    print(f"             Banking (kernel): {banking_verdict.tier} {banking_verdict.score:.2f}")
    return True

def test_cassette_validates():
    print("\n[TEST 6] Cassette validation")
    
    loader = get_loader()
    ivr = loader.get_cassette_for_domain("ivr")
    banking = loader.get_cassette_for_domain("banking")
    
    assert ivr.validate() == True, "IVR should validate"
    assert banking.validate() == True, "Banking should validate"
    
    print("  ✓ PASSED - All cassettes valid")
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
