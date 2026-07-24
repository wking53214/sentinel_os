"""
test_sealed_demographic_channel -- proof suite for the sealed channel
protected-characteristic data flows through (sealed_demographic_channel.py),
and the two structural guarantees the C2 dimension-4 build depends on:

1. The live judgment path (episode.py / cassette judge()/explain()) has
   no import of this module at all -- a static, direct proof, same
   posture as episode.py's actor_report wall.
2. The DB-level access wall actually holds: ledger_reader (the runtime
   identity the live judgment/ledger path connects as) has NO grant on
   protected_characteristic_estimates, and sealed_channel_writer has NO
   grant on ledger_entries -- proven against the real Postgres, not
   asserted.

Requires the same live Postgres the rest of the suite uses.
"""

import ast
import os
import sys

import psycopg2
import psycopg2.extras
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sealed_demographic_channel import (
    SOURCE_BISG_ESTIMATED,
    SOURCE_SELF_REPORTED,
    SealedDemographicChannel,
)

DSN = dict(
    host=os.environ.get("PGHOST", "127.0.0.1"),
    port=int(os.environ.get("PGPORT", "5432")),
    dbname=os.environ.get("PGDATABASE", "iceberg"),
    user=os.environ.get("PGUSER", "iceberg"),
    password=os.environ.get("PGPASSWORD", "iceberg"),
)

_TEST_PASSWORD = "sealed_channel_test_pw"


def _conn():
    c = psycopg2.connect(**DSN)
    c.autocommit = True
    return c


def _channel():
    return SealedDemographicChannel(
        **DSN, runtime_user="sealed_channel_writer", runtime_password=_TEST_PASSWORD,
    )


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ==========================================================================
# 1. Static proof: the live judgment path never imports this module.
# ==========================================================================

def _imports_in(path: str):
    with open(path) as f:
        tree = ast.parse(f.read(), filename=path)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_episode_module_never_imports_sealed_channel():
    imports = _imports_in(os.path.join(REPO_ROOT, "episode.py"))
    assert "sealed_demographic_channel" not in imports


def test_cfpb_lens_judgment_surface_never_imports_sealed_channel():
    """review()/explain() (via c2_rollup) are the judgment-adjacent
    surface; c2_rollup only ever ACCEPTS an already-computed dimension-4
    findings list as a parameter (see cfpb_reg_b.py's own docstring) --
    it must never import the module that could compute one itself."""
    imports = _imports_in(
        os.path.join(REPO_ROOT, "regulatory_cassettes", "cfpb_reg_b.py")
    )
    assert "sealed_demographic_channel" not in imports


def test_cassette_interface_and_kernel_never_import_sealed_channel():
    for module in ("cassette_interface.py", "episode.py", "regulatory_deck.py"):
        imports = _imports_in(os.path.join(REPO_ROOT, module))
        assert "sealed_demographic_channel" not in imports, module


# ==========================================================================
# 2. DB-level proof: the grants actually enforce the wall.
# ==========================================================================

def test_construction_requires_runtime_identity():
    with pytest.raises(RuntimeError, match="SEALED_CHANNEL_RUNTIME_USER"):
        SealedDemographicChannel(**DSN)


def test_construction_refuses_privileged_runtime_user():
    with pytest.raises(RuntimeError, match="superuser|table owner"):
        SealedDemographicChannel(**DSN, runtime_user="iceberg", runtime_password="iceberg")


def test_sealed_channel_writer_has_no_grant_on_ledger_entries():
    _channel()  # ensures the role exists
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT privilege_type FROM information_schema.table_privileges "
        "WHERE table_name = 'ledger_entries' AND grantee = 'sealed_channel_writer';"
    )
    assert cur.fetchall() == []


def test_ledger_reader_has_no_grant_on_protected_characteristic_estimates():
    _channel()  # ensures the table exists
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'ledger_reader';")
    if cur.fetchone() is None:
        pytest.skip("ledger_reader role not provisioned in this session yet")
    cur.execute(
        "SELECT privilege_type FROM information_schema.table_privileges "
        "WHERE table_name = 'protected_characteristic_estimates' "
        "AND grantee = 'ledger_reader';"
    )
    assert cur.fetchall() == []


