"""circuit_breaker.py -- per-resource circuit breaker (roadster phase-1).

WHY THIS FILE EXISTS (fifth-pass audit, F-A, confirmed by live execution):
the old system (operational_resilience.py's CircuitBreaker, wired into
resilient_harness.py) was ONE instance gating every caller through every
downstream dependency. Two malformed requests tripped it and every
caller -- including ones whose calls never touched the failing
dependency -- got 60 seconds of rejection. One bad actor took down the
whole ingress.

The fix is not a smarter breaker. It's refusing to let one exist as a
shared global. This module has no module-level breaker instance and no
registry that would let two unrelated call sites collide on a shared
key by accident: every CircuitBreaker is a plain object a caller
constructs and holds itself. production_harness.py holds two -- one for
the Claude governor call, one for the Postgres ledger write -- as
instance attributes on IcebergProductionHarness, so each harness
(each worker process) has independent breakers per resource, and a
struggling Postgres connection can never gate the Claude call, or vice
versa. If a future call site wants breaker protection, it constructs
its own instance; it does not reach for someone else's.

WHAT THIS DOES: wraps a synchronous callable. CLOSED -> call flows
through, failures counted. failure_threshold consecutive failures ->
OPEN: calls rejected immediately with CircuitOpenError (a real
exception, not a raised-through value -- callers that already wrap
their call site in `except Exception` need no new branch; see the
production_harness.py integration, where a tripped breaker lands in
the exact fallback shape a governor exception or a ledger exception
already produced). After reset_timeout_s, one call is let through as a
probe (HALF_OPEN); half_open_success_threshold consecutive successful
probes close it again, any probe failure re-opens it immediately and
restarts the timeout.

WHAT THIS DELIBERATELY DOES NOT DO: it does not add a timeout to the
wrapped call. A call that hangs without raising will hang the same way
whether or not it's wrapped -- the breaker only reacts to what the
callable actually raises. If a resource can hang indefinitely, that's
a client-level timeout problem (e.g. the anthropic client's own
`timeout=` kwarg, or psycopg2's `connect_timeout` / statement_timeout),
separate from and prior to breaker coverage.

Thread-safety: one lock per instance. sentinel_worker.py's reaper runs
on a background thread; a future multi-threaded caller (e.g. an async
ingress guard) may share one breaker instance across concurrent calls
to the SAME resource, which is exactly the case a lock needs to cover
correctly. Cross-resource concurrency needs no lock, because it never
touches the same instance.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Optional

from operational_resilience import setup_logging


class CircuitState(Enum):
    CLOSED = "closed"        # normal operation
    OPEN = "open"             # tripped; calls rejected without attempting
    HALF_OPEN = "half_open"   # probing; a limited number of calls allowed through


class CircuitOpenError(Exception):
    """Raised in place of calling through when the breaker is OPEN (or a
    HALF_OPEN probe slot isn't available). Subclasses Exception, not any
    resource-specific error type, so it lands in the SAME except-Exception
    block a caller already has around the wrapped call -- no new branch
    needed at call sites that already treat "this dependency failed" as
    one case."""

    def __init__(self, breaker_name: str, state: "CircuitState", opened_at: Optional[float]):
        self.breaker_name = breaker_name
        self.state = state
        self.opened_at = opened_at
        wait = ""
        if opened_at is not None:
            wait = f" (opened {time.time() - opened_at:.1f}s ago)"
        super().__init__(f"circuit '{breaker_name}' is {state.value}{wait}; call rejected")


class CircuitBreaker:
    """One breaker for one resource. Construct one per downstream
    dependency and hold it yourself -- see module docstring for why this
    file will not do that construction for you via a shared registry."""

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        reset_timeout_s: float = 30.0,
        half_open_success_threshold: int = 2,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if half_open_success_threshold < 1:
            raise ValueError("half_open_success_threshold must be >= 1")
        if reset_timeout_s <= 0:
            raise ValueError("reset_timeout_s must be > 0")

        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout_s = reset_timeout_s
        self.half_open_success_threshold = half_open_success_threshold

        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._half_open_successes = 0
        self._opened_at: Optional[float] = None
        self._half_open_probe_in_flight = False

        # Lifetime counters -- observability only, never gate behavior.
        self.total_calls = 0
        self.total_failures = 0
        self.total_rejections = 0
        self.total_opens = 0

        # setup_logging() (operational_resilience.py) adds a handler on
        # every call and never checks for an existing one -- fine for
        # the module's original one-logger-per-process callers, but two
        # CircuitBreaker instances sharing a name (e.g. across harness
        # instances in one process) would otherwise stack handlers and
        # multiply every log line. Trimmed back to one handler here
        # rather than patching that gap in a third file out of scope
        # for this build; see the verification report.
        self.logger = setup_logging(f"CircuitBreaker.{name}")
        if len(self.logger.handlers) > 1:
            self.logger.handlers = self.logger.handlers[:1]

    # ---------------------------------------------------------- state --
    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state_locked()

    def _state_locked(self) -> CircuitState:
        """Must hold self._lock. Lazily transitions OPEN -> HALF_OPEN
        once reset_timeout_s has elapsed; this is the only place time
        is consulted, so state is always derived fresh rather than
        cached across an idle period."""
        if self._state is CircuitState.OPEN and self._opened_at is not None:
            if time.time() - self._opened_at >= self.reset_timeout_s:
                self._state = CircuitState.HALF_OPEN
                self._half_open_successes = 0
                self._half_open_probe_in_flight = False
                self.logger.info(
                    "circuit entering HALF_OPEN",
                    extra={"extra_data": {
                        "breaker": self.name, "action": "half_open_transition",
                        "open_duration_s": round(time.time() - self._opened_at, 3),
                    }},
                )
        return self._state

    def snapshot(self) -> Dict[str, Any]:
        """Point-in-time status for logging / a future /health-style
        endpoint. Never used internally to gate calls -- call() always
        re-derives state under the lock."""
        with self._lock:
            st = self._state_locked()
            return {
                "name": self.name,
                "state": st.value,
                "failure_count": self._failure_count,
                "failure_threshold": self.failure_threshold,
                "half_open_successes": self._half_open_successes,
                "half_open_success_threshold": self.half_open_success_threshold,
                "opened_at": (
                    datetime.fromtimestamp(self._opened_at, tz=timezone.utc).isoformat()
                    if self._opened_at else None
                ),
                "total_calls": self.total_calls,
                "total_failures": self.total_failures,
                "total_rejections": self.total_rejections,
                "total_opens": self.total_opens,
            }

    # ----------------------------------------------------------- call --
    def call(
        self,
        func: Callable,
        *args,
        is_failure: Optional[Callable[[Any], bool]] = None,
        **kwargs,
    ) -> Any:
        """Execute func(*args, **kwargs) under breaker protection.

        Raises CircuitOpenError (without calling func) when OPEN, or
        when HALF_OPEN and a probe is already in flight -- concurrent
        callers during a probe window don't all get to test the
        resource at once; only one probe is outstanding at a time.
        Otherwise calls through and records the outcome.

        is_failure: optional predicate run on a SUCCESSFUL return value
        (func did not raise) to decide whether it should still count as
        a breaker failure. Exists because some callables in this
        codebase deliberately swallow their own exceptions and encode
        failure in the return value instead (see
        claude_governance_api.py's safety_check(), which fails closed
        by returning a dict rather than raising -- an exception-only
        breaker wrapped around it would never trip no matter how down
        the API is). Default None means "only a raised exception counts
        as failure," which is correct for callables like
        PostgreSQLLedger.append_decision that do raise on real failure.
        """
        with self._lock:
            st = self._state_locked()
            if st is CircuitState.OPEN:
                self.total_rejections += 1
                raise CircuitOpenError(self.name, st, self._opened_at)
            if st is CircuitState.HALF_OPEN:
                if self._half_open_probe_in_flight:
                    self.total_rejections += 1
                    raise CircuitOpenError(self.name, st, self._opened_at)
                self._half_open_probe_in_flight = True
            self.total_calls += 1

        try:
            result = func(*args, **kwargs)
        except Exception:
            self._on_failure()
            raise

        if is_failure is not None and is_failure(result):
            self._on_failure()
            return result

        self._on_success()
        return result

    # ------------------------------------------------------ outcomes --
    def _on_success(self) -> None:
        with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                self._half_open_successes += 1
                self._half_open_probe_in_flight = False
                if self._half_open_successes >= self.half_open_success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._opened_at = None
                    self.logger.info(
                        "circuit CLOSED (recovered)",
                        extra={"extra_data": {
                            "breaker": self.name, "action": "closed",
                            "half_open_successes": self._half_open_successes,
                        }},
                    )
            elif self._state is CircuitState.CLOSED:
                self._failure_count = 0

    def _on_failure(self) -> None:
        with self._lock:
            self.total_failures += 1
            if self._state is CircuitState.HALF_OPEN:
                # Probe failed: back to OPEN immediately, timeout restarts.
                self._half_open_probe_in_flight = False
                self._reopen_locked()
                return

            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._reopen_locked()
            else:
                self.logger.warning(
                    "circuit call failed",
                    extra={"extra_data": {
                        "breaker": self.name, "action": "failure_recorded",
                        "failure_count": self._failure_count,
                        "failure_threshold": self.failure_threshold,
                    }},
                )

    def _reopen_locked(self) -> None:
        """Must hold self._lock."""
        was_open_already = self._state is CircuitState.OPEN
        self._state = CircuitState.OPEN
        self._opened_at = time.time()
        self._half_open_successes = 0
        if not was_open_already:
            self.total_opens += 1
        self.logger.error(
            "circuit OPEN",
            extra={"extra_data": {
                "breaker": self.name, "action": "opened",
                "failure_count": self._failure_count,
                "failure_threshold": self.failure_threshold,
                "reset_timeout_s": self.reset_timeout_s,
            }},
        )

    # -------------------------------------------------------- testing --
    def reset(self) -> None:
        """Force CLOSED. Test/ops utility only -- never called from
        call()'s own state machine."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._half_open_successes = 0
            self._opened_at = None
            self._half_open_probe_in_flight = False
