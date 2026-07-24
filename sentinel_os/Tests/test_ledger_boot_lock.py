"""
test_ledger_boot_lock -- regression test for the migration-lock stall
found and documented during the regulatory-cassette-framework build:
PostgreSQLLedger.__init__ used to re-run every `ALTER TABLE ... ADD
COLUMN IF NOT EXISTS` migration block on EVERY construction, and ALTER
TABLE takes an ACCESS EXCLUSIVE lock just to EVALUATE that IF NOT EXISTS
check -- so a boot against an already-current schema could still queue
indefinitely behind any lingering reader holding ACCESS SHARE on
ledger_entries (an idle-in-transaction connection, a long-running
query). This test reproduces that exact scenario directly: hold an
open read transaction on ledger_entries in one connection, then
construct a fresh PostgreSQLLedger in another -- construction must not
block on the reader when the schema is already fully migrated.

Requires a live Postgres (PG_CONFIG in Tests/conftest.py); skipped
otherwise, matching every other Postgres-backed test in this repo.
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import psycopg2
import pytest

from Tests.conftest import PG_CONFIG, _pg_available
from governance.ledger_postgres import PostgreSQLLedger


pytestmark = pytest.mark.skipif(not _pg_available(), reason="Postgres not available")


def _fully_migrated_ledger():
    """Construct once (runs every migration block if needed) so the
    schema is guaranteed current before the actual test begins -- the
    behavior under test is what happens on the NEXT construction
    against an already-current schema, not the first one.
    """
    ledger = PostgreSQLLedger(**PG_CONFIG)
    ledger.close()


def test_construction_does_not_block_behind_a_lingering_reader():
    _fully_migrated_ledger()

    # Hold an open read transaction on ledger_entries, exactly the
    # "idle-in-transaction reader" scenario that used to make every
    # subsequent construction queue behind it waiting for the ACCESS
    # EXCLUSIVE lock ALTER TABLE takes.
    reader_conn = psycopg2.connect(connect_timeout=2, **PG_CONFIG)
    reader_conn.autocommit = False
    reader_cur = reader_conn.cursor()
    reader_cur.execute("SELECT * FROM ledger_entries LIMIT 1;")
    # Deliberately do NOT commit or close -- this connection now holds
    # ACCESS SHARE on ledger_entries until we explicitly release it
    # below, simulating a lingering reader.

    try:
        result = {}

        def _construct():
            start = time.monotonic()
            ledger = PostgreSQLLedger(**PG_CONFIG)
            result["elapsed"] = time.monotonic() - start
            ledger.close()

        t = threading.Thread(target=_construct)
        t.start()
        # If construction is queuing behind the reader's ACCESS SHARE
        # (the bug), it can only proceed once the reader releases --
        # which we do only after this timeout, so a still-blocked
        # thread here proves the regression is back.
        t.join(timeout=5)
        blocked = t.is_alive()
    finally:
        reader_conn.rollback()
        reader_conn.close()

    if blocked:
        # Let the now-unblocked construction finish so the thread
        # doesn't leak past the test, then fail with the real signal.
        t.join(timeout=10)
        pytest.fail(
            "PostgreSQLLedger construction blocked for >5s behind a "
            "lingering reader's ACCESS SHARE lock -- migration blocks "
            "are taking ACCESS EXCLUSIVE even though the schema was "
            "already current (the exact stall this test guards against)."
        )

    assert result["elapsed"] < 5, (
        f"construction took {result['elapsed']:.2f}s -- expected well "
        "under the 5s the (still-held, until this assert) reader lock "
        "would have forced if migrations were still running unconditionally"
    )


def test_still_restores_a_genuinely_missing_trigger():
    """Same guarding principle as the column-migration test above, but
    for _apply_immutability_and_verify's trigger/role/grant guard: it
    must only skip reapplying ledger_immutability.sql when everything
    is already correct. Drop one protective trigger directly, then
    confirm construction restores it rather than silently starting an
    unprotected ledger.
    """
    _fully_migrated_ledger()

    conn = psycopg2.connect(connect_timeout=2, **PG_CONFIG)
    conn.autocommit = True
    conn.cursor().execute(
        "DROP TRIGGER IF EXISTS prevent_ledger_delete ON ledger_entries;"
    )
    conn.close()

    ledger = PostgreSQLLedger(**PG_CONFIG)
    ledger.close()

    conn = psycopg2.connect(connect_timeout=2, **PG_CONFIG)
    cur = conn.cursor()
    cur.execute("""
        SELECT tgname FROM pg_trigger t
        JOIN pg_class r ON t.tgrelid = r.oid
        WHERE r.relname = 'ledger_entries' AND NOT t.tgisinternal;
    """)
    installed = {row[0] for row in cur.fetchall()}
    conn.close()

    assert "prevent_ledger_delete" in installed, (
        "prevent_ledger_delete was dropped to simulate a genuinely "
        "unprotected ledger, but the next construction did not "
        "restore it -- the immutability-reapplication guard is "
        "skipping when it shouldn't, which would let a ledger boot "
        "with UPDATE/DELETE/TRUNCATE unprotected."
    )
def test_still_migrates_a_genuinely_stale_schema():
    """The guard must only skip a migration block when its columns are
    ALL already present -- a schema that's missing even one column in
    a block must still get that block's ALTER TABLE run. Simulate a
    stale schema by dropping one Phase-2 column, then confirm
    construction restores it.
    """
    _fully_migrated_ledger()

    conn = psycopg2.connect(connect_timeout=2, **PG_CONFIG)
    conn.autocommit = True
    conn.cursor().execute(
        "ALTER TABLE ledger_entries DROP COLUMN IF EXISTS model_identity;"
    )
    conn.close()

    ledger = PostgreSQLLedger(**PG_CONFIG)
    ledger.close()

    conn = psycopg2.connect(connect_timeout=2, **PG_CONFIG)
    cur = conn.cursor()
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'ledger_entries' AND column_name = 'model_identity';"
    )
    restored = cur.fetchone() is not None
    conn.close()

    assert restored, (
        "model_identity was dropped to simulate a stale schema, but "
        "the next construction did not restore it -- the existence "
        "guard is skipping a migration block it shouldn't."
    )
