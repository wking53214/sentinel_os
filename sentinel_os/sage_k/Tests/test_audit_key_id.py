"""Audit records name the algorithm and key ID that signed them.

Run in a subprocess, because the audit key is read once at import time.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

PROBE = (
    "import os, sys; "
    "os.environ['FORTRESS_AUDIT_KEY'] = 'a-sufficiently-long-audit-key-for-tests-0123'; "
    "os.environ['FORTRESS_AUDIT_KEY_ID'] = 'test-key'; "
    "import sage_k.kernel as k; "
    "k.audit_append('probe', {'n': 1}); "
    "print(k._AUDIT_KEY)"
)


def test_audit_records_carry_algorithm_and_key_id(tmp_path):
    env_log = tmp_path / "audit.log"
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        env={**os.environ, "FORTRESS_AUDIT_LOG": str(env_log)},
        cwd=str(Path(__file__).resolve().parents[2]),
        check=True,
    )
    key = result.stdout.strip().splitlines()[-1]
    assert env_log.exists(), "audit log path not honoured"
    record = json.loads(env_log.read_text().strip().splitlines()[-1])
    assert record["algorithm"] == "hmac-sha256"
    assert record["key_id"] == "test-key"
    assert key not in record["key_id"]
