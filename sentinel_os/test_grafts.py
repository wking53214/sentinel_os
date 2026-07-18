"""Graft verification suite for the UNIFIED transmission queue.

Every capability grafted from the rebuild (Fighter R) onto the original
engine (Fighter O) gets proven here, against real Redis, including the
fencing interactions between grafts and the original write path. The
original 18-test chaos suite (test_queue_schema.py) continues to guard
the pre-existing guarantees; this file only covers what is NEW.

Run: python3 -m pytest test_grafts.py -v
"""
from __future__ import annotations

import subprocess
import time
import uuid

import pytest
import redis as redis_lib

from queue_schema import (
    ClaimedJob,
    EnqueueResult,
    Outcome,
    Reason,
    TransmissionQueue,
)

PORT = 6403


@pytest.fixture(scope="session")
def redis_url(tmp_path_factory):
    d = tmp_path_factory.mktemp("redis")
    proc = subprocess.Popen(
        ["redis-server", "--port", str(PORT), "--dir", str(d),
         "--save", "", "--appendonly", "no",
         "--logfile", str(d / "redis.log")])
    c = redis_lib.Redis(port=PORT, socket_connect_timeout=0.5)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if c.ping():
                break
        except redis_lib.exceptions.RedisError:
            time.sleep(0.1)
    else:
        raise RuntimeError("redis never came up")
    c.close()
    yield f"redis://localhost:{PORT}/0"
    proc.terminate()
    proc.wait(timeout=5)


def make_o(url, **kw):
    kw.setdefault("jitter_ms", 0)
    return TransmissionQueue(name="g-" + uuid.uuid4().hex[:8],
                             redis_url=url, **kw)


def make_r(url, **kw):
    return TransmissionQueue(namespace="gr-" + uuid.uuid4().hex[:8],
                             redis_url=url, **kw)


# ---------------------------------------------------------------- G1 --
def test_G1_heartbeat_extends_lease_and_reap_respects_renewal(redis_url):
    """A heartbeat before expiry moves the deadline; the reaper honors
    the renewed deadline, and only reaps once the RENEWED lease lapses."""
    q = make_o(redis_url)
    q.enqueue({"x": 1}, job_id="HB1")
    job = q.claim("w1", lease_ms=400)
    assert isinstance(job, ClaimedJob)

    time.sleep(0.25)
    assert q.heartbeat(job, lease_ms=800) is Outcome.OK

    time.sleep(0.3)                       # original 400ms deadline has passed
    report = q.reap_expired()
    assert report["requeued"] == [] and report["dead"] == [], (
        "reaper must honor a renewed lease")

    assert q.ack(job) is Outcome.OK       # still the live claim holder
    inv = q.verify_invariants()
    assert inv["ok"], inv["violations"]


def test_G1b_heartbeat_after_reap_is_fenced(redis_url):
    """A worker that missed its lease and got reaped cannot resurrect the
    claim by heartbeating: same fence as ack/fail."""
    q = make_o(redis_url, max_attempts=5)
    q.enqueue({"x": 1}, job_id="HB2")
    zombie = q.claim("wz", lease_ms=200)
    time.sleep(0.35)
    report = q.reap_expired()
    assert report["requeued"] == ["HB2"]
    assert q.heartbeat(zombie, lease_ms=1000) is Outcome.STALE
    assert q.stats()["counters"]["stale_heartbeats"] == 1

    rescuer = q.claim("wr", lease_ms=5000)
    assert rescuer.attempt == 2
    assert q.heartbeat(zombie, lease_ms=1000) is Outcome.STALE
    assert q.ack(rescuer) is Outcome.OK
    assert q.heartbeat(rescuer) is Outcome.GONE     # completed -> gone
    inv = q.verify_invariants()
    assert inv["ok"], inv["violations"]


# ---------------------------------------------------------------- G2 --
def test_G2_promote_due_is_an_explicit_operation(redis_url):
    """fail() schedules; promote_due() alone (no claim) moves the due
    retry to pending with a consistent status field."""
    q = make_o(redis_url, base_backoff_ms=100, max_backoff_ms=100)
    q.enqueue({"x": 1}, job_id="PD1")
    job = q.claim("w1")
    out, backoff = q.fail(job, Reason.NETWORK_LATENCY, "warming up")
    assert out is Outcome.SCHEDULED and backoff == 100

    assert q.promote_due() == 0            # not due yet
    time.sleep(0.15)
    assert q.promote_due() == 1
    view = q.get_job("PD1")
    assert view["status"] == "pending"     # explicit op sets the field
    assert q.stats()["pending"] == 1 and q.stats()["scheduled"] == 0
    j2 = q.claim("w2")
    assert j2.id == "PD1" and j2.attempt == 2
    assert q.ack(j2) is Outcome.OK
    inv = q.verify_invariants()
    assert inv["ok"], inv["violations"]


