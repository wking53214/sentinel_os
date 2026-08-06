"""
queue_schema.py — the transmission (REBUILD).

NOTE FOR WILLIAM / FUTURE SESSIONS
----------------------------------
The original queue_schema.py + lua/ were built and live-verified in a prior
session (transmission_verification_report_v1.md) but were NEVER PUSHED to
origin — the repo HEAD is still 87ae59a. This file is a faithful rebuild of
the documented, verified contract so api_server_v2.py could be built and
chaos-tested against a real queue in this environment. Before merging,
diff this against the local original; the ingress touches only:
    enqueue(job_id, payload)   -> {"job_id","status","deduped"}
    get_job(job_id)            -> dict | None   (read-only lookup)
    stats()                    -> depth per state + oldest_pending_age_s
If the local original's names differ, adjust those three call sites in
api_server_v2.py (they are the only queue-facing calls it makes).

Contract implemented (from the verified transmission):
  * States: pending / scheduled / processing / done / dead
  * enqueue() idempotent on job_id (call sid) — existing jobs are never
    reset or re-queued; resubmission returns the same job_id + status.
  * Atomic transitions via 7 Lua scripts (enqueue, promote_due, claim,
    heartbeat, ack, fail, reap_expired).
  * claim-token fencing: a reaped job's original worker cannot ack/fail it.
  * Bounded retries with exponential backoff -> scheduled; exhausted or
    non-retryable failures -> dead with a taxonomy reason + error trail.
  * DLQ reason taxonomy (DR vocabulary): network_latency,
    service_interruption, data_corruption_in_transit, process_crash_restart,
    db_connection_loss, disk_exhaustion, unclassified.
  * Crash recovery from queue state alone (reap_expired).
  * stats() exposes depth/staleness/DLQ observability.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, Optional

import redis

DLQ_REASONS = frozenset({
    "network_latency",
    "service_interruption",
    "data_corruption_in_transit",
    "process_crash_restart",
    "db_connection_loss",
    "disk_exhaustion",
    "unclassified",
})

_LUA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lua")


def _load(name: str) -> str:
    with open(os.path.join(_LUA_DIR, f"{name}.lua"), "r", encoding="utf-8") as f:
        return f.read()


class TransmissionQueue:
    """Redis-backed job queue between the stateless ingress and V12 workers."""

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        namespace: str = "tq",
        max_attempts: int = 3,
        lease_seconds: float = 30.0,
        backoff_base: float = 0.5,
        backoff_cap: float = 30.0,
        socket_timeout: float = 2.0,
        socket_connect_timeout: float = 2.0,
        max_connections: int = 64,
    ) -> None:
        self.ns = namespace
        self.max_attempts = int(max_attempts)
        self.lease_seconds = float(lease_seconds)
        self.backoff_base = float(backoff_base)
        self.backoff_cap = float(backoff_cap)
        self.pool = redis.ConnectionPool.from_url(
            redis_url,
            max_connections=max_connections,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
            decode_responses=True,
        )
        self.r = redis.Redis(connection_pool=self.pool)
        self._enqueue = self.r.register_script(_load("enqueue"))
        self._promote = self.r.register_script(_load("promote_due"))
        self._claim = self.r.register_script(_load("claim"))
        self._heartbeat = self.r.register_script(_load("heartbeat"))
        self._ack = self.r.register_script(_load("ack"))
        self._fail = self.r.register_script(_load("fail"))
        self._reap = self.r.register_script(_load("reap_expired"))

    # ---- key helpers -----------------------------------------------------
    def _k(self, suffix: str) -> str:
        return f"{self.ns}:{suffix}"

    @property
    def _job_prefix(self) -> str:
        return self._k("job:")

    def _job_key(self, job_id: str) -> str:
        return self._job_prefix + job_id

    # ---- producer side (what the ingress uses) ---------------------------
    def enqueue(
        self,
        job_id: str,
        payload: Dict[str, Any],
        max_attempts: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Idempotent on job_id. Returns {"job_id","status","deduped"}."""
        outcome, status = self._enqueue(
            keys=[self._job_key(job_id), self._k("pending")],
            args=[job_id, json.dumps(payload), int(max_attempts or self.max_attempts)],
        )
        return {"job_id": job_id, "status": status, "deduped": outcome == "deduped"}

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Read-only lookup of the full job record. None if it never existed."""
        raw = self.r.hgetall(self._job_key(job_id))
        if not raw:
            return None
        job: Dict[str, Any] = dict(raw)
        for f in ("attempts", "max_attempts"):
            if f in job:
                job[f] = int(job[f])
        for f in ("created_at", "updated_at", "completed_at", "died_at",
                  "scheduled_for", "lease_expires_at"):
            if f in job:
                job[f] = float(job[f])
        for f in ("payload", "result", "error_trail"):
            if f in job:
                try:
                    job[f] = json.loads(job[f])
                except (ValueError, TypeError):
                    pass
        return job

    def stats(self) -> Dict[str, Any]:
        pipe = self.r.pipeline()
        pipe.llen(self._k("pending"))
        pipe.zcard(self._k("scheduled"))
        pipe.zcard(self._k("processing"))
        pipe.get(self._k("done_count"))
        pipe.llen(self._k("dead"))
        pipe.lindex(self._k("pending"), 0)
        pending, scheduled, processing, done, dead, oldest = pipe.execute()
        oldest_age = None
        if oldest:
            created = self.r.hget(self._job_key(oldest), "created_at")
            if created:
                oldest_age = max(0.0, time.time() - float(created))
        return {
            "pending": pending,
            "scheduled": scheduled,
            "processing": processing,
            "done": int(done or 0),
            "dead": dead,
            "oldest_pending_age_s": oldest_age,
        }

    def ping(self) -> bool:
        return bool(self.r.ping())

    # ---- worker side (what sentinel_worker.py uses) ----------------------
    def claim(self, worker_id: str, lease_seconds: Optional[float] = None) -> Optional[Dict[str, Any]]:
        self._promote(keys=[self._k("scheduled"), self._k("pending")],
                      args=[100, self._job_prefix])
        token = uuid.uuid4().hex
        raw = self._claim(
            keys=[self._k("pending"), self._k("processing")],
            args=[worker_id, float(lease_seconds or self.lease_seconds), token, self._job_prefix],
        )
        if not raw:
            return None
        job = dict(zip(raw[::2], raw[1::2]))
        job["attempts"] = int(job["attempts"])
        job["max_attempts"] = int(job["max_attempts"])
        try:
            job["payload"] = json.loads(job.get("payload", "{}"))
        except (ValueError, TypeError):
            pass
        return job

    def heartbeat(self, job_id: str, claim_token: str, lease_seconds: Optional[float] = None) -> bool:
        return bool(self._heartbeat(
            keys=[self._k("processing")],
            args=[job_id, claim_token, float(lease_seconds or self.lease_seconds), self._job_prefix],
        ))

    def ack(self, job_id: str, claim_token: str, result: Optional[Dict[str, Any]] = None) -> bool:
        return bool(self._ack(
            keys=[self._k("processing"), self._k("done_count")],
            args=[job_id, claim_token, json.dumps(result) if result else "", self._job_prefix],
        ))

    def fail(
        self,
        job_id: str,
        claim_token: str,
        reason: str,
        error: str,
        retryable: bool = True,
    ) -> str:
        if reason not in DLQ_REASONS:
            reason = "unclassified"
        out = self._fail(
            keys=[self._k("processing"), self._k("scheduled"), self._k("dead")],
            args=[job_id, claim_token, reason, error,
                  self.backoff_base, self.backoff_cap,
                  "0" if retryable else "1", self._job_prefix],
        )
        return out[0]  # "scheduled" | "dead" | "fenced"

    def reap_expired(self, limit: int = 100) -> Dict[str, int]:
        requeued, killed = self._reap(
            keys=[self._k("processing"), self._k("pending"), self._k("dead")],
            args=[limit, self._job_prefix],
        )
        return {"requeued": int(requeued), "dead": int(killed)}

    # ---- maintenance -----------------------------------------------------
    def flush_namespace(self) -> int:
        """Test helper: delete every key in this namespace. Never used by the ingress."""
        n = 0
        for key in self.r.scan_iter(match=f"{self.ns}:*", count=500):
            self.r.delete(key)
            n += 1
        return n
