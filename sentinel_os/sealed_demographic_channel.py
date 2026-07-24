"""
Sealed Demographic Channel -- protected-characteristic data, structurally
walled off from the live decision path.

C2 dimension 4 (statistical outcome-equity) needs estimated or
self-reported protected-characteristic data (race/ethnicity) to compare
outcomes across groups. This module is the ONLY way to write or read that
data. Two guarantees, both structural (enforced by Postgres grants, not
just convention):

1. NEVER the ledger's own connection. SealedDemographicChannel always
   connects as `sealed_channel_writer` (see sealed_demographic_channel.sql),
   a role granted SELECT/INSERT on protected_characteristic_estimates
   and NOTHING else -- no grant on ledger_entries, ever. Symmetrically,
   ledger_reader (the runtime identity governance/ledger_postgres.py's
   PostgreSQLLedger actually connects as -- the live judgment path's own
   identity) is never granted anything on this table either (the SQL
   file's REVOKE ALL ... FROM ledger_reader makes this explicit and
   re-assertable, not just an absence).
2. episode.py / cassette judge()/explain() never import this module.
   That is enforced by convention plus a direct test
   (Tests/test_sealed_demographic_channel.py::
   test_judgment_path_has_no_import_of_sealed_channel) -- the same
   "never trust the actor's self-report" posture episode.py already
   holds for actor_report, extended to protected-characteristic data:
   recorded, walled off, never read by judgment.

Same construction posture as PostgreSQLLedger, deliberately: an owner
connection creates/migrates the schema and role once, then is discarded;
every read/write after that goes through the restricted runtime role,
fail-closed if unset, with an explicit post-connection check that the
resolved identity is not privileged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json
from psycopg2.pool import SimpleConnectionPool

_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "sealed_demographic_channel.sql")

SOURCE_SELF_REPORTED = "self_reported"
SOURCE_BISG_ESTIMATED = "bisg_estimated"
VALID_SOURCES = (SOURCE_SELF_REPORTED, SOURCE_BISG_ESTIMATED)


@dataclass(frozen=True)
class ProtectedCharacteristicEstimate:
    """One row as read back from the sealed channel. `estimate` is a
    JSON-safe distribution (e.g. {"white": 0.7, "black": 0.1, ...} for a
    BISG posterior, or {"hispanic": 1.0} for a single-category
    self-report) -- vocabulary is a checker/profile concern, not this
    module's."""

    subject_id: str
    cohort_key: Optional[str]
    source: str
    estimate: Dict[str, float]
    method: Optional[str]
    recorded_at: Any = field(default=None)


