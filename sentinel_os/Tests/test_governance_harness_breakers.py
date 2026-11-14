"""Live proof that GovernanceHarness (the path sentinel_worker.py actually runs)
is protected by two SEPARATE circuit breakers and by real time limits.

History: the breakers were first wired into production_harness.py (July 2026).
That file was extracted away in August, so the live worker path had no
breaker at all until this change. These tests fail if the wiring is removed.

Nothing here is mocked: the Claude path uses the real GovernanceDecider and
the real anthropic client (bad key, so every call is a genuine transport or
auth failure); the ledger path uses a real Postgres whose role is genuinely
made unable to write; the timeout test uses a real TCP socket that never
answers.

Run:  python3 -m pytest Tests/test_governance_harness_breakers.py -v -s
"""
import os
import socket
import sys
import threading
import time
import uuid

import psycopg2
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from cassettes.mortgage_cassette import MortgageCassette
from circuit_breaker import CircuitOpenError, CircuitState
from episode import make_episode
from governance_decider import GovernanceDecider
from governance_harness import GovernanceHarness

OWNER = dict(host="localhost", port=5432, dbname="iceberg", user="iceberg", password="iceberg")
PG_CONFIG = {"postgres_host": "localhost", "postgres_port": 5432, "postgres_db": "iceberg",
             "postgres_user": "iceberg", "postgres_password": "iceberg", "claude_api_key": None}
OFFLINE = {"postgres_host": None, "claude_api_key": None}


def _pg_up():
    try:
        psycopg2.connect(connect_timeout=2, **OWNER).close()
        return True
    except Exception:
        return False


requires_pg = pytest.mark.skipif(not _pg_up(), reason="live PostgreSQL not available")


def _governed_episode():
    # one requested-vs-actual mismatch >= the cassette's governance trigger
    eid = f"BRK-{uuid.uuid4().hex[:12]}"
    return make_episode(eid, "mortgage", requested={"granted": True, "amount": 500.0},
                        actual={"granted": True, "amount": 350.0},
                        outcome_reasons=("amount capped by program ceiling",))


# ---------------------------------------------------------------- Claude --
def test_claude_breaker_opens_on_real_failures_and_ledger_breaker_is_untouched(monkeypatch):
    monkeypatch.setenv("GOVERNOR_TIMEOUT_S", "5")
    h = GovernanceHarness(OFFLINE, MortgageCassette(), require_cassette_binding=False)
    h.decider = GovernanceDecider(api_key="sk-ant-this-key-is-invalid-on-purpose")
    assert h.claude_breaker.snapshot()["state"] == CircuitState.CLOSED.value

    for i in range(5):
        r = h.process(_governed_episode(), issue_count=1)
        assert r["governed"] is True and r["governance_approved"] is False  # fails closed
    snap = h.claude_breaker.snapshot()
    assert snap["state"] == CircuitState.OPEN.value, snap
    assert snap["total_failures"] == 5

    # 6th call: breaker rejects WITHOUT touching the network, still fails closed.
    t0 = time.monotonic()
    r = h.process(_governed_episode(), issue_count=1)
    elapsed = time.monotonic() - t0
    assert r["governance_approved"] is False
    assert elapsed < 0.5, f"open breaker should reject instantly, took {elapsed:.2f}s"
    assert h.claude_breaker.snapshot()["total_rejections"] == 1

    # F-A isolation: the other dependency's breaker saw nothing.
    ls = h.ledger_breaker.snapshot()
    assert ls["state"] == CircuitState.CLOSED.value and ls["total_calls"] == 0


def test_two_harnesses_never_share_breaker_state():
    a = GovernanceHarness(OFFLINE, MortgageCassette(), require_cassette_binding=False)
    b = GovernanceHarness(OFFLINE, MortgageCassette(), require_cassette_binding=False)
    assert a.claude_breaker is not b.claude_breaker
    assert a.claude_breaker is not a.ledger_breaker


# --------------------------------------------------------------- timeouts --
def test_governor_default_time_limit_is_20s_with_no_hidden_retries():
    d = GovernanceDecider(api_key="sk-ant-x")
    assert d.client.timeout == 20.0
    assert d.client.max_retries == 0