def test_append_only_triggers_present():
    """pg_trigger, not information_schema.triggers: the latter is the
    SQL-standard view and does not surface TRUNCATE-event triggers at
    all (a documented Postgres/SQL-standard gap, not a sign the trigger
    is missing) -- confirmed directly: prevent_pce_truncate exists and
    fires (test_update_and_delete_blocked_even_for_owner and the
    equivalent TRUNCATE case both prove the UPDATE/DELETE triggers fire;
    this test proves all three are actually attached)."""
    _channel()
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT tgname FROM pg_trigger "
        "WHERE tgrelid = 'protected_characteristic_estimates'::regclass "
        "AND NOT tgisinternal;"
    )
    names = {r[0] for r in cur.fetchall()}
    assert {"prevent_pce_update", "prevent_pce_delete", "prevent_pce_truncate"} <= names


def test_update_and_delete_blocked_even_for_owner():
    """Table-level triggers, not just the writer role's own revoked
    grants -- fire for ANY role, same posture as ledger_entries."""
    conn = _conn()
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO protected_characteristic_estimates "
        "(subject_id, source, estimate) VALUES (%s, %s, %s) RETURNING id;",
        ("owner-trigger-test", SOURCE_SELF_REPORTED, psycopg2.extras.Json({"white": 1.0})),
    )
    row_id = cur.fetchone()[0]
    conn.commit()
    with pytest.raises(psycopg2.errors.RaiseException, match="append-only"):
        cur.execute("UPDATE protected_characteristic_estimates SET method='x' WHERE id=%s;",
                   (row_id,))
    conn.rollback()
    with pytest.raises(psycopg2.errors.RaiseException, match="immutable"):
        cur.execute("DELETE FROM protected_characteristic_estimates WHERE id=%s;", (row_id,))
    conn.rollback()


def test_truncate_blocked_even_for_owner():
    conn = _conn()
    conn.autocommit = False
    cur = conn.cursor()
    with pytest.raises(psycopg2.errors.RaiseException, match="append-only"):
        cur.execute("TRUNCATE protected_characteristic_estimates;")
    conn.rollback()


# ==========================================================================
# 3. Functional: write/read through the restricted role.
# ==========================================================================

def test_record_and_read_back_self_reported():
    channel = _channel()
    subject = f"subj-{os.urandom(4).hex()}"
    channel.record_estimate(
        subject_id=subject, source=SOURCE_SELF_REPORTED,
        estimate={"hispanic": 1.0}, cohort_key="cohort-a",
        method="customer self-report via intake form",
    )
    got = channel.get_estimate_for_subject(subject)
    assert got.subject_id == subject
    assert got.source == SOURCE_SELF_REPORTED
    assert got.estimate == {"hispanic": 1.0}


def test_record_and_read_back_bisg_estimated_cohort():
    channel = _channel()
    cohort = f"cohort-{os.urandom(4).hex()}"
    for i in range(3):
        channel.record_estimate(
            subject_id=f"{cohort}-{i}", source=SOURCE_BISG_ESTIMATED,
            estimate={"white": 0.6, "black": 0.2, "hispanic": 0.2},
            cohort_key=cohort, method="bisg_v1_tract",
        )
    rows = channel.get_estimates_for_cohort(cohort)
    assert len(rows) == 3
    assert all(r.source == SOURCE_BISG_ESTIMATED for r in rows)


def test_invalid_source_rejected():
    channel = _channel()
    with pytest.raises(ValueError, match="source"):
        channel.record_estimate(subject_id="x", source="guessed", estimate={})


def test_blank_subject_id_rejected():
    channel = _channel()
    with pytest.raises(ValueError, match="subject_id"):
        channel.record_estimate(subject_id="   ", source=SOURCE_SELF_REPORTED, estimate={})


def test_unknown_subject_returns_none():
    channel = _channel()
    assert channel.get_estimate_for_subject(f"never-recorded-{os.urandom(8).hex()}") is None
