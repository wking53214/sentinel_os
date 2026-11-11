"""Operator tool that gives pre-existing replicas a read token."""
import os
import stat
import uuid

import psycopg2
import pytest

import twin_issue_read_tokens as tool
from twin_receiver import SCHEMA

DSN = "host=localhost dbname=iceberg user=iceberg password=iceberg"


def _pg_available() -> bool:
    try:
        psycopg2.connect(DSN + " connect_timeout=2").close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="read-token tool needs PostgreSQL (iceberg/iceberg@localhost)")


@pytest.fixture
def conn():
    c = psycopg2.connect(DSN)
    with c.cursor() as cur:
        cur.execute(SCHEMA)  # a fresh database has no replica_meta until the receiver creates it
        cur.execute("ALTER TABLE replica_meta ADD COLUMN IF NOT EXISTS read_token TEXT")
    c.commit()
    yield c
    c.close()


def _insert(conn, rid, read_token):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO replica_meta (replica_id, site, custody_model, recipient_pub, "
                    "recipient_fp, customer_sign_pub, ship_token, read_token) "
                    "VALUES (%s,'s','A','p','f','k','t',%s)", (rid, read_token))
    conn.commit()


def test_dry_run_changes_nothing(conn, tmp_path):
    rid = f"tok-{uuid.uuid4().hex[:8]}"
    _insert(conn, rid, None)
    assert tool.main(["--dsn", DSN]) == 0
    with conn.cursor() as cur:
        cur.execute("SELECT read_token FROM replica_meta WHERE replica_id=%s", (rid,))
        assert cur.fetchone()[0] is None


def test_apply_fills_only_missing_tokens_and_never_overwrites(conn, tmp_path):
    unset = f"tok-{uuid.uuid4().hex[:8]}"
    keep = f"tok-{uuid.uuid4().hex[:8]}"
    _insert(conn, unset, None)
    _insert(conn, keep, "already-set")
    out = tmp_path / "issued.tsv"
    assert tool.main(["--dsn", DSN, "--apply", "--output", str(out)]) == 0
    with conn.cursor() as cur:
        cur.execute("SELECT read_token FROM replica_meta WHERE replica_id=%s", (unset,))
        filled = cur.fetchone()[0]
        cur.execute("SELECT read_token FROM replica_meta WHERE replica_id=%s", (keep,))
        assert cur.fetchone()[0] == "already-set"
    assert filled and len(filled) >= 32
    text = out.read_text()
    assert f"{unset}\t{filled}" in text
    assert keep not in text
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600


def test_apply_requires_an_output_file(conn):
    with pytest.raises(SystemExit):
        tool.main(["--dsn", DSN, "--apply"])
