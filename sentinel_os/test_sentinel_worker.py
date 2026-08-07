"""Live verification suite for sentinel_worker.py.

Runs against REAL Postgres and REAL Redis -- no mocks of either. The
Claude client is left unconfigured (no CLAUDE_API_KEY) so governed
episodes take GovernanceDecider's own documented "No API client
configured" fail-closed path -- a real code path in
governance_decider.py, not a test double, and one that still exercises
the full ledger write.

HARNESS (2026-08-07): re-pointed from IcebergProductionHarness onto
GovernanceHarness -- see sentinel_worker.py's module docstring and
GovernanceHarnessJobAdapter's own docstring for the two things that
had to change to make that work (GovernanceHarness refuses telephony
cassettes, so this worker now governs MortgageCassette decisions
instead of Twilio calls; and GovernanceHarness has no sid_exists-
equivalent redelivery-dedup, closed here via the new
PostgreSQLLedger.episode_decision_exists()). SentinelWorker's own
claim/process/ack-or-fail loop, lease/heartbeat, dead-letter routing,
and worker_id/processed/acked/failed counters are UNCHANGED and this
file proves it with the SAME scenarios the IVR-shaped version proved,
just against mortgage-shaped payloads: `good_record()`'s Twilio fields
(sid/status/from/duration) are gone, replaced by
governed_payload()/ungoverned_payload() below (requested/actual/
outcome_reasons, matching MortgageCassette's own vocabulary).

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

from cassettes.mortgage_cassette import MortgageCassette
from governance_harness import GovernanceHarness
from queue_schema import Outcome, TransmissionQueue
from sentinel_worker import GovernanceHarnessJobAdapter, SentinelWorker

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
    """Named `harness` for continuity with the original IVR-shaped
    suite (same role: the object handed to SentinelWorker), but this
    is now a GovernanceHarnessJobAdapter wrapping a real
    GovernanceHarness(MortgageCassette()) -- see sentinel_worker.py.
    `.harness` on the returned adapter reaches the underlying
    GovernanceHarness (`.harness.ledger`, `.harness.cassette`, ...)
    for tests that need to reach into it directly, the same way the
    original suite reached into a bare IcebergProductionHarness."""
    gh = GovernanceHarness({
        "postgres_host": PG_DSN["host"], "postgres_port": PG_DSN["port"],
        "postgres_db": PG_DSN["dbname"], "postgres_user": PG_DSN["user"],
        "postgres_password": PG_DSN["password"],
    }, MortgageCassette())
    adapter = GovernanceHarnessJobAdapter(gh)
    _clear_ledger()
    yield adapter
    adapter.shutdown()


def make_worker(harness, redis_url, **kw):
    q = TransmissionQueue(name="w-" + uuid.uuid4().hex[:8], redis_url=redis_url)
    return SentinelWorker(harness, q, worker_id="w-" + uuid.uuid4().hex[:6], **kw)


def governed_payload(episode_id, reason="reduced based on updated appraisal value on file"):
    """1 requested-vs-actual mismatch with a substantive reason on
    file -- >= mortgage's governance_trigger (1), so this is governed."""
    return {"episode_id": episode_id, "requested": {"amount": 300000.0},
            "actual": {"amount": 250000.0}, "outcome_reasons": [reason]}


def ungoverned_payload(episode_id):
    """Clean match, zero requested-vs-actual mismatches and zero thin
    reasons -- 0 issues, below governance_trigger, never governed."""
    return {"episode_id": episode_id,
            "requested": {"outcome": "approved", "amount": 300000.0},
            "actual": {"outcome": "approved", "amount": 300000.0}}


def malformed_payload(episode_id):
    """A requested-vs-actual mismatch with NO outcome_reasons on file
    -- the kernel's own invariant (episode.validate_episode) refuses
    this outright (EpisodeIntegrityError), the mortgage-domain analog
    of IVR's "not in TWILIO_TO_ICEBERG" unparseable call record."""
    return {"episode_id": episode_id, "requested": {"amount": 300000.0},
            "actual": {"amount": 250000.0}}


# ------------------------------------------------------------ happy path --
def test_happy_path_governed_episode_completes_and_is_recorded(harness, redis_url):
    w = make_worker(harness, redis_url)
    w.queue.enqueue(governed_payload("CAH1"), job_id="CAH1")
    job = w.queue.claim(w.worker_id, wait_timeout_s=2.0)
    outcome = w.handle_one(job)
    assert outcome is Outcome.OK
    assert harness.harness.ledger.episode_decision_exists("CAH1")
    assert w.queue.stats()["counters"]["completed"] == 1
    assert w.acked == 1 and w.failed == 0


def test_ungoverned_episode_also_completes(harness, redis_url):
    # 0 issues -> below governance_trigger, no governor call at all,
    # still a fully successful job.
    w = make_worker(harness, redis_url)
    w.queue.enqueue(ungoverned_payload("CAH2"), job_id="CAH2")
    job = w.queue.claim(w.worker_id, wait_timeout_s=2.0)
    outcome = w.handle_one(job)
    assert outcome is Outcome.OK
    assert harness.harness.ledger.episode_decision_exists("CAH2") is False  # ungoverned: no ledger row expected
    # (governed=False path never calls append_decision at all)


