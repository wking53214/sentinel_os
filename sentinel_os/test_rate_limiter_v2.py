"""test_rate_limiter_v2.py -- live verification for rate_limiter_v2.py.

Boots a REAL Redis and a REAL uvicorn api_server_v2 process (same
pattern as test_api_server_v2.py's L-series), then drives REAL
concurrent HTTP load at it with real TCP connections -- no mocking of
the rate limiter, Redis, or the HTTP layer. Every "N over the limit get
rejected" claim below is proven by actually sending N+1 concurrent
requests and reading real response codes, per the build's own
non-negotiables.

Uses a deliberately small, fast configuration
(RATE_LIMIT_REQUESTS_PER_MINUTE / RATE_LIMIT_BURST_CAPACITY /
RATE_LIMIT_BUCKET_TTL_SECONDS) so the tests run in seconds instead of
minutes -- the production defaults (100/min, burst 20) are exercised
directly in test_rate_limiter_unit.py against the Lua script, not
timed out here.

Run:  python3 -m pytest test_rate_limiter_v2.py -v -s
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Dict

import httpx
import pytest

BUILD_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BUILD_DIR)

REDIS_PORT = 6391
REDIS_URL = f"redis://localhost:{REDIS_PORT}/0"
INGRESS_PORT = 8110
BASE = f"http://127.0.0.1:{INGRESS_PORT}"
API_KEY_A = "rl-test-key-aaa"
API_KEY_B = "rl-test-key-bbb"
NAMESPACE = f"rltest_{uuid.uuid4().hex[:8]}"


def wait_for(fn, timeout=15.0, interval=0.1, desc="condition"):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            out = fn()
            if out:
                return out
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(interval)
    raise TimeoutError(f"timed out waiting for {desc} (last error: {last})")


def sid() -> str:
    return f"RL{uuid.uuid4().hex}"


@pytest.fixture(scope="session")
def redis_proc():
    proc = subprocess.Popen(
        ["redis-server", "--port", str(REDIS_PORT), "--save", "", "--appendonly", "no"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    def ping():
        with socket.create_connection(("127.0.0.1", REDIS_PORT), timeout=0.5) as s:
            s.sendall(b"PING\r\n")
            return s.recv(16).startswith(b"+PONG")

    wait_for(ping, desc="redis-server PONG")
    yield proc
    try:
        os.kill(proc.pid, signal.SIGCONT)
    except ProcessLookupError:
        pass
    proc.terminate()
    proc.wait(timeout=5)


def _spawn_ingress(port: int, extra_env: Dict[str, str]) -> subprocess.Popen:
    env = os.environ.copy()
    inner = os.path.join(BUILD_DIR, "sentinel_os")
    pythonpath = BUILD_DIR if not os.path.isdir(inner) else f"{BUILD_DIR}:{inner}"
    env.update({
        "TRANSMISSION_REDIS_URL": REDIS_URL,
        "TRANSMISSION_NAMESPACE": NAMESPACE,
        "PYTHONPATH": pythonpath,
        "PYTHONUNBUFFERED": "1",
        "ICEBERG_API_KEYS": f"{API_KEY_A}:tester-a,{API_KEY_B}:tester-b",
    })
    env.update(extra_env)
    logpath = os.path.join(BUILD_DIR, f"rl_ingress_{port}.log")
    logf = open(logpath, "w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api_server_v2:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=BUILD_DIR, env=env, stdout=logf, stderr=subprocess.STDOUT, text=True,
    )
    try:
        wait_for(lambda: httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0).status_code == 200,
                 desc=f"ingress /health on :{port}")
    except TimeoutError:
        logf.flush()
        tail = open(logpath).read()[-2000:]
        raise RuntimeError(f"ingress on :{port} never came up. Log tail:\n{tail}")
    return proc


@pytest.fixture()
def ingress(redis_proc):
    """Small, fast bucket: capacity 5, 60 req/min (=1/sec refill), so a
    burst of 6 concurrent requests is enough to prove the limit without
    waiting minutes, and a 1s wait is enough to prove real refill.

    RATE_LIMIT_NAMESPACE is unique per test invocation: Redis itself is
    session-scoped (one real server for the whole file) but this
    fixture is function-scoped (fresh ingress process per test), so
    without a fresh namespace every test would share the SAME Redis
    bucket keys as every other test that ran before it in this
    session -- exactly the cross-test bleed a real isolation test must
    not have.
    """
    rl_namespace = f"rl_{uuid.uuid4().hex[:10]}"
    proc = _spawn_ingress(INGRESS_PORT, extra_env={
        "RATE_LIMIT_REQUESTS_PER_MINUTE": "60",
        "RATE_LIMIT_BURST_CAPACITY": "5",
        "RATE_LIMIT_BUCKET_TTL_SECONDS": "60",
        "RATE_LIMIT_NAMESPACE": rl_namespace,
    })
    yield proc
    proc.terminate()
    proc.wait(timeout=5)


def submit(key: str, timeout=10.0) -> httpx.Response:
    with httpx.Client(timeout=timeout) as c:
        return c.post(f"{BASE}/submit-call", json={"sid": sid()},
                      headers={"x-api-key": key})


# --------------------------------------------------------------- burst --
def test_burst_within_capacity_all_succeed(ingress):
    """5 requests, capacity 5 -- all must succeed (202), none 429."""
    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(lambda _: submit(API_KEY_A), range(5)))
    codes = [r.status_code for r in results]
    assert codes.count(202) == 5, f"expected 5x202, got {codes}"


def test_real_concurrent_burst_over_capacity_gets_429s(ingress):
    """THE core claim, proven live: 15 real concurrent requests against
    a capacity-5 bucket. Exactly 5 must succeed (202), the rest must be
    429 with a Retry-After header -- not simulated, not counted after
    the fact from logs, read directly off real HTTP responses."""
    with ThreadPoolExecutor(max_workers=15) as ex:
        results = list(ex.map(lambda _: submit(API_KEY_B), range(15)))
    codes = [r.status_code for r in results]
    n_ok = codes.count(202)
    n_limited = codes.count(429)
    assert n_ok + n_limited == 15, f"unexpected status codes present: {codes}"
    assert n_ok == 5, f"expected exactly 5 successes (capacity), got {n_ok}: {codes}"
    assert n_limited == 10, f"expected exactly 10 rejections, got {n_limited}: {codes}"

    limited = [r for r in results if r.status_code == 429]
    for r in limited:
        assert "retry-after" in {k.lower() for k in r.headers.keys()}
        body = r.json()
        assert body["detail"]["error"] == "rate_limit_exceeded"


# ---------------------------------------------------------- isolation --
def test_two_principals_have_independent_buckets(ingress):
    """F-F's core property, proven live: exhausting API_KEY_A's bucket
    to zero must not affect API_KEY_B's bucket -- a fresh capacity-5
    burst for B must still succeed in full immediately afterward, on
    the SAME ingress process (same Redis, same namespace, different
    principal)."""
    with ThreadPoolExecutor(max_workers=8) as ex:
        results_a = list(ex.map(lambda _: submit(API_KEY_A), range(8)))
    a_ok = sum(1 for r in results_a if r.status_code == 202)
    a_limited = sum(1 for r in results_a if r.status_code == 429)
    assert a_ok == 5 and a_limited == 3, f"sanity: A's bucket should be capacity-bound, got ok={a_ok} limited={a_limited}"

    with ThreadPoolExecutor(max_workers=5) as ex:
        results_b = list(ex.map(lambda _: submit(API_KEY_B), range(5)))
    b_ok = sum(1 for r in results_b if r.status_code == 202)
    assert b_ok == 5, (
        f"B's bucket must be completely unaffected by A's exhaustion, "
        f"expected 5/5 success, got {b_ok}/5: {[r.status_code for r in results_b]}"
    )


def test_unauthenticated_requests_never_reach_the_bucket(ingress):
    """No X-API-Key -> 401 from require_api_key, which runs BEFORE
    rate_limit_v2 in the guard chain. Proves an attacker can't burn
    through or discover bucket state without a valid key first."""
    with httpx.Client(timeout=10.0) as c:
        r = c.post(f"{BASE}/submit-call", json={"sid": sid()})
    assert r.status_code == 401


def test_health_never_rate_limited(ingress):
    """`/health` takes no guards at all -- must stay open even after
    a caller's bucket is fully exhausted."""
    with ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(lambda _: submit(API_KEY_A), range(10)))
    for _ in range(5):
        r = httpx.get(f"{BASE}/health", timeout=5)
        assert r.status_code == 200


