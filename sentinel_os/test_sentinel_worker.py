"""Live verification suite for sentinel_worker.py.

Runs against REAL Postgres and REAL Redis -- no mocks of either. The
Claude client is left unconfigured (no CLAUDE_API_KEY) so governed
calls take the harness's own documented "No API client configured"
fail-closed path -- a real code path in production_harness.py, not a
test double, and one that still exercises the full ledger write.

Run:  pytest -q -s test_sentinel_worker.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

import psycopg2
import pytest

os.environ.setdefault("ICEBERG_LEDGER_RUNTIME_USER", "")

from production_harness import IcebergProductionHarness
from queue_schema import Outcome, TransmissionQueue
from sentinel_worker import SentinelWorker

REDIS_PORT = 6398
PG_DSN = dict(host="localhost", port=5432, dbname="iceberg",
              user="iceberg", password="iceberg")


# ------------------------------------------------------------------ infra --
def _wait_ping(port, timeout_s=10.0):
    import redis as redis_lib
    c = redis_lib.Redis(port=port, socket_connect_timeout=0.5)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if c.ping():
                c.close()
                return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"redis on :{port} never came up")


@pytest.fixture(scope="session")
def redis_url():
    d = tempfile.mkdtemp(prefix="sw-redis-")
    proc = subprocess.Popen(
        ["redis-server", "--port", str(REDIS_PORT), "--dir", d,
         "--appendonly", "yes", "--save", "",
         "--logfile", os.path.join(d, "redis.log")],
    )
    _wait_ping(REDIS_PORT)
    yield f"redis://localhost:{REDIS_PORT}/0"
    proc.terminate()
    proc.wait(timeout=5)
    shutil.rmtree(d, ignore_errors=True)


def _clear_ledger():
    conn = psycopg2.connect(**PG_DSN)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("ALTER TABLE ledger_entries DISABLE TRIGGER USER;")
    cur.execute("TRUNCATE ledger_entries RESTART IDENTITY;")
    cur.execute("ALTER TABLE ledger_entries ENABLE TRIGGER USER;")
    conn.close()


@pytest.fixture()
def harness():
    h = IcebergProductionHarness({
        "postgres_host": PG_DSN["host"], "postgres_port": PG_DSN["port"],
        "postgres_db": PG_DSN["dbname"], "postgres_user": PG_DSN["user"],
        "postgres_password": PG_DSN["password"], "cassette_domain": "ivr",
    })
    _clear_ledger()
    yield h
    h.shutdown()


def make_worker(harness, redis_url, **kw):
    q = TransmissionQueue(name="w-" + uuid.uuid4().hex[:8], redis_url=redis_url)
    return SentinelWorker(harness, q, worker_id="w-" + uuid.uuid4().hex[:6], **kw)


def good_record(sid, digit="1", status="completed", duration=320):
    return {"sid": sid, "status": status, "from": f"+1555123456{digit}",
            "duration": duration, "start_time": 0}


# ------------------------------------------------------------ happy path --
def test_happy_path_governed_call_completes_and_is_recorded(harness, redis_url):
    w = make_worker(harness, redis_url)
    w.queue.enqueue(good_record("CAH1"), job_id="CAH1")
    job = w.queue.claim(w.worker_id, wait_timeout_s=2.0)
    outcome = w.handle_one(job)
    assert outcome is Outcome.OK
    assert harness.ledger.sid_exists("CAH1")
    assert w.queue.stats()["counters"]["completed"] == 1
    assert w.acked == 1 and w.failed == 0


def test_ungoverned_call_also_completes(harness, redis_url):
    # low friction / short duration -> below governance_trigger, no
    # governor call at all, still a fully successful job.
    w = make_worker(harness, redis_url)
    rec = good_record("CAH2", digit="4", duration=15)
    w.queue.enqueue(rec, job_id="CAH2")
    job = w.queue.claim(w.worker_id, wait_timeout_s=2.0)
    outcome = w.handle_one(job)
    assert outcome is Outcome.OK
    assert harness.ledger.sid_exists("CAH2") is False  # ungoverned: no ledger row expected
    # (governed=False path never calls append_decision at all)


def test_governed_and_blocked_is_still_a_completed_job(harness, redis_url):
    # No Claude client configured -> harness's own documented fail-closed
    # path: safe=False, but the decision IS durably recorded. That is a
    # successfully processed job, not a queue failure.
    w = make_worker(harness, redis_url)
    w.queue.enqueue(good_record("CAH3"), job_id="CAH3")
    job = w.queue.claim(w.worker_id, wait_timeout_s=2.0)
    outcome = w.handle_one(job)
    assert outcome is Outcome.OK
    assert harness.ledger.sid_exists("CAH3")


# --------------------------------------------------------------- bad input --
def test_bad_input_dead_letters_without_touching_the_ledger(harness, redis_url):
    w = make_worker(harness, redis_url)
    bad = {"sid": "CAH4", "status": "ringing", "from": "+15551234561"}  # not in TWILIO_TO_ICEBERG
    w.queue.enqueue(bad, job_id="CAH4")
    job = w.queue.claim(w.worker_id, wait_timeout_s=2.0)
    outcome = w.handle_one(job)
    assert outcome is Outcome.DEAD
    assert harness.ledger.sid_exists("CAH4") is False
    dead = w.queue.dlq_peek(1)[0]
    assert dead["dead_reason"] == "data_corruption"
    assert "Failed to parse call" in dead["error_trail"][0]["detail"]


def test_missing_sid_dead_letters(harness, redis_url):
    w = make_worker(harness, redis_url)
    w.queue.enqueue({"status": "completed", "from": "+15551234561",
                     "duration": 10}, job_id="CAH5")
    job = w.queue.claim(w.worker_id, wait_timeout_s=2.0)
    outcome = w.handle_one(job)
    assert outcome is Outcome.DEAD


# ------------------------------------------------- the F-2 shape, live -----
def test_ledger_write_failure_retries_and_never_acks(harness, redis_url, monkeypatch):
    """The exact bug this whole system exists to prevent, forced live:
    append_decision raises. The worker must fail(), never ack -- and on
    a later successful attempt, the job completes and IS recorded
    exactly once."""
    w = make_worker(harness, redis_url, claim_wait_s=2.0)
    w.queue.enqueue(good_record("CAH6"), job_id="CAH6")

    real_append = harness.ledger.append_decision
    calls = {"n": 0}

    def flaky_append(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated ledger outage")
        return real_append(*a, **kw)

    monkeypatch.setattr(harness.ledger, "append_decision", flaky_append)

    job1 = w.queue.claim(w.worker_id, wait_timeout_s=2.0)
    outcome1 = w.handle_one(job1)
    assert outcome1 is Outcome.SCHEDULED
    assert harness.ledger.sid_exists("CAH6") is False   # NOT acked, NOT recorded
    assert w.failed == 1 and w.acked == 0

    trail = w.queue.error_trail("CAH6")
    assert trail[0]["reason"] == "db_connection_loss"
    assert "NOT durably recorded" in trail[0]["detail"]

    # backoff elapses, job becomes claimable again
    time.sleep(1.2)
    job2 = w.queue.claim(w.worker_id, wait_timeout_s=2.0)
    assert job2 is not None and job2.attempt == 2
    outcome2 = w.handle_one(job2)
    assert outcome2 is Outcome.OK
    assert harness.ledger.sid_exists("CAH6") is True
    assert calls["n"] == 2                              # exactly one retry needed


# --------------------------------- crash between commit and ack, live -----
def test_worker_crash_between_ledger_commit_and_ack_causes_no_duplicate(
    harness, redis_url
):
    """Real crash-recovery integration: run process_call for real (ledger
    row IS committed), simulate the worker dying before it could ack
    (don't call ack), let the lease expire, reap, redeliver, and process
    again. Expect: exactly one ledger row, second attempt acked via the
    duplicate_sid path, zero data loss, zero duplication."""
    q = TransmissionQueue(name="crash-" + uuid.uuid4().hex[:8], redis_url=redis_url)
    w = SentinelWorker(harness, q, worker_id="doomed-worker")

    q.enqueue(good_record("CAH7"), job_id="CAH7")
    job = q.claim(w.worker_id, lease_ms=400, wait_timeout_s=2.0)
    result = harness.process_call(job.payload)     # the real, committing call
    assert result.get("ledger_write_failed") is False
    assert harness.ledger.sid_exists("CAH7") is True
    # worker "dies" here -- no ack, no fail, just gone

    time.sleep(0.6)                                  # lease expires
    report = q.reap_expired()
    assert report["requeued"] == ["CAH7"]
    trail = q.error_trail("CAH7")
    assert trail[0]["reason"] == "process_crash"

    rescuer = SentinelWorker(harness, q, worker_id="rescuer-worker")
    job2 = q.claim(rescuer.worker_id, wait_timeout_s=2.0)
    assert job2 is not None and job2.attempt == 2
    outcome = rescuer.handle_one(job2)
    assert outcome is Outcome.OK                      # acked via duplicate_sid path
    assert rescuer.acked == 1

    rows = [d for d in harness.ledger.get_decisions(limit=50)
            if d["input_data"].get("call_sid") == "CAH7"]
    assert len(rows) == 1, f"expected exactly one ledger row, found {len(rows)}"
    assert q.stats()["counters"]["completed"] == 1


# ------------------------------------------------------------ concurrency --
def test_multiple_workers_share_one_queue_no_dup_no_loss(harness, redis_url):
    q = TransmissionQueue(name="multi-" + uuid.uuid4().hex[:8], redis_url=redis_url)
    n = 25
    sids = []
    for i in range(n):
        sid = f"CAM{i:03d}"
        sids.append(sid)
        q.enqueue(good_record(sid, digit=str(i % 5)), job_id=sid)

    workers = [SentinelWorker(harness, q, worker_id=f"mw-{i}", claim_wait_s=0.3)
               for i in range(4)]

    def drain(w):
        while True:
            job = w.queue.claim(w.worker_id, wait_timeout_s=0.3)
            if job is None:
                return
            w.handle_one(job)

    threads = [threading.Thread(target=drain, args=(w,)) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total_acked = sum(w.acked for w in workers)
    assert total_acked == n
    for sid in sids:
        assert harness.ledger.sid_exists(sid)
    rows = [d for d in harness.ledger.get_decisions(limit=200)
            if d["input_data"].get("call_sid") in sids]
    assert len(rows) == n, "no duplicate ledger rows across concurrent workers"
    assert q.stats()["counters"]["completed"] == n
