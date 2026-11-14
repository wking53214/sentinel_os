"""
test_ledger_first_boot_race -- regression test for the first-boot crash found
by test_multi_worker_breakers.py in CI (sentinel_os #128).

Two workers started together against a database that has no ledger table yet
both ran PostgreSQLLedger._initialize_schema(). CREATE TABLE IF NOT EXISTS is
not safe against itself: two sessions can both pass the existence check and
then collide on the system catalog's unique index. The loser raised

    psycopg2.errors.UniqueViolation: duplicate key value violates unique
    constraint "pg_type_typname_nsp_index"

out of the constructor, so that worker never started. The race only exists on
the FIRST boot of an empty database, which is why the failure showed up on the
first run of a CI job and not on the second.

Each round below builds a brand-new empty database, releases several threads
at once into PostgreSQLLedger(), and requires that every one of them
constructs. Several rounds are run because the race is a matter of timing.

Requires a live Postgres (PG_CONFIG in Tests/conftest.py) whose user may create
databases; skipped otherwise, like every other Postgres-backed test here.
"""

import os
import sys
import threading
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import psycopg2
import pytest

from Tests.conftest import PG_CONFIG, _pg_available
from governance.ledger_postgres import PostgreSQLLedger


pytestmark = pytest.mark.skipif(not _pg_available(), reason="Postgres not available")

THREADS = 6
ROUNDS = 3


def _admin():
    conn = psycopg2.connect(connect_timeout=2, **PG_CONFIG)
    conn.autocommit = True
    return conn


def _boot_together(dbname):
    """Release THREADS constructions at the same instant; return their errors."""
    config = {**PG_CONFIG, "dbname": dbname}
    barrier = threading.Barrier(THREADS)
    errors = []

    def _boot():
        try:
            barrier.wait(timeout=30)
            PostgreSQLLedger(**config).close()
        except Exception as exc:  # collected, then asserted on below
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=_boot) for _ in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    return errors


def test_simultaneous_first_boots_against_an_empty_database_all_construct():
    admin = _admin()
    try:
        for _ in range(ROUNDS):
            dbname = f"race_{uuid.uuid4().hex[:10]}"
            admin.cursor().execute(f'CREATE DATABASE "{dbname}"')
            try:
                errors = _boot_together(dbname)
            finally:
                admin.cursor().execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
            assert errors == [], (
                f"{len(errors)} of {THREADS} simultaneous first boots failed; the first was: {errors[0]}"
            )
    finally:
        admin.close()
