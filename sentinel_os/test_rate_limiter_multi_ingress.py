"""test_rate_limiter_multi_ingress.py -- live proof that the PRODUCTION
rate-limit numbers (100 requests/min, burst 20; no RATE_LIMIT_* override) are
enforced per caller ACROSS several ingress processes sharing one Redis.

Why this matters: if the buckets lived inside each ingress process, three
ingress copies would quietly give each caller 3x the quota. Sending 90
requests for one key, 30 to each of three real ingress processes, tells the
two designs apart: per-process buckets would admit 60, a shared bucket
admits about 20.

Real Redis, three real uvicorn processes, real HTTP. Nothing mocked.

Run:  python3 -m pytest test_rate_limiter_multi_ingress.py -v -s
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

import httpx
import pytest

BUILD_DIR = os.path.dirname(os.path.abspath(__file__))
REDIS_PORT = 6392
REDIS_URL = f"redis://localhost:{REDIS_PORT}/0"
PORTS = [8131, 8132, 8133]
KEYS = {"A": "mi-key-aaa", "B": "mi-key-bbb", "C": "mi-key-ccc"}
NAMESPACE = f"mi_{uuid.uuid4().hex[:8]}"
RL_NAMESPACE = f"mirl_{uuid.uuid4().hex[:8]}"


def wait_for(fn, timeout=20.0, interval=0.1, desc="condition"):
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


@pytest.fixture(scope="module")
def cluster():
    redis_proc = subprocess.Popen(
        ["redis-server", "--port", str(REDIS_PORT), "--save", "", "--appendonly", "no"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def ping():
        with socket.create_connection(("127.0.0.1", REDIS_PORT), timeout=0.5) as s:
            s.sendall(b"PING\r\n")
            return s.recv(16).startswith(b"+PONG")

    wait_for(ping, desc="redis PONG")
    procs = []
    try:
        for port in PORTS:
            env = os.environ.copy()
            env.update({
                "TRANSMISSION_REDIS_URL": REDIS_URL, "TRANSMISSION_NAMESPACE": NAMESPACE,
                "PYTHONPATH": BUILD_DIR, "PYTHONUNBUFFERED": "1",
                "ICEBERG_API_KEYS": ",".join(f"{k}:tester-{n}" for n, k in KEYS.items()),
                "RATE_LIMIT_NAMESPACE": RL_NAMESPACE,
            })
            for k in ("RATE_LIMIT_REQUESTS_PER_MINUTE", "RATE_LIMIT_BURST_CAPACITY",
                      "RATE_LIMIT_BUCKET_TTL_SECONDS"):
                env.pop(k, None)   # production defaults on purpose
            logf = open(os.path.join(BUILD_DIR, f"mi_ingress_{port}.log"), "w")
            procs.append(subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "api_server_v2:app", "--host", "127.0.0.1",
                 "--port", str(port), "--log-level", "warning"],
                cwd=BUILD_DIR, env=env, stdout=logf, stderr=subprocess.STDOUT))
        for port in PORTS:
            wait_for(lambda p=port: httpx.get(f"http://127.0.0.1:{p}/health", timeout=1.0).status_code == 200,
                     desc=f"ingress :{port}")
        yield
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        redis_proc.terminate()
        redis_proc.wait(timeout=5)


def submit(i: int, key: str) -> int:
    port = PORTS[i % len(PORTS)]
    with httpx.Client(timeout=10.0) as c:
        return c.post(f"http://127.0.0.1:{port}/submit-call", json={"sid": f"MI{uuid.uuid4().hex}"},
                      headers={"x-api-key": key}).status_code


def test_three_ingress_processes_share_one_bucket_per_key(cluster):
    """90 concurrent requests for one key, 30 to each of 3 processes.
    Shared bucket: about 20 admitted (burst) plus a couple of refills during
    the second or so the burst takes. Per-process buckets would admit 60."""
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=30) as ex:
        codes = list(ex.map(lambda i: submit(i, KEYS["A"]), range(90)))
    dt = time.monotonic() - t0
    ok = codes.count(202)
    limited = codes.count(429)
    assert ok + limited == 90, f"unexpected codes: {sorted(set(codes))}"
    allowance = 20 + 100 / 60 * dt + 1     # burst + refill earned during the run + 1 for timing
    print(f"\n  admitted={ok} limited={limited} in {dt:.2f}s (ceiling {allowance:.1f})")
    assert 20 <= ok <= allowance, f"shared bucket should admit ~20, admitted {ok}"
    assert ok < 60, "3x the quota would mean the buckets are per-process"


def test_other_callers_are_untouched_across_all_processes(cluster):
    """Key A is exhausted (previous test). Key B gets its full burst of 20,
    even when its requests land on all three processes."""
    with ThreadPoolExecutor(max_workers=20) as ex:
        codes = list(ex.map(lambda i: submit(i, KEYS["B"]), range(20)))
    assert codes.count(202) == 20, f"B should be unaffected by A, got {codes}"


def test_sustained_rate_is_about_100_per_minute(cluster):
    """Drain C's burst, then offer ~10 requests/second for 12s spread over
    all three processes. Refill is 100/60 = 1.67/s, so about 20 more are
    admitted (accept 14..28 to allow timer jitter)."""
    with ThreadPoolExecutor(max_workers=20) as ex:
        list(ex.map(lambda i: submit(i, KEYS["C"]), range(40)))   # drain the burst
    admitted = 0
    n = 0
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as ex:
        while time.monotonic() - start < 12.0:
            admitted += (ex.submit(submit, n, KEYS["C"]).result() == 202)
            n += 1
            time.sleep(0.1)
    elapsed = time.monotonic() - start
    expected = 100 / 60 * elapsed
    print(f"\n  sustained: admitted {admitted} in {elapsed:.1f}s, expected about {expected:.1f}")
    assert expected - 6 <= admitted <= expected + 8
