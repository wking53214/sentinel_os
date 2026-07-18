"""test_circuit_breaker.py -- every test here calls the real CircuitBreaker
state machine with real time.sleep() and real threads. Nothing about the
breaker itself is mocked; the only stand-ins are the wrapped *callables*
(deliberately flaky functions), which is the correct level to fake at --
we're proving the breaker's reaction to failure, not re-testing Postgres
or the Claude API here (those are covered in test_production_harness_
breakers.py against the real services)."""
from __future__ import annotations

import threading
import time

import pytest

from circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState


def test_closed_allows_calls_through():
    cb = CircuitBreaker("t1", failure_threshold=3, reset_timeout_s=1)
    assert cb.call(lambda: 42) == 42
    assert cb.state is CircuitState.CLOSED


def test_opens_after_exact_threshold_not_before():
    cb = CircuitBreaker("t2", failure_threshold=3, reset_timeout_s=5)

    def boom():
        raise ValueError("nope")

    for i in range(2):
        with pytest.raises(ValueError):
            cb.call(boom)
        assert cb.state is CircuitState.CLOSED, f"tripped early at failure {i+1}"

    with pytest.raises(ValueError):
        cb.call(boom)  # 3rd failure -- this is the trip
    assert cb.state is CircuitState.OPEN


def test_open_rejects_without_calling_through():
    cb = CircuitBreaker("t3", failure_threshold=1, reset_timeout_s=10)
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        cb.call(boom)
    assert calls["n"] == 1
    assert cb.state is CircuitState.OPEN

    # Next 5 calls must be rejected by the breaker, never reach boom().
    for _ in range(5):
        with pytest.raises(CircuitOpenError):
            cb.call(boom)
    assert calls["n"] == 1, "breaker let a call through while OPEN"


def test_success_resets_failure_count_while_closed():
    cb = CircuitBreaker("t4", failure_threshold=3, reset_timeout_s=5)

    with pytest.raises(ValueError):
        cb.call(lambda: (_ for _ in ()).throw(ValueError()))
    with pytest.raises(ValueError):
        cb.call(lambda: (_ for _ in ()).throw(ValueError()))
    assert cb.state is CircuitState.CLOSED  # 2 of 3, not yet tripped

    cb.call(lambda: "ok")  # success resets the counter

    with pytest.raises(ValueError):
        cb.call(lambda: (_ for _ in ()).throw(ValueError()))
    with pytest.raises(ValueError):
        cb.call(lambda: (_ for _ in ()).throw(ValueError()))
    assert cb.state is CircuitState.CLOSED, "prior successes should have cleared the count"


def test_half_open_after_real_timeout_elapses():
    cb = CircuitBreaker("t5", failure_threshold=1, reset_timeout_s=1)
    with pytest.raises(RuntimeError):
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError()))
    assert cb.state is CircuitState.OPEN

    # Still OPEN before the real timeout elapses.
    time.sleep(0.3)
    assert cb.state is CircuitState.OPEN

    time.sleep(0.8)  # total >1.0s elapsed -- real wall-clock wait
    assert cb.state is CircuitState.HALF_OPEN


def test_half_open_probe_failure_reopens_and_restarts_timeout():
    cb = CircuitBreaker("t6", failure_threshold=1, reset_timeout_s=1,
                          half_open_success_threshold=2)
    with pytest.raises(RuntimeError):
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError()))
    time.sleep(1.1)
    assert cb.state is CircuitState.HALF_OPEN

    with pytest.raises(RuntimeError):
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError()))  # failed probe
    assert cb.state is CircuitState.OPEN

    # Immediately after re-opening, still OPEN (timeout restarted, not reused).
    assert cb.state is CircuitState.OPEN
    time.sleep(1.1)
    assert cb.state is CircuitState.HALF_OPEN, "timeout should have restarted on re-open"


def test_half_open_needs_n_consecutive_successes_to_close():
    cb = CircuitBreaker("t7", failure_threshold=1, reset_timeout_s=1,
                          half_open_success_threshold=2)
    with pytest.raises(RuntimeError):
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError()))
    time.sleep(1.1)
    assert cb.state is CircuitState.HALF_OPEN

    cb.call(lambda: "ok")  # 1st probe success
    assert cb.state is CircuitState.HALF_OPEN, "should not close on a single probe success"

    cb.call(lambda: "ok")  # 2nd probe success
    assert cb.state is CircuitState.CLOSED


def test_half_open_allows_only_one_probe_at_a_time_under_concurrency():
    """Real threads, real concurrency -- not simulated. 20 threads all hit
    a HALF_OPEN breaker at once; exactly one must be let through to call
    the underlying function, the rest must be rejected as CircuitOpenError
    without ever invoking it."""
    cb = CircuitBreaker("t8", failure_threshold=1, reset_timeout_s=1)
    with pytest.raises(RuntimeError):
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError()))
    time.sleep(1.1)
    assert cb.state is CircuitState.HALF_OPEN

    probe_started = threading.Event()
    release_probe = threading.Event()
    call_count = {"n": 0}
    lock = threading.Lock()

    def slow_probe():
        with lock:
            call_count["n"] += 1
        probe_started.set()
        release_probe.wait(timeout=5)
        return "ok"

    results = {"through": 0, "rejected": 0}
    results_lock = threading.Lock()

    def worker():
        try:
            cb.call(slow_probe)
            with results_lock:
                results["through"] += 1
        except CircuitOpenError:
            with results_lock:
                results["rejected"] += 1

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    assert probe_started.wait(timeout=5), "no probe call started"
    time.sleep(0.2)  # let all other threads pile up on the lock
    release_probe.set()
    for t in threads:
        t.join(timeout=5)

    assert call_count["n"] == 1, f"expected exactly 1 underlying call, got {call_count['n']}"
    assert results["through"] == 1
    assert results["rejected"] == 19


