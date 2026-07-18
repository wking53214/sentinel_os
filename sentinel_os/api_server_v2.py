"""
api_server_v2.py — stateless ingress (roadster tier 1).

Replaces api_server.py / api_server_resilient.py's synchronous call chain.
The old shape (fifth-pass audit, F-A + F-E):

    HTTP -> async handler -> ResilientHarness.process_call()
              -> SHARED CircuitBreaker(failure_threshold=5, timeout=60)
              -> V12 engine + governor + ledger, synchronously, on the
                 event loop.

  * F-A: two bad calls open the ONE breaker; every caller's submission
    then fails for 60s. No per-caller / per-job isolation.
  * F-E: process_call is blocking work inside `async def`; one slow call
    freezes the event loop — /health measured frozen ~3s.

The new shape (this file):

    HTTP -> validate (Pydantic, cheap + unambiguous only)
         -> TransmissionQueue.enqueue(job_id=sid, payload=record)   [atomic]
         -> 202 {job_id}                       ...worker drains later.

Structural guarantees, in order of the build's non-negotiables:

  1. F-A fixed structurally: there is NO shared breaker and NO shared
     mutable per-job state in this process. The only shared object is
     the Redis connection pool with per-call socket timeouts. One job's
     malformed body dies in validation (422) before any queue call; one
     job's death in the DLQ is just a hash in Redis. Neither can gate
     another caller's submission.
  2. F-E fixed structurally: every queue-touching endpoint is a plain
     `def` — FastAPI runs those on the AnyIO worker threadpool, so the
     event loop never executes blocking I/O. /health is `async def`
     with ZERO I/O (liveness must survive a frozen Redis); /ready is
     the endpoint that actually touches the queue.
  3. No untrackable 202: the 202 is returned only AFTER enqueue()'s Lua
     script has atomically created the job hash and pushed it pending.
     If you hold a 202, GET /job/{id} finds the job — there is no
     "landed later" window.
  4. Never lie about state: unknown job_id -> 404 job_not_found
     (distinct shape). Queue unreachable -> 503 queue_unavailable
     ("status unknown right now"), never 404, never a fabricated
     status, and never a 202 with a job_id that wasn't enqueued.
  5. Cheap validation only: `sid` must be a non-empty string usable as
     a queue key and URL path segment. Everything else passes through
     untouched — the harness/worker owns real Twilio parsing. Rejecting
     more here would duplicate that logic and drift from it.

This file talks to queue state ONLY through TransmissionQueue
(enqueue / get_job / stats / ping). It must never import
production_harness, resilient_harness, or anything under governance/ —
that synchronous coupling is the bug this file exists to remove.
test_api_server_v2.py asserts this at import time.

Plug-in seam (deliberately not built here):
  INGRESS_GUARDS below is the single attachment point for
  rate_limiter_v2.py (F-F: key on connecting IP / authenticated
  principal — NEVER on attacker-supplied payload identity) and for any
  future scoped circuit breaker, if one is still wanted once nothing
  synchronous remains to break. The existing api_key_auth.require_api_key
  attaches there today when ICEBERG_API_KEYS is configured.

Run:  python3 api_server_v2.py            (env: INGRESS_HOST/PORT,
      TRANSMISSION_REDIS_URL, TRANSMISSION_NAMESPACE, ICEBERG_API_KEYS)
"""

from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import redis  # exception types only — queue state goes through TransmissionQueue
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from queue_schema import TransmissionQueue

SERVICE = "sentinel-ingress"
VERSION = "2.0.0"

