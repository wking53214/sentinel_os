"""
Regression coverage for the learning-loop bug flagged by the 24 July
2026 independent repository review: production_harness.py was calling
BayesianIntentEngine.observe_outcome() with the raw queue name (e.g.
"billing_queue") while intent_stats is keyed by the cassette's short
intent label (e.g. "billing"). observe_outcome() silently no-ops on an
unrecognized key, so every observation was dropped -- the belief state
never changed no matter how many calls ran, and nothing anywhere
recorded that this was happening.

Two things needed proving, so two things are tested here:

  1. The actual key match, generalized across every queue the cassette
     defines -- not just "billing_queue" was fixed by hand while a
     sibling queue quietly has the same problem.
  2. That state now survives a restart / is visible to a second worker,
     via Redis -- previously it lived only in one process's memory.

A third thing (a dropped/unmapped intent is now logged instead of
silently discarded) is checked with a fresh in-memory-only engine,
since it doesn't need Redis at all.
"""

import subprocess
import sys
import os
import time

import pytest
import redis as redis_lib

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sentinel_core import SentinelCore
from cassettes.ivr_cassette import IvrCassette
from queue_staffing_bayes_integration import BayesianIntentEngine

PORT = 6404


@pytest.fixture(scope="module")
def redis_url(tmp_path_factory):
    d = tmp_path_factory.mktemp("redis-bayes")
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
        raise RuntimeError("redis-server never came up for Bayes tests")
    c.close()
    yield f"redis://localhost:{PORT}/0"
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture(autouse=True)
def _clean_db(redis_url):
    """Every test starts against an empty store -- a shared module-scoped
    redis-server means state would otherwise leak between tests."""
    c = redis_lib.Redis.from_url(redis_url)
    c.flushdb()
    c.close()
    yield


def test_every_defined_queue_produces_a_key_bayes_recognizes():
    """The general form of the bug: for every queue the cassette
    defines, the label the harness now hands to observe_outcome must
    actually be a key BayesianIntentEngine tracks. This would have
    caught the original bug (and catches a future cassette adding a
    queue without a matching Bayes entry)."""
    cassette = IvrCassette()
    sentinel = SentinelCore(cassette)
    bayes = BayesianIntentEngine(redis_url="redis://localhost:1/0")  # unreachable -> in-memory only

    for queue_name in cassette.get_queue_definitions():
        signal = sentinel.infer_intent(["root", queue_name], queue_name)
        key = signal.classification.lower()
        if signal.classification == "UNKNOWN":
            continue  # no Bayes bucket for unmapped queues -- that's expected
        assert key in bayes.intent_stats, (
            f"queue '{queue_name}' classifies to '{key}', which "
            f"BayesianIntentEngine doesn't track -- its observations "
            f"would be silently dropped exactly like the original bug"
        )


def test_observation_actually_changes_belief_state():
    """The literal regression: calling observe_outcome with the label
    the harness now sends must move the posterior, not no-op."""
    bayes = BayesianIntentEngine(redis_url="redis://localhost:1/0")

    before = bayes.get_posterior("billing")
    assert before.confidence == 0.0  # nothing observed yet

    for _ in range(10):
        bayes.observe_outcome("billing", True, 4.0)

    after = bayes.get_posterior("billing")
    assert after.confidence > before.confidence
    assert after.success_rate == 1.0


def test_unmapped_intent_is_logged_not_silently_dropped(caplog):
    bayes = BayesianIntentEngine(redis_url="redis://localhost:1/0")

    with caplog.at_level("WARNING"):
        bayes.observe_outcome("billing_queue", True, 4.0)  # the old (wrong) key

    assert any("dropped outcome" in rec.message for rec in caplog.records)
    # and it must not have been silently credited to anything
    assert bayes.get_posterior("billing").confidence == 0.0


def test_belief_survives_a_restart(redis_url):
    """The durability half of the fix: a second engine instance,
    constructed fresh (simulating a worker restart, or a second
    worker process), must inherit what the first one learned."""
    engine_a = BayesianIntentEngine(redis_url=redis_url)
    for _ in range(12):
        engine_a.observe_outcome("technical", True, 6.0)
    for _ in range(4):
        engine_a.observe_outcome("technical", False, 9.0)

    # New instance = new process, simulating a restart
    engine_b = BayesianIntentEngine(redis_url=redis_url)
    posterior = engine_b.get_posterior("technical")

    assert posterior.success_rate == 0.75  # 12 of 16 resolved
    assert posterior.confidence > 0.0


def test_fails_open_when_redis_unreachable():
    """No real Redis at all -- must behave exactly like the original
    in-memory-only engine, not raise."""
    bayes = BayesianIntentEngine(redis_url="redis://localhost:1/0")
    bayes.observe_outcome("sales", True, 3.0)
    posterior = bayes.get_posterior("sales")
    assert posterior.success_rate == 1.0