def test_independent_instances_do_not_share_state():
    """THE core F-A regression test: two breakers for two different
    resources must never influence each other. This is the property
    the old shared-global breaker violated."""
    resource_a = CircuitBreaker("resource-a", failure_threshold=1, reset_timeout_s=30)
    resource_b = CircuitBreaker("resource-b", failure_threshold=1, reset_timeout_s=30)

    with pytest.raises(RuntimeError):
        resource_a.call(lambda: (_ for _ in ()).throw(RuntimeError()))
    assert resource_a.state is CircuitState.OPEN

    # resource_b must be completely unaffected.
    assert resource_b.state is CircuitState.CLOSED
    assert resource_b.call(lambda: "still fine") == "still fine"
    assert resource_b.state is CircuitState.CLOSED


def test_no_module_level_shared_breaker_exists():
    """Structural check mirroring test_api_server_v2.py's own AST-walk
    convention: this module must expose no pre-built CircuitBreaker
    instance that two unrelated call sites could accidentally share."""
    import circuit_breaker as mod
    for attr_name in dir(mod):
        if attr_name.startswith("_"):
            continue
        attr = getattr(mod, attr_name)
        assert not isinstance(attr, CircuitBreaker), (
            f"module-level CircuitBreaker instance found: {attr_name} -- "
            "this is exactly the shared-global shape F-A was about"
        )


def test_snapshot_reflects_state_without_mutating_it():
    cb = CircuitBreaker("t9", failure_threshold=1, reset_timeout_s=1)
    with pytest.raises(RuntimeError):
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError()))
    snap1 = cb.snapshot()
    assert snap1["state"] == "open"
    assert snap1["total_opens"] == 1
    time.sleep(1.1)
    snap2 = cb.snapshot()
    assert snap2["state"] == "half_open"


def test_constructor_rejects_invalid_config():
    with pytest.raises(ValueError):
        CircuitBreaker("bad", failure_threshold=0, reset_timeout_s=1)
    with pytest.raises(ValueError):
        CircuitBreaker("bad", failure_threshold=1, reset_timeout_s=0)
    with pytest.raises(ValueError):
        CircuitBreaker("bad", failure_threshold=1, reset_timeout_s=1,
                        half_open_success_threshold=0)


def test_is_failure_predicate_trips_breaker_on_return_value():
    """The claude_governor use case: func never raises, but its return
    value encodes failure. Without is_failure, this breaker would never
    trip no matter how many bad results come back."""
    cb = CircuitBreaker("t11", failure_threshold=2, reset_timeout_s=5)

    def swallows_and_returns_failure():
        return {"safe": False, "reasoning": "transport_error: connection refused"}

    is_failure = lambda r: isinstance(r, dict) and str(r.get("reasoning", "")).startswith("transport_error:")

    r1 = cb.call(swallows_and_returns_failure, is_failure=is_failure)
    assert r1["safe"] is False
    assert cb.state is CircuitState.CLOSED  # 1 of 2

    r2 = cb.call(swallows_and_returns_failure, is_failure=is_failure)
    assert r2["safe"] is False
    assert cb.state is CircuitState.OPEN  # 2 of 2 -- tripped


def test_is_failure_predicate_does_not_trip_on_non_matching_return():
    """A live response that just happens to be a rejection (safe=False,
    ordinary reasoning) must NOT count as an infra failure -- only the
    transport_error-tagged shape should."""
    cb = CircuitBreaker("t12", failure_threshold=1, reset_timeout_s=5)

    def ordinary_rejection():
        return {"safe": False, "reasoning": "risk too high, policy violation"}

    is_failure = lambda r: isinstance(r, dict) and str(r.get("reasoning", "")).startswith("transport_error:")

    for _ in range(10):
        r = cb.call(ordinary_rejection, is_failure=is_failure)
        assert r["safe"] is False
    assert cb.state is CircuitState.CLOSED, "ordinary rejections must never trip the breaker"


def test_is_failure_none_preserves_exception_only_behavior():
    """Default (no is_failure) must behave exactly as before -- the
    ledger-write use case, where the callable DOES raise on failure."""
    cb = CircuitBreaker("t13", failure_threshold=1, reset_timeout_s=5)
    with pytest.raises(RuntimeError):
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError()))
    assert cb.state is CircuitState.OPEN


def test_reset_forces_closed_for_test_ops_use():
    cb = CircuitBreaker("t10", failure_threshold=1, reset_timeout_s=30)
    with pytest.raises(RuntimeError):
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError()))
    assert cb.state is CircuitState.OPEN
    cb.reset()
    assert cb.state is CircuitState.CLOSED
    assert cb.call(lambda: "ok") == "ok"
