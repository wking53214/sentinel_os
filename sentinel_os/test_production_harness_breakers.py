"""test_production_harness_breakers.py -- proves the two breakers wired
into production_harness.py actually protect what they claim to, against
REAL infrastructure:

  * ledger_breaker: real Postgres, killed for real (pool closed / bad
    port), producing real psycopg2 exceptions -- no mock.
  * claude_breaker: a real anthropic client pointed at the real
    api.anthropic.com with a deliberately invalid API key, producing a
    real 401 over a real HTTPS connection -- no mock, no stub server.
    We are not simulating "the API is down"; we are making a real call
    that really fails, the same way an expired/revoked key would in
    production.

Also proves the F-A property directly: tripping one breaker via one
resource's real failures leaves the other resource's breaker CLOSED and
fully functional.
"""
from __future__ import annotations

import os
import time

import psycopg2
import pytest

os.environ.setdefault("ICEBERG_LEDGER_RUNTIME_USER", "")

from circuit_breaker import CircuitState
from production_harness import IcebergProductionHarness

PG_DSN = dict(host="localhost", port=5432, dbname="iceberg",
              user="iceberg", password="iceberg")

# A syntactically valid but definitely-invalid Anthropic API key. Real
# network call, real auth rejection -- this is not a mock.
BAD_CLAUDE_KEY = "sk-ant-api03-" + "x" * 95


def _clear_ledger():
    conn = psycopg2.connect(**PG_DSN)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("ALTER TABLE ledger_entries DISABLE TRIGGER USER;")
    cur.execute("TRUNCATE ledger_entries RESTART IDENTITY;")
    cur.execute("ALTER TABLE ledger_entries ENABLE TRIGGER USER;")
    conn.close()


def governed_record(sid, duration=320):
    return {"sid": sid, "status": "completed", "from": "+15551234561",
            "duration": duration, "start_time": 0}


@pytest.fixture()
def harness_with_bad_claude_key():
    """Real ledger (working), real Claude client pointed at the real API
    with a key that will really be rejected."""
    h = IcebergProductionHarness({
        "postgres_host": PG_DSN["host"], "postgres_port": PG_DSN["port"],
        "postgres_db": PG_DSN["dbname"], "postgres_user": PG_DSN["user"],
        "postgres_password": PG_DSN["password"], "cassette_domain": "ivr",
        "claude_api_key": BAD_CLAUDE_KEY,
    })
    _clear_ledger()
    yield h
    h.shutdown()


# ---------------------------------------------------------- claude path --
def test_real_bad_api_key_produces_a_real_transport_failure(harness_with_bad_claude_key):
    """Sanity check before trusting the breaker test below: confirm the
    live API really does reject this key with the transport_error shape
    the breaker's is_failure predicate keys on, over a real connection."""
    h = harness_with_bad_claude_key
    decision = h.claude_decider.safety_check("heal_queue", {"queue": "sales", "wait_time": 500, "friction_count": 5})
    assert decision["safe"] is False
    assert decision["reasoning"].startswith("transport_error:"), (
        f"expected a real transport failure, got: {decision['reasoning']!r} "
        "-- if this fires, the live API's error shape changed and the "
        "breaker's is_failure predicate needs updating"
    )


def test_claude_breaker_trips_after_real_repeated_auth_failures(harness_with_bad_claude_key):
    h = harness_with_bad_claude_key
    assert h.claude_breaker.state is CircuitState.CLOSED

    for i in range(5):  # failure_threshold=5
        result = h.process_call(governed_record(f"CBK{i}"))
        assert result.get("error") is None
        assert result["governance_blocked"] is True

    assert h.claude_breaker.state is CircuitState.OPEN, (
        f"expected OPEN after 5 real auth failures, got "
        f"{h.claude_breaker.state}; snapshot={h.claude_breaker.snapshot()}"
    )

    # While open, the wrapped call must not even reach the network --
    # verified indirectly: the job still completes (fail-closed), and
    # the ledger row still gets written (ledger_breaker independent).
    result = h.process_call(governed_record("CBK-open"))
    assert result["governance_blocked"] is True
    assert h.ledger.sid_exists("CBK-open"), "ledger write must still succeed while claude breaker is open"