# ----------------------------------------------------------- recovery --
def test_bucket_refills_after_real_wait(ingress):
    """Exhaust the bucket completely, wait for real wall-clock time to
    pass, and confirm refill actually happened -- not simulated."""
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(lambda _: submit(API_KEY_A), range(6)))
    assert sum(1 for r in results if r.status_code == 202) == 5
    assert sum(1 for r in results if r.status_code == 429) == 1

    # Immediately after exhaustion, the next request must still be limited.
    r_immediate = submit(API_KEY_A)
    assert r_immediate.status_code == 429

    # Real wall-clock wait for ~2 tokens to refill at 1/sec.
    time.sleep(2.2)
    r = submit(API_KEY_A)
    assert r.status_code == 202, f"expected refilled capacity to allow a request, got {r.status_code}: {r.text}"


# --------------------------------------------------------- fail-open --
RL_ONLY_REDIS_PORT = 6392


@pytest.fixture()
def rl_only_redis_proc():
    """A SEPARATE Redis instance dedicated to the rate limiter only, so
    freezing it exercises rate_limit_v2's fail-open path in isolation
    from the transmission queue's own (already correct, and different
    by design) fail-CLOSED 503 behavior when ITS Redis is unreachable.
    Sharing one Redis for both in production is a legitimate choice,
    but it makes the two failure domains untestable independently."""
    proc = subprocess.Popen(
        ["redis-server", "--port", str(RL_ONLY_REDIS_PORT), "--save", "", "--appendonly", "no"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    def ping():
        with socket.create_connection(("127.0.0.1", RL_ONLY_REDIS_PORT), timeout=0.5) as s:
            s.sendall(b"PING\r\n")
            return s.recv(16).startswith(b"+PONG")

    wait_for(ping, desc="rl-only redis-server PONG")
    yield proc
    try:
        os.kill(proc.pid, signal.SIGCONT)
    except ProcessLookupError:
        pass
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture()
def ingress_separate_rl_redis(redis_proc, rl_only_redis_proc):
    rl_namespace = f"rl_{uuid.uuid4().hex[:10]}"
    proc = _spawn_ingress(INGRESS_PORT, extra_env={
        "RATE_LIMIT_REQUESTS_PER_MINUTE": "60",
        "RATE_LIMIT_BURST_CAPACITY": "5",
        "RATE_LIMIT_BUCKET_TTL_SECONDS": "60",
        "RATE_LIMIT_NAMESPACE": rl_namespace,
        "RATE_LIMIT_REDIS_URL": f"redis://localhost:{RL_ONLY_REDIS_PORT}/0",
    })
    yield proc
    proc.terminate()
    proc.wait(timeout=5)


def test_fails_open_when_redis_is_unreachable(ingress_separate_rl_redis, rl_only_redis_proc):
    """Freeze ONLY the rate limiter's dedicated Redis (SIGSTOP, same
    chaos technique test_api_server_v2.py uses for L11) and confirm
    submissions still succeed -- fail-open is a deliberate design
    choice (see rate_limit_v2's docstring), not an accident. The
    transmission queue's own Redis is untouched, so a 503 here would
    mean the RATE LIMITER failed closed, not an unrelated queue outage.
    Un-freezes Redis in a finally so the fixture can tear down cleanly.
    """
    os.kill(rl_only_redis_proc.pid, signal.SIGSTOP)
    try:
        r = submit(API_KEY_A, timeout=6.0)
        assert r.status_code == 202, (
            f"expected fail-open (202) with the rate limiter's Redis frozen, "
            f"got {r.status_code}: {r.text}"
        )
    finally:
        os.kill(rl_only_redis_proc.pid, signal.SIGCONT)