def test_governor_call_gives_up_on_a_server_that_never_answers(monkeypatch):
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(5)
    port = srv.getsockname()[1]
    held = []
    stop = threading.Event()

    def accept_and_say_nothing():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                held.append(srv.accept()[0])
            except OSError:
                pass

    threading.Thread(target=accept_and_say_nothing, daemon=True).start()
    monkeypatch.setenv("GOVERNOR_TIMEOUT_S", "1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    try:
        d = GovernanceDecider(api_key="sk-ant-x")
        t0 = time.monotonic()
        out = d.safety_check("episode_judgment", {"k": "v"})
        elapsed = time.monotonic() - t0
    finally:
        stop.set(); srv.close()
        for c in held:
            c.close()
    assert out["safe"] is False
    assert out["reasoning"].startswith("transport_error:"), out["reasoning"]
    assert elapsed < 4.0, f"1s limit, but the call hung {elapsed:.1f}s"


@requires_pg
def test_ledger_connection_carries_the_time_limits_and_a_slow_query_is_cancelled(monkeypatch):
    monkeypatch.setenv("LEDGER_STATEMENT_TIMEOUT_S", "1")
    monkeypatch.setenv("LEDGER_CONNECT_TIMEOUT_S", "5")
    h = GovernanceHarness(PG_CONFIG, MortgageCassette(), require_cassette_binding=True)
    try:
        conn = h.ledger.pool.getconn()
        try:
            assert conn.get_dsn_parameters()["connect_timeout"] == "5"
            cur = conn.cursor()
            cur.execute("SHOW statement_timeout"); assert cur.fetchone()[0] == "1s"
            t0 = time.monotonic()
            with pytest.raises(psycopg2.errors.QueryCanceled):
                cur.execute("SELECT pg_sleep(5)")
            assert time.monotonic() - t0 < 3.0
            conn.rollback()
        finally:
            h.ledger.pool.putconn(conn)
    finally:
        h.shutdown()


# ----------------------------------------------------------------- ledger --
@requires_pg
def test_ledger_breaker_opens_on_real_write_failures_and_claude_breaker_is_untouched(monkeypatch):
    role, pw = "brk_writer_test", "brk_writer_pw"
    own = psycopg2.connect(**OWNER); own.autocommit = True
    cur = own.cursor()
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
    if not cur.fetchone():
        cur.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{pw}'")
    cur.execute(f"ALTER ROLE {role} RESET default_transaction_read_only")
    # Same powers as the real runtime role (SELECT + INSERT, nothing else).
    cur.execute(f"GRANT ledger_reader TO {role}")
    monkeypatch.setenv("ICEBERG_LEDGER_RUNTIME_USER", role)
    monkeypatch.setenv("ICEBERG_LEDGER_RUNTIME_PASSWORD", pw)
    h = None
    try:
        h = GovernanceHarness(PG_CONFIG, MortgageCassette(), require_cassette_binding=True)
        h.decider = _SafeDecider()
        # healthy first: a real write succeeds through the breaker
        r = h.process(_governed_episode(), issue_count=1)
        assert r["governance_approved"] is True
        assert h.ledger_breaker.snapshot()["total_failures"] == 0

        # now genuinely remove its ability to write, and drop its live sessions
        cur.execute(f"ALTER ROLE {role} SET default_transaction_read_only = on")
        cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename=%s", (role,))

        for _ in range(3):
            with pytest.raises(Exception):
                h.process(_governed_episode(), issue_count=1)
        snap = h.ledger_breaker.snapshot()
        assert snap["state"] == CircuitState.OPEN.value, snap
        assert snap["total_failures"] == 3

        t0 = time.monotonic()
        with pytest.raises(CircuitOpenError):
            h.process(_governed_episode(), issue_count=1)
        assert time.monotonic() - t0 < 1.0

        # F-A isolation: Claude breaker never failed and never opened.
        cs = h.claude_breaker.snapshot()
        assert cs["state"] == CircuitState.CLOSED.value and cs["total_failures"] == 0
    finally:
        cur.execute(f"ALTER ROLE {role} RESET default_transaction_read_only")
        if h is not None:
            try:
                h.shutdown()
            except Exception:
                pass
        own.close()


class _SafeDecider:
    def safety_check(self, action, details):
        return {"safe": True, "reasoning": "stub-approve", "model_identity": "stub", "cost": None}