# ---------------------------------------------------------------- G3 --
def test_G3_get_job_views_across_the_lifecycle(redis_url):
    """get_job renders every state read-only, in the rebuild's view
    shape, and returns None for a job that never existed."""
    q = make_o(redis_url, base_backoff_ms=100, max_backoff_ms=100,
               max_attempts=2)
    assert q.get_job("nope") is None

    q.enqueue({"call": 1}, job_id="GJ1")
    v = q.get_job("GJ1")
    assert v["status"] == "pending" and v["payload"] == {"call": 1}
    assert v["attempts"] == 0 and v["max_attempts"] == 2
    assert "error_trail" not in v

    job = q.claim("w1", lease_ms=5000)
    v = q.get_job("GJ1")
    assert v["status"] == "processing" and v["claimed_by"] == "w1"
    assert v["lease_expires_at"] is not None

    q.fail(job, Reason.NETWORK_LATENCY, "slow upstream")
    v = q.get_job("GJ1")
    assert v["status"] == "scheduled" and v["scheduled_for"] is not None
    assert v["error_trail"][0]["attempt"] == 1
    assert v["error_trail"][0]["reason"] == "network_latency"
    assert "slow upstream" in v["last_error"]

    time.sleep(0.15)
    job2 = q.claim("w1")
    q.fail(job2, Reason.DATA_CORRUPTION, "bad bytes", retryable=False)
    v = q.get_job("GJ1")
    assert v["status"] == "dead"
    assert v["dead_reason"] == "data_corruption_in_transit"  # rebuild vocab
    assert v["died_at"] is not None
    assert [e["attempt"] for e in v["error_trail"]] == [1, 2]


# ---------------------------------------------------------------- G4 --
def test_G4_done_record_views_dedup_and_gone_contract(redis_url):
    """Completion leaves a pollable done record: result persisted,
    post-completion resubmit dedups (never re-runs), second ack GONE,
    fail-after-done GONE, requeue_from_dlq-after-done GONE."""
    q = make_o(redis_url)
    q.enqueue({"call": 9}, job_id="DN1")
    job = q.claim("w1")
    assert q.ack(job) is Outcome.OK

    v = q.get_job("DN1")
    assert v["status"] == "done" and v["completed_at"] is not None

    res = q.enqueue({"call": 9}, job_id="DN1")      # resubmit after done
    jid, created = res
    assert created is False and res["deduped"] is True
    assert res["status"] == "done"
    assert q.claim("w2") is None                     # never re-queued

    assert q.ack(job) is Outcome.GONE                # ack retry contract
    assert q.fail(job, Reason.NETWORK_LATENCY, "late fail")[0] is Outcome.GONE
    assert q.requeue_from_dlq("DN1") is Outcome.GONE
    assert q.stats()["counters"]["completed"] == 1   # exactly once counted
    inv = q.verify_invariants()
    assert inv["ok"], inv["violations"]


def test_G4b_done_keep_zero_restores_v1_delete_on_ack(redis_url):
    q = make_o(redis_url, done_keep_ms=0)
    q.enqueue({"x": 1}, job_id="DZ1")
    assert q.ack(q.claim("w")) is Outcome.OK
    assert q.get_job("DZ1") is None                  # v1 behavior: gone
    _, created = q.enqueue({"x": 2}, job_id="DZ1")   # v1 behavior: fresh job
    assert created is True
    inv = q.verify_invariants()
    assert inv["ok"], inv["violations"]


def test_G4c_done_records_expire_at_the_retention_boundary(redis_url):
    q = make_o(redis_url, done_keep_ms=1200)
    q.enqueue({"x": 1}, job_id="DT1")
    assert q.ack(q.claim("w")) is Outcome.OK
    assert q.get_job("DT1")["status"] == "done"
    assert q.stats()["done_retained"] == 1
    time.sleep(1.4)
    assert q.get_job("DT1") is None                  # hash TTL fired
    inv = q.verify_invariants()                      # sweep prunes the zset
    assert inv["ok"], inv["violations"]
    assert q.stats()["done_retained"] == 0
    assert q.stats()["counters"]["completed"] == 1   # lifetime count survives