# --------------------------------------------------------------------------
# Logging: reuse the project's structured-JSON convention when importable
# (operational_resilience lives in the inner sentinel_os/ dir), else fall
# back to an equivalent local formatter so this file runs from repo root.
# --------------------------------------------------------------------------
def _project_logger(name: str) -> logging.Logger:
    here = os.path.dirname(os.path.abspath(__file__))
    for extra in (here, os.path.join(here, "sentinel_os")):
        if extra not in sys.path and os.path.isdir(extra):
            sys.path.append(extra)
    try:
        from operational_resilience import setup_logging  # type: ignore
        return setup_logging(name)
    except Exception:
        import json as _json

        class _JF(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                return _json.dumps({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                })

        lg = logging.getLogger(name)
        if not lg.handlers:
            h = logging.StreamHandler()
            h.setFormatter(_JF())
            lg.addHandler(h)
        lg.setLevel(logging.INFO)
        return lg


logger = _project_logger("IngressV2")

# --------------------------------------------------------------------------
# Configuration (env). The ingress owns no queue tuning — retry/backoff/
# lease policy belongs to queue_schema.py defaults and the worker.
# --------------------------------------------------------------------------
REDIS_URL = os.getenv("TRANSMISSION_REDIS_URL", "redis://localhost:6379/0")
NAMESPACE = os.getenv("TRANSMISSION_NAMESPACE", "tq")
MAX_BODY_BYTES = int(os.getenv("INGRESS_MAX_BODY_BYTES", str(256 * 1024)))
RETRY_AFTER_S = int(os.getenv("INGRESS_RETRY_AFTER_SECONDS", "5"))

# --------------------------------------------------------------------------
# Guard seam — the ONE place auth / rate limiting / future scoped breakers
# attach. Order matters: guards run left to right.
#
#   * TODAY: api_key_auth.require_api_key (current production auth, which
#     already rate-limits pre-auth ATTEMPTS by connecting IP) attaches
#     automatically when ICEBERG_API_KEYS is set.
#   * NEXT (out of scope here, F-F): rate_limiter_v2 appends its own
#     dependency:   INGRESS_GUARDS.append(Depends(rate_limit_v2))
#     keyed on connecting IP / authenticated principal — never on any
#     identity the payload supplies.
#
# /health deliberately takes NO guards: liveness must not depend on auth
# config, rate budgets, or Redis.
# --------------------------------------------------------------------------
INGRESS_GUARDS: List[Any] = []

_REQUIRE_KEYS = os.getenv("ICEBERG_REQUIRE_API_KEYS", "").lower() in ("1", "true", "yes")
if os.getenv("ICEBERG_API_KEYS") or _REQUIRE_KEYS:
    try:
        from api_key_auth import require_api_key  # type: ignore
        INGRESS_GUARDS.append(Depends(require_api_key))
        logger.info("ingress auth: api_key_auth.require_api_key attached")
    except Exception as exc:  # pragma: no cover - config error path
        if _REQUIRE_KEYS:
            raise RuntimeError(
                "ICEBERG_REQUIRE_API_KEYS=true but api_key_auth could not be "
                f"imported ({exc}); refusing to start an open ingress."
            ) from exc
        logger.warning(f"ingress auth: api_key_auth unavailable ({exc}); starting OPEN")

    # F-F: rate limiting keyed on validated caller identity, never
    # connecting IP (this ingress sits behind nginx — one IP for every
    # caller — and never on any payload-supplied identity). Attached
    # ONLY here, alongside auth: without a validated principal there is
    # no safe identity to bucket by, and falling back to IP for an
    # unauthenticated deployment would silently reintroduce the exact
    # shared-IP defect (F-F) this file exists to fix. Appended after
    # the auth guard above so it always runs second: an unvalidated key
    # 401/403s at require_api_key first and never reaches this bucket.
    try:
        from rate_limiter_v2 import rate_limit_v2  # type: ignore
        INGRESS_GUARDS.append(Depends(rate_limit_v2))
        logger.info("ingress rate limiting: rate_limiter_v2.rate_limit_v2 attached")
    except Exception as exc:  # pragma: no cover - config error path
        logger.warning(f"ingress rate limiting: rate_limiter_v2 unavailable ({exc}); starting WITHOUT it")
else:
    logger.warning(
        "ingress auth: DISABLED (no ICEBERG_API_KEYS set) — dev mode only. "
        "Set ICEBERG_API_KEYS=key:name to attach the existing guard. "
        "Rate limiting (rate_limiter_v2) also does not attach in this mode: "
        "there is no validated caller identity to bucket by without auth, "
        "and bucketing by connecting IP behind nginx would reintroduce F-F."
    )

# --------------------------------------------------------------------------
# App + queue lifecycle. The queue client is constructed at startup and is
# the process's ONLY shared state: a connection pool (max 64) whose every
# call carries socket timeouts. TransmissionQueue's constructor performs no
# network I/O, so the ingress starts (and serves /health, and returns honest
# 503s) even if Redis is down at boot.
# --------------------------------------------------------------------------
@asynccontextmanager
async def _lifespan(app: FastAPI):
    app.state.queue = TransmissionQueue(redis_url=REDIS_URL, namespace=NAMESPACE)
    logger.info(f"ingress up: queue namespace='{NAMESPACE}' redis='{REDIS_URL}'")
    yield
    logger.info("ingress shutdown")


app = FastAPI(
    title="Sentinel OS Ingress (v2, queued)",
    description="Stateless submit/poll front for the transmission queue. "
                "Does no governance work; sentinel_worker.py drains the queue.",
    version=VERSION,
    lifespan=_lifespan,
)


def get_queue(request: Request) -> TransmissionQueue:
    return request.app.state.queue


# Oversize guard: refuse obviously huge bodies before parsing them. This is
# ingress hygiene, not rate limiting. Honest limitation: it checks the
# declared Content-Length; a chunked request without one is bounded by the
# fronting proxy (nginx client_max_body_size), same as today.
@app.middleware("http")
async def _body_size_guard(request: Request, call_next):
    if request.method == "POST":
        cl = request.headers.get("content-length", "")
        if cl.isdigit() and int(cl) > MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={
                    "error": "payload_too_large",
                    "limit_bytes": MAX_BODY_BYTES,
                    "detail": "Call record exceeds the ingress body limit; "
                              "nothing was enqueued.",
                },
            )
    return await call_next(request)


