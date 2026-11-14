"""test_api_key_ip_limiter.py -- the per-IP limiter in api_key_auth.py counts
only FAILED key checks. Real uvicorn ingress, real Redis, real HTTP.

Before 2026-11-13 it counted every request, so behind a shared proxy IP the
whole product was capped at ICEBERG_RATE_LIMIT_MAX per minute whatever each
customer's own allowance was (F-F again). The brute-force protection it was
built for (500 guessed keys, 0 throttled) must still hold.

Run:  python3 -m pytest test_api_key_ip_limiter.py -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid

import httpx
import pytest

BUILD_DIR = os.path.dirname(os.path.abspath(__file__))
REDIS_URL = "redis://localhost:6379/0"
GOOD = "ipl-good-key"
LIMIT = 5


def _start(port: int):
    env = os.environ.copy()
    env.update({
        "TRANSMISSION_REDIS_URL": REDIS_URL, "TRANSMISSION_NAMESPACE": f"ipl_{uuid.uuid4().hex[:8]}",
        "PYTHONPATH": BUILD_DIR, "ICEBERG_API_KEYS": f"{GOOD}:tester",
        "ICEBERG_RATE_LIMIT_MAX": str(LIMIT),
        # take the per-caller limiter out of the picture
        "RATE_LIMIT_NAMESPACE": f"iplrl_{uuid.uuid4().hex[:8]}",
        "RATE_LIMIT_BURST_CAPACITY": "1000", "RATE_LIMIT_REQUESTS_PER_MINUTE": "100000",
    })
    log = open(os.path.join(BUILD_DIR, f"ipl_{port}.log"), "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "api_server_v2:app", "--host", "127.0.0.1",
                             "--port", str(port), "--log-level", "warning"],
                            cwd=BUILD_DIR, env=env, stdout=log, stderr=subprocess.STDOUT)
    end = time.monotonic() + 25
    while time.monotonic() < end:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200:
                return proc
        except httpx.HTTPError:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError("ingress never came up")


@pytest.fixture()
def ingress():
    port = 8240 + (os.getpid() % 50)
    proc = _start(port)
    yield f"http://127.0.0.1:{port}"
    proc.terminate()
    proc.wait(timeout=5)


def _submit(base, key):
    return httpx.post(f"{base}/submit-call", json={"sid": f"IPL{uuid.uuid4().hex}"},
                      headers={"x-api-key": key}, timeout=5).status_code


def test_valid_requests_are_never_counted_toward_the_ip_limit(ingress):
    codes = [_submit(ingress, GOOD) for _ in range(LIMIT * 6)]
    assert codes.count(202) == LIMIT * 6, f"valid traffic was throttled: {set(codes)}"


def test_guessing_is_still_throttled_per_ip(ingress):
    first = [_submit(ingress, f"guess-{i}") for i in range(LIMIT)]
    assert first == [403] * LIMIT
    assert _submit(ingress, "guess-next") == 429, "the brute-force throttle must still bite"


def test_valid_requests_do_not_use_up_the_failure_budget(ingress):
    for _ in range(LIMIT * 3):
        assert _submit(ingress, GOOD) == 202
    # the full failure budget is still available afterwards
    assert [_submit(ingress, f"g{i}") for i in range(LIMIT)] == [403] * LIMIT
    assert _submit(ingress, "g-over") == 429