# ---------------------------------------------------------------- G5 --
def test_G5_ping_and_flush_namespace(redis_url):
    q = make_r(redis_url)
    assert q.ping() is True
    q.enqueue(payload={"x": 1}, job_id="F1")
    q.enqueue(payload={"x": 2}, job_id="F2")
    assert q.flush_namespace() > 0
    assert q.get_job("F1") is None and q.stats()["pending"] == 0


# ---------------------------------------------------------------- G6 --
def test_G6_rebuild_dialect_facade_end_to_end(redis_url):
    """namespace= construction speaks the rebuild's surface over the
    original engine: dict claims with claim_token, bool ack with result
    persistence, string fail outcomes, rebuild reason names accepted and
    rendered, rebuild defaults (max_attempts=3)."""
    q = make_r(redis_url, backoff_base=0.1, backoff_cap=0.1)
    out = q.enqueue(job_id="RD1", payload={"n": 1})
    assert isinstance(out, EnqueueResult)
    assert out["job_id"] == "RD1" and out["deduped"] is False
    assert out["status"] == "pending"
    dup = q.enqueue(job_id="RD1", payload={"n": 1})
    assert dup["deduped"] is True

    job = q.claim(worker_id="dw", lease_seconds=5.0)
    assert isinstance(job, dict)
    assert job["job_id"] == "RD1" and job["attempts"] == 1
    assert job["max_attempts"] == 3                  # rebuild default
    token = job["claim_token"]

    assert q.fail("RD1", token, "process_crash_restart",
                  "worker vanished") == "scheduled"
    v = q.get_job("RD1")
    assert v["error_trail"][0]["reason"] == "process_crash_restart"
    assert q.error_trail("RD1")[0]["reason"] == "process_crash"  # stored vocab

    time.sleep(0.15)
    job2 = q.claim(worker_id="dw")
    assert job2["attempts"] == 2
    assert q.fail("RD1", job2["claim_token"], "network_latency",
                  "timed out") == "scheduled"
    time.sleep(0.25)
    job3 = q.claim(worker_id="dw")
    assert q.fail("RD1", job3["claim_token"], "db_connection_loss",
                  "pg down") == "dead"               # budget of 3 exhausted
    assert q.get_job("RD1")["dead_reason"] == "db_connection_loss"

    # a zombie's fail after the job dead-lettered is fenced, stringly
    assert q.fail("RD1", token, "network_latency", "zombie") == "fenced"

    assert q.requeue_from_dlq("RD1") is Outcome.OK
    job4 = q.claim(worker_id="dw2", lease_seconds=5.0)
    assert q.heartbeat("RD1", job4["claim_token"], lease_seconds=9.0) is True
    assert q.ack("RD1", job4["claim_token"], {"governance": "ok"}) is True
    assert q.ack("RD1", job4["claim_token"]) is False        # gone -> False
    assert q.get_job("RD1")["result"] == {"governance": "ok"}

    o_view = TransmissionQueue(namespace=q.prefix, redis_url=redis_url)
    assert o_view.stats()["done"] == 1               # rebuild stats key
    inv = q.verify_invariants()
    assert inv["ok"], inv["violations"]


# ---------------------------------------------------------------- G7 --
def test_G7_dialects_share_one_engine_and_one_fence(redis_url):
    """The two facades are skins over the same keys: a job enqueued via
    the original dialect is claimable via a rebuild-dialect handle on
    the same prefix, and the fence spans both."""
    o = TransmissionQueue(name="xd-" + uuid.uuid4().hex[:6],
                          redis_url=redis_url, jitter_ms=0)
    r = TransmissionQueue(namespace=o.prefix, redis_url=redis_url)
    o.enqueue({"x": 1}, job_id="XD1")
    job = r.claim(worker_id="rw", lease_seconds=5.0)
    assert job["job_id"] == "XD1"
    zombie = ClaimedJob(id="XD1", payload={}, attempt=1, worker_id="ow",
                        claim_id="deadbeefdeadbeef", enqueued_at_ms=0,
                        lease_deadline_ms=0)
    assert o.ack(zombie) is Outcome.STALE            # wrong claim -> fenced
    assert r.ack("XD1", job["claim_token"]) is True
    assert o.get_job("XD1")["status"] == "done"
    inv = o.verify_invariants()
    assert inv["ok"], inv["violations"]
