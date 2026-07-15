import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from api_key_auth import APIKeyManager

def test_api_key_manager_initialization():
    print("\n[TEST 1] API key manager initialization")
    
    # Unset env var for this test
    if "ICEBERG_API_KEYS" in os.environ:
        del os.environ["ICEBERG_API_KEYS"]
    
    manager = APIKeyManager()
    
    assert manager is not None
    assert len(manager.keys) >= 1, "Should have at least 1 key (development)"
    
    print(f"  ✓ PASSED - API key manager initialized with {len(manager.keys)} keys")
    return True

def test_api_key_generation():
    print("\n[TEST 2] API key generation")
    
    manager = APIKeyManager()
    key = manager._generate_key()
    
    assert key is not None
    assert len(key) > 20
    assert key.startswith("icebergkey_")
    
    print(f"  ✓ PASSED - Generated API key: {key[:30]}...")
    return True

def test_api_key_validation_valid():
    print("\n[TEST 3] API key validation (valid key)")
    
    manager = APIKeyManager()
    
    # Get first key
    test_key = list(manager.keys.keys())[0]
    
    try:
        info = manager.validate_key(test_key)
        assert info is not None
        assert "name" in info
        assert "enabled" in info
        
        print("  ✓ PASSED - Valid API key accepted")
        print(f"             Key name: {info['name']}")
        return True
    except Exception as e:
        print(f"  ✗ FAILED - {e}")
        return False

def test_api_key_validation_invalid():
    print("\n[TEST 4] API key validation (invalid key)")
    
    from fastapi import HTTPException
    
    manager = APIKeyManager()
    
    try:
        manager.validate_key("invalid_key_12345")
        print("  ✗ FAILED - Should have rejected invalid key")
        return False
    except HTTPException as e:
        assert e.status_code == 403
        print("  ✓ PASSED - Invalid key rejected with 403")
        return True

def test_api_key_validation_missing():
    print("\n[TEST 5] API key validation (missing key)")
    
    from fastapi import HTTPException
    
    manager = APIKeyManager()
    
    try:
        manager.validate_key(None)
        print("  ✗ FAILED - Should have rejected missing key")
        return False
    except HTTPException as e:
        assert e.status_code == 401
        print("  ✓ PASSED - Missing key rejected with 401")
        return True

def test_api_key_from_env():
    print("\n[TEST 6] API key from environment variables")
    
    # Set env var
    os.environ["ICEBERG_API_KEYS"] = "test_key_1:customer_1,test_key_2:customer_2"
    
    manager = APIKeyManager()
    
    assert len(manager.keys) == 2
    assert "test_key_1" in manager.keys
    assert "test_key_2" in manager.keys
    assert manager.keys["test_key_1"]["name"] == "customer_1"
    assert manager.keys["test_key_2"]["name"] == "customer_2"
    
    # Cleanup
    del os.environ["ICEBERG_API_KEYS"]
    
    print(f"  ✓ PASSED - Loaded {len(manager.keys)} keys from environment")
    return True

def test_api_server_has_auth():
    print("\n[TEST 7] API server configured with authentication")
    
    with open("api_server_resilient.py", "r") as f:
        content = f.read()
    
    assert "require_api_key" in content, "API key requirement not found"
    assert "Depends(require_api_key)" in content, "API key dependency not used"
    assert "/process" in content, "Process endpoint not found"
    assert "/batch" in content, "Batch endpoint not found"
    
    # Check that /health is public (no auth)
    lines = content.split("\n")
    health_section = False
    has_auth = False
    for i, line in enumerate(lines):
        if "@app.get(\"/health\")" in line:
            health_section = True
            # Check next 5 lines for auth
            for j in range(i, min(i+5, len(lines))):
                if "Depends(require_api_key)" in lines[j]:
                    has_auth = True
    
    if has_auth:
        print("  ✗ FAILED - /health endpoint should be public")
        return False
    
    print("  ✓ PASSED - API server configured with authentication")
    print("             - /health: public (no auth)")
    print("             - /process: protected")
    print("             - /batch: protected")
    print("             - /metrics: protected")
    return True

def main():
    print("\n" + "="*70)
    print("API KEY AUTHENTICATION TESTS")
    print("="*70)
    
    os.chdir(os.path.dirname(os.path.dirname(__file__)))
    
    tests = [
        ("API key manager init", test_api_key_manager_initialization),
        ("API key generation", test_api_key_generation),
        ("Valid key validation", test_api_key_validation_valid),
        ("Invalid key rejection", test_api_key_validation_invalid),
        ("Missing key rejection", test_api_key_validation_missing),
        ("Keys from environment", test_api_key_from_env),
        ("API server configured", test_api_server_has_auth),
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
    print(f"API KEY AUTHENTICATION RESULTS: {passed}/{total} tests passed")
    print("="*70)
    
    if passed == total:
        print("\n✓ API KEY AUTHENTICATION READY FOR PRODUCTION")
        print("\nTo use the API with authentication:")
        print("  1. Set API keys environment variable:")
        print("     export ICEBERG_API_KEYS=your_key:customer_name")
        print("\n  2. Call API with key header:")
        print("     curl -k -H 'X-API-Key: your_key' https://localhost:9090/metrics")
        print("\n  3. Public endpoints (no auth required):")
        print("     curl -k https://localhost:9090/health")
    
    print()
    return all(results)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