class SealedDemographicChannel:
    """The only writer/reader for protected_characteristic_estimates.

    `owner_user`/`owner_password` must be privileged enough to create
    the table/role/triggers (run once at construction, then discarded --
    never reused for reads/writes, same as PostgreSQLLedger's owner
    connection). `runtime_user`/`runtime_password` (or
    SEALED_CHANNEL_RUNTIME_USER / SEALED_CHANNEL_RUNTIME_PASSWORD) are
    what every record/read after that actually connects as -- required,
    fail-closed, no privileged fallback, same posture as
    ICEBERG_LEDGER_RUNTIME_USER.
    """

    def __init__(self, host: str = "localhost", port: int = 5432,
                 dbname: str = "iceberg", user: str = "iceberg",
                 password: str = "iceberg", min_connections: int = 1,
                 max_connections: int = 5,
                 runtime_user: Optional[str] = None,
                 runtime_password: Optional[str] = None):
        runtime_user = runtime_user or os.getenv("SEALED_CHANNEL_RUNTIME_USER")
        runtime_password = runtime_password or os.getenv("SEALED_CHANNEL_RUNTIME_PASSWORD")
        if not runtime_user:
            raise RuntimeError(
                "SEALED_CHANNEL_RUNTIME_USER is not set. SealedDemographicChannel "
                "refuses to start without an explicitly declared runtime identity "
                "-- there is no privileged fallback. Set SEALED_CHANNEL_RUNTIME_USER "
                "/ SEALED_CHANNEL_RUNTIME_PASSWORD to the restricted "
                "sealed_channel_writer role created by "
                "sealed_demographic_channel.sql (SELECT + INSERT on "
                "protected_characteristic_estimates only -- no grant on "
                "ledger_entries or anything else)."
            )

        owner_pool = SimpleConnectionPool(1, 1, host=host, port=port,
                                          database=dbname, user=user, password=password)
        try:
            self._apply_schema(owner_pool)
            if runtime_password:
                self._provision_runtime_password(owner_pool, runtime_user, runtime_password)
        finally:
            owner_pool.closeall()

        self.pool = SimpleConnectionPool(
            min_connections, max_connections,
            host=host, port=port, database=dbname,
            user=runtime_user, password=runtime_password,
        )
        self._verify_runtime_user_is_not_privileged(runtime_user, dbname)

    def _apply_schema(self, owner_pool: SimpleConnectionPool) -> None:
        with open(_SCHEMA_PATH) as f:
            schema_sql = f.read()
        conn = owner_pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute(schema_sql)
            conn.commit()
        finally:
            owner_pool.putconn(conn)

    def _provision_runtime_password(self, owner_pool: SimpleConnectionPool,
                                    runtime_user: str, runtime_password: str) -> None:
        """Mirrors PostgreSQLLedger._provision_runtime_password exactly:
        self-provisioned every startup using the still-open owner
        connection, so a fresh deployment works the moment both env vars
        are set."""
        from psycopg2 import sql
        conn = owner_pool.getconn()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    sql.SQL("ALTER ROLE {} WITH PASSWORD %s;").format(
                        sql.Identifier(runtime_user)
                    ),
                    (runtime_password,),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise RuntimeError(
                    f"Could not set the password for sealed-channel runtime role "
                    f"'{runtime_user}': {e}. If this role doesn't exist yet, it "
                    f"should have been created by sealed_demographic_channel.sql "
                    f"(only true for the default 'sealed_channel_writer' role -- "
                    f"a custom SEALED_CHANNEL_RUNTIME_USER value must be created "
                    f"manually first)."
                ) from e
        finally:
            owner_pool.putconn(conn)

    def _verify_runtime_user_is_not_privileged(self, runtime_user: str, dbname: str) -> None:
        """Same hard floor as PostgreSQLLedger's identically-named check:
        refuse to run if the resolved runtime identity is a superuser or
        the table owner, even when explicitly configured that way."""
        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user;")
            row = cursor.fetchone()
            is_superuser = bool(row and row[0])

            cursor.execute("""
                SELECT pg_catalog.pg_get_userbyid(c.relowner)
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = 'protected_characteristic_estimates' AND n.nspname = 'public';
            """)
            owner_row = cursor.fetchone()
            table_owner = owner_row[0] if owner_row else None
            is_owner = (table_owner is not None and table_owner == runtime_user)

            if is_superuser or is_owner:
                reason = "a superuser" if is_superuser else f"the table owner ({table_owner})"
                raise RuntimeError(
                    f"SEALED_CHANNEL_RUNTIME_USER='{runtime_user}' resolves to "
                    f"{reason} on database '{dbname}'. Refusing to start: the "
                    "sealed-channel connection must be a restricted, non-owner "
                    "role so protected-characteristic data can never be rewritten "
                    "or exfiltrated through a privileged path, even if the app is "
                    "compromised or misused."
                )
        finally:
            self.pool.putconn(conn)

    # ------------------------------------------------------------------
    # Write / read -- the only two operations this channel exposes.
    # ------------------------------------------------------------------

    def record_estimate(self, subject_id: str, source: str,
                        estimate: Dict[str, float],
                        cohort_key: Optional[str] = None,
                        method: Optional[str] = None) -> None:
        if source not in VALID_SOURCES:
            raise ValueError(f"source must be one of {VALID_SOURCES}, got {source!r}")
        if not subject_id or not str(subject_id).strip():
            raise ValueError("subject_id is required")
        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO protected_characteristic_estimates "
                "(subject_id, cohort_key, source, estimate, method) "
                "VALUES (%s, %s, %s, %s, %s);",
                (str(subject_id), cohort_key, source, Json(estimate), method),
            )
            conn.commit()
        finally:
            self.pool.putconn(conn)

    def get_estimates_for_cohort(self, cohort_key: str, limit: int = 10000
                                  ) -> List[ProtectedCharacteristicEstimate]:
        """Every recorded estimate for a cohort -- the batch read C2
        dimension 4's cohort-level check runs against. Never called from
        the live judgment path; see the module docstring."""
        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT subject_id, cohort_key, source, estimate, method, recorded_at "
                "FROM protected_characteristic_estimates "
                "WHERE cohort_key = %s ORDER BY id ASC LIMIT %s;",
                (cohort_key, limit),
            )
            rows = cursor.fetchall()
        finally:
            self.pool.putconn(conn)
        return [
            ProtectedCharacteristicEstimate(
                subject_id=r[0], cohort_key=r[1], source=r[2],
                estimate=dict(r[3]), method=r[4], recorded_at=r[5],
            )
            for r in rows
        ]

    def get_estimate_for_subject(self, subject_id: str
                                  ) -> Optional[ProtectedCharacteristicEstimate]:
        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT subject_id, cohort_key, source, estimate, method, recorded_at "
                "FROM protected_characteristic_estimates "
                "WHERE subject_id = %s ORDER BY id DESC LIMIT 1;",
                (str(subject_id),),
            )
            row = cursor.fetchone()
        finally:
            self.pool.putconn(conn)
        if row is None:
            return None
        return ProtectedCharacteristicEstimate(
            subject_id=row[0], cohort_key=row[1], source=row[2],
            estimate=dict(row[3]), method=row[4], recorded_at=row[5],
        )
