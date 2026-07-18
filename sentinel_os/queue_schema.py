"""Sentinel OS -- "the transmission": Redis-backed job queue between
stateless ingress and the V12 governance worker pool.

UNIFIED BUILD (cage_match_report_v1.md). Fighter O -- the original,
chaos-verified engine -- is the canonical write path per the match
rules (full-match tie; Rule 3: longer verification lineage). Every
claim/fail/reap transition and every guarantee below is the original's,
unchanged. Fighter R's capabilities are grafted on top:

  grafted from the rebuild (Fighter R):
    * get_job()            read-only job lookup (rebuild-shaped view)
    * ping()               broker liveness
    * heartbeat()          lease renewal, fenced by the SAME owner check
                           as ack/fail (new lua/heartbeat.lua)
    * promote_due()        explicit promotion op (new lua/promote_due.lua)
    * flush_namespace()    test/ops namespace wipe
    * namespace= kwarg     constructs the queue in the rebuild's dialect
    * done records         ack() now leaves a TTL'd status='done' record
                           (completed_at_ms, optional result) in the job
                           hash plus a 'done' zset, instead of deleting
                           outright -- this is what makes a completed job
                           pollable and a post-completion resubmit a
                           dedup rather than a re-run. done_keep_ms=0
                           restores the v1 delete-on-ack behavior.
    * claim_token calls    ack/fail/heartbeat accept (job_id, claim_token)
                           in the rebuild's dialect
    * enqueue status view  enqueue returns a tuple/mapping hybrid:
                           (job_id, created) AND {job_id,status,deduped}

  DIALECTS. The wire-level fork this merge closes means two live callers
  speak two surfaces. The constructor picks the facade:
    * TransmissionQueue(name=...)       -> original dialect (default):
        enqueue -> (job_id, created)-compatible; claim -> ClaimedJob;
        ack(job) -> Outcome; fail(job, Reason, detail) -> (Outcome, backoff_ms)
    * TransmissionQueue(namespace=...)  -> rebuild dialect:
        claim -> dict with claim_token; ack(job_id, token, result) -> bool;
        fail(job_id, token, reason, error, retryable) -> str;
        rebuild defaults (max_attempts=3, 30s lease, 0.5s/30s backoff,
        deterministic backoff [jitter 0], 2s socket timeouts);
        read views use the rebuild's reason vocabulary
        (process_crash_restart, data_corruption_in_transit).
  One engine, one write path, two skins. See the queue contract doc.

Design invariant (the whole safety story hangs on this):
    Every admitted job ID lives in EXACTLY ONE of five structures at all
    times -- pending (list), scheduled (zset), processing (zset), dead
    (zset), done (zset; TTL-bounded) -- and every transition between
    them is a single atomic Lua script. There is no state a job can be
    in where a crash loses it, and no interleaving in which two workers
    hold the same job.

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
  api_server_v2.py calls enqueue(job_id=call_sid, payload=twilio_record)
  and polls job state via get_job(); rate limiting and circuit breaking
  live in front of, not inside, this queue.
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
from typing import Any, Dict, List, Optional, Tuple, Union

import redis

__all__ = [
    "Reason",
    "Outcome",
    "ClaimedJob",
    "EnqueueResult",
    "TransmissionQueue",
]

_LUA_DIR = Path(__file__).resolve().parent / "lua"
_OPS = ("enqueue", "claim", "ack", "fail", "reap", "requeue_dead",
        "heartbeat", "promote_due")


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

#: Reason vocabulary bridge: the rebuild's dialect uses two different
#: names. Stored state always uses the original's names (the canonical
#: write path); rebuild-dialect reads and writes translate at the edge.
_REASON_O2R = {
    "process_crash": "process_crash_restart",
    "data_corruption": "data_corruption_in_transit",
}
_REASON_R2O = {v: k for k, v in _REASON_O2R.items()}


def _reason_from_any(reason: Union[Reason, str]) -> Reason:
    """Accept the original's enum/names AND the rebuild's names."""
    if isinstance(reason, Reason):
        return reason
    canonical = _REASON_R2O.get(reason, reason)
    try:
        return Reason(canonical)
    except ValueError:
        return Reason.UNCLASSIFIED


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


class EnqueueResult(tuple):
    """One enqueue result, both dialect views.

    Tuple view (original):   job_id, created = q.enqueue(...)
    Mapping view (rebuild):  out["job_id"], out["status"], out["deduped"]
    """

    def __new__(cls, job_id: str, created: bool, status: str):
        self = super().__new__(cls, (job_id, bool(created)))
        self._view = {"job_id": job_id, "status": status,
                      "deduped": not created}
        return self

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._view[key]
        return super().__getitem__(key)

    def get(self, key: str, default=None):
        return self._view.get(key, default)

    def keys(self):
        return self._view.keys()


_TOKEN_SEP = "\x1f"  # joins (worker_id, claim_id) into an opaque claim_token


class TransmissionQueue:
    """Redis-backed queue with atomic claims, bounded diagnosable
    retries, crash recovery via lease reaping, lease renewal, and
    built-in observability. See module docstring for the guarantees
    and for the two constructor dialects."""

    def __init__(
        self,
        name: Optional[str] = None,
        redis_url: Optional[str] = None,
        client: Optional[redis.Redis] = None,
        *,
        namespace: Optional[str] = None,
        max_attempts: Optional[int] = None,
        lease_ms: Optional[int] = None,
        lease_seconds: Optional[float] = None,
        base_backoff_ms: Optional[int] = None,
        backoff_base: Optional[float] = None,
        max_backoff_ms: Optional[int] = None,
        backoff_cap: Optional[float] = None,
        jitter_ms: Optional[int] = None,
        promote_batch: int = 64,
        error_trail_keep: Optional[int] = None,
        done_keep_ms: int = 86_400_000,
        socket_timeout: Optional[float] = None,
        socket_connect_timeout: Optional[float] = None,
        health_check_interval: int = 30,
        max_connections: Optional[int] = None,
    ) -> None:
        # ---- dialect resolution -----------------------------------------
        if namespace is not None and name is not None:
            raise ValueError("pass name= (original dialect) OR namespace= "
                             "(rebuild dialect), not both")
        self.dialect = "rebuild" if namespace is not None else "original"
        if self.dialect == "rebuild":
            self.prefix = namespace
            self.name = namespace
            max_attempts = 3 if max_attempts is None else max_attempts
            _lease_ms = int((lease_seconds if lease_seconds is not None
                             else 30.0) * 1000) if lease_ms is None else lease_ms
            _base_ms = int((backoff_base if backoff_base is not None
                            else 0.5) * 1000) if base_backoff_ms is None else base_backoff_ms
            _max_ms = int((backoff_cap if backoff_cap is not None
                           else 30.0) * 1000) if max_backoff_ms is None else max_backoff_ms
            jitter_ms = 0 if jitter_ms is None else jitter_ms  # deterministic
            socket_timeout = 2.0 if socket_timeout is None else socket_timeout
            socket_connect_timeout = (2.0 if socket_connect_timeout is None
                                      else socket_connect_timeout)
            max_connections = 64 if max_connections is None else max_connections
        else:
            self.name = name or "v12"
            self.prefix = f"sq:{self.name}"
            max_attempts = 5 if max_attempts is None else max_attempts
            _lease_ms = 30_000 if lease_ms is None else lease_ms
            if base_backoff_ms is None:
                _base_ms = int(backoff_base * 1000) if backoff_base is not None else 1_000
            else:
                _base_ms = base_backoff_ms
            if max_backoff_ms is None:
                _max_ms = int(backoff_cap * 1000) if backoff_cap is not None else 60_000
            else:
                _max_ms = max_backoff_ms
            jitter_ms = 250 if jitter_ms is None else jitter_ms
            socket_timeout = 5.0 if socket_timeout is None else socket_timeout
            socket_connect_timeout = (2.0 if socket_connect_timeout is None
                                      else socket_connect_timeout)
            max_connections = 50 if max_connections is None else max_connections

        # ---- connection (the original's, verbatim) ----------------------
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

        self.max_attempts = int(max_attempts)
        self.lease_ms = int(_lease_ms)
        self.base_backoff_ms = int(_base_ms)
        self.max_backoff_ms = int(_max_ms)
        self.jitter_ms = int(jitter_ms)
        self.promote_batch = int(promote_batch)
        self.done_keep_ms = int(done_keep_ms)
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

    def _job_hash(self, job_id: str) -> Dict[str, str]:
        raw = self.r.hgetall(self._k(f"job:{job_id}"))
        return {k.decode(): v.decode() for k, v in raw.items()}

    # ------------------------------------------------------- enqueue --
    def enqueue(
        self,
        payload: Optional[Dict[str, Any]] = None,
        job_id: Optional[str] = None,
        *,
        max_attempts: Optional[int] = None,
    ) -> EnqueueResult:
        """Admit a job. Idempotent on job_id.

        For Sentinel, pass job_id=call_sid so an ingress retry of the
        same Twilio webhook cannot double-enqueue. Returns an
        EnqueueResult usable as (job_id, created) AND as
        {"job_id","status","deduped"}; created is False (deduped True)
        for a duplicate, and a duplicate NEVER resets or re-queues the
        existing job -- including one whose retained record is 'done'.
        Raises on any Redis failure -- an enqueue that did not happen
        must never look like one that did.
        """
        if payload is None:
            raise TypeError("enqueue() requires payload")
        jid = job_id or uuid.uuid4().hex
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        checksum = hashlib.sha256(body.encode("utf-8")).hexdigest()
        created, status = self._scripts["enqueue"](
            keys=[],
            args=[
                self.prefix,
                jid,
                body,
                checksum,
                int(max_attempts or self.max_attempts),
            ],
        )
        return EnqueueResult(jid, int(created) == 1, status.decode())

    # --------------------------------------------------------- claim --
    def claim(
        self,
        worker_id: str,
        *,
        lease_ms: Optional[int] = None,
        lease_seconds: Optional[float] = None,
        wait_timeout_s: float = 0.0,
        poll_interval_s: float = 0.05,
    ) -> Optional[Union[ClaimedJob, Dict[str, Any]]]:
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

        Return shape follows the constructor dialect: ClaimedJob
        (original) or a claim_token dict (rebuild).
        """
        if lease_seconds is not None and lease_ms is None:
            lease_ms = int(lease_seconds * 1000)
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
                job = ClaimedJob(
                    id=jid,
                    payload=json.loads(body.decode("utf-8")),
                    attempt=attempt,
                    worker_id=worker_id,
                    claim_id=claim_id,
                    enqueued_at_ms=enq_ms,
                    lease_deadline_ms=lease_dl,
                )
                if self.dialect == "rebuild":
                    return self._claim_view(job)
                return job
            if time.monotonic() >= deadline:
                return None
            time.sleep(min(poll_interval_s,
                           max(0.0, deadline - time.monotonic())))

    def _claim_view(self, job: ClaimedJob) -> Dict[str, Any]:
        """Rebuild-dialect claim shape: flat dict with an opaque
        claim_token that carries the original's (worker_id, claim_id)
        fence."""
        max_a = self.r.hget(self._k(f"job:{job.id}"), "max_attempts")
        return {
            "job_id": job.id,
            "payload": job.payload,
            "status": "processing",
            "attempts": job.attempt,
            "max_attempts": int(max_a) if max_a is not None else None,
            "claimed_by": job.worker_id,
            "claim_token": f"{job.worker_id}{_TOKEN_SEP}{job.claim_id}",
            "created_at": job.enqueued_at_ms / 1000.0,
            "lease_expires_at": job.lease_deadline_ms / 1000.0,
        }

    def _fence_from_token(self, job_id: str, claim_token: str) -> ClaimedJob:
        worker_id, _, claim_id = str(claim_token).partition(_TOKEN_SEP)
        return ClaimedJob(id=job_id, payload={}, attempt=0,
                          worker_id=worker_id, claim_id=claim_id,
                          enqueued_at_ms=0, lease_deadline_ms=0)

    # ----------------------------------------------------------- ack --
    def ack(
        self,
        job: Union[ClaimedJob, str],
        claim_token: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> Union[Outcome, bool]:
        """Complete a job. Call ONLY after the ledger write committed.

        Fenced: if this worker's lease expired and the job was
        reclaimed, returns Outcome.STALE (or GONE) and the other
        claim's state is untouched. Idempotent to retry on a dropped
        connection: a second ack of a completed job returns GONE.

        Original dialect: ack(claimed_job) -> Outcome.
        Rebuild dialect:  ack(job_id, claim_token, result=None) -> bool;
        result, if given, is persisted on the retained done record.
        """
        if isinstance(job, ClaimedJob):
            fence, r_style = job, False
        else:
            if claim_token is None:
                raise TypeError("ack(job_id, claim_token) requires the token")
            fence, r_style = self._fence_from_token(job, claim_token), True
        raw = self._scripts["ack"](
            keys=[],
            args=[self.prefix, fence.id, fence.worker_id, fence.claim_id,
                  json.dumps(result) if result is not None else "",
                  self.done_keep_ms],
        )
        out = Outcome(raw.decode())
        return (out is Outcome.OK) if r_style else out

    # ---------------------------------------------------------- fail --
    def fail(
        self,
        job: Union[ClaimedJob, str],
        reason: Union[Reason, str, None] = None,
        detail: Optional[str] = None,
        _r_error: Optional[str] = None,
        *,
        retryable: Optional[bool] = None,
        claim_token: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Union[Tuple[Outcome, Optional[int]], str]:
        """Report a failed attempt with WHY.

        Original dialect: fail(claimed_job, Reason, detail, retryable=)
        -> (outcome, backoff_ms). Retryable failures are scheduled with
        capped exponential backoff plus jitter; exhausted or
        non-retryable ones dead-letter with the full error trail intact.
        Fenced exactly like ack().

        Rebuild dialect: fail(job_id, claim_token, reason, error,
        retryable=True) -> "scheduled" | "dead" | "fenced". Accepts the
        rebuild's reason names; unknown reasons become 'unclassified'
        (the rebuild's own guard behavior).
        """
        if isinstance(job, ClaimedJob):
            fence, r_style = job, False
            f_reason = _reason_from_any(reason)
            f_detail = detail if detail is not None else ""
        else:
            token = claim_token if claim_token is not None else reason
            if token is None:
                raise TypeError("fail(job_id, claim_token, reason, error)")
            fence, r_style = self._fence_from_token(job, str(token)), True
            f_reason = _reason_from_any(detail if claim_token is None else reason)
            f_detail = (_r_error if claim_token is None else detail) or error or ""
            if retryable is None:
                retryable = True
        if retryable is None:
            retryable = RETRYABLE_DEFAULT[f_reason]
        jitter = secrets.randbelow(self.jitter_ms + 1) if self.jitter_ms else 0
        raw = self._scripts["fail"](
            keys=[],
            args=[self.prefix, fence.id, fence.worker_id, fence.claim_id,
                  f_reason.value, str(f_detail)[:2000], "1" if retryable else "0",
                  self.base_backoff_ms, self.max_backoff_ms, jitter,
                  self.error_trail_keep],
        ).decode()
        if raw.startswith("scheduled:"):
            out: Tuple[Outcome, Optional[int]] = (Outcome.SCHEDULED,
                                                  int(raw.split(":", 1)[1]))
        else:
            out = (Outcome(raw), None)
        if not r_style:
            return out
        if out[0] is Outcome.SCHEDULED:
            return "scheduled"
        if out[0] is Outcome.DEAD:
            return "dead"
        return "fenced"    # STALE and GONE both fence in the rebuild dialect

    # ------------------------------------------------------ heartbeat --
    def heartbeat(
        self,
        job: Union[ClaimedJob, str],
        claim_token: Optional[str] = None,
        lease_ms: Optional[int] = None,
        lease_seconds: Optional[float] = None,
    ) -> Union[Outcome, bool]:
        """GRAFT (from the rebuild): renew a live claim's lease.

        Runs under the same owner fence as ack/fail: only the worker
        holding the live (worker_id, claim_id) can extend it; a reaped
        or reclaimed claim gets STALE, a completed/vanished job GONE.
        The v1 engine had NO lease renewal -- a lease could only expire.

        Original dialect: heartbeat(claimed_job, lease_ms=) -> Outcome.
        Rebuild dialect:  heartbeat(job_id, claim_token, lease_seconds=)
        -> bool.
        """
        if isinstance(job, ClaimedJob):
            fence, r_style = job, False
        else:
            if claim_token is None:
                raise TypeError("heartbeat(job_id, claim_token)")
            fence, r_style = self._fence_from_token(job, claim_token), True
        if lease_seconds is not None and lease_ms is None:
            lease_ms = int(lease_seconds * 1000)
        lease = int(lease_ms if lease_ms is not None else self.lease_ms)
        raw = self._scripts["heartbeat"](
            keys=[],
            args=[self.prefix, fence.id, fence.worker_id, fence.claim_id, lease],
        ).decode()
        out = Outcome.OK if raw.startswith("ok:") else Outcome(raw)
        return (out is Outcome.OK) if r_style else out

    # ---------------------------------------------------- promote_due --
    def promote_due(self, batch: Optional[int] = None) -> int:
        """GRAFT (from the rebuild): promote due scheduled retries to
        pending as an explicit operation. claim() still promotes
        opportunistically exactly as v1 did; this exists for schedulers
        and operators. Returns the number promoted."""
        return int(self._scripts["promote_due"](
            keys=[], args=[self.prefix, int(batch or self.promote_batch)]
        ))

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

    # --------------------------------------------------------- get_job --
    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """GRAFT (from the rebuild): read-only lookup of the full job
        record, in the rebuild's view shape and reason vocabulary.
        None if the job never existed or its done record has expired
        (done_keep_ms). Never mutates anything."""
        h = self._job_hash(job_id)
        if not h:
            return None
        trail_raw = self.r.lrange(self._k(f"errors:{job_id}"), 0, -1)
        trail_o = [json.loads(x) for x in trail_raw][::-1]   # oldest first
        trail = [
            {
                "attempt": e.get("attempt"),
                "reason": _REASON_O2R.get(e.get("reason"), e.get("reason")),
                "error": e.get("detail"),
                "at": (e.get("at_ms") or 0) / 1000.0,
            }
            for e in trail_o
        ]
        job: Dict[str, Any] = {
            "job_id": h.get("id", job_id),
            "status": h.get("status"),
            "attempts": int(h.get("attempts", 0)),
            "max_attempts": int(h.get("max_attempts", 0)),
            "created_at": int(h.get("enqueued_at_ms", 0)) / 1000.0,
        }
        if "payload" in h:
            try:
                job["payload"] = json.loads(h["payload"])
            except (ValueError, TypeError):
                job["payload"] = h["payload"]
        updated_ms = None
        for f in ("completed_at_ms", "dead_at_ms", "next_retry_at_ms",
                  "claimed_at_ms"):
            if h.get(f):
                updated_ms = int(h[f])
                break
        if updated_ms is None and trail:
            updated_ms = int(trail[-1]["at"] * 1000)
        job["updated_at"] = (updated_ms
                             or int(h.get("enqueued_at_ms", 0))) / 1000.0
        if trail:
            job["error_trail"] = trail
            job["last_error"] = trail[-1]["error"]
        st = job["status"]
        if st == "scheduled" and h.get("next_retry_at_ms"):
            job["scheduled_for"] = int(h["next_retry_at_ms"]) / 1000.0
        elif st == "processing":
            job["claimed_by"] = h.get("claimed_by")
            if h.get("lease_deadline_ms"):
                job["lease_expires_at"] = int(h["lease_deadline_ms"]) / 1000.0
        elif st == "done":
            if h.get("completed_at_ms"):
                job["completed_at"] = int(h["completed_at_ms"]) / 1000.0
            if "result" in h:
                try:
                    job["result"] = json.loads(h["result"])
                except (ValueError, TypeError):
                    job["result"] = h["result"]
        elif st == "dead":
            job["dead_reason"] = _REASON_O2R.get(h.get("dead_reason"),
                                                 h.get("dead_reason"))
            if h.get("dead_at_ms"):
                job["died_at"] = int(h["dead_at_ms"]) / 1000.0
        return job

    def ping(self) -> bool:
        """GRAFT (from the rebuild): broker liveness."""
        return bool(self.r.ping())

    def flush_namespace(self) -> int:
        """GRAFT (from the rebuild): test/ops helper -- delete every key
        under this queue's prefix. Never used by the ingress."""
        n = 0
        for key in self.r.scan_iter(match=f"{self.prefix}:*", count=500):
            self.r.delete(key)
            n += 1
        return n

    # ------------------------------------------------- observability --
    def stats(self) -> Dict[str, Any]:
        """Depth, staleness, DLQ state, lifetime counters -- one call,
        cheap enough for a /metrics scrape. Superset of both dialects:
        the original's keys plus the rebuild's 'done' (lifetime
        completions) and 'oldest_pending_age_s'."""
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
        p.zcard(self._k("done"))                                     # 11 retained
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
            "done": int(counters.get("completed", 0)),   # lifetime, rebuild view
            "done_retained": r[11],           # records currently kept
            "oldest_pending_age_ms": oldest_pending_age_ms,
            "oldest_pending_age_s": (oldest_pending_age_ms / 1000.0
                                     if oldest_pending_age_ms is not None else None),
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
        call. Knows the grafted 'done' structure and prunes done-zset
        entries at the retention boundary before judging, so a record
        mid-expiry is never a false violation."""
        if self.done_keep_ms > 0:
            self.r.zremrangebyscore(
                self._k("done"), "-inf",
                self._now_ms() - max(0, self.done_keep_ms - 1000))
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
                "done": self.r.zscore(self._k("done"), jid) is not None,
            }
            places = [k for k, v in member.items() if v]
            if len(places) != 1:
                violations.append(f"{jid}: in {places or 'NO structure'} "
                                  f"(status={status})")
            elif places[0] != status:
                violations.append(f"{jid}: status={status} but held in "
                                  f"{places[0]}")

        for struct in ("scheduled", "processing", "dead", "done"):
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