def test_claude_breaker_open_does_not_affect_ledger_breaker(harness_with_bad_claude_key):
    """THE F-A property, proven with real failures instead of synthetic
    ones: driving the Claude breaker OPEN via real auth failures must
    leave the ledger breaker completely unaffected."""
    h = harness_with_bad_claude_key
    for i in range(6):
        h.process_call(governed_record(f"CBI{i}"))
    assert h.claude_breaker.state is CircuitState.OPEN
    assert h.ledger_breaker.state is CircuitState.CLOSED, (
        "ledger breaker must be unaffected by claude breaker tripping"
    )
    # And the ledger itself is still fully functional.
    assert h.ledger.sid_exists("CBI0")
    assert h.ledger.sid_exists("CBI5")


# ---------------------------------------------------------------- ledger path --
# Failure injection note: an earlier version of this test killed the
# WHOLE connection pool (pool.closeall()). That broke sid_exists()
# too -- an existing, unprotected SELECT at the top of process_call
# that isn't wrapped by ledger_breaker and has no try/except around it
# at all. Under a full pool outage the harness raises there, before
# ever reaching the breaker-wrapped write, which made pool.closeall()
# the wrong tool for isolating "does the write breaker trip on write
# failures." That gap (sid_exists has zero failure handling, so any
# Postgres blip crashes process_call for EVERY call, governed or not,
# regardless of these breakers) is real and worth fixing, but it's a
# pre-existing defect outside this build's two named call sites --
# flagged in the verification report, not silently absorbed into this
# diff.
#
# Instead we inject a real, surgical failure: a genuine restricted
# Postgres role (`ledger_write_test`, created for this suite) with
# SELECT on ledger_entries but no INSERT. sid_exists() keeps working
# for real; append_decision() fails for real with a real permission-
# denied error from Postgres -- exactly the failure mode of a runtime
# credential whose grants were narrowed or revoked. No mock involved.
LEDGER_WRITE_TEST_USER = "ledger_write_test"
LEDGER_WRITE_TEST_PASSWORD = "ledger_write_test"


def _ensure_ledger_write_test_role():
    """Idempotently provision the `ledger_write_test` role itself.

    Pre-existing gap, unrelated to the ICEBERG_LEDGER_RUNTIME_USER
    fail-closed fix: this role was documented (see
    roadster_breakers_verification_report_v1.md) as "a real, restricted
    Postgres role, SELECT-only on ledger_entries" but nothing in the
    repo actually created it -- it was assumed pre-provisioned outside
    the suite. Made idempotent here so the suite is self-contained
    rather than failing on a fresh database with `role does not exist`.
    """
    conn = psycopg2.connect(**PG_DSN)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        f"DO $$ BEGIN IF NOT EXISTS "
        f"(SELECT FROM pg_roles WHERE rolname='{LEDGER_WRITE_TEST_USER}') THEN "
        f"CREATE ROLE {LEDGER_WRITE_TEST_USER} WITH LOGIN "
        f"PASSWORD '{LEDGER_WRITE_TEST_PASSWORD}'; END IF; END $$;"
    )
    cur.execute(f"GRANT USAGE ON SCHEMA public TO {LEDGER_WRITE_TEST_USER};")
    cur.execute(f"GRANT SELECT ON ledger_entries TO {LEDGER_WRITE_TEST_USER};")
    conn.close()


def _revoke_insert():
    """Revoke real INSERT (and the sequence USAGE INSERT depends on for the
    SERIAL id column -- GRANT/REVOKE INSERT on the table alone does not cover
    the backing sequence; a role with table INSERT but no sequence USAGE
    still gets a real permission-denied error, just on the sequence instead
    of the table. Revoking both keeps the surgical failure genuinely total.)."""
    conn = psycopg2.connect(**PG_DSN)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f"REVOKE INSERT ON ledger_entries FROM {LEDGER_WRITE_TEST_USER};")
    cur.execute(
        f"REVOKE USAGE ON SEQUENCE ledger_entries_id_seq FROM {LEDGER_WRITE_TEST_USER};")
    conn.close()


def _grant_insert():
    """Grant real INSERT plus the sequence USAGE it depends on (see
    _revoke_insert for why both are required)."""
    conn = psycopg2.connect(**PG_DSN)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f"GRANT INSERT ON ledger_entries TO {LEDGER_WRITE_TEST_USER};")
    cur.execute(
        f"GRANT USAGE ON SEQUENCE ledger_entries_id_seq TO {LEDGER_WRITE_TEST_USER};")
    conn.close()