def test_governed_and_blocked_is_still_a_completed_job(harness, redis_url):
    # No Claude client configured -> GovernanceDecider's own documented
    # fail-closed path: safe=False, but the decision IS durably
    # recorded. That is a successfully processed job, not a queue
    # failure.
    w = make_worker(harness, redis_url)
    w.queue.enqueue(governed_payload("CAH3"), job_id="CAH3")
    job = w.queue.claim(w.worker_id, wait_timeout_s=2.0)
    outcome = w.handle_one(job)
    assert outcome is Outcome.OK
    assert harness.harness.ledger.episode_decision_exists("CAH3")


# --------------------------------------------------------------- bad input --
def test_bad_input_dead_letters_without_touching_the_ledger(harness, redis_url):
    w = make_worker(harness, redis_url)
    bad = malformed_payload("CAH4")  # mismatch, no outcome_reasons -> kernel refuses
    w.queue.enqueue(bad, job_id="CAH4")
    job = w.queue.claim(w.worker_id, wait_timeout_s=2.0)
    outcome = w.handle_one(job)
    assert outcome is Outcome.DEAD
    assert harness.harness.ledger.episode_decision_exists("CAH4") is False
    dead = w.queue.dlq_peek(1)[0]
    assert dead["dead_reason"] == "data_corruption"
    assert "Failed to parse job" in dead["error_trail"][0]["detail"]