# --------------------------------------------------------------------------
# Submission model — the CHEAP, UNAMBIGUOUS checks only.
#
# `sid` is the job identity: it keys the queue hash and is the /job/{id}
# path segment, so it must be a real, non-blank string with no whitespace,
# control characters, or '/'. StrictStr means a numeric sid (12345) is a
# type error, not silently coerced. EVERYTHING else in the record is passed
# through untouched (extra="allow"): Twilio-shape parsing (status, duration,
# from/to, timestamps...) is the harness's logic and is deliberately NOT
# duplicated here — a record with sid present but garbage elsewhere is
# ADMITTED and will dead-letter with a real reason + error trail, which is
# the queue doing its job, not a validation gap.
# --------------------------------------------------------------------------
class CallSubmission(BaseModel):
    model_config = ConfigDict(extra="allow")

    sid: StrictStr = Field(min_length=1, max_length=256,
                           description="Twilio call SID; becomes the job_id.")

    @field_validator("sid")
    @classmethod
    def _sid_is_a_sane_key(cls, v: str) -> str:
        if v != v.strip() or not v.strip():
            raise ValueError("sid must be non-blank with no surrounding whitespace")
        if any(ch.isspace() or ord(ch) < 0x20 or ch == "\x7f" for ch in v):
            raise ValueError("sid must not contain whitespace or control characters")
        if "/" in v:
            raise ValueError("sid must be usable as a URL path segment ('/' not allowed)")
        return v