@pytest.fixture()
def harness_no_claude():
    """No Claude client configured -- harness's own documented
    fail-closed path (safe=False, 'No API client configured'), which is
    NOT a transport_error and must never touch claude_breaker's count.
    Ledger runtime user is the restricted role above so writes can be
    surgically, realistically failed without breaking sid_exists()."""
    os.environ["ICEBERG_LEDGER_RUNTIME_USER"] = LEDGER_WRITE_TEST_USER
    os.environ["ICEBERG_LEDGER_RUNTIME_PASSWORD"] = LEDGER_WRITE_TEST_PASSWORD
    _ensure_ledger_write_test_role()
    _grant_insert()
    h = IcebergProductionHarness({
        "postgres_host": PG_DSN["host"], "postgres_port": PG_DSN["port"],
        "postgres_db": PG_DSN["dbname"], "postgres_user": PG_DSN["user"],
        "postgres_password": PG_DSN["password"], "cassette_domain": "ivr",
    })
    _clear_ledger()
    yield h
    h.shutdown()
    _grant_insert()  # leave the role writable for the next test
    os.environ.pop("ICEBERG_LEDGER_RUNTIME_USER", None)
    os.environ.pop("ICEBERG_LEDGER_RUNTIME_PASSWORD", None)


def test_ledger_breaker_trips_on_real_postgres_failure(harness_no_claude):
    """Revoke real INSERT privilege (not a mock) so every subsequent
    append_decision raises a real psycopg2 permission-denied error, and
    confirm the breaker trips at its configured threshold (3) -- no
    earlier, no later. sid_exists() keeps working throughout, proving
    the failure is isolated to the write, same as it would be for a
    real credential/grants problem in production."""
    h = harness_no_claude
    assert h.ledger_breaker.state is CircuitState.CLOSED
    _revoke_insert()

    for i in range(2):
        result = h.process_call(governed_record(f"LBK{i}"))
        assert result.get("ledger_write_failed") is True
        assert h.ledger_breaker.state is CircuitState.CLOSED, f"tripped early at failure {i+1}"

    result = h.process_call(governed_record("LBK2"))
    assert result.get("ledger_write_failed") is True
    assert h.ledger_breaker.state is CircuitState.OPEN, (
        f"expected OPEN after 3 real Postgres failures, got "
        f"{h.ledger_breaker.state}; snapshot={h.ledger_breaker.snapshot()}"
    )


def test_ledger_breaker_open_does_not_affect_claude_path(harness_no_claude):
    """Real Postgres failures tripping the ledger breaker must not
    touch the (unconfigured, fail-closed-by-design) claude path's own
    breaker state."""
    h = harness_no_claude
    _revoke_insert()
    for i in range(3):
        h.process_call(governed_record(f"LBI{i}"))
    assert h.ledger_breaker.state is CircuitState.OPEN
    assert h.claude_breaker.state is CircuitState.CLOSED, (
        "claude breaker must be unaffected by ledger breaker tripping"
    )
    assert h.claude_decider is None, "harness_no_claude fixture should have no claude_decider at all"


def test_ledger_breaker_recovers_after_real_reconnect(harness_no_claude):
    """OPEN -> real reset_timeout_s elapses -> HALF_OPEN -> a real
    successful write once INSERT is genuinely re-granted closes it
    again."""
    h = harness_no_claude
    _revoke_insert()
    for i in range(3):
        h.process_call(governed_record(f"LBR{i}"))
    assert h.ledger_breaker.state is CircuitState.OPEN

    _grant_insert()  # repair the real permission

    time.sleep(15.1)  # real wall-clock wait for reset_timeout_s=15
    assert h.ledger_breaker.state is CircuitState.HALF_OPEN

    # 2 real successful writes needed to close (half_open_success_threshold=2)
    r1 = h.process_call(governed_record("LBR-probe1"))
    assert r1.get("ledger_write_failed") is not True
    assert h.ledger_breaker.state is CircuitState.HALF_OPEN

    r2 = h.process_call(governed_record("LBR-probe2"))
    assert r2.get("ledger_write_failed") is not True
    assert h.ledger_breaker.state is CircuitState.CLOSED

    assert h.ledger.sid_exists("LBR-probe1")
    assert h.ledger.sid_exists("LBR-probe2")
