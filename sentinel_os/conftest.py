"""Repo-root pytest configuration.

governance/ledger_postgres.py's PostgreSQLLedger now refuses to boot
unless ICEBERG_LEDGER_RUNTIME_USER is set to a non-owner, non-superuser
role (see the ICEBERG_LEDGER_RUNTIME_USER fail-closed fix). Most tests
in this repo aren't testing THAT behavior specifically -- they just
want a working ledger -- so this gives every test a real, working,
already-restricted default identity (the `ledger_reader` role the
ledger itself creates via ledger_immutability.sql) instead of making
every test file hand-roll credentials.

Tests that want a different runtime identity on purpose (e.g.
test_production_harness_breakers.py's `harness_no_claude` fixture,
which needs a role it can surgically revoke INSERT from) set their own
env vars for the duration of that test and are unaffected -- this
fixture only fills in a default when the var is unset or empty, never
overrides an explicit value.
"""
import os
import pytest

_PG_OWNER = dict(host="localhost", port=5432, dbname="iceberg",
                  user="iceberg", password="iceberg")
_LEDGER_READER_TEST_PASSWORD = "ledger_reader_test_pw"


def _pg_available() -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(connect_timeout=2, **_PG_OWNER)
        conn.close()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session", autouse=True)
def _ensure_default_ledger_runtime_role():
    """Once per session: make sure `ledger_reader` has a known password,
    and that sentinelsvc (if the twin live-suite's OS identity exists)
    is a member of it.

    The role itself is created idempotently by ledger_immutability.sql
    (which every PostgreSQLLedger construction applies), but CREATE ROLE
    there deliberately doesn't set a password inline (no credential
    baked into a file that gets committed). Tests need one set up front
    so the very first ledger construction of the session -- which now
    must resolve a working non-owner runtime user before it can even
    open its pool -- doesn't fail on auth before it gets anywhere near
    the code being tested.

    The sentinelsvc grant lives HERE rather than only in
    scripts/twin_ensure_services.sh because this fixture is the one
    place guaranteed to run before ledger_reader is first needed,
    regardless of collection order -- twin_ensure_services.sh grants
    membership too (for a bare `twin_ensure_services` run outside
    pytest), but if test_twin_live.py's own session-scoped `services`
    fixture happened to run before this one in a given collection
    order, that script would find no ledger_reader role yet to grant
    into and silently skip, leaving twin_shipper.py unable to read the
    ledger for the rest of that session. Doing it here removes the
    ordering dependency: by the time ANY test in the session needs
    ledger_reader, both the role and (when applicable) sentinelsvc's
    membership in it already exist.
    """
    if not _pg_available():
        yield
        return
    import psycopg2
    conn = psycopg2.connect(connect_timeout=2, **_PG_OWNER)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='ledger_reader') THEN "
        "CREATE ROLE ledger_reader WITH LOGIN PASSWORD %(pw)s; "
        "ELSE ALTER ROLE ledger_reader WITH PASSWORD %(pw)s; "
        "END IF; END $$;",
        {"pw": _LEDGER_READER_TEST_PASSWORD},
    )
    cur.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT FROM pg_roles WHERE rolname='sentinelsvc') THEN "
        "GRANT ledger_reader TO sentinelsvc; "
        "END IF; END $$;"
    )
    conn.close()
    yield


@pytest.fixture(autouse=True)
def _default_ledger_runtime_user(_ensure_default_ledger_runtime_role):
    """Before every test: fill in a working runtime identity if the test
    (or a stale module-level default) left the var unset or empty.

    Checked with .get() + falsy test rather than setdefault(), because
    a couple of test files do
    `os.environ.setdefault("ICEBERG_LEDGER_RUNTIME_USER", "")` at import
    time -- a leftover from when an empty string meant "fall back to
    the owner credentials". That value is present-but-empty, so a plain
    setdefault() here would never override it. Falsy-check does.
    """
    if not os.environ.get("ICEBERG_LEDGER_RUNTIME_USER"):
        os.environ["ICEBERG_LEDGER_RUNTIME_USER"] = "ledger_reader"
    if not os.environ.get("ICEBERG_LEDGER_RUNTIME_PASSWORD"):
        os.environ["ICEBERG_LEDGER_RUNTIME_PASSWORD"] = _LEDGER_READER_TEST_PASSWORD
    yield