def test_missing_episode_id_dead_letters(harness, redis_url):
    w = make_worker(harness, redis_url)
    w.queue.enqueue({"requested": {"amount": 300000.0}, "actual": {"amount": 250000.0},
                     "outcome_reasons": ["reduced based on updated appraisal value on file"]},
                    job_id="CAH5")
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
    w.queue.enqueue(governed_payload("CAH6"), job_id="CAH6")

    real_append = harness.harness.ledger.append_decision
    calls = {"n": 0}

    def flaky_append(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated ledger outage")
        return real_append(*a, **kw)

    monkeypatch.setattr(harness.harness.ledger, "append_decision", flaky_append)

    job1 = w.queue.claim(w.worker_id, wait_timeout_s=2.0)
    outcome1 = w.handle_one(job1)
    assert outcome1 is Outcome.SCHEDULED
    assert harness.harness.ledger.episode_decision_exists("CAH6") is False   # NOT acked, NOT recorded
    assert w.failed == 1 and w.acked == 0

    trail = w.queue.error_trail("CAH6")
    assert trail[0]["reason"] == "db_connection_loss"
    # This message is generated entirely inside handle_one (unchanged
    # by the harness re-point) from result["claude_safe"]/["intent"],
    # which the adapter now supplies as None/"mortgage" respectively --
    # see GovernanceHarnessJobAdapter.process_call's ledger_write_failed
    # branch.
    assert "NOT durably recorded" in trail[0]["detail"]

    # backoff elapses, job becomes claimable again
    time.sleep(1.2)
    job2 = w.queue.claim(w.worker_id, wait_timeout_s=2.0)
    assert job2 is not None and job2.attempt == 2
    outcome2 = w.handle_one(job2)
    assert outcome2 is Outcome.OK
    assert harness.harness.ledger.episode_decision_exists("CAH6") is True
    assert calls["n"] == 2                              # exactly one retry needed


# --------------------------------- crash between commit and ack, live -----
def test_worker_crash_between_ledger_commit_and_ack_causes_no_duplicate(
    harness, redis_url
):
    """Real crash-recovery integration: run process_call for real (ledger
    row IS committed), simulate the worker dying before it could ack
    (don't call ack), let the lease expire, reap, redeliver, and process
    again. Expect: exactly one ledger row, second attempt acked via the
    duplicate_sid path (episode_decision_exists -- the new dedup
    pre-check GovernanceHarness itself does not have), zero data loss,
    zero duplication."""
    q = TransmissionQueue(name="crash-" + uuid.uuid4().hex[:8], redis_url=redis_url)
    w = SentinelWorker(harness, q, worker_id="doomed-worker")

    q.enqueue(governed_payload("CAH7"), job_id="CAH7")
    job = q.claim(w.worker_id, lease_ms=400, wait_timeout_s=2.0)
    result = harness.process_call(job.payload)     # the real, committing call
    assert result.get("ledger_write_failed") is False
    assert harness.harness.ledger.episode_decision_exists("CAH7") is True
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

    rows = [d for d in harness.harness.ledger.get_decisions(limit=50)
            if d["input_data"].get("episode_id") == "CAH7"]
    assert len(rows) == 1, f"expected exactly one ledger row, found {len(rows)}"
    assert q.stats()["counters"]["completed"] == 1


# ------------------------------------------------------------ concurrency --
def test_multiple_workers_share_one_queue_no_dup_no_loss(harness, redis_url):
    q = TransmissionQueue(name="multi-" + uuid.uuid4().hex[:8], redis_url=redis_url)
    n = 25
    eids = []
    for i in range(n):
        eid = f"CAM{i:03d}"
        eids.append(eid)
        q.enqueue(governed_payload(eid), job_id=eid)

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
    for eid in eids:
        assert harness.harness.ledger.episode_decision_exists(eid)
    rows = [d for d in harness.harness.ledger.get_decisions(limit=200)
            if d["input_data"].get("episode_id") in eids]
    assert len(rows) == n, "no duplicate ledger rows across concurrent workers"
    assert q.stats()["counters"]["completed"] == n


# ------------------------------------------------------------- heartbeat --
# 2026-07-31: queue_schema.py's heartbeat() (lease renewal, grafted in from
# the rebuild) existed but was never called from this file -- see module
# docstring's HEARTBEAT section. These tests prove the fix both ways: the
# danger is real without it, and the reaper-driven heartbeat closes it.
# Unaffected by the GovernanceHarness re-point -- they exercise the reaper
# thread and process_call's TIMING, not any harness-specific behavior.

def test_without_heartbeat_a_slow_call_would_get_reclaimed(harness, redis_url):
    """Documents the bug this session fixed, not the fix itself: claim a
    job with a short lease, run something slow (simulating a real
    process_call) WITHOUT ever heartbeating, and confirm a concurrent
    reap sweep -- exactly what start_reaper()'s timer does on every other
    worker -- really does reclaim it out from under the still-working
    claim. If this test ever starts failing, something upstream (Redis
    lease semantics, reap_expired's own logic) changed, not this file."""
    q = TransmissionQueue(name="hb-danger-" + uuid.uuid4().hex[:8], redis_url=redis_url)
    q.enqueue(governed_payload("HBDANGER1"), job_id="HBDANGER1")
    job = q.claim("slow-worker", lease_ms=300, wait_timeout_s=2.0)
    assert job is not None

    time.sleep(0.5)                       # no heartbeat sent -- lease lapses
    report = q.reap_expired()
    assert report["requeued"] == ["HBDANGER1"], (
        "a lease with nothing renewing it must be reclaimed -- this is "
        "the exact failure mode heartbeat wiring exists to prevent")


def test_reaper_heartbeats_this_workers_in_flight_job(harness, redis_url):
    """The fix: a slow process_call's lease survives, via the SAME reaper
    timer, even though the lease alone is far too short to cover it."""
    q = TransmissionQueue(name="hb-fix-" + uuid.uuid4().hex[:8], redis_url=redis_url)
    w = SentinelWorker(harness, q, worker_id="hb-worker", reap_interval_s=0.1)

    real_process_call = harness.process_call

    def slow_process_call(payload):
        time.sleep(0.8)                   # several times the 250ms lease
        return real_process_call(payload)

    harness.process_call = slow_process_call
    try:
        q.enqueue(governed_payload("HBFIX1"), job_id="HBFIX1")
        job = q.claim(w.worker_id, lease_ms=250, wait_timeout_s=2.0)
        w.start_reaper()
        try:
            outcome = w.handle_one(job)
        finally:
            w.stop()
    finally:
        harness.process_call = real_process_call

    assert outcome is Outcome.OK
    assert w.acked == 1
    assert w.failed == 0
    rows = [d for d in harness.harness.ledger.get_decisions(limit=50)
            if d["input_data"].get("episode_id") == "HBFIX1"]
    assert len(rows) == 1, (
        "exactly one ledger row -- if the lease had lapsed and the job "
        "got redelivered to a second worker, either this worker's own "
        "ack would come back non-OK, or there would be a second row"
    )


def test_current_job_is_cleared_after_handling_so_the_reaper_stops_touching_it(
    harness, redis_url,
):
    """A completed job must not keep getting heartbeated forever -- once
    handle_one returns, _current_job goes back to None."""
    q = TransmissionQueue(name="hb-clear-" + uuid.uuid4().hex[:8], redis_url=redis_url)
    w = SentinelWorker(harness, q, worker_id="hb-clear-worker")
    q.enqueue(governed_payload("HBCLEAR1"), job_id="HBCLEAR1")
    job = q.claim(w.worker_id, wait_timeout_s=2.0)

    assert w._current_job is None          # nothing in flight yet
    w.handle_one(job)
    assert w._current_job is None          # cleared after handling


def test_reaper_tolerates_no_in_flight_job(harness, redis_url):
    """The common case: reaper ticks happen constantly while a worker is
    idle between claims. Must not error just because there's nothing to
    heartbeat."""
    q = TransmissionQueue(name="hb-idle-" + uuid.uuid4().hex[:8], redis_url=redis_url)
    w = SentinelWorker(harness, q, worker_id="hb-idle-worker", reap_interval_s=0.1)
    w.start_reaper()
    time.sleep(0.35)                       # several idle reaper ticks
    w.stop()
    # No exception escaping the background thread is the assertion --
    # nothing here would raise visibly in the test process if the reaper
    # thread itself died, so this is really just "the worker is still
    # usable afterward":
    q.enqueue(governed_payload("HBIDLE1"), job_id="HBIDLE1")
    job = q.claim(w.worker_id, wait_timeout_s=2.0)
    assert w.handle_one(job) is Outcome.OK
