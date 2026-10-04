"""The audit key: where it comes from, what production refuses, what warns.

The key is read once, when ``sage_k.kernel`` is imported, so each case runs in a
fresh interpreter with a controlled environment. Nothing here touches the real
audit log: cases that append use a log file under pytest's temporary directory.
"""

import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]  # the sentinel_os directory, which contains sage_k/
PUBLISHED_DEFAULT = "development-key"
REFUSAL = "Production deployments require unique cryptographic keys"
UNSET_WARNING = "FORTRESS_AUDIT_KEY is not set"
PUBLISHED_WARNING = "published development key"


def run_kernel(code="import sage_k.kernel as k; print(k._AUDIT_KEY)", **env_overrides):
    """Run ``code`` in a fresh interpreter. A value of None removes that variable."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("FORTRESS_")}
    for name, value in env_overrides.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return subprocess.run(
        [sys.executable, "-W", "default", "-c", code],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120,
    )


def test_unset_outside_production_uses_a_random_key_and_warns():
    result = run_kernel()
    assert result.returncode == 0, result.stderr
    key = result.stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{64}", key), key
    assert key != PUBLISHED_DEFAULT
    assert "RuntimeWarning" in result.stderr and UNSET_WARNING in result.stderr


def test_the_random_key_differs_between_processes():
    assert run_kernel().stdout.strip() != run_kernel().stdout.strip()


def test_unset_in_production_is_refused():
    result = run_kernel(FORTRESS_ENV="production")
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr and REFUSAL in result.stderr


def test_empty_key_is_treated_as_unset():
    refused = run_kernel(FORTRESS_ENV="production", FORTRESS_AUDIT_KEY="")
    assert refused.returncode != 0 and REFUSAL in refused.stderr
    outside = run_kernel(FORTRESS_AUDIT_KEY="")
    assert outside.returncode == 0, outside.stderr
    assert re.fullmatch(r"[0-9a-f]{64}", outside.stdout.strip())
    assert UNSET_WARNING in outside.stderr


def test_the_published_default_is_refused_in_production_even_when_set_explicitly():
    result = run_kernel(FORTRESS_ENV="production", FORTRESS_AUDIT_KEY=PUBLISHED_DEFAULT)
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr and REFUSAL in result.stderr


def test_the_published_default_outside_production_is_used_but_warns():
    result = run_kernel(FORTRESS_AUDIT_KEY=PUBLISHED_DEFAULT)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == PUBLISHED_DEFAULT
    assert "RuntimeWarning" in result.stderr and PUBLISHED_WARNING in result.stderr


@pytest.mark.parametrize("environment", [{"FORTRESS_ENV": "production"}, {}])
def test_a_real_key_is_used_without_any_warning(environment):
    """Production and not production: a configured unique key must be silent."""
    result = run_kernel(FORTRESS_AUDIT_KEY="a-unique-secret", **environment)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "a-unique-secret"
    assert result.stderr == ""


@pytest.mark.parametrize("environment_name", ["development", "staging", "test"])
def test_only_exactly_production_is_gated(environment_name):
    """Another environment name with no key is allowed, with the unset-key warning."""
    result = run_kernel(FORTRESS_ENV=environment_name)
    assert result.returncode == 0, result.stderr
    assert re.fullmatch(r"[0-9a-f]{64}", result.stdout.strip())
    assert UNSET_WARNING in result.stderr


def test_the_configured_key_is_the_one_that_signs_the_log(tmp_path):
    log = tmp_path / "audit.log"
    code = "import sage_k.kernel as k; k.audit_append('probe', {'n': 1})"
    result = run_kernel(code, FORTRESS_AUDIT_KEY="a-unique-secret", FORTRESS_AUDIT_LOG=str(log))
    assert result.returncode == 0, result.stderr
    record = json.loads(log.read_text().splitlines()[-1])
    signature = record.pop("hmac")
    body = json.dumps(record, separators=(",", ":"), sort_keys=True).encode()
    expected = hmac.new(b"a-unique-secret", body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(signature, expected)
    wrong = hmac.new(PUBLISHED_DEFAULT.encode(), body, hashlib.sha256).hexdigest()
    assert not hmac.compare_digest(signature, wrong)


def test_without_a_key_the_log_is_signed_with_the_process_key(tmp_path):
    log = tmp_path / "audit.log"
    code = "import sage_k.kernel as k; k.audit_append('probe', {'n': 1}); print(k._AUDIT_KEY)"
    result = run_kernel(code, FORTRESS_AUDIT_LOG=str(log))
    assert result.returncode == 0, result.stderr
    key = result.stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{64}", key)
    record = json.loads(log.read_text().splitlines()[-1])
    signature = record.pop("hmac")
    body = json.dumps(record, separators=(",", ":"), sort_keys=True).encode()
    assert hmac.compare_digest(signature, hmac.new(key.encode(), body, hashlib.sha256).hexdigest())


def test_without_a_key_the_log_does_not_verify_under_the_published_default(tmp_path):
    log = tmp_path / "audit.log"
    code = "import sage_k.kernel as k; k.audit_append('probe', {'n': 1})"
    result = run_kernel(code, FORTRESS_AUDIT_LOG=str(log))
    assert result.returncode == 0, result.stderr
    record = json.loads(log.read_text().splitlines()[-1])
    signature = record.pop("hmac")
    body = json.dumps(record, separators=(",", ":"), sort_keys=True).encode()
    forged = hmac.new(PUBLISHED_DEFAULT.encode(), body, hashlib.sha256).hexdigest()
    assert not hmac.compare_digest(signature, forged)
