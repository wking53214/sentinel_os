"""rate_limiter_v2.py -- per-caller rate limiting for the roadster
ingress (F-F, fifth-pass audit, confirmed by live execution).

WHY THIS FILE EXISTS: the old ICEBERG_API_KEYS-era rate limiter had two
generations of the same defect, both confirmed live.

  Generation 1: keyed on the caller-SUPPLIED API key string itself, even
  before it was validated. A brute-force attacker gets a fresh quota on
  every guess (a different string costs nothing to mint), so the limit
  never engages against the attack it exists to stop -- confirmed live:
  500 distinct guessed keys, 0 throttled, plus one unbounded in-memory
  bucket per guess.

  Generation 2 (commit 5fb20b3, the fix for generation 1): switched to
  keying on connecting IP. That closes the brute-force-quota bypass, but
  behind nginx (this ingress's deployment target) every caller shares
  one IP -- confirmed live: this "fix" throttles everyone or no one, a
  product-wide DoS in either direction.

THE ARCHITECTURAL DECISION THIS FILE IMPLEMENTS (already made, not
re-litigated here): bucket by validated caller identity -- the API key
itself, once it has passed authentication -- never by connecting IP and
never by anything the request payload merely claims. This is safe from
generation 1's defect because rate_limit_v2() is a plain dependency
alongside api_key_auth.require_api_key in INGRESS_GUARDS: FastAPI
resolves both from the SAME validated X-API-Key header on the SAME
request, so an unauthenticated guess never reaches this bucket at all
-- it dies at require_api_key's 401/403 first. Only a key that already
passed validation gets a bucket.

WHAT THIS DOES: a Redis-backed token bucket per principal, atomic via
rate_limit.lua (same clock-authority convention as
queue_schema.py/lua/_common.lua: Redis server TIME, never a host
clock, so every ingress replica agrees). Token bucket, not fixed
window, deliberately: a fixed window lets a caller burst 2x the
configured rate for free by timing requests around the window boundary
(all of their previous-window budget at :59, all of their new-window
budget at :00). A token bucket has no boundary to burst across --
capacity bounds the largest burst allowed at any moment, refill_rate
bounds the sustained rate, and those are the only two knobs.

WHAT THIS DELIBERATELY DOES NOT DO: it does not decide identity when no
auth is configured. In that mode (ICEBERG_API_KEYS unset, per
api_server_v2.py's own documented "dev mode only" warning) there is no
validated principal to bucket by, so this falls back to connecting IP
-- the same posture api_key_auth's own pre-auth limiter already takes,
and an explicitly-labeled dev-only trade-off, not a claim that IP
bucketing is safe in production behind nginx.

Plug-in seam (api_server_v2.py):
    if os.getenv("ICEBERG_API_KEYS") or _REQUIRE_KEYS:
        ...
        INGRESS_GUARDS.append(Depends(require_api_key))
    INGRESS_GUARDS.append(Depends(rate_limit_v2))   # <- this file
Guards run left to right, so require_api_key (when configured) always
resolves first; rate_limit_v2 re-reads the same X-API-Key header
FastAPI has already validated by the time it runs, rather than
depending on api_key_auth.py's return value directly, to keep this
file important-to-security but import-independent of it.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional

import redis
from fastapi import Header, HTTPException, Request

# --------------------------------------------------------------------------
# Logging: same fallback-import pattern api_server_v2.py itself uses, so
# this file works whether run from the repo root or the nested sentinel_os/
# package dir.
# --------------------------------------------------------------------------
def _project_logger(name: str):
    here = os.path.dirname(os.path.abspath(__file__))
    for extra in (here, os.path.join(here, "sentinel_os")):
        if extra not in sys.path and os.path.isdir(extra):
            sys.path.append(extra)
    from operational_resilience import setup_logging  # type: ignore
    logger = setup_logging(name)
    if len(logger.handlers) > 1:  # see circuit_breaker.py for why
        logger.handlers = logger.handlers[:1]
    return logger


logger = _project_logger("RateLimiterV2")

# --------------------------------------------------------------------------
# Configuration (env). Defaults are a starting policy, not a proven-correct
# number -- tune per deployment. 100 req/min steady rate with a 20-token
# burst allowance: generous enough that a legitimate caller's retry-with-
# backoff never trips it, tight enough to bound a single misbehaving
# integration from saturating a shared worker pool.
# --------------------------------------------------------------------------
REDIS_URL = os.getenv("RATE_LIMIT_REDIS_URL", os.getenv("TRANSMISSION_REDIS_URL", "redis://localhost:6379/0"))
NAMESPACE = os.getenv("RATE_LIMIT_NAMESPACE", "rl")
REQUESTS_PER_MINUTE = float(os.getenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "100"))
BURST_CAPACITY = float(os.getenv("RATE_LIMIT_BURST_CAPACITY", "20"))
BUCKET_TTL_S = int(os.getenv("RATE_LIMIT_BUCKET_TTL_SECONDS", "300"))

_REFILL_RATE_PER_MS = REQUESTS_PER_MINUTE / 60000.0

_LUA_PATH = Path(__file__).with_name("lua") / "rate_limit.lua"


class RateLimiterV2:
    """Owns one Redis connection pool and the loaded Lua script. One
    instance per ingress process (constructed at app startup, same
    lifecycle as TransmissionQueue in api_server_v2.py's lifespan) --
    NOT a per-request object, so the connection pool and script SHA are
    reused across requests."""

    def __init__(
        self,
        redis_url: str = REDIS_URL,
        namespace: str = NAMESPACE,
        requests_per_minute: float = REQUESTS_PER_MINUTE,
        burst_capacity: float = BURST_CAPACITY,
        bucket_ttl_s: int = BUCKET_TTL_S,
    ) -> None:
        self.namespace = namespace
        self.capacity = burst_capacity
        self.refill_rate_per_ms = requests_per_minute / 60000.0
        self.bucket_ttl_ms = bucket_ttl_s * 1000
        # Socket timeouts mirror TransmissionQueue's posture: a hung
        # Redis must cost a bounded thread, never a hang (F-E lineage).
        self._redis = redis.Redis.from_url(
            redis_url, socket_timeout=2.0, socket_connect_timeout=2.0,
        )
        self._script = self._redis.register_script(_LUA_PATH.read_text())

    def check(self, principal: str, cost: float = 1.0) -> "RateLimitResult":
        """Atomic check-and-consume for one principal. Raises
        redis.exceptions.RedisError / OSError on real Redis failure --
        callers decide fail-open vs fail-closed (this ingress fails
        OPEN on Redis unavailability; see rate_limit_v2() below and its
        docstring for why)."""
        key = f"{self.namespace}:{principal}"
        allowed, tokens_remaining, retry_after_ms = self._script(
            keys=[key],
            args=[self.capacity, self.refill_rate_per_ms, cost, self.bucket_ttl_ms],
        )
        return RateLimitResult(
            allowed=bool(int(allowed)),
            tokens_remaining=float(tokens_remaining),
            retry_after_s=max(0.0, float(retry_after_ms) / 1000.0),
        )

    def close(self) -> None:
        self._redis.close()


class RateLimitResult:
    __slots__ = ("allowed", "tokens_remaining", "retry_after_s")

    def __init__(self, allowed: bool, tokens_remaining: float, retry_after_s: float) -> None:
        self.allowed = allowed
        self.tokens_remaining = tokens_remaining
        self.retry_after_s = retry_after_s


# --------------------------------------------------------------------------
# FastAPI dependency -- the actual INGRESS_GUARDS seam attachment.
# --------------------------------------------------------------------------
_limiter: Optional[RateLimiterV2] = None


def get_rate_limiter() -> RateLimiterV2:
    """Lazily constructed singleton, mirroring api_server_v2.py's
    app.state.queue pattern but module-level since this file has no
    lifespan hook of its own -- constructed on first request, not at
    import time, so importing this module never performs I/O."""
    global _limiter
    if _limiter is None:
        _limiter = RateLimiterV2()
        logger.info(
            "rate limiter initialized",
            extra={"extra_data": {
                "namespace": NAMESPACE, "requests_per_minute": REQUESTS_PER_MINUTE,
                "burst_capacity": BURST_CAPACITY,
            }},
        )
    return _limiter


def rate_limit_v2(request: Request, x_api_key: Optional[str] = Header(None)) -> None:
    """FastAPI dependency: INGRESS_GUARDS.append(Depends(rate_limit_v2)).

    Identity: the validated X-API-Key header when auth is configured
    (the SAME header require_api_key already checked earlier in the
    same guard chain -- an invalid key never reaches this dependency at
    all, it 401/403s first). api_server_v2.py's guard-seam wiring only
    attaches this dependency at all when ICEBERG_API_KEYS is configured
    (see its own comment there), so the IP fallback below is defensive
    only -- for a caller of rate_limit_v2() outside that documented
    seam -- never the production posture: this file does not decide to
    bucket unauthenticated traffic by IP, because behind nginx that is
    exactly F-F again.

    Fails OPEN on Redis unavailability: a rate limiter that itself
    becomes the outage when its backing store blips would be strictly
    worse than no rate limiter, and the queue's own admission control
    (bounded depth, DLQ) is the backstop against a caller that a
    temporarily-open limiter lets through. Every fail-open event is
    logged at ERROR so it's visible in metrics, not silent.
    """
    principal = x_api_key or (request.client.host if request.client else "unknown")
    limiter = get_rate_limiter()
    try:
        result = limiter.check(principal)
    except (redis.exceptions.RedisError, OSError) as exc:
        logger.error(
            "rate limiter backend unavailable -- failing OPEN",
            extra={"extra_data": {"error": f"{type(exc).__name__}: {exc}"}},
        )
        return

    if not result.allowed:
        logger.warning(
            "rate limit exceeded",
            extra={"extra_data": {
                "principal_keyed_on": "api_key" if x_api_key else "ip",
                "retry_after_s": result.retry_after_s,
            }},
        )
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "detail": "Too many requests for this caller. Retry after "
                          "the indicated delay.",
            },
            headers={"Retry-After": str(max(1, int(result.retry_after_s) + 1))},
        )
