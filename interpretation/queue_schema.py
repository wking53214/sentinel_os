"""Sentinel OS -- "the transmission": Redis-backed job queue between
stateless ingress and the V12 governance worker pool.

Design invariant (the whole safety story hangs on this):
    Every admitted job ID lives in EXACTLY ONE of four structures at all
    times -- pending (list), scheduled (zset), processing (zset), dead
    (zset) -- and every transition between them is a single atomic Lua
    script. There is no state a job can be in where a crash loses it,
    and no interleaving in which two workers hold the same job.

Delivery semantics (explicit, because the swallowed-print bug was a
semantics failure, not a code typo):
    The queue is AT-LEAST-ONCE. Exactly-once *effect* is achieved by the
    dedup at each end of the pipe, not by the pipe:
      ingress retry  -> enqueue() is idempotent on job_id (use call_sid)
      worker retry   -> the Postgres ledger dedups on call_sid
    Worker contract: claim -> do work -> COMMIT LEDGER WRITE -> ack.
    Ack only after the ledger commit. If the worker dies between commit
    and ack, the job is retried and the ledger's sid dedup absorbs it.
    A job must NEVER be acked on a failed ledger write -- fail() it with
    a reason instead. Failures here are loud: every Redis error raises;
    nothing is caught-and-printed.

Clock authority: Redis server TIME, read inside each Lua script. Worker
host clocks never participate in lease or retry arithmetic.

Deployment notes:
  - Redis persistence: run with appendonly yes. appendfsync everysec
    means a kill -9 of Redis itself can lose up to ~1s of acknowledged
    enqueues; appendfsync always closes that window at a write-latency
    cost. Choose per environment; the crash test in the suite runs
    'always' so the zero-loss claim it makes is honest.
  - Single Redis instance / primary assumed. Keys are derived from a
    prefix inside Lua, which is not Redis-Cluster slot-safe. Sentinel
    (Redis Sentinel) failover is compatible in principle but is NOT
    verified by this suite -- see the verification report.

Out of scope by design (clean seams for the other roadster pieces):
  sentinel_worker.py consumes ClaimedJob and calls ack/fail;
  api_server_v2.py calls enqueue(payload=twilio_record, job_id=call_sid)
  and polls job state; rate limiting and circuit breaking live in front
  of, not inside, this queue.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import redis

__all__ = [
    "Reason",
    "Outcome",
    "ClaimedJob",
    "TransmissionQueue",
]

_LUA_DIR = Path(__file__).resolve().parent / "lua"
_OPS = ("enqueue", "claim", "ack", "fail", "reap", "requeue_dead")


class Reason(str, Enum):
    """Dead-letter reason taxonomy.

    Reuses the project's established disaster-recovery vocabulary
    verbatim rather than inventing a parallel one.
    """

    NETWORK_LATENCY = "network_latency"          # timeouts, slow upstream
    SERVICE_INTERRUPTION = "service_interruption"  # upstream refused/unavailable
    DATA_CORRUPTION = "data_corruption"          # payload failed checksum/schema
    PROCESS_CRASH = "process_crash"              # worker died/stalled; lease expired
    DB_CONNECTION_LOSS = "db_connection_loss"    # ledger/Postgres connectivity
    DISK_EXHAUSTION = "disk_exhaustion"          # ENOSPC anywhere in the path
    UNCLASSIFIED = "unclassified"                # unknown -> escalate=1 on the job


#: Default retryability per reason. A corrupted payload will not heal by
#: retrying; everything else gets its bounded budget. Workers may override
#: per-call via fail(..., retryable=...).
RETRYABLE_DEFAULT: Dict[Reason, bool] = {
    Reason.NETWORK_LATENCY: True,
    Reason.SERVICE_INTERRUPTION: True,
    Reason.DATA_CORRUPTION: False,
    Reason.PROCESS_CRASH: True,
    Reason.DB_CONNECTION_LOSS: True,
    Reason.DISK_EXHAUSTION: True,
    Reason.UNCLASSIFIED: True,
}


class Outcome(str, Enum):
    """Result of ack()/fail() under the claim fence."""

    OK = "ok"            # transition applied
    DEAD = "dead"        # fail(): job moved to the dead-letter set
    SCHEDULED = "scheduled"  # fail(): job scheduled for a backed-off retry
    GONE = "gone"        # job no longer exists (completed/removed elsewhere)
    STALE = "stale"      # your lease expired and the job was reclaimed;
    #                      your side effects are NOT the canonical run --
    #                      the ledger's sid dedup is what makes this safe.


@dataclass(frozen=True)
class ClaimedJob:
    """What claim() hands a worker. Pass it back to ack()/fail()."""

    id: str
    payload: Dict[str, Any]
    attempt: int              # 1-based execution count including this one
    worker_id: str
    claim_id: str             # fencing token for this specific claim
    enqueued_at_ms: int
    lease_deadline_ms: int    # per the Redis clock, not the worker's


class TransmissionQueue:
    """Redis-backed queue with atomic claims, bounded diagnosable
    retries, crash recovery via lease reaping, and built-in
    observability. See module docstring for the guarantees."""

    def __init__(
        self,
        name: str = "v12",
        redis_url: Optional[str] = None,
        client: Optional[redis.Redis] = None,
        *,
        max_attempts: int = 5,
        lease_ms: int = 30_000,
        base_backoff_ms: int = 1_000,
        max_backoff_ms: int = 60_000,
        jitter_ms: int = 250,
        promote_batch: int = 64,
        error_trail_keep: Optional[int] = None,
        socket_timeout: float = 5.0,
        socket_connect_timeout: float = 2.0,
        health_check_interval: int = 30,
        max_connections: int = 50,
    ) -> None:
        if client is not None:
            self.r = client
        else:
            url = redis_url or os.environ.get(
                "SENTINEL_REDIS_URL", "redis://localhost:6379/0"
            )
            # retry_on_timeout is deliberately OFF: blind client-side
            # retries of non-idempotent ops are how silent duplicates
            # happen. enqueue() is idempotent, so callers may retry it
            # explicitly; claim/ack/fail races are covered by the fence.
            pool = redis.ConnectionPool.from_url(
                url,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_connect_timeout,
                health_check_interval=health_check_interval,
                max_connections=max_connections,
                retry_on_timeout=False,
                decode_responses=False,
            )
            self.r = redis.Redis(connection_pool=pool)

        self.name = name
        self.prefix = f"sq:{name}"
        self.max_attempts = int(max_attempts)
        self.lease_ms = int(lease_ms)
        self.base_backoff_ms = int(base_backoff_ms)
        self.max_backoff_ms = int(max_backoff_ms)
        self.jitter_ms = int(jitter_ms)
        self.promote_batch = int(promote_batch)
        self.error_trail_keep = int(
            error_trail_keep if error_trail_keep is not None else max_attempts + 3
        )

        common = (_LUA_DIR / "_common.lua").read_text()
        self._scripts = {
            op: self.r.register_script(
                common + "\n" + (_LUA_DIR / f"{op}.lua").read_text()
            )
            for op in _OPS
        }

    # ---------------------------------------------------------- keys --
    def _k(self, suffix: str) -> str:
        return f"{self.prefix}:{suffix}"

    def _now_ms(self) -> int:
        sec, usec = self.r.time()
        return int(sec) * 1000 + int(usec) // 1000

    # ------------------------------------------------------- enqueue --
    def enqueue(
        self,
        payload: Dict[str, Any],
        job_id: Optional[str] = None,
        *,
        max_attempts: Optional[int] = None,
    ) -> Tuple[str, bool]:
        """Admit a job. Idempotent on job_id.

        For Sentinel, pass job_id=call_sid so an ingress retry of the
        same Twilio webhook cannot double-enqueue. Returns
        (job_id, created); created is False for a duplicate.
        Raises on any Redis failure -- an enqueue that did not happen
        must never look like one that did.
        """
        jid = job_id or uuid.uuid4().hex
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        checksum = hashlib.sha256(body.encode("utf-8")).hexdigest()
        created = self._scripts["enqueue"](
            keys=[],
            args=[
                self.prefix,
                jid,
                body,
                checksum,
                int(max_attempts or self.max_attempts),
            ],
        )
        return jid, int(created) == 1

    # --------------------------------------------------------- claim --
    def claim(
        self,
        worker_id: str,
        *,
        lease_ms: Optional[int] = None,
        wait_timeout_s: float = 0.0,
        poll_interval_s: float = 0.05,
    ) -> Optional[ClaimedJob]:
        """Atomically take exactly one job, or None if none is ready
        within wait_timeout_s.

        Polling rather than BLMOVE is a deliberate trade: a blocking
        move would need a two-step claim (move, then stamp lease) and a
        second recovery path for crashes between the steps. One atomic
        script plus <=poll_interval_s added latency wins for a queue
        whose failure mode of record is silent loss, not milliseconds.

        Payload integrity is verified here (sha256 vs the checksum
        stamped at enqueue). A corrupted payload is dead-lettered with
        reason=data_corruption and evidence, the worker never sees it,
        and the loop keeps draining.
        """
        lease = int(lease_ms if lease_ms is not None else self.lease_ms)
        deadline = time.monotonic() + max(0.0, wait_timeout_s)
        while True:
            claim_id = secrets.token_hex(8)
            res = self._scripts["claim"](
                keys=[],
                args=[self.prefix, worker_id, claim_id, lease,
                      self.promote_batch, 8],
            )
            if res:
                jid = res[0].decode()
                body = res[1]
                stored_sum = res[2].decode()
                attempt = int(res[3])
                enq_ms = int(res[4])
                lease_dl = int(res[5])
                actual_sum = hashlib.sha256(body).hexdigest()
                if actual_sum != stored_sum:
                    detail = (
                        f"payload checksum mismatch: stored {stored_sum[:12]}.. "
                        f"actual {actual_sum[:12]}.. ({len(body)} bytes)"
                    )
                    self._scripts["fail"](
                        keys=[],
                        args=[self.prefix, jid, worker_id, claim_id,
                              Reason.DATA_CORRUPTION.value, detail, "0",
                              self.base_backoff_ms, self.max_backoff_ms, 0,
                              self.error_trail_keep],
                    )
                    self.r.hincrby(self._k("counters"), "corrupt_payloads", 1)
                    continue  # keep draining; the bad job is quarantined
                return ClaimedJob(
                    id=jid,
                    payload=json.loads(body.decode("utf-8")),
                    attempt=attempt,
                    worker_id=worker_id,
                    claim_id=claim_id,
                    enqueued_at_ms=enq_ms,
                    lease_deadline_ms=lease_dl,
                )
            if time.monotonic() >= deadline:
                return None
            time.sleep(min(poll_interval_s,
                           max(0.0, deadline - time.monotonic())))

    # ----------------------------------------------------------- ack --
    def ack(self, job: ClaimedJob) -> Outcome:
        """Complete a job. Call ONLY after the ledger write committed.

        Fenced: if this worker's lease expired and the job was
        reclaimed, returns Outcome.STALE (or GONE) and the other
        claim's state is untouched. Idempotent to retry on a dropped
        connection: a second ack of a completed job returns GONE.
        """
        raw = self._scripts["ack"](
            keys=[], args=[self.prefix, job.id, job.worker_id, job.claim_id]
        )
        return Outcome(raw.decode())

    # ---------------------------------------------------------- fail --
    def fail(
        self,
        job: ClaimedJob,
        reason: Reason,
        detail: str,
        *,
        retryable: Optional[bool] = None,
    ) -> Tuple[Outcome, Optional[int]]:
        """Report a failed attempt with WHY. Returns (outcome, backoff_ms).

        Retryable failures are scheduled with capped exponential backoff
        plus jitter; exhausted or non-retryable ones dead-letter with the
        full error trail intact. Fenced exactly like ack().
        """
        if retryable is None:
            retryable = RETRYABLE_DEFAULT[reason]
        jitter = secrets.randbelow(self.jitter_ms + 1) if self.jitter_ms else 0
        raw = self._scripts["fail"](
            keys=[],
            args=[self.prefix, job.id, job.worker_id, job.claim_id,
                  reason.value, str(detail)[:2000], "1" if retryable else "0",
                  self.base_backoff_ms, self.max_backoff_ms, jitter,
                  self.error_trail_keep],
        ).decode()
        if raw.startswith("scheduled:"):
            return Outcome.SCHEDULED, int(raw.split(":", 1)[1])
        return Outcome(raw), None

    # ---------------------------------------------------------- reap --
    def reap_expired(self, batch: int = 100) -> Dict[str, List[str]]:
        """Recover jobs whose worker crashed or stalled past its lease.

        Idempotent and safe to run from any/every worker on a timer; the
        queue's own state is sufficient -- no worker registry needed.
        """
        raw = self._scripts["reap"](
            keys=[], args=[self.prefix, int(batch), self.error_trail_keep]
        )
        report: Dict[str, List[str]] = {"requeued": [], "dead": [], "orphaned": []}
        for item in raw:
            jid, disposition = item.decode().rsplit(":", 1)
            key = {"requeued": "requeued", "dead": "dead",
                   "orphan": "orphaned"}[disposition]
            report[key].append(jid)
        return report

    # ------------------------------------------------- observability --
    def stats(self) -> Dict[str, Any]:
        """Depth, staleness, DLQ state, lifetime counters -- one call,
        cheap enough for a /metrics scrape."""
        now = self._now_ms()
        p = self.r.pipeline(transaction=False)
        p.llen(self._k("pending"))                                   # 0
        p.zcard(self._k("scheduled"))                                # 1
        p.zcount(self._k("scheduled"), "-inf", now)                  # 2
        p.zcard(self._k("processing"))                               # 3
        p.zcount(self._k("processing"), "-inf", now)                 # 4 overdue
        p.zcard(self._k("dead"))                                     # 5
        p.zcount(self._k("dead"), now - 3_600_000, now)              # 6 last hr
        p.hgetall(self._k("counters"))                               # 7
        p.hgetall(self._k("dead_reasons"))                           # 8
        p.lindex(self._k("pending"), -1)                             # 9 head
        p.llen(self._k("orphans"))                                   # 10
        r = p.execute()

        oldest_pending_age_ms = None
        if r[9] is not None:
            enq = self.r.hget(self._k("job:" + r[9].decode()), "enqueued_at_ms")
            if enq is not None:
                oldest_pending_age_ms = now - int(enq)

        dec = lambda h: {k.decode(): v.decode() for k, v in h.items()}
        counters = {k: int(v) if v.lstrip("-").isdigit() else v
                    for k, v in dec(r[7]).items()}
        return {
            "now_ms": now,
            "depth_ready": r[0] + r[2],       # claimable right now
            "pending": r[0],
            "scheduled": r[1],
            "scheduled_due": r[2],
            "processing": r[3],
            "processing_overdue": r[4],       # >0 means reaper is behind
            "dead": r[5],
            "dead_last_hour": r[6],
            "oldest_pending_age_ms": oldest_pending_age_ms,
            "orphan_refs": r[10],
            "counters": counters,
            "dead_reasons": {k: int(v) for k, v in dec(r[8]).items()},
        }

    def dlq_rate(self, window_s: int = 3600) -> Dict[str, float]:
        """Dead-letter arrivals in the trailing window."""
        now = self._now_ms()
        n = self.r.zcount(self._k("dead"), now - window_s * 1000, now)
        return {"window_s": float(window_s), "count": float(n),
                "per_minute": n / (window_s / 60.0)}

    def error_trail(self, job_id: str) -> List[Dict[str, Any]]:
        """Newest-first record of every failed attempt: attempt number,
        reason, detail, worker, timestamp."""
        raw = self.r.lrange(self._k(f"errors:{job_id}"), 0, -1)
        return [json.loads(x) for x in raw]

    def dlq_peek(self, n: int = 10) -> List[Dict[str, Any]]:
        """Most recent dead jobs with their full diagnosis -- the WHY,
        not just the THAT."""
        ids = self.r.zrevrange(self._k("dead"), 0, n - 1)
        out = []
        for bid in ids:
            jid = bid.decode()
            h = self.r.hgetall(self._k(f"job:{jid}"))
            job = {k.decode(): v.decode() for k, v in h.items()}
            job["error_trail"] = self.error_trail(jid)
            out.append(job)
        return out

    def requeue_from_dlq(self, job_id: str) -> Outcome:
        """Operator action: return a dead job to pending with a fresh
        attempt budget. Its error trail is preserved."""
        raw = self._scripts["requeue_dead"](
            keys=[], args=[self.prefix, job_id]
        ).decode()
        if raw == "ok":
            return Outcome.OK
        if raw == "gone":
            return Outcome.GONE
        return Outcome.STALE  # 'not_dead': job is live elsewhere

    # ------------------------------------------------ invariant sweep --
    def verify_invariants(self) -> Dict[str, Any]:
        """Audit: every job hash's ID appears in exactly one structure,
        the structure matches its status field, and every structure
        member has a job hash. O(N) -- a test/ops tool, not a hot-path
        call."""
        violations: List[str] = []
        pending = [x.decode() for x in self.r.lrange(self._k("pending"), 0, -1)]
        pending_set = set(pending)
        if len(pending) != len(pending_set):
            violations.append("duplicate ids inside pending list")

        checked = 0
        job_ids = set()
        for key in self.r.scan_iter(match=self._k("job:*"), count=500):
            jid = key.decode().rsplit(":", 1)[1]
            job_ids.add(jid)
            checked += 1
            status = self.r.hget(key, "status")
            status = status.decode() if status else None
            member = {
                "pending": jid in pending_set,
                "scheduled": self.r.zscore(self._k("scheduled"), jid) is not None,
                "processing": self.r.zscore(self._k("processing"), jid) is not None,
                "dead": self.r.zscore(self._k("dead"), jid) is not None,
            }
            places = [k for k, v in member.items() if v]
            if len(places) != 1:
                violations.append(f"{jid}: in {places or 'NO structure'} "
                                  f"(status={status})")
            elif places[0] != status:
                violations.append(f"{jid}: status={status} but held in "
                                  f"{places[0]}")

        for struct in ("scheduled", "processing", "dead"):
            for bid in self.r.zrange(self._k(struct), 0, -1):
                if bid.decode() not in job_ids:
                    violations.append(f"{bid.decode()}: in {struct} with no "
                                      f"job hash")
        for jid in pending_set - job_ids:
            violations.append(f"{jid}: in pending with no job hash")

        return {"ok": not violations, "checked": checked,
                "violations": violations}

    # ------------------------------------------------------- helpers --
    @staticmethod
    def classify_exception(exc: BaseException) -> Reason:
        """Best-effort mapping from a worker exception to the taxonomy.
        A suggestion helper only -- the worker owns final classification."""
        name = type(exc).__name__.lower()
        text = str(exc).lower()
        if isinstance(exc, TimeoutError) or "timeout" in name:
            return Reason.NETWORK_LATENCY
        if getattr(exc, "errno", None) == errno.ENOSPC or "no space left" in text:
            return Reason.DISK_EXHAUSTION
        if name in ("operationalerror", "interfaceerror"):
            return Reason.DB_CONNECTION_LOSS
        if isinstance(exc, ConnectionError) or "connection" in name:
            return Reason.SERVICE_INTERRUPTION
        if name in ("jsondecodeerror", "unicodedecodeerror"):
            return Reason.DATA_CORRUPTION
        return Reason.UNCLASSIFIED

    def close(self) -> None:
        self.r.close()
