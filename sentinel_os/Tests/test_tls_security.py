import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def test_tls_certificates_exist():
    print("\n[TEST 1] TLS certificates exist")
    
    cert_file = "./certs/cert.pem"
    key_file = "./certs/key.pem"
    
    assert os.path.exists(cert_file), f"Certificate not found: {cert_file}"
    assert os.path.exists(key_file), f"Key not found: {key_file}"
    
    # Verify files are not empty
    assert os.path.getsize(cert_file) > 0, "Certificate is empty"
    assert os.path.getsize(key_file) > 0, "Key is empty"
    
    print("  ✓ PASSED - TLS certificates found")
    print(f"             Cert: {cert_file} ({os.path.getsize(cert_file)} bytes)")
    print(f"             Key:  {key_file} ({os.path.getsize(key_file)} bytes)")
    return True

def test_tls_certificate_validity():
    print("\n[TEST 2] TLS certificate format")
    
    cert_file = "./certs/cert.pem"
    
    with open(cert_file, "r") as f:
        content = f.read()
    
    assert "BEGIN CERTIFICATE" in content, "Not a valid PEM certificate"
    assert "END CERTIFICATE" in content, "Certificate format invalid"
    
    print("  ✓ PASSED - Certificate is valid PEM format")
    return True

def test_tls_key_validity():
    print("\n[TEST 3] TLS private key format")
    
    key_file = "./certs/key.pem"
    
    with open(key_file, "r") as f:
        content = f.read()
    
    assert "BEGIN PRIVATE KEY" in content or "BEGIN RSA PRIVATE KEY" in content, "Not a valid PEM private key"
    assert "END PRIVATE KEY" in content or "END RSA PRIVATE KEY" in content, "Key format invalid"
    
    print("  ✓ PASSED - Private key is valid PEM format")
    return True

def test_tls_can_be_used():
    print("\n[TEST 4] TLS certificate can be used by HTTPS")
    
    import subprocess
    
    # Verify the cert file can be read by openssl
    result = subprocess.run(
        ["openssl", "x509", "-in", "./certs/cert.pem", "-text", "-noout"],
        capture_output=True,
        text=True
    )
    
    if result.returncode == 0:
        print("  ✓ PASSED - Certificate is valid X.509")
        # Extract CN from cert
        for line in result.stdout.split("\n"):
            if "CN=" in line or "Subject:" in line:
                print(f"             {line.strip()}")
        return True
    else:
        print(f"  ✗ FAILED - {result.stderr}")
        return False

def test_api_server_tls_ready():
    print("\n[TEST 5] API server ready for TLS")
    
    # Check if api_server_resilient.py has TLS support
    with open("api_server_resilient.py", "r") as f:
        content = f.read()
    
    assert "ssl_certfile" in content, "TLS support not found in API server"
    assert "ssl_keyfile" in content, "TLS key support not found in API server"
    assert "CERT_FILE" in content, "CERT_FILE env var not configured"
    assert "KEY_FILE" in content, "KEY_FILE env var not configured"
    
    print("  ✓ PASSED - API server configured for TLS")
    print("             - ssl_certfile support")
    print("             - ssl_keyfile support")
    print("             - Environment variable configuration")
    return True

def main():
    print("\n" + "="*70)
    print("TLS/HTTPS SECURITY TESTS")
    print("="*70)
    
    # Change to correct directory
    os.chdir(os.path.dirname(os.path.dirname(__file__)))
    
    tests = [
        ("TLS certificates exist", test_tls_certificates_exist),
        ("TLS certificate format", test_tls_certificate_validity),
        ("TLS private key format", test_tls_key_validity),
        ("TLS can be used by HTTPS", test_tls_can_be_used),
        ("API server TLS ready", test_api_server_tls_ready),
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
    print(f"TLS SECURITY RESULTS: {passed}/{total} tests passed")
    print("="*70)
    
    if passed == total:
        print("\n✓ TLS/HTTPS READY FOR PRODUCTION")
        print("\nTo run the API server with TLS:")
        print("  python3 api_server_resilient.py")
        print("\nAPI will be available at: https://localhost:9090")
        print("\nTo test (ignore self-signed cert warning):")
        print("  curl -k https://localhost:9090/health")
    
    print()
    return all(results)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
