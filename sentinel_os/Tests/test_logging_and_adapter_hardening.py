"""2026-11-13 hardening: (1) setup_logging no longer stacks a handler per
call
(2) the worker adapter's duplicate pre-check failing is a retryable
ledger_write_failed, not an exception out of process_call.

Run: python3 -m pytest Tests/test_logging_and_adapter_hardening.py -v
"""
import io
import logging
import os
import sys
import uuid

import psycopg2
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from cassettes.mortgage_cassette import MortgageCassette
from circuit_breaker import CircuitBreaker
from governance_harness import GovernanceHarness
from operational_resilience import setup_logging
from sentinel_worker import GovernanceHarnessJobAdapter


def test_setup_logging_is_idempotent_for_one_name():
    name = f"hardening.{uuid.uuid4().hex}"
    for _ in range(6):
        lg = setup_logging(name)
    assert len(lg.handlers) == 1


def test_many_breakers_with_one_name_log_each_event_once():
    name = f"dup-{uuid.uuid4().hex[:8]}"
    brs = [CircuitBreaker(name=name, failure_threshold=1, reset_timeout_s=30) for _ in range(5)]
    lg = logging.getLogger(f"CircuitBreaker.{name}")
    buf = io.StringIO()
    lg.handlers[0].setStream(buf)
    with pytest.raises(RuntimeError):
        brs[0].call(lambda: (_ for _ in ()).throw(RuntimeError("x")))
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert len(lines) == len(set(lines)), f"duplicate log lines: {lines}"


def _pg_up():
    try:
        psycopg2.connect(connect_timeout=2, host="localhost", dbname="iceberg",
                         user="iceberg", password="iceberg").close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _pg_up(), reason="live PostgreSQL not available")
def test_duplicate_precheck_failure_is_a_retryable_ledger_failure_not_an_exception():
    cfg = {"postgres_host": "localhost", "postgres_port": 5432, "postgres_db": "iceberg",
           "postgres_user": "iceberg", "postgres_password": "iceberg", "claude_api_key": None}
    h = GovernanceHarness(cfg, MortgageCassette(), require_cassette_binding=True)
    adapter = GovernanceHarnessJobAdapter(h, require_events=True)
    eid = f"HARD-{uuid.uuid4().hex[:10]}"
    payload = {"episode_id": eid, "requested": {"amount": 300000.0},
               "events": [{"event_id": f"{eid}:decision", "episode_id": eid, "domain": "mortgage",
                           "kind": "decision_observed", "occurred_at": 1000.0, "observed_at": 1001.0,
                           "source": "loan_system", "provenance": "verified",
                           "fields": {"amount": 250000.0}}],
               "outcome_reasons": ["reduced based on updated appraisal value on file"]}
    h.ledger.pool.closeall()          # a genuinely dead connection pool
    out = adapter.process_call(payload)  # must NOT raise
    assert out["ledger_write_failed"] is True and out["error"] is None