def _iso(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()


def _queue_unavailable(exc: Exception) -> HTTPException:
    logger.error(f"queue unavailable: {type(exc).__name__}: {exc}")
    return HTTPException(
        status_code=503,
        detail={
            "error": "queue_unavailable",
            "detail": "The transmission queue is unreachable; job state is "
                      "unknown right now. Retry shortly.",
        },
        headers={"Retry-After": str(RETRY_AFTER_S)},
    )


# --------------------------------------------------------------------------
# POST /submit-call
# --------------------------------------------------------------------------
@app.post("/submit-call", status_code=202, dependencies=INGRESS_GUARDS)
def submit_call(call: CallSubmission, q: TransmissionQueue = Depends(get_queue)) -> Dict[str, Any]:
    """Accept a Twilio-style call record and enqueue it.

    Returns 202 the moment the job is durably in the queue — enqueue() is a
    single atomic Lua script, and this handler does not return until it has.
    job_id == sid, and enqueue is idempotent on it: a client retry of the
    same submission gets the SAME job (deduped=true, current status echoed),
    never a duplicate and never a reset of a job already in flight.
    """
    try:
        out = q.enqueue(job_id=call.sid, payload=call.model_dump())
    except (redis.exceptions.RedisError, OSError) as exc:
        raise _queue_unavailable(exc)
    logger.info(f"submitted job_id={out['job_id']} status={out['status']} deduped={out['deduped']}")
    return {
        "job_id": out["job_id"],
        "status": out["status"],
        "deduped": out["deduped"],
        "status_url": f"/job/{out['job_id']}",
    }


# --------------------------------------------------------------------------
# GET /job/{job_id}
# --------------------------------------------------------------------------
def _public_view(job: Dict[str, Any]) -> Dict[str, Any]:
    """Shape a queue record for callers. Payload is NOT echoed back (the
    caller submitted it; replaying call records out of a poll endpoint
    leaks PII for free). The error trail IS surfaced whenever it exists —
    'dead' must be diagnosable from this response alone."""
    status = job.get("status")
    view: Dict[str, Any] = {
        "job_id": job.get("job_id"),
        "status": status,
        "attempts": job.get("attempts", 0),
        "max_attempts": job.get("max_attempts"),
        "created_at": _iso(job.get("created_at")),
        "updated_at": _iso(job.get("updated_at")),
    }
    trail = job.get("error_trail") or []
    if isinstance(trail, list) and trail:
        view["error_trail"] = [
            {
                "attempt": e.get("attempt"),
                "reason": e.get("reason"),
                "error": e.get("error"),
                "at": _iso(e.get("at")),
            }
            for e in trail
        ]
        view["last_error"] = job.get("last_error")
    if status == "scheduled":
        view["scheduled_for"] = _iso(job.get("scheduled_for"))
        view["retry_in_s"] = max(0.0, round(float(job.get("scheduled_for", 0)) - time.time(), 3))
    elif status == "processing":
        view["claimed_by"] = job.get("claimed_by")
        view["lease_expires_at"] = _iso(job.get("lease_expires_at"))
    elif status == "done":
        view["completed_at"] = _iso(job.get("completed_at"))
        if "result" in job:
            view["result"] = job.get("result")
    elif status == "dead":
        view["dead_reason"] = job.get("dead_reason")
        view["died_at"] = _iso(job.get("died_at"))
    return view


@app.get("/job/{job_id}", dependencies=INGRESS_GUARDS)
def job_status(job_id: str, q: TransmissionQueue = Depends(get_queue)) -> Dict[str, Any]:
    """Current state of a submitted job: pending / scheduled / processing /
    done / dead. Distinct outcomes, never conflated:

      * 200 — the job exists; body carries state + diagnosis (dead includes
        dead_reason and the full error trail).
      * 404 job_not_found — this ID was NEVER accepted by the ingress.
        Not "pending", not a placeholder.
      * 503 queue_unavailable — the queue can't be consulted, so state is
        UNKNOWN. Deliberately not 404: unreachable is not nonexistent.
    """
    try:
        job = q.get_job(job_id)
    except (redis.exceptions.RedisError, OSError) as exc:
        raise _queue_unavailable(exc)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "job_not_found",
                "job_id": job_id,
                "detail": "No job with this ID was ever accepted; nothing "
                          "was enqueued under it.",
            },
        )
    return _public_view(job)


# --------------------------------------------------------------------------
# Health (liveness) vs readiness — split on purpose (F-E).
# --------------------------------------------------------------------------
@app.get("/health")
async def health() -> Dict[str, Any]:
    """Liveness. `async def`, zero I/O, no guards: answers instantly even
    with Redis frozen, the threadpool saturated, or auth misconfigured.
    Proves the event loop is alive — nothing more. Queue truth lives in
    /ready and /queue/stats."""
    return {
        "status": "alive",
        "service": SERVICE,
        "version": VERSION,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready")
def ready(q: TransmissionQueue = Depends(get_queue)) -> Dict[str, Any]:
    """Readiness: can this ingress actually accept work right now?"""
    try:
        q.ping()
        return {"ready": True, "queue": q.stats()}
    except (redis.exceptions.RedisError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"ready": False, "error": "queue_unavailable",
                    "detail": f"{type(exc).__name__}: {exc}"},
            headers={"Retry-After": str(RETRY_AFTER_S)},
        )


@app.get("/queue/stats", dependencies=INGRESS_GUARDS)
def queue_stats(q: TransmissionQueue = Depends(get_queue)) -> Dict[str, Any]:
    """Depth per state + oldest-pending staleness, straight from
    TransmissionQueue.stats(). Operational visibility only."""
    try:
        return q.stats()
    except (redis.exceptions.RedisError, OSError) as exc:
        raise _queue_unavailable(exc)


# --------------------------------------------------------------------------
# Entrypoint. workers=1 is the honest default for a Chromebook; scale by
# raising INGRESS_WORKERS or replicas — the ingress is stateless, so any
# number of processes can front the same queue namespace safely.
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    kw: Dict[str, Any] = {}
    if os.getenv("SSL_CERTFILE") and os.getenv("SSL_KEYFILE"):
        kw["ssl_certfile"] = os.environ["SSL_CERTFILE"]
        kw["ssl_keyfile"] = os.environ["SSL_KEYFILE"]
    uvicorn.run(
        "api_server_v2:app",
        host=os.getenv("INGRESS_HOST", "0.0.0.0"),
        port=int(os.getenv("INGRESS_PORT", "8000")),
        workers=int(os.getenv("INGRESS_WORKERS", "1")),
        **kw,
    )
