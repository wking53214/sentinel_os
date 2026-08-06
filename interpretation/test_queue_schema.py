"""Live verification suite for the transmission (queue_schema.py).

Every test here runs against a REAL redis-server process started by the
suite itself -- no mocks, no fakes. The chaos tests use real SIGKILL on
real OS processes (worker and Redis both). Run:  pytest -q -s
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import uuid

import pytest
import redis as redis_lib

from queue_schema import ClaimedJob, Outcome, Reason, TransmissionQueue

MAIN_PORT = 6399
CRASH_PORT = 6400
PERF: dict = {}


# ------------------------------------------------------------------ infra --
def _wait_ping(port: int, timeout_s: float = 10.0) -> None:
    c = redis_lib.Redis(port=port, socket_connect_timeout=0.5)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if c.ping():
                c.close()
                return
        except redis_lib.exceptions.RedisError:
            time.sleep(0.1)
    raise RuntimeError(f"redis on :{port} never came up")


def _start_redis(port: int, workdir: str, fsync: str) -> subprocess.Popen:
    proc = subprocess.Popen(
        ["redis-server", "--port", str(port), "--dir", workdir,
         "--appendonly", "yes", "--appendfsync", fsync, "--save", "",
         "--logfile", os.path.join(workdir, "redis.log")],
    )
    _wait_ping(port)
    return proc


@pytest.fixture(scope="session")
def redis_url():
    d = tempfile.mkdtemp(prefix="sq-redis-main-")
    proc = _start_redis(MAIN_PORT, d, "everysec")
    info = redis_lib.Redis(port=MAIN_PORT).info("server")
    PERF["redis_version"] = info["redis_version"]
    yield f"redis://localhost:{MAIN_PORT}/0"
    proc.terminate()
    proc.wait(timeout=5)
    shutil.rmtree(d, ignore_errors=True)


def make_q(redis_url: str, **kw) -> TransmissionQueue:
    return TransmissionQueue(
        name="t-" + uuid.uuid4().hex[:8], redis_url=redis_url, **kw
    )


def _assert_invariant(q: TransmissionQueue) -> None:
    rep = q.verify_invariants()
    assert rep["ok"], rep["violations"]


# ------------------------------------------------------------ correctness --
def test_happy_path(redis_url):
    q = make_q(redis_url)
    jid, created = q.enqueue({"call_sid": "CA1", "n": 1}, job_id="CA1")
    assert created and jid == "CA1"
    job = q.claim("w1")
    assert job.id == "CA1" and job.attempt == 1
    assert job.payload == {"call_sid": "CA1", "n": 1}
    _assert_invariant(q)
    assert q.ack(job) is Outcome.OK
    s = q.stats()
    assert s["counters"]["completed"] == 1 and s["pending"] == 0
    assert q.claim("w1") is None
    _assert_invariant(q)


def test_enqueue_idempotent_on_job_id(redis_url):
    q = make_q(redis_url)
    _, c1 = q.enqueue({"a": 1}, job_id="CA2")
    _, c2 = q.enqueue({"a": 1}, job_id="CA2")   # ingress retry
    assert c1 is True and c2 is False
    s = q.stats()
    assert s["counters"]["enqueued"] == 1
    assert s["counters"]["duplicate_enqueues"] == 1
    assert s["pending"] == 1
    _assert_invariant(q)


def test_fifo_order(redis_url):
    q = make_q(redis_url)
    for i in range(5):
        q.enqueue({"i": i}, job_id=f"f{i}")
    got = [q.claim("w").id for _ in range(5)]
    assert got == [f"f{i}" for i in range(5)]


def test_ack_retry_after_dropped_response_is_safe(redis_url):
    q = make_q(redis_url)
    q.enqueue({"x": 1}, job_id="CA3")
    job = q.claim("w1")
    assert q.ack(job) is Outcome.OK
    # client never saw the reply and retries: must be a no-op, not a crash
    assert q.ack(job) is Outcome.GONE
    assert q.stats()["counters"]["completed"] == 1


# ------------------------------------------------------------- concurrency --
def test_no_double_claim_under_concurrent_storm(redis_url):
    q = make_q(redis_url)
    n_jobs, n_workers = 2000, 12
    for i in range(n_jobs):
        q.enqueue({"i": i}, job_id=f"c{i}")

    lock = threading.Lock()
    claims: list = []

    def worker(wid: str):
        while True:
            job = q.claim(wid, lease_ms=120_000, wait_timeout_s=0.3,
                          poll_interval_s=0.01)
            if job is None:
                return
            with lock:
                claims.append((job.id, wid, job.claim_id))
            assert q.ack(job) is Outcome.OK

    t0 = time.monotonic()
    threads = [threading.Thread(target=worker, args=(f"w{i}",))
               for i in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    dt = time.monotonic() - t0

    ids = [c[0] for c in claims]
    assert len(ids) == n_jobs, f"claimed {len(ids)} of {n_jobs}"
    assert len(set(ids)) == n_jobs, "DOUBLE CLAIM DETECTED"
    s = q.stats()
    assert s["counters"]["completed"] == n_jobs
    assert s["pending"] == 0 and s["processing"] == 0
    _assert_invariant(q)
    PERF["storm"] = (f"{n_jobs} jobs / {n_workers} threads: 0 double-claims, "
                     f"drained in {dt:.2f}s ({n_jobs/dt:.0f} claim+ack/s)")
    print("\nPERF storm:", PERF["storm"])


# ------------------------------------------------------ retries + the WHY --
def test_retry_backoff_then_dead_letter_with_diagnosis(redis_url):
    q = make_q(redis_url, max_attempts=3, base_backoff_ms=100,
               max_backoff_ms=400, jitter_ms=0)
    q.enqueue({"call_sid": "CAX"}, job_id="CAX")

    backoffs = []
    for attempt in (1, 2):
        job = q.claim("w1", wait_timeout_s=2.0, poll_interval_s=0.02)
        assert job.attempt == attempt
        out, backoff = q.fail(job, Reason.NETWORK_LATENCY,
                              f"governor timeout on attempt {attempt}")
        assert out is Outcome.SCHEDULED
        backoffs.append(backoff)

    job = q.claim("w1", wait_timeout_s=2.0, poll_interval_s=0.02)
    assert job.attempt == 3
    out, backoff = q.fail(job, Reason.NETWORK_LATENCY, "governor timeout, final")
    assert out is Outcome.DEAD and backoff is None

    assert backoffs == [100, 200], backoffs  # base * 2^(n-1), jitter 0

    dead = q.dlq_peek(1)[0]
    assert dead["dead_reason"] == "network_latency"
    assert dead["escalate"] == "0"
    trail = dead["error_trail"]
    assert [e["attempt"] for e in trail] == [3, 2, 1]      # newest first
    assert all(e["reason"] == "network_latency" for e in trail)
    assert "governor timeout" in trail[0]["detail"]
    s = q.stats()
    assert s["dead_reasons"] == {"network_latency": 1}
    assert s["counters"]["retries"] == 2 and s["counters"]["dead"] == 1
    _assert_invariant(q)


def test_nonretryable_reason_dead_letters_immediately(redis_url):
    q = make_q(redis_url)
    q.enqueue({"x": 1}, job_id="CAY")
    job = q.claim("w1")
    out, _ = q.fail(job, Reason.DATA_CORRUPTION, "schema mismatch")
    assert out is Outcome.DEAD                      # default: not retryable
    assert q.stats()["counters"].get("retries", 0) == 0
    _assert_invariant(q)


def test_unclassified_reason_sets_escalate_flag(redis_url):
    q = make_q(redis_url, max_attempts=1)
    q.enqueue({"x": 1}, job_id="CAZ")
    job = q.claim("w1")
    out, _ = q.fail(job, Reason.UNCLASSIFIED, "no idea, raising the flag")
    assert out is Outcome.DEAD
    assert q.dlq_peek(1)[0]["escalate"] == "1"


def test_corrupted_payload_is_quarantined_with_evidence(redis_url):
    q = make_q(redis_url)
    q.enqueue({"x": 1}, job_id="CORR")
    # simulate corruption at rest / in transit
    q.r.hset(f"{q.prefix}:job:CORR", "payload", b'{"tampered": true}')
    assert q.claim("w1") is None          # worker never sees the bad payload
    dead = q.dlq_peek(1)[0]
    assert dead["dead_reason"] == "data_corruption"
    assert "checksum mismatch" in dead["error_trail"][0]["detail"]
    assert q.stats()["counters"]["corrupt_payloads"] == 1
    _assert_invariant(q)


# -------------------------------------------------------- chaos: the crash --
def _victim(url: str, name: str, out_q):
    """Claims a job, reports it, then hangs until SIGKILLed. Never acks."""
    q = TransmissionQueue(name=name, redis_url=url)
    job = q.claim("victim-w", lease_ms=700, wait_timeout_s=5.0)
    out_q.put(job.id)
    time.sleep(30)


def test_worker_sigkill_recovery(redis_url):
    q = make_q(redis_url)
    q.enqueue({"call_sid": "KILL1"}, job_id="KILL1")

    ctx = mp.get_context("fork")
    chan = ctx.Queue()
    p = ctx.Process(target=_victim, args=(redis_url, q.name, chan))
    p.start()
    claimed_id = chan.get(timeout=10)
    assert claimed_id == "KILL1"
    os.kill(p.pid, signal.SIGKILL)          # real kill -9, mid-job
    p.join(timeout=5)

    assert q.stats()["processing"] == 1     # queue still holds the truth
    time.sleep(0.9)                          # let the 700ms lease expire
    report = q.reap_expired()
    assert report["requeued"] == ["KILL1"] and not report["dead"]

    trail = q.error_trail("KILL1")
    assert trail[0]["reason"] == "process_crash"
    assert "lease expired" in trail[0]["detail"]

    rescue = q.claim("rescuer-w")
    assert rescue.id == "KILL1" and rescue.attempt == 2
    assert q.ack(rescue) is Outcome.OK
    s = q.stats()
    assert s["counters"]["completed"] == 1 and s["counters"]["reaped"] == 1
    _assert_invariant(q)


def test_stale_worker_cannot_ack_a_reclaimed_job(redis_url):
    q = make_q(redis_url)
    q.enqueue({"x": 1}, job_id="FENCE")
    job_a = q.claim("worker-A", lease_ms=300)
    time.sleep(0.45)                         # A stalls past its lease
    assert q.reap_expired()["requeued"] == ["FENCE"]
    job_b = q.claim("worker-B")
    assert job_b.attempt == 2
    assert q.ack(job_a) is Outcome.STALE     # A wakes up: fenced out
    assert q.fail(job_a, Reason.UNCLASSIFIED, "zombie")[0] is Outcome.STALE
    assert q.ack(job_b) is Outcome.OK        # B's claim is untouched
    s = q.stats()
    assert s["counters"]["completed"] == 1
    assert s["counters"]["stale_acks"] == 1 and s["counters"]["stale_fails"] == 1
    _assert_invariant(q)


def test_crash_looping_job_dead_letters_after_budget(redis_url):
    q = make_q(redis_url, max_attempts=2)
    q.enqueue({"x": 1}, job_id="LOOP")
    for _ in range(2):                       # claim, "crash", reap
        assert q.claim("w", lease_ms=200, wait_timeout_s=1.0) is not None
        time.sleep(0.3)
        q.reap_expired()
    dead = q.dlq_peek(1)[0]
    assert dead["id"] == "LOOP" and dead["dead_reason"] == "process_crash"
    assert len(dead["error_trail"]) == 2
    assert q.stats()["dead_reasons"]["process_crash"] == 1
    _assert_invariant(q)


# --------------------------------------------- chaos: Redis itself dies ----
def test_redis_kill9_loses_nothing_and_pool_recovers():
    d = tempfile.mkdtemp(prefix="sq-redis-crash-")
    proc = _start_redis(CRASH_PORT, d, "always")   # honest zero-loss config
    url = f"redis://localhost:{CRASH_PORT}/0"
    try:
        q = TransmissionQueue(name="crashq", redis_url=url)
        for i in range(100):
            q.enqueue({"i": i}, job_id=f"d{i}")
        for _ in range(20):                        # completed before crash
            q.ack(q.claim("pre-w"))
        held = [q.claim("doomed-w", lease_ms=1500) for _ in range(10)]
        assert all(h is not None for h in held)    # in-flight at crash time

        os.kill(proc.pid, signal.SIGKILL)          # kill -9 the broker
        proc.wait(timeout=5)
        time.sleep(0.3)
        proc = _start_redis(CRASH_PORT, d, "always")

        s = q.stats()                              # SAME client object: pool recovers
        assert s["counters"]["enqueued"] == 100
        assert s["counters"]["completed"] == 20
        assert s["pending"] == 70 and s["processing"] == 10

        time.sleep(1.6)                            # let pre-crash leases lapse
        report = q.reap_expired()
        assert len(report["requeued"]) == 10 and not report["dead"]

        drained = 0
        while True:
            job = q.claim("post-w", wait_timeout_s=0.3)
            if job is None:
                break
            assert q.ack(job) is Outcome.OK
            drained += 1
        assert drained == 80
        s = q.stats()
        assert s["counters"]["completed"] == 100   # zero loss, zero dupes
        assert s["pending"] == s["processing"] == s["dead"] == 0
        _assert_invariant(q)
        PERF["kill9"] = ("100 enqueued; kill -9 redis with 70 pending + 10 "
                         "in-flight; restart: 0 lost, 10 reaped+recovered, "
                         "completed 100/100, invariant clean")
        print("\nPERF kill9:", PERF["kill9"])
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
        shutil.rmtree(d, ignore_errors=True)


def test_broker_unreachable_fails_loud_not_silent():
    q = TransmissionQueue(name="deadend",
                          redis_url="redis://localhost:6553/0",
                          socket_connect_timeout=0.3)
    with pytest.raises(redis_lib.exceptions.RedisError):
        q.enqueue({"x": 1}, job_id="NOPE")   # raises; never fake-succeeds


# ----------------------------------------------------------- observability --
def test_stats_expose_depth_staleness_and_dlq(redis_url):
    q = make_q(redis_url, max_attempts=1)
    for i in range(3):
        q.enqueue({"i": i}, job_id=f"s{i}")
    time.sleep(0.15)
    s = q.stats()
    assert s["pending"] == 3 and s["depth_ready"] == 3
    assert s["oldest_pending_age_ms"] >= 100          # staleness is visible

    job = q.claim("w", lease_ms=60_000)
    q.fail(q.claim("w"), Reason.SERVICE_INTERRUPTION, "upstream 503")
    s = q.stats()
    assert s["processing"] == 1 and s["dead"] == 1
    assert s["dead_last_hour"] == 1
    assert q.dlq_rate(60)["count"] == 1.0
    assert s["processing_overdue"] == 0               # reaper not behind
    q.ack(job)
    _assert_invariant(q)


def test_requeue_from_dlq_gives_fresh_budget_keeps_history(redis_url):
    q = make_q(redis_url)
    q.enqueue({"x": 1}, job_id="RQ")
    q.fail(q.claim("w"), Reason.DATA_CORRUPTION, "bad payload flag")
    assert q.requeue_from_dlq("RQ") is Outcome.OK
    job = q.claim("w")
    assert job.attempt == 1                            # fresh budget
    assert q.error_trail("RQ")[0]["reason"] == "data_corruption"  # history kept
    q.ack(job)
    assert q.requeue_from_dlq("RQ") is Outcome.GONE
    _assert_invariant(q)


def test_dangling_reference_is_quarantined_not_lost(redis_url):
    q = make_q(redis_url)
    q.r.lpush(f"{q.prefix}:pending", "ghost-id")       # corruption injection
    assert q.claim("w") is None
    assert q.r.llen(f"{q.prefix}:orphans") == 1
    assert q.stats()["counters"]["orphan_refs"] == 1


# ------------------------------------------------------------- saturation --
def test_burst_saturation_accounting_is_exact(redis_url):
    q = make_q(redis_url)
    n = 5000
    t0 = time.monotonic()
    for i in range(n):
        q.enqueue({"i": i}, job_id=f"b{i}")
    t_enq = time.monotonic() - t0

    def drain(wid):
        while True:
            job = q.claim(wid, lease_ms=60_000, wait_timeout_s=0.3,
                          poll_interval_s=0.005)
            if job is None:
                return
            q.ack(job)

    t0 = time.monotonic()
    ts = [threading.Thread(target=drain, args=(f"dw{i}",)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    t_drain = time.monotonic() - t0

    s = q.stats()
    assert s["counters"]["enqueued"] == n
    assert s["counters"]["completed"] == n             # every job accounted
    assert s["pending"] == s["processing"] == s["scheduled"] == s["dead"] == 0
    _assert_invariant(q)
    PERF["burst"] = (f"{n} jobs: enqueue {n/t_enq:.0f}/s, "
                     f"drain (8 workers) {n/t_drain:.0f} claim+ack/s")
    print("\nPERF burst:", PERF["burst"])
    print("PERF redis:", PERF.get("redis_version"))
