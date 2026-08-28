"""
PostgreSQL Ledger Adapter - Production-grade persistent audit trail

Replaces LocalDiskAdapter with real database: transactions, durability, ACID
"""

import json
import hashlib
import os
from canonical_fields import (CONTRACT_CANONICAL_FIELDS,
                              CONTRACT_KINDS_WITH_FINDING,
                              apply_optional_hashed_fields,
                              event_v1_to_body,
                              observed_event_canonical)
from .human_selection_v1 import HUMAN_SELECTIONS
from .authorized_by_attestation import (
    SIGNATURE_FIELD as _AUTHORIZED_BY_SIG_FIELD,
    STATUS_INVALID as _ATT_STATUS_INVALID,
    STATUS_RETIRED_KEY as _ATT_STATUS_RETIRED_KEY,
    STATUS_UNKNOWN_KEY as _ATT_STATUS_UNKNOWN_KEY,
    attestation_key,
    attestation_keyset,
    enforcement_required,
    sign_authorized_by,
    verify_authorized_by_signature,
)
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from psycopg2.pool import SimpleConnectionPool

# Forensic cassette handling (ledger item: cassette snapshots for audit)
try:
    from cassette_forensics import serialize_cassette_for_ledger, compute_cassette_hash, reconstruct_cassette_for_decision
except ImportError:
    # Fallback if cassette_forensics not yet available
    serialize_cassette_for_ledger = None
    compute_cassette_hash = None
    reconstruct_cassette_for_decision = None


@dataclass
class GovernanceDecisionRecord:
    """One structured, forensically complete governance decision.

    Every field a regulator would ask for rides in the record AND
    inside the SHA-256 chain: which policy governed (cassette_version +
    the full policy_parameters snapshot), what the system saw
    (input_data), what the governor reasoned (reasoning), and what came
    out (output, approvals AND rejections alike).

    applied_value mirrors previous_value with parameter_changed=False
    unless a parameter was actually written: this system records
    advisory decisions, and a fabricated "applied" number would be a
    forged fact in a tamper-evident ledger.

    NEW: cassette_snapshot and cassette_hash allow regulators to
    reconstruct the exact cassette that governed the decision. The
    snapshot is the full cassette config (schema, version, parameters);
    the hash cryptographically ties it to the decision in the chain.
    """

    action_type: str
    node: str
    cassette_version: str
    input_data: Dict[str, Any]
    policy_parameters: Dict[str, Any]
    reasoning: str
    output: Dict[str, Any]
    previous_value: float = 0.0
    applied_value: float = 0.0
    parameter_changed: bool = False
    cassette_snapshot: Optional[Dict[str, Any]] = None
    cassette_hash: Optional[str] = None
    # --- Phase 2 forensic fields (all optional; all hashed-when-present) ---
    # Item 3: hash of the cassette's DECISION CODE (not just its parameters).
    #   Two cassettes with identical params but different score_outcome()
    #   hash identically under cassette_hash alone; this closes that.
    cassette_code_hash: Optional[str] = None
    # Item 5: the model string the governor's API call actually resolved to
    #   (response.model), so "which model governed decision N" is in the chain.
    model_identity: Optional[str] = None
    # Item 7: the identity the writer NAMES as accountable for this decision
    #   -- an API-key NAME or service identity (e.g. "harness:production"),
    #   never a raw key and never PII.
    #   HONEST SCOPE: this is a claim, not a verified fact. Nothing here
    #   confirms the named party exists, holds the authority implied, or was
    #   involved at all -- the only check any write path applies is that the
    #   string is non-empty. When ICEBERG_LEDGER_ATTESTATION_KEY is
    #   configured, a companion authorized_by_sig column carries a keyed
    #   HMAC attesting that this string was written by a component holding
    #   the service signing key and has not changed since (see
    #   governance/authorized_by_attestation.py). That still does not
    #   establish that the named party authorized anything -- one shared key,
    #   any holder indistinguishable from any other, a leaked key forges it.
    authorized_by: Optional[str] = None
    # Item 6: for a supersession row, the current_hash of the row it
    #   supersedes -- proving the reviewer saw the actual decision. NULL on
    #   ordinary governance_decision rows.
    supersedes_hash: Optional[str] = None
    # Replacement link: the current_hash of an EARLIER governance_decision
    #   this new, independently-judged decision replaces -- e.g. a mortgage
    #   permanent modification's new-loan-number decision, which makes the
    #   original loan's outcome obligation moot (see cassettes.mortgage_
    #   cassette's module docstring and obligation_supersession.py). NOT the
    #   same concept as supersedes_hash (a human correcting an existing
    #   decision's own verdict) -- kept as its own field so the two can never
    #   be confused reading the ledger. NULL on every decision that isn't a
    #   declared replacement. Fail-closed: append_decision refuses a
    #   replaces_hash that does not name a real governance_decision row
    #   already on the chain.
    replaces_hash: Optional[str] = None
    # OutcomeV1: the maturation rule in force when this decision was made,
    #   as a declaration string ("loan_performance@24mo"). Knowable AT
    #   decision time, so it hashes in immediately and never changes -- the
    #   decision row is closed forever and must never be edited to point at
    #   an outcome that lands later. The obligation record points the other
    #   way instead, at this row's current_hash. NULL for domains whose
    #   outcomes are settled at decision time (an IVR call at hangup).
    outcome_obligation: Optional[str] = None
    # Item 8 (2026-07-31): real usage-derived cost of the Claude API call
    #   that produced this decision, if any -- see ai_cost_tracking.py and
    #   claude_governance_api.py's module docstring. A dict (model,
    #   input_tokens, output_tokens, cost_usd, unpriced_reason,
    #   pricing_source), same shape claude_governance_api already returns
    #   as `cost` on its decision dicts -- passed straight through, never
    #   recomputed here. None for a decision that never called the API.
    ai_cost: Optional[Dict[str, Any]] = None

class PostgreSQLLedger:
    """Production ledger backed by PostgreSQL"""

    # Trigger names ledger_immutability.sql installs. Verified present
    # after applying the file so a missing or failed apply halts
    # construction instead of silently leaving the ledger mutable.
    _REQUIRED_IMMUTABILITY_TRIGGERS = (
        "prevent_ledger_update",
        "prevent_ledger_delete",
        "prevent_ledger_truncate",
    )

    def __init__(self, host: str = "localhost", port: int = 5432, 
                 dbname: str = "iceberg", user: str = "iceberg", 
                 password: str = "iceberg", min_connections: int = 1, max_connections: int = 10,
                 runtime_user: str = None, runtime_password: str = None):
        """Initialize connection pool.

        `user`/`password` must be privileged enough to create/alter the
        ledger schema (run once, at startup, then discarded).

        `runtime_user`/`runtime_password` (or the ICEBERG_LEDGER_RUNTIME_USER /
        ICEBERG_LEDGER_RUNTIME_PASSWORD env vars) are what every append/read
        after startup actually connects as. This should be a restricted role
        (see ledger_immutability.sql's `ledger_reader`: SELECT + INSERT only,
        no UPDATE/DELETE/DDL) so the app itself cannot tamper with or drop the
        immutability triggers even if compromised or misused. Required: there
        is no privileged fallback if unset (see the RuntimeError below), and
        a resolved identity that turns out to be the table owner or a
        superuser is rejected too (see _verify_runtime_user_is_not_privileged).
        """
        runtime_user = runtime_user or os.getenv("ICEBERG_LEDGER_RUNTIME_USER")
        runtime_password = runtime_password or os.getenv("ICEBERG_LEDGER_RUNTIME_PASSWORD")
        if not runtime_user:
            raise RuntimeError(
                "ICEBERG_LEDGER_RUNTIME_USER is not set. The ledger refuses to "
                "start without an explicitly declared runtime identity -- there "
                "is no privileged fallback. Set ICEBERG_LEDGER_RUNTIME_USER / "
                "ICEBERG_LEDGER_RUNTIME_PASSWORD to a restricted role (e.g. the "
                "ledger_reader role created by ledger_immutability.sql: SELECT + "
                "INSERT only, no UPDATE/DELETE/DDL) so the app connection itself "
                "cannot UPDATE/DELETE/DROP TRIGGER even if compromised or misused."
            )

        # Attestation enforcement is opt-in and OFF by default (D3). But if
        # it has been turned ON, a signing key MUST be configured -- there is
        # no default or fallback key (D4). A signing system with a publicly
        # known key provides no security while appearing to, so refuse to
        # start rather than proceed with enforcement that cannot be honoured.
        if enforcement_required() and attestation_key() is None:
            raise RuntimeError(
                "ICEBERG_LEDGER_REQUIRE_ATTESTATION is set but "
                "ICEBERG_LEDGER_ATTESTATION_KEY is not. The ledger refuses to "
                "start: authorized_by attestation enforcement requires a real "
                "service signing key supplied by the environment. There is no "
                "default key and no fallback -- see "
                "governance/authorized_by_attestation.py."
            )
        # Build the verification key set once at startup so a broken
        # ATTESTATION_KEYS_PREVIOUS / _RETIRED file (set but unreadable) fails
        # here, not silently on the first verify_chain. No-op when nothing is
        # configured (the default): the set is simply empty.
        attestation_keyset()

        # One-off privileged connection: create/migrate schema, then discard.
        # Never reused for ongoing reads/writes.
        self.pool = SimpleConnectionPool(
            1, 1,
            host=host, port=port, database=dbname,
            user=user, password=password
        )
        self._initialize_schema()
        self._apply_immutability_and_verify()
        if runtime_password:
            self._provision_runtime_password(runtime_user, runtime_password)
        self.pool.closeall()

        self.pool = SimpleConnectionPool(
            min_connections, max_connections,
            host=host, port=port, database=dbname,
            user=runtime_user, password=runtime_password
        )
        self._verify_runtime_user_is_not_privileged(runtime_user, dbname)

    def _provision_runtime_password(self, runtime_user: str, runtime_password: str):
        """Set the runtime role's password using the still-open owner
        connection, so a fresh deployment works the moment
        ICEBERG_LEDGER_RUNTIME_USER/PASSWORD are set -- no separate manual
        set_ledger_reader_password.py step required (that script still
        exists for rotating the password on an already-running system
        without a restart). Idempotent: safe to run every startup, just
        resets the role's password to the same value each time.

        Only ever touches the resolved runtime_user's own password --
        never any other role. Uses sql.Identifier for the role name (it
        can't be parameterized as a literal in DDL) and a parameterized
        literal for the password itself, mirroring set_ledger_reader_password.py.

        Advisory-locked (same pattern as bind_cassette_version's own
        pg_advisory_xact_lock, keyed here per runtime_user rather than
        globally): ALTER ROLE modifies the shared pg_authid catalog, and
        concurrent ALTER ROLE statements on the SAME role from different
        sessions can raise Postgres's own "tuple concurrently updated" --
        confirmed live, not theoretical: 9 of 10 threads each
        constructing their own PostgreSQLLedger simultaneously (this
        method running once per construction) failed with exactly that
        error before this lock was added. The lock is transaction-scoped
        (released at the commit/rollback below), so it only serializes
        the brief ALTER ROLE window, not the whole harness construction.
        """
        from psycopg2 import sql
        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext('ledger_runtime_password_' || %s));",
                    (runtime_user,)
                )
                cursor.execute(
                    sql.SQL("ALTER ROLE {} WITH PASSWORD %s;").format(
                        sql.Identifier(runtime_user)
                    ),
                    (runtime_password,)
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise RuntimeError(
                    f"Could not set the password for runtime role "
                    f"'{runtime_user}': {e}. If this role doesn't exist yet, "
                    f"it should have been created by ledger_immutability.sql "
                    f"(only true for the default 'ledger_reader' role -- a "
                    f"custom ICEBERG_LEDGER_RUNTIME_USER value must be "
                    f"created manually first)."
                ) from e
        finally:
            self.pool.putconn(conn)

    def _verify_runtime_user_is_not_privileged(self, runtime_user: str, dbname: str):
        """Hard floor: refuse to run if the *resolved* runtime identity turns
        out to be a superuser or the owner of ledger_entries, even when
        ICEBERG_LEDGER_RUNTIME_USER was set explicitly. A privileged runtime
        connection can UPDATE/DELETE ledger rows or DROP the immutability
        triggers outright, defeating connection-level defense-in-depth no
        matter how carefully the env var was configured. This check runs on
        every startup, not just when the var is unset, because a
        misconfigured-but-present value is exactly the silent-privilege case
        this fix exists to close.
        """
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
                WHERE c.relname = 'ledger_entries' AND n.nspname = 'public';
            """)
            owner_row = cursor.fetchone()
            table_owner = owner_row[0] if owner_row else None
            is_owner = (table_owner is not None and table_owner == runtime_user)

            if is_superuser or is_owner:
                reason = "a superuser" if is_superuser else f"the table owner ({table_owner})"
                raise RuntimeError(
                    f"ICEBERG_LEDGER_RUNTIME_USER='{runtime_user}' resolves to {reason} "
                    f"on database '{dbname}'. Refusing to start: the runtime ledger "
                    "connection must be a restricted, non-owner role (e.g. "
                    "ledger_reader: SELECT + INSERT only) so the app cannot rewrite "
                    "or wipe the ledger, or drop its immutability triggers, even if "
                    "the app itself is compromised or misused."
                )
        finally:
            self.pool.putconn(conn)
    
    def _table_columns(self, cursor) -> set:
        """Column names currently on ledger_entries. A plain
        information_schema query -- reads the system catalogs, takes only
        an AccessShareLock, never queues behind concurrent readers the way
        ALTER TABLE's ACCESS EXCLUSIVE lock does. Used to skip each
        migration block below when the schema is already current, so a
        normal boot against an up-to-date ledger never takes that
        exclusive lock at all.
        """
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'ledger_entries';"
        )
        return {row[0] for row in cursor.fetchall()}

    def _initialize_schema(self):
        """Create ledger table if not exists"""
        
        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ledger_entries (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    action_type VARCHAR(50),
                    node VARCHAR(100),
                    previous_value FLOAT,
                    applied_value FLOAT,
                    reason TEXT,
                    previous_hash VARCHAR(64),
                    current_hash VARCHAR(64),
                    data JSONB,
                    UNIQUE(current_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_timestamp ON ledger_entries(timestamp);
                CREATE INDEX IF NOT EXISTS idx_node ON ledger_entries(node);
                CREATE INDEX IF NOT EXISTS idx_hash ON ledger_entries(current_hash);
            """)
            # Every ALTER TABLE ... ADD COLUMN below is individually
            # idempotent (IF NOT EXISTS), but ALTER TABLE takes an ACCESS
            # EXCLUSIVE lock to even EVALUATE that IF NOT EXISTS check --
            # so re-running these unconditionally on every construction
            # meant a boot against an already-current schema could still
            # queue indefinitely behind any lingering reader holding
            # ACCESS SHARE on ledger_entries (idle-in-transaction
            # connections, a long-running query, etc.). existing_columns
            # is a one-time, lock-cheap read of the current schema;
            # each block below only runs -- and only then takes the
            # exclusive lock -- when it would actually change something.
            existing_columns = self._table_columns(cursor)

            # In-place migration for structured governance decisions.
            # Legacy rows keep their shape (columns stay NULL); new
            # decision rows fill them. The hash chain is shared: legacy
            # append() and structured append_decision() interleave on
            # one chain, each hashing its own canonical form.
            if not {"record_kind", "cassette_version", "input_data",
                    "policy_parameters", "decision_output"} <= existing_columns:
                cursor.execute("""
                    ALTER TABLE ledger_entries
                        ADD COLUMN IF NOT EXISTS record_kind VARCHAR(40) DEFAULT 'legacy',
                        ADD COLUMN IF NOT EXISTS cassette_version VARCHAR(200),
                        ADD COLUMN IF NOT EXISTS input_data JSONB,
                        ADD COLUMN IF NOT EXISTS policy_parameters JSONB,
                        ADD COLUMN IF NOT EXISTS decision_output JSONB;
                    CREATE INDEX IF NOT EXISTS idx_cassette_version
                        ON ledger_entries(cassette_version);
                """)
            # Forensic ledger item: cassette snapshots for regulatory audit.
            # Safe to run on existing ledgers (adds nullable columns, no data deleted).
            # Backfill: existing decisions have NULL cassette_snapshot/cassette_hash
            # (cannot be reconstructed, but chain remains intact and verifiable).
            if not {"cassette_snapshot", "cassette_hash"} <= existing_columns:
                cursor.execute("""
                    ALTER TABLE ledger_entries
                        ADD COLUMN IF NOT EXISTS cassette_snapshot JSONB,
                        ADD COLUMN IF NOT EXISTS cassette_hash VARCHAR(64);
                    CREATE INDEX IF NOT EXISTS idx_cassette_hash
                        ON ledger_entries(cassette_hash);
                """)
            # Phase 2 forensic columns. Same migration guarantee as above:
            # all nullable, no data deleted, legacy rows keep NULL and hash
            # exactly as before (the fields only enter the canonical form
            # when present). Deployable online against a populated ledger.
            #   cassette_code_hash -- Item 3 (decision-code integrity)
            #   model_identity     -- Item 5 (governing model per decision)
            #   authorized_by      -- Item 7 (authorizing identity)
            #   supersedes_id/hash -- Item 6 (formal supersession link)
            if not {"cassette_code_hash", "model_identity", "authorized_by",
                    "supersedes_id", "supersedes_hash"} <= existing_columns:
                cursor.execute("""
                    ALTER TABLE ledger_entries
                        ADD COLUMN IF NOT EXISTS cassette_code_hash VARCHAR(64),
                        ADD COLUMN IF NOT EXISTS model_identity VARCHAR(120),
                        ADD COLUMN IF NOT EXISTS authorized_by VARCHAR(120),
                        ADD COLUMN IF NOT EXISTS supersedes_id INTEGER,
                        ADD COLUMN IF NOT EXISTS supersedes_hash VARCHAR(64);
                    CREATE INDEX IF NOT EXISTS idx_model_identity
                        ON ledger_entries(model_identity);
                    CREATE INDEX IF NOT EXISTS idx_authorized_by
                        ON ledger_entries(authorized_by);
                    CREATE INDEX IF NOT EXISTS idx_supersedes_id
                        ON ledger_entries(supersedes_id);
                """)
            # OutcomeV1 column. Same migration guarantee as every optional
            # hashed field before it: nullable, no backfill, rows written
            # before it existed omit it from the canonical form and hash
            # byte-identically to what they hashed at write time. Indexed
            # because the twin's independent derivation of the open-obligation
            # set scans exactly this column.
            if "outcome_obligation" not in existing_columns:
                cursor.execute("""
                    ALTER TABLE ledger_entries
                        ADD COLUMN IF NOT EXISTS outcome_obligation VARCHAR(120);
                    CREATE INDEX IF NOT EXISTS idx_outcome_obligation
                        ON ledger_entries(outcome_obligation);
                """)
            # Idempotency: store the raw Twilio sid so duplicate
            # submissions can be rejected before processing. UNIQUE
            # constraint on the column itself is the last-resort guard
            # (catches races the application-level check can't); the
            # normal path rejects earlier via sid_exists(). Nullable
            # so legacy/non-Twilio rows don't collide.
            if not {"call_sid"} <= existing_columns:
                cursor.execute("""
                    ALTER TABLE ledger_entries
                        ADD COLUMN IF NOT EXISTS call_sid VARCHAR(100);
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_call_sid
                        ON ledger_entries(call_sid)
                        WHERE call_sid IS NOT NULL;
                """)
            # Replacement link (distinct from Item 6's supersedes_id/hash --
            # see GovernanceDecisionRecord.replaces_hash docstring). Same
            # migration guarantee: nullable, no backfill, legacy rows hash
            # byte-identically since the field is omitted from the canonical
            # form when absent. Partial index -- the vast majority of
            # decisions will never set this, same posture as call_sid's
            # unique partial index above.
            if "replaces_hash" not in existing_columns:
                cursor.execute("""
                    ALTER TABLE ledger_entries
                        ADD COLUMN IF NOT EXISTS replaces_hash VARCHAR(64);
                    CREATE INDEX IF NOT EXISTS idx_replaces_hash
                        ON ledger_entries(replaces_hash)
                        WHERE replaces_hash IS NOT NULL;
                """)
            # Item 8: AI cost tracking. JSONB, not a scalar -- the whole
            # cost dict (model/tokens/cost_usd/unpriced_reason) rides as
            # one unit, same posture as decision_output. Nullable, no
            # backfill, legacy rows hash byte-identically -- same
            # migration guarantee every field above already has.
            if "ai_cost" not in existing_columns:
                cursor.execute("""
                    ALTER TABLE ledger_entries
                        ADD COLUMN IF NOT EXISTS ai_cost JSONB;
                """)
            # Item 9: which shadow run a shadow score is scoring -- its
            # own field, see canonical_fields.py's comment for why this
            # is deliberately NOT a reuse of replaces_hash.
            if "shadow_run_hash" not in existing_columns:
                cursor.execute("""
                    ALTER TABLE ledger_entries
                        ADD COLUMN IF NOT EXISTS shadow_run_hash VARCHAR(64);
                    CREATE INDEX IF NOT EXISTS idx_shadow_run_hash
                        ON ledger_entries(shadow_run_hash)
                        WHERE shadow_run_hash IS NOT NULL;
                """)
            # F2 (2026-08-07): which governance_decision a human_selection
            # row is reviewing. Own column, own index -- see
            # canonical_fields.py's comment on decision_hash for why this
            # is deliberately not a reuse of shadow_run_hash/replaces_hash.
            if "decision_hash" not in existing_columns:
                cursor.execute("""
                    ALTER TABLE ledger_entries
                        ADD COLUMN IF NOT EXISTS decision_hash VARCHAR(64);
                    CREATE INDEX IF NOT EXISTS idx_decision_hash
                        ON ledger_entries(decision_hash)
                        WHERE decision_hash IS NOT NULL;
                """)
            # Keyed attestation over the authorized_by claim. Nullable, no
            # backfill: rows written before this column existed -- and every
            # row written while no ICEBERG_LEDGER_ATTESTATION_KEY is
            # configured -- keep NULL here and hash byte-identically to what
            # they hashed at write time (an absent optional field is omitted
            # from the canonical form). VARCHAR(96) holds the v2 signature
            # envelope "abv2.<16-hex keyfp>.<64-hex digest>" (~86 chars) that
            # carries the signing key's fingerprint for rotation; a legacy
            # bare 64-hex digest still fits and still verifies. No index: this
            # column is read back alongside the row it attests, and the
            # rotation dashboard query filters on a LIKE 'abv2.%' prefix. See
            # governance/authorized_by_attestation.py.
            if "authorized_by_sig" not in existing_columns:
                cursor.execute("""
                    ALTER TABLE ledger_entries
                        ADD COLUMN IF NOT EXISTS authorized_by_sig VARCHAR(96);
                """)
            else:
                # A ledger created before v2 has this column at VARCHAR(64).
                # Widening a varchar is a catalog-only change in PG 9.2+ (no
                # table rewrite); guarded on the current length so a normal
                # boot never issues the ALTER. Existing 64-char bare digests
                # are untouched and still verify.
                cursor.execute(
                    "SELECT character_maximum_length FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'ledger_entries' "
                    "AND column_name = 'authorized_by_sig';"
                )
                _abs_len = cursor.fetchone()
                if _abs_len and _abs_len[0] is not None and _abs_len[0] < 96:
                    cursor.execute(
                        "ALTER TABLE ledger_entries "
                        "ALTER COLUMN authorized_by_sig TYPE VARCHAR(96);"
                    )
            # observed_event rows (the persisted EventV1 stream, written in
            # the same transaction as the governance_decision they feed --
            # see append_decision's observed_events arg). No new column: the
            # event body lives in input_data JSONB. This partial unique index
            # is the DB-level backstop against a re-delivered event being
            # double-recorded -- event_id is stable-at-creation and contains
            # the episode id, so it is globally unique in practice. Same
            # posture as idx_unique_call_sid: the normal path never hits it
            # (append_decision writes each episode's events exactly once),
            # it only catches a retry race. Guarded on a catalog read so a
            # normal boot never issues the CREATE.
            cursor.execute(
                "SELECT 1 FROM pg_indexes WHERE schemaname = 'public' "
                "AND tablename = 'ledger_entries' "
                "AND indexname = 'idx_unique_observed_event_id';"
            )
            if cursor.fetchone() is None:
                cursor.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_observed_event_id
                        ON ledger_entries ((input_data->>'event_id'))
                        WHERE record_kind = 'observed_event';
                """)
            # Item 10: timestamp column made timezone-aware. The original
            # TIMESTAMP (no zone) column stored CURRENT_TIMESTAMP under
            # whatever timezone the Postgres session happened to be
            # configured with. On any server/session not set to UTC, that
            # meant rows were stamped in local wall-clock time while every
            # caller (get_decisions_by_node_in_window, get_unscored_shadow_runs,
            # recommendation_impact.py) builds its since/until window in real
            # UTC -- a mismatch invisible until the window is narrow enough,
            # or the offset large enough, that real rows fall outside it.
            # That's what surfaced as get_decisions_by_node_in_window
            # returning zero rows for windows that should have matched.
            # A plain information_schema read (AccessShareLock only, same
            # lock-avoidance posture as existing_columns above) so a normal
            # boot against an already-migrated ledger never takes the
            # ACCESS EXCLUSIVE lock ALTER COLUMN TYPE requires.
            # USING reinterprets each existing naive value under the
            # CURRENT session timezone -- the same timezone CURRENT_TIMESTAMP
            # used to write it -- so already-written rows convert to the
            # correct absolute instant rather than silently shifting.
            # timestamp is never part of any hashed canonical_entry (see
            # append_decision/append), so this migration cannot affect any
            # decision's hash or break chain verification.
            cursor.execute("""
                SELECT data_type FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'ledger_entries'
                  AND column_name = 'timestamp';
            """)
            ts_row = cursor.fetchone()
            if ts_row and ts_row[0] == "timestamp without time zone":
                cursor.execute("""
                    ALTER TABLE ledger_entries
                        ALTER COLUMN timestamp TYPE TIMESTAMPTZ
                        USING timestamp AT TIME ZONE current_setting('TIMEZONE');
                """)
            conn.commit()
        finally:
            self.pool.putconn(conn)

    def _immutability_already_applied(self, cursor) -> bool:
        """True when the triggers, role, and grants ledger_immutability.sql
        sets up are already exactly in place -- i.e. reapplying the file
        would be a no-op. Every check here is a plain catalog read
        (AccessShareLock only); used to skip the file entirely on a
        normal boot against an already-protected ledger, since its
        DROP TRIGGER / CREATE TRIGGER and GRANT/REVOKE statements all
        take the same ACCESS EXCLUSIVE-class lock on ledger_entries
        that ALTER TABLE does -- the other half of the migration-lock
        stall alongside the column migrations in _initialize_schema.
        """
        cursor.execute("""
            SELECT tgname FROM pg_trigger t
            JOIN pg_class r ON t.tgrelid = r.oid
            WHERE r.relname = 'ledger_entries' AND NOT t.tgisinternal;
        """)
        installed = {row[0] for row in cursor.fetchall()}
        if not set(self._REQUIRED_IMMUTABILITY_TRIGGERS) <= installed:
            return False

        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = 'ledger_reader';")
        if cursor.fetchone() is None:
            return False

        cursor.execute("""
            SELECT privilege_type FROM information_schema.role_table_grants
            WHERE table_name = 'ledger_entries' AND grantee = 'ledger_reader';
        """)
        grants = {row[0] for row in cursor.fetchall()}
        return grants == {"SELECT", "INSERT"}

    def _apply_immutability_and_verify(self):
        """Apply ledger_immutability.sql and verify it actually took effect.

        Previously this file was applied ONLY by the test fixture
        (Tests/conftest.py) -- nothing in the application startup path
        ever ran it. A real deployment got a ledger_entries table with
        zero immutability triggers: UPDATE/DELETE/TRUNCATE all succeed
        against a production-constructed ledger (confirmed live). This
        runs on the same privileged connection that creates the schema,
        applies the same file the test fixture used, then queries
        pg_trigger to confirm the three protective triggers exist --
        refusing to construct the ledger otherwise. No fallback: a
        ledger that cannot prove its own immutability does not start.

        Skips actually reapplying the file when
        _immutability_already_applied() says everything is already in
        place -- see that method's docstring for why unconditional
        reapplication was a real bug, not just belt-and-suspenders.
        Verification (the pg_trigger check below) still runs either way,
        so a ledger that's missing protection for any other reason still
        refuses to start.
        """
        sql_path = Path(__file__).resolve().parent.parent / "ledger_immutability.sql"
        if not sql_path.exists():
            raise RuntimeError(
                f"Cannot apply ledger immutability: {sql_path} not found. "
                "Refusing to start an unprotected ledger."
            )

        conn = self.pool.getconn()
        try:
            conn.autocommit = False
            cursor = conn.cursor()

            if not self._immutability_already_applied(cursor):
                cursor.execute(sql_path.read_text())
                conn.commit()

            cursor.execute("""
                SELECT tgname FROM pg_trigger t
                JOIN pg_class r ON t.tgrelid = r.oid
                WHERE r.relname = 'ledger_entries' AND NOT t.tgisinternal;
            """)
            installed = {row[0] for row in cursor.fetchall()}
            missing = [t for t in self._REQUIRED_IMMUTABILITY_TRIGGERS if t not in installed]
            if missing:
                raise RuntimeError(
                    f"Ledger immutability triggers missing after applying "
                    f"{sql_path.name}: {missing}. The ledger would be mutable "
                    f"(UPDATE/DELETE/TRUNCATE unprotected). Refusing to start."
                )
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    def _authorized_by_sig(self, authorized_by: Optional[str],
                           previous_hash: str,
                           record_kind: str) -> Optional[str]:
        """Compute the keyed attestation for a row's authorized_by claim.

        Returns the hex HMAC (a component holding ICEBERG_LEDGER_ATTESTATION_KEY
        wrote this claim and it has not changed since) or None when there is
        no claim or no key configured -- in which case the row is written with
        a NULL signature and is honestly unattested (D3/D4).

        When enforcement is ON (ICEBERG_LEDGER_REQUIRE_ATTESTATION) and a
        present authorized_by claim could not be signed, this refuses the
        write rather than record an unattested authorization claim. The
        ledger will not even start with enforcement on and no key (see
        __init__), so in practice this fires only if signing itself fails.

        This attests writer-authenticity and integrity of one string. It does
        NOT verify that the named party holds the authority claimed -- see
        governance/authorized_by_attestation.py.
        """
        sig = sign_authorized_by(authorized_by, previous_hash, record_kind,
                                 attestation_key())
        if authorized_by and enforcement_required() and not sig:
            raise RuntimeError(
                f"authorized_by attestation enforcement is on but no signature "
                f"was produced for the authorized_by claim on this "
                f"{record_kind} row. Refusing to record an unattested "
                f"authorization claim."
            )
        return sig

    def append(self, action_type: str, node: str, previous_value: float,
               applied_value: float, reason: str, data: Dict) -> bool:
        """Append entry to ledger (transaction).

        Hashes are computed internally -- callers cannot supply
        previous_hash/current_hash. A ledger that trusted a caller's
        own fingerprint would just be a table with a hash-shaped
        column, not a tamper-evident chain.

        An advisory lock, held for the transaction, serializes the
        read-last-entry / compute-next-hash / insert sequence, so two
        callers appending at nearly the same instant can't both read
        the same "last entry" and each honestly build a next link
        that only one of them should have won.
        """

        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()

            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('ledger_entries'))")

            cursor.execute("""
                SELECT current_hash FROM ledger_entries
                ORDER BY id DESC LIMIT 1
            """)
            row = cursor.fetchone()
            previous_hash = row[0] if row else "genesis"

            canonical_entry = {
                "action_type": action_type,
                "node": node,
                "previous_value": previous_value,
                "applied_value": applied_value,
                "reason": reason,
                "data": data,
                "previous_hash": previous_hash,
            }
            current_hash = hashlib.sha256(
                json.dumps(canonical_entry, sort_keys=True, default=str).encode()
            ).hexdigest()

            cursor.execute("""
                INSERT INTO ledger_entries 
                (action_type, node, previous_value, applied_value, reason, 
                 previous_hash, current_hash, data)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (action_type, node, previous_value, applied_value, reason,
                  previous_hash, current_hash, json.dumps(data)))
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            print(f"Ledger append failed: {e}")
            return False
        finally:
            self.pool.putconn(conn)
    
    def append_decision(self, record: GovernanceDecisionRecord,
                       governance_params: Optional[Any] = None,
                       observed_events: "Optional[Any]" = None) -> bool:
        """Append one structured governance decision (transaction).

        TRIPWIRE: a decision without a cassette_version is an error,
        not a warning. The whole point of the record is "which policy
        governed this" -- a row that cannot answer that is refused
        before it ever touches the chain. Same for the policy snapshot
        itself.

        NEW: governance_params (GovernanceParameters from cassette_schema.py)
        is required. We serialize and hash the cassette at decision time
        so regulators can reconstruct it and prove it hasn't been changed.

        observed_events (optional): an iterable of EventV1 (event_v1.py) --
        the raw observations this decision was assembled from. When given,
        each event is written as its own observed_event row IN THE SAME
        TRANSACTION AND UNDER THE SAME ADVISORY LOCK as the decision, chained
        immediately AFTER the decision row (decision.current_hash -> event 1
        -> event 2 -> ...). Chain position is not chronology: the
        observations happened first, but the decision row references them by
        id in input_data and the rows carry occurred_at. Default None keeps
        every existing caller a no-op. A decision either commits with all its
        events or not at all -- a half-written stream is never left behind.

        All forensic fields are inside the canonical form that gets
        hashed, so editing any of them after the fact breaks the chain.
        """

        if not isinstance(record, GovernanceDecisionRecord):
            raise TypeError(
                f"append_decision requires GovernanceDecisionRecord, got {type(record).__name__}"
            )
        if not record.cassette_version or not isinstance(record.cassette_version, str):
            raise ValueError(
                "Governance decision rejected: cassette_version is required on every "
                "decision record (ledger tripwire -- no decision may be recorded "
                "without the policy version that governed it)"
            )
        if not isinstance(record.policy_parameters, dict) or not record.policy_parameters:
            raise ValueError(
                "Governance decision rejected: policy_parameters snapshot is required "
                "(the record must carry the parameters that governed it)"
            )
        if not isinstance(record.input_data, dict):
            raise ValueError("Governance decision rejected: input_data must be a dict")
        if not isinstance(record.output, dict) or not record.output:
            raise ValueError("Governance decision rejected: output must be a non-empty dict")

        # observed_events: validate the whole batch BEFORE the transaction
        # opens, same fail-early posture as the record checks above. One bad
        # event rejects the decision -- a governed call whose observation
        # stream will not validate is a finding, not something to silently
        # record half of.
        event_bodies: List[Dict[str, Any]] = []
        if observed_events:
            from event_v1 import validate_event  # lazy: keeps the cold path clean
            events_list = list(observed_events)
            seen_ids: set = set()
            episode_ids: set = set()
            for ev in events_list:
                validate_event(ev)
                if ev.event_id in seen_ids:
                    raise ValueError(
                        f"Governance decision rejected: duplicate observed event_id "
                        f"{ev.event_id!r} in this batch"
                    )
                seen_ids.add(ev.event_id)
                episode_ids.add(ev.episode_id)
                event_bodies.append(event_v1_to_body(ev))
            if len(episode_ids) > 1:
                raise ValueError(
                    f"Governance decision rejected: observed_events span multiple "
                    f"episodes {sorted(episode_ids)!r} -- one decision's stream is "
                    f"one episode"
                )

        # NEW: Capture cassette snapshot for forensic reconstruction
        cassette_snapshot = None
        cassette_hash = None

        if governance_params is not None:
            if serialize_cassette_for_ledger is None:
                raise RuntimeError(
                    "cassette_forensics module not available; "
                    "cannot capture cassette snapshot"
                )
            cassette_snapshot = serialize_cassette_for_ledger(governance_params)
            cassette_hash = compute_cassette_hash(cassette_snapshot)
        else:
            # Warnings only if governance_params explicitly None;
            # migration allows pre-snapshot decisions to coexist
            pass

        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()

            # Fail-closed, same posture as supersede_decision's existence
            # check: a replaces_hash naming a decision that isn't actually on
            # the chain is refused BEFORE this row is appended, never
            # recorded as if the claim were verified. Checked inside the
            # same transaction/connection as the insert below so there is no
            # window for the referenced row to vanish between the check and
            # the write (this table is append-only, so it can't be edited
            # out from under us, but a consistent read is still the honest
            # thing to do here).
            if record.replaces_hash:
                cursor.execute("""
                    SELECT 1 FROM ledger_entries
                    WHERE current_hash = %s AND record_kind = 'governance_decision'
                """, (record.replaces_hash,))
                if cursor.fetchone() is None:
                    conn.rollback()
                    raise ValueError(
                        f"Governance decision rejected: replaces_hash "
                        f"{record.replaces_hash!r} does not match any "
                        f"governance_decision on the chain -- a replacement "
                        f"link must reference a real prior decision, never "
                        f"an unverifiable claim"
                    )

            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('ledger_entries'))")

            cursor.execute("""
                SELECT current_hash FROM ledger_entries
                ORDER BY id DESC LIMIT 1
            """)
            row = cursor.fetchone()
            previous_hash = row[0] if row else "genesis"

            data = {
                "record_kind": "governance_decision",
                "parameter_changed": bool(record.parameter_changed),
            }

            canonical_entry = {
                "record_kind": "governance_decision",
                "action_type": record.action_type,
                "node": record.node,
                "cassette_version": record.cassette_version,
                "input_data": record.input_data,
                "policy_parameters": record.policy_parameters,
                "reasoning": record.reasoning,
                "output": record.output,
                "previous_value": record.previous_value,
                "applied_value": record.applied_value,
                "parameter_changed": bool(record.parameter_changed),
                "previous_hash": previous_hash,
            }

            # Optional hashed fields (cassette_hash + Phase-2 fields) enter
            # the canonical form ONLY when present, via the one contract the
            # twin's recompute_current_hash also uses -- so old rows (all
            # fields NULL) hash exactly as before and stay verifiable, and
            # writer/witness cannot drift. cassette_hash is computed above
            # from governance_params; the rest ride on the record.
            authorized_by_sig = self._authorized_by_sig(
                record.authorized_by, previous_hash, "governance_decision")
            optional_source = {
                "cassette_hash": cassette_hash,
                "cassette_code_hash": record.cassette_code_hash,
                "model_identity": record.model_identity,
                "authorized_by": record.authorized_by,
                "supersedes_hash": record.supersedes_hash,
                "outcome_obligation": record.outcome_obligation,
                "replaces_hash": record.replaces_hash,
                "ai_cost": record.ai_cost,
                _AUTHORIZED_BY_SIG_FIELD: authorized_by_sig,
            }
            apply_optional_hashed_fields(canonical_entry, optional_source)

            current_hash = hashlib.sha256(
                json.dumps(canonical_entry, sort_keys=True, default=str).encode()
            ).hexdigest()

            cursor.execute("""
                INSERT INTO ledger_entries
                (action_type, node, previous_value, applied_value, reason,
                 previous_hash, current_hash, data,
                 record_kind, cassette_version, input_data, policy_parameters,
                 decision_output, cassette_snapshot, cassette_hash, call_sid,
                 cassette_code_hash, model_identity, authorized_by,
                 supersedes_id, supersedes_hash, outcome_obligation, replaces_hash,
                 ai_cost, authorized_by_sig)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (record.action_type, record.node, record.previous_value,
                  record.applied_value, record.reasoning,
                  previous_hash, current_hash, json.dumps(data),
                  "governance_decision", record.cassette_version,
                  json.dumps(record.input_data),
                  json.dumps(record.policy_parameters),
                  json.dumps(record.output),
                  json.dumps(cassette_snapshot) if cassette_snapshot else None,
                  cassette_hash,
                  record.input_data.get("call_sid"),
                  record.cassette_code_hash, record.model_identity,
                  record.authorized_by,
                  getattr(record, "supersedes_id", None), record.supersedes_hash,
                  record.outcome_obligation, record.replaces_hash,
                  json.dumps(record.ai_cost) if record.ai_cost else None,
                  authorized_by_sig))

            # observed_event rows: chained after the decision row, still
            # inside this transaction and still holding the advisory lock, so
            # the decision and its whole observation stream commit together
            # or not at all. Each row hashes a FIXED canonical form (no
            # optional fields -- every observed_event row is new) via the
            # shared observed_event_canonical the verifier and witness use.
            event_prev = current_hash
            for body in event_bodies:
                ev_canonical = observed_event_canonical(body, event_prev)
                ev_hash = hashlib.sha256(
                    json.dumps(ev_canonical, sort_keys=True, default=str).encode()
                ).hexdigest()
                cursor.execute("""
                    INSERT INTO ledger_entries
                    (action_type, node, reason, previous_hash, current_hash,
                     data, record_kind, input_data)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    body["kind"], body["episode_id"],
                    f"observed_event {body['event_id']} "
                    f"({body['provenance']}) for episode {body['episode_id']}",
                    event_prev, ev_hash,
                    json.dumps({"record_kind": "observed_event",
                                "parameter_changed": False}),
                    "observed_event", json.dumps(body),
                ))
                event_prev = ev_hash

            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    def bind_cassette_version(self, cassette_version: str, cassette_hash: str,
                              cassette_code_hash: Optional[str] = None,
                              authorized_by: Optional[str] = None) -> Dict[str, Any]:
        """Item 2: content-bind a cassette_version to its hashes, in the chain.

        The problem: cassette_version ("domain:name:version") is a self-asserted
        label. An operator could change the cassette's parameters or code without
        changing the string, and historical queries by version would silently
        return rows governed by different content.

        The fix, WITHOUT a second source of truth: the binding lives in the
        ledger itself as a `cassette_binding` chain row. The FIRST time a version
        is bound, its (cassette_hash, cassette_code_hash) is committed into the
        hash chain. Any later bind of the SAME version with DIFFERENT hashes is
        refused loud -- the version string is now a commitment, not a claim.
        Because the registry IS the chain, there is no sidecar table or file that
        could disagree with the ledger (preserves cassette-as-single-source).

        Idempotent: re-binding a version with identical hashes returns the
        existing binding and appends nothing.

        Returns {"status": "created"|"exists", "cassette_version", "cassette_hash",
        "cassette_code_hash", "current_hash"|"existing_hash"}.

        Raises ValueError on a content-mismatch (same version, changed hashes) --
        this is the tripwire the whole item exists to trip. Legitimate content
        changes require a NEW version string; silent content changes are refused.
        """
        if not cassette_version or not isinstance(cassette_version, str):
            raise ValueError("bind_cassette_version requires a non-empty version string")
        if not cassette_hash or not isinstance(cassette_hash, str):
            raise ValueError("bind_cassette_version requires a cassette_hash")

        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('ledger_entries'))")

            # Existing binding for this version?
            cursor.execute("""
                SELECT cassette_hash, cassette_code_hash, current_hash
                FROM ledger_entries
                WHERE record_kind = 'cassette_binding' AND cassette_version = %s
                ORDER BY id ASC LIMIT 1
            """, (cassette_version,))
            existing = cursor.fetchone()
            if existing is not None:
                ex_hash, ex_code_hash, ex_current = existing
                # Content-mismatch tripwire: same label, different content.
                if ex_hash != cassette_hash or (
                    ex_code_hash is not None and cassette_code_hash is not None
                    and ex_code_hash != cassette_code_hash
                ):
                    conn.rollback()
                    raise ValueError(
                        f"Cassette version binding conflict for '{cassette_version}': "
                        f"already bound to cassette_hash={ex_hash} "
                        f"code_hash={ex_code_hash}, but load presents "
                        f"cassette_hash={cassette_hash} code_hash={cassette_code_hash}. "
                        "A version string is a content commitment -- changed content "
                        "requires a new version, not a silent re-bind."
                    )
                conn.commit()
                return {
                    "status": "exists",
                    "cassette_version": cassette_version,
                    "cassette_hash": ex_hash,
                    "cassette_code_hash": ex_code_hash,
                    "existing_hash": ex_current,
                }

            # New binding -> append a chain row.
            cursor.execute("""
                SELECT current_hash FROM ledger_entries ORDER BY id DESC LIMIT 1
            """)
            row = cursor.fetchone()
            previous_hash = row[0] if row else "genesis"

            canonical_entry = {
                "record_kind": "cassette_binding",
                "cassette_version": cassette_version,
                "previous_hash": previous_hash,
            }
            # cassette_hash + cassette_code_hash enter the hash via the SAME
            # shared contract used by decisions -- so a binding row's integrity
            # recomputes identically on the twin.
            authorized_by_sig = self._authorized_by_sig(
                authorized_by, previous_hash, "cassette_binding")
            apply_optional_hashed_fields(canonical_entry, {
                "cassette_hash": cassette_hash,
                "cassette_code_hash": cassette_code_hash,
                "authorized_by": authorized_by,
                _AUTHORIZED_BY_SIG_FIELD: authorized_by_sig,
            })
            current_hash = hashlib.sha256(
                json.dumps(canonical_entry, sort_keys=True, default=str).encode()
            ).hexdigest()

            data = {"record_kind": "cassette_binding", "parameter_changed": False}
            cursor.execute("""
                INSERT INTO ledger_entries
                (action_type, node, previous_value, applied_value, reason,
                 previous_hash, current_hash, data,
                 record_kind, cassette_version, cassette_hash, cassette_code_hash,
                 authorized_by, authorized_by_sig)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, ("cassette_binding", cassette_version, 0.0, 0.0,
                  "cassette version->content binding",
                  previous_hash, current_hash, json.dumps(data),
                  "cassette_binding", cassette_version, cassette_hash,
                  cassette_code_hash, authorized_by, authorized_by_sig))
            conn.commit()
            return {
                "status": "created",
                "cassette_version": cassette_version,
                "cassette_hash": cassette_hash,
                "cassette_code_hash": cassette_code_hash,
                "current_hash": current_hash,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    # ------------------------------------------------------------------
    # Regulatory-cassette events (lens insertion / removal / disclosure)
    # ------------------------------------------------------------------

    _REGULATORY_CASSETTE_EVENTS = (
        "regulatory_cassette_inserted",
        "regulatory_cassette_removed",
    )

    def record_regulatory_cassette_event(self, event: str, cassette_version: str,
                                         cassette_hash: str,
                                         cassette_code_hash: Optional[str],
                                         mode: str, regulation: str,
                                         authorized_by: str) -> Dict[str, Any]:
        """Record a regulatory lens's insertion or removal as a
        FIRST-CLASS hash-chained event.

        A regulatory cassette is an auditor-inserted lens (see
        regulatory_cassette_interface); "when was the CFPB lens
        active, in which mode, inserted by whom, with what content
        hash" must be a direct ledger query -- not an inference from
        the generic authorized_by field on some other row. So
        insertion/removal get their own record_kind on the SAME chain
        as every other event (no parallel logging path), hashed
        through the same shared optional-field contract
        (canonical_fields) the writer, verify_chain, and the twin all
        use. The authorized_by COLUMN still stores who acted -- that
        is what the column is for -- but the event stands as its own
        queryable row, which is what "first-class" means here.

        mode and regulation live inside the canonical form (and the
        data JSONB, from which verification reconstructs them):
        whether a lens sat read-only or live in the decision path is
        exactly the kind of fact tampering would want to change.
        """
        if event not in self._REGULATORY_CASSETTE_EVENTS:
            raise ValueError(
                f"Unknown regulatory cassette event '{event}'; known: "
                f"{list(self._REGULATORY_CASSETTE_EVENTS)}"
            )
        if not cassette_version or not isinstance(cassette_version, str):
            raise ValueError("record_regulatory_cassette_event requires a "
                             "non-empty cassette_version")
        if not cassette_hash or not isinstance(cassette_hash, str):
            raise ValueError("record_regulatory_cassette_event requires a "
                             "cassette_hash (lens insertion without a content "
                             "hash is an unevidenced insertion)")
        if mode not in ("observer", "live"):
            raise ValueError(f"mode must be 'observer' or 'live', got {mode!r}")
        if not regulation or not str(regulation).strip():
            raise ValueError("record_regulatory_cassette_event requires the "
                             "regulation the lens claims to check")
        if not authorized_by or not str(authorized_by).strip():
            raise ValueError("record_regulatory_cassette_event requires "
                             "authorized_by: an anonymous lens insertion is "
                             "exactly the record an examiner cannot accept")

        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('ledger_entries'))")
            cursor.execute("""
                SELECT current_hash FROM ledger_entries ORDER BY id DESC LIMIT 1
            """)
            row = cursor.fetchone()
            previous_hash = row[0] if row else "genesis"

            canonical_entry = {
                "record_kind": event,
                "cassette_version": cassette_version,
                "mode": mode,
                "regulation": str(regulation),
                "previous_hash": previous_hash,
            }
            authorized_by_sig = self._authorized_by_sig(
                authorized_by, previous_hash, event)
            apply_optional_hashed_fields(canonical_entry, {
                "cassette_hash": cassette_hash,
                "cassette_code_hash": cassette_code_hash,
                "authorized_by": authorized_by,
                _AUTHORIZED_BY_SIG_FIELD: authorized_by_sig,
            })
            current_hash = hashlib.sha256(
                json.dumps(canonical_entry, sort_keys=True, default=str).encode()
            ).hexdigest()

            # mode + regulation are stored in data so verify_chain and
            # the twin can rebuild the exact canonical form from the row.
            data = {"record_kind": event, "mode": mode,
                    "regulation": str(regulation), "parameter_changed": False}
            verb = "inserted" if event.endswith("inserted") else "removed"
            cursor.execute("""
                INSERT INTO ledger_entries
                (action_type, node, previous_value, applied_value, reason,
                 previous_hash, current_hash, data,
                 record_kind, cassette_version, cassette_hash, cassette_code_hash,
                 authorized_by, authorized_by_sig)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (event, str(regulation)[:100], 0.0, 0.0,
                  f"regulatory lens {verb} ({mode} mode)",
                  previous_hash, current_hash, json.dumps(data),
                  event, cassette_version, cassette_hash,
                  cassette_code_hash, authorized_by, authorized_by_sig))
            conn.commit()
            return {
                "status": "created",
                "event": event,
                "cassette_version": cassette_version,
                "mode": mode,
                "regulation": str(regulation),
                "cassette_hash": cassette_hash,
                "current_hash": current_hash,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    def record_regulatory_disclosure(self, cassette_version: str, regulation: str,
                                     check: str, action: str, subject_id: str,
                                     finding: Dict[str, Any],
                                     cassette_hash: Optional[str] = None,
                                     authorized_by: Optional[str] = None
                                     ) -> Dict[str, Any]:
        """Record that a LIVE regulatory lens flagged/blocked a decision
        -- the disclosure event itself.

        THE safeguard of the regulatory framework: any time a live lens
        causes a flag, block, or (if ever built) adjustment, THAT
        ACTION is written to the chain naming which regulation and
        which specific check triggered it, before the action takes
        effect (regulatory_deck calls this first and does not catch
        failures). Undisclosed compliance-driven steering of outputs
        is the failure mode this event type exists to make structurally
        impossible -- the same conduct the FTC's July 2026 Section 5
        proposal treats as potentially deceptive. There is no silent
        path and no parallel log: this is a chain row like every other.

        The full finding body (JSON-safe, from RegulatoryFinding.as_dict)
        is stored in decision_output and included in the canonical hash
        -- the evidence an examiner reads is the evidence the chain
        protects. It is normalized through a JSON round-trip before
        hashing so the bytes hashed at write time are exactly the bytes
        recomputed from the JSONB column at verify time.
        """
        if not cassette_version or not isinstance(cassette_version, str):
            raise ValueError("record_regulatory_disclosure requires cassette_version")
        if not regulation or not str(regulation).strip():
            raise ValueError("record_regulatory_disclosure requires the regulation")
        if not check or not str(check).strip():
            raise ValueError("record_regulatory_disclosure requires the specific "
                             "check that triggered -- 'something fired' is not a "
                             "disclosure")
        if action not in ("flag", "block", "adjust"):
            raise ValueError(f"action must be 'flag', 'block', or 'adjust', "
                             f"got {action!r}")
        if not subject_id or not str(subject_id).strip():
            raise ValueError("record_regulatory_disclosure requires the subject "
                             "(episode/decision id) the action applies to")
        if not isinstance(finding, dict):
            raise ValueError("record_regulatory_disclosure requires the finding "
                             "body as a dict")
        # Normalize NOW so write-time hash bytes == verify-time hash
        # bytes after the JSONB round-trip (tuples->lists, etc).
        finding = json.loads(json.dumps(finding, sort_keys=True, default=str))

        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('ledger_entries'))")
            cursor.execute("""
                SELECT current_hash FROM ledger_entries ORDER BY id DESC LIMIT 1
            """)
            row = cursor.fetchone()
            previous_hash = row[0] if row else "genesis"

            canonical_entry = {
                "record_kind": "regulatory_disclosure",
                "cassette_version": cassette_version,
                "regulation": str(regulation),
                "check": str(check),
                "action": action,
                "subject": str(subject_id),
                "finding": finding,
                "previous_hash": previous_hash,
            }
            authorized_by_sig = self._authorized_by_sig(
                authorized_by, previous_hash, "regulatory_disclosure")
            apply_optional_hashed_fields(canonical_entry, {
                "cassette_hash": cassette_hash,
                "authorized_by": authorized_by,
                _AUTHORIZED_BY_SIG_FIELD: authorized_by_sig,
            })
            current_hash = hashlib.sha256(
                json.dumps(canonical_entry, sort_keys=True, default=str).encode()
            ).hexdigest()

            data = {"record_kind": "regulatory_disclosure",
                    "regulation": str(regulation), "check": str(check),
                    "action": action, "subject": str(subject_id),
                    "parameter_changed": False}
            cursor.execute("""
                INSERT INTO ledger_entries
                (action_type, node, previous_value, applied_value, reason,
                 previous_hash, current_hash, data,
                 record_kind, cassette_version, decision_output, cassette_hash,
                 authorized_by, authorized_by_sig)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, ("regulatory_disclosure", str(check)[:100], 0.0, 0.0,
                  f"regulatory lens {action}: {check} ({str(regulation)[:120]})",
                  previous_hash, current_hash, json.dumps(data),
                  "regulatory_disclosure", cassette_version,
                  json.dumps(finding), cassette_hash, authorized_by,
                  authorized_by_sig))
            conn.commit()
            return {
                "status": "created",
                "cassette_version": cassette_version,
                "regulation": str(regulation),
                "check": str(check),
                "action": action,
                "subject": str(subject_id),
                "current_hash": current_hash,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    def record_recommendation_shadow_run(
            self, recommendation_kind: str, subject: str, cassette_version: str,
            inputs: Dict[str, Any], recommendation: Dict[str, Any],
            authorized_by: Optional[str] = None) -> Dict[str, Any]:
        """Record one AI-generated recommendation in SHADOW MODE.

        Wm's 'recommendation impact testing' roadmap item (2026-07-31),
        scoped to predictive-accuracy measurement: this NEVER causes
        anything to be acted on -- it exists purely so a later pass
        (record_recommendation_shadow_score) can compare `recommendation`'s
        predicted values against what actually happened and score the
        AI's predictive accuracy. Scoped this way, rather than true A/B
        impact testing, because none of the three recommendation methods
        this covers (decide_healing_bounds, decide_queue_reordering;
        decide_staffing_adjustment deliberately excluded, see
        recommendation_impact.py's module docstring) are wired into the
        live decision path today -- only safety_check is.

        `inputs` is the real data the recommendation was computed from
        (recommendation_impact.py pulls it from get_decisions_by_node_in_window,
        never simulated). `recommendation` is decide_healing_bounds'/
        decide_queue_reordering's own return dict, unmodified.

        Scoring is a SEPARATE row (record_recommendation_shadow_score),
        never an update to this one -- an append-only chain has no other
        way to attach a fact that wasn't knowable until later.
        """
        if not recommendation_kind or not str(recommendation_kind).strip():
            raise ValueError("record_recommendation_shadow_run requires "
                             "recommendation_kind")
        if not subject or not str(subject).strip():
            raise ValueError("record_recommendation_shadow_run requires subject "
                             "(the queue/node this recommendation is about)")
        if not cassette_version or not isinstance(cassette_version, str):
            raise ValueError("record_recommendation_shadow_run requires "
                             "cassette_version")
        if not isinstance(inputs, dict) or not isinstance(recommendation, dict):
            raise ValueError("record_recommendation_shadow_run requires inputs "
                             "and recommendation as dicts")
        inputs = json.loads(json.dumps(inputs, sort_keys=True, default=str))
        recommendation = json.loads(
            json.dumps(recommendation, sort_keys=True, default=str))

        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('ledger_entries'))")
            cursor.execute("""
                SELECT current_hash FROM ledger_entries ORDER BY id DESC LIMIT 1
            """)
            row = cursor.fetchone()
            previous_hash = row[0] if row else "genesis"

            canonical_entry = {
                "record_kind": "recommendation_shadow_run",
                "cassette_version": cassette_version,
                "recommendation_kind": str(recommendation_kind),
                "subject": str(subject),
                "inputs": inputs,
                "recommendation": recommendation,
                "previous_hash": previous_hash,
            }
            authorized_by_sig = self._authorized_by_sig(
                authorized_by, previous_hash, "recommendation_shadow_run")
            apply_optional_hashed_fields(canonical_entry, {
                "authorized_by": authorized_by,
                _AUTHORIZED_BY_SIG_FIELD: authorized_by_sig,
            })
            current_hash = hashlib.sha256(
                json.dumps(canonical_entry, sort_keys=True, default=str).encode()
            ).hexdigest()

            data = {"record_kind": "recommendation_shadow_run",
                    "recommendation_kind": str(recommendation_kind),
                    "subject": str(subject), "parameter_changed": False}
            cursor.execute("""
                INSERT INTO ledger_entries
                (action_type, node, previous_value, applied_value, reason,
                 previous_hash, current_hash, data,
                 record_kind, cassette_version, input_data, decision_output,
                 authorized_by, authorized_by_sig)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, ("recommendation_shadow_run", str(subject)[:100], 0.0, 0.0,
                  f"shadow recommendation ({recommendation_kind}) for {subject}, "
                  "never acted on -- predictive-accuracy measurement only",
                  previous_hash, current_hash, json.dumps(data),
                  "recommendation_shadow_run", cassette_version,
                  json.dumps(inputs), json.dumps(recommendation), authorized_by,
                  authorized_by_sig))
            conn.commit()
            return {
                "status": "created",
                "recommendation_kind": str(recommendation_kind),
                "subject": str(subject),
                "current_hash": current_hash,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    def record_recommendation_shadow_score(
            self, shadow_run_hash: str, actual: Dict[str, Any],
            score: Dict[str, Any], authorized_by: Optional[str] = None,
            ) -> Dict[str, Any]:
        """Record what actually happened against one shadow run's
        prediction, and the computed accuracy/error -- a NEW row that
        references shadow_run_hash, never a mutation of the original.

        `actual` is the real outcome data pulled from
        get_decisions_by_node_in_window for the window AFTER the
        recommendation was made. `score` is recommendation_impact.py's
        own comparison output (e.g. {"predicted_wait": ..., "actual_wait":
        ..., "error": ..., "within_confidence": ...}) -- this method
        stores it, it does not compute it; see
        recommendation_impact.score_healing_bounds_run /
        score_queue_reordering_run for the actual comparison logic.
        """
        if not shadow_run_hash or not str(shadow_run_hash).strip():
            raise ValueError("record_recommendation_shadow_score requires "
                             "shadow_run_hash")
        if not isinstance(actual, dict) or not isinstance(score, dict):
            raise ValueError("record_recommendation_shadow_score requires "
                             "actual and score as dicts")
        actual = json.loads(json.dumps(actual, sort_keys=True, default=str))
        score = json.loads(json.dumps(score, sort_keys=True, default=str))

        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('ledger_entries'))")
            cursor.execute("""
                SELECT current_hash FROM ledger_entries
                WHERE current_hash = %s AND record_kind = 'recommendation_shadow_run'
            """, (shadow_run_hash,))
            if cursor.fetchone() is None:
                raise ValueError(
                    f"shadow_run_hash {shadow_run_hash!r} does not match any "
                    "recommendation_shadow_run row -- refusing to score "
                    "something that was never actually recommended")

            cursor.execute("""
                SELECT current_hash FROM ledger_entries ORDER BY id DESC LIMIT 1
            """)
            row = cursor.fetchone()
            previous_hash = row[0] if row else "genesis"

            canonical_entry = {
                "record_kind": "recommendation_shadow_score",
                "actual": actual,
                "score": score,
                "previous_hash": previous_hash,
            }
            authorized_by_sig = self._authorized_by_sig(
                authorized_by, previous_hash, "recommendation_shadow_score")
            apply_optional_hashed_fields(canonical_entry, {
                "authorized_by": authorized_by,
                "shadow_run_hash": shadow_run_hash,
                _AUTHORIZED_BY_SIG_FIELD: authorized_by_sig,
            })
            current_hash = hashlib.sha256(
                json.dumps(canonical_entry, sort_keys=True, default=str).encode()
            ).hexdigest()

            data = {"record_kind": "recommendation_shadow_score",
                    "shadow_run_hash": shadow_run_hash, "parameter_changed": False}
            cursor.execute("""
                INSERT INTO ledger_entries
                (action_type, node, previous_value, applied_value, reason,
                 previous_hash, current_hash, data,
                 record_kind, shadow_run_hash, input_data, decision_output,
                 authorized_by, authorized_by_sig)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, ("recommendation_shadow_score", shadow_run_hash[:100], 0.0, 0.0,
                  f"scored shadow recommendation {shadow_run_hash[:12]}",
                  previous_hash, current_hash, json.dumps(data),
                  "recommendation_shadow_score", shadow_run_hash,
                  json.dumps(actual), json.dumps(score), authorized_by,
                  authorized_by_sig))
            conn.commit()
            return {"status": "created", "shadow_run_hash": shadow_run_hash,
                    "current_hash": current_hash}
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    def record_human_selection(
            self, decision_hash: str, human_selection: str, selected_by: str,
            rationale: Optional[str] = None) -> Dict[str, Any]:
        """Record a human's response to ONE governance_decision row's verdict.

        F2 (2026-08-07): the founding idea that had never been built --
        nothing captured which recommendation a human accepted, overrode,
        or rejected. Piloted on exactly one surface: GovernanceDecider.
        safety_check's verdict on a governed episode, the only
        recommendation confirmed live in the whole system (see
        record_recommendation_shadow_run's own docstring, and
        governance/human_selection_v1.py's module docstring for the full
        survey that ruled out every other candidate). Every other
        recommendation surface (queue-reordering, healing-bounds,
        staffing) is untouched by this method.

        `recommendation_shown` is never accepted from the caller -- same
        "never trust the actor's self-report" posture as episode.py's
        Provenance Rule. It is looked up here from the actual
        governance_decision row decision_hash names, so a human_selection
        row can never misrepresent what was actually recommended.

        This is capture only. Nothing here feeds simple_rl_trainer.py or
        any other learner -- that trainer is simulator-only and slated
        for GSA-815; wiring a real signal into it is a separate, later
        decision this method deliberately does not make.
        """
        if not decision_hash or not str(decision_hash).strip():
            raise ValueError("record_human_selection requires decision_hash")
        if human_selection not in HUMAN_SELECTIONS:
            raise ValueError(
                f"record_human_selection requires human_selection to be one "
                f"of {sorted(HUMAN_SELECTIONS)}, got {human_selection!r}")
        if not selected_by or not str(selected_by).strip():
            raise ValueError("record_human_selection requires selected_by "
                             "(who reviewed this decision)")

        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('ledger_entries'))")

            cursor.execute("""
                SELECT record_kind, cassette_version, reason, decision_output
                FROM ledger_entries WHERE current_hash = %s
            """, (decision_hash,))
            parent = cursor.fetchone()
            if parent is None or parent[0] != "governance_decision":
                raise ValueError(
                    f"decision_hash {decision_hash!r} does not match any "
                    "governance_decision row -- refusing to record a human "
                    "selection against something that was never actually "
                    "decided")
            _, cassette_version, parent_reason, parent_output = parent
            recommendation_shown = {
                "reasoning": parent_reason,
                "output": self._as_json(parent_output),
            }

            cursor.execute("""
                SELECT current_hash FROM ledger_entries ORDER BY id DESC LIMIT 1
            """)
            row = cursor.fetchone()
            previous_hash = row[0] if row else "genesis"

            canonical_entry = {
                "record_kind": "human_selection",
                "cassette_version": cassette_version,
                "human_selection": human_selection,
                "rationale": rationale,
                "recommendation_shown": recommendation_shown,
                "previous_hash": previous_hash,
            }
            authorized_by_sig = self._authorized_by_sig(
                selected_by, previous_hash, "human_selection")
            apply_optional_hashed_fields(canonical_entry, {
                "authorized_by": selected_by,
                "decision_hash": decision_hash,
                _AUTHORIZED_BY_SIG_FIELD: authorized_by_sig,
            })
            current_hash = hashlib.sha256(
                json.dumps(canonical_entry, sort_keys=True, default=str).encode()
            ).hexdigest()

            data = {"record_kind": "human_selection",
                    "human_selection": human_selection,
                    "rationale": rationale, "parameter_changed": False}
            cursor.execute("""
                INSERT INTO ledger_entries
                (action_type, node, previous_value, applied_value, reason,
                 previous_hash, current_hash, data,
                 record_kind, cassette_version, decision_hash, decision_output,
                 authorized_by, authorized_by_sig)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, ("human_selection", decision_hash[:100], 0.0, 0.0,
                  f"human selection ({human_selection}) on decision "
                  f"{decision_hash[:12]}",
                  previous_hash, current_hash, json.dumps(data),
                  "human_selection", cassette_version, decision_hash,
                  json.dumps(recommendation_shown), selected_by,
                  authorized_by_sig))
            conn.commit()
            return {
                "status": "created",
                "human_selection": human_selection,
                "decision_hash": decision_hash,
                "current_hash": current_hash,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    def get_human_selections(self, decision_hash: Optional[str] = None,
                             limit: int = 100) -> List[Dict[str, Any]]:
        """human_selection rows, newest first. `decision_hash`, when given,
        filters to selections reviewing that one governance_decision row --
        mirrors get_decisions'/get_unscored_shadow_runs' filter posture.
        """
        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            query = """
                SELECT id, timestamp, cassette_version, decision_hash,
                       authorized_by, current_hash, previous_hash, data,
                       decision_output
                FROM ledger_entries
                WHERE record_kind = 'human_selection'
            """
            params: list = []
            if decision_hash is not None:
                query += " AND decision_hash = %s"
                params.append(decision_hash)
            query += " ORDER BY id DESC LIMIT %s"
            params.append(limit)
            cursor.execute(query, tuple(params))

            selections = []
            for row in cursor.fetchall():
                data = self._as_json(row[7]) or {}
                selections.append({
                    "id": row[0],
                    "timestamp": row[1].isoformat() if row[1] else None,
                    "cassette_version": row[2],
                    "decision_hash": row[3],
                    "selected_by": row[4],
                    "current_hash": row[5],
                    "previous_hash": row[6],
                    "human_selection": data.get("human_selection"),
                    "rationale": data.get("rationale"),
                    "recommendation_shown": self._as_json(row[8]),
                })
            return selections
        finally:
            self.pool.putconn(conn)

    def get_unscored_shadow_runs(self, older_than_iso: Optional[str] = None,
                                 limit: int = 100) -> List[Dict[str, Any]]:
        """Shadow runs (recommendation_shadow_run rows) that have no
        matching recommendation_shadow_score row yet -- what the scoring
        pass (recommendation_impact.py / the CLI) should process next.
        `older_than_iso`: only shadow runs made before this timestamp --
        the caller's job to pick a value that guarantees the outcome
        window has fully elapsed; this method has no opinion on how long
        that should be.
        """
        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            query = """
                SELECT r.current_hash, r.timestamp, r.cassette_version,
                       r.data, r.input_data, r.decision_output
                FROM ledger_entries r
                WHERE r.record_kind = 'recommendation_shadow_run'
                  AND NOT EXISTS (
                      SELECT 1 FROM ledger_entries s
                      WHERE s.record_kind = 'recommendation_shadow_score'
                        AND s.shadow_run_hash = r.current_hash
                  )
            """
            params: list = []
            if older_than_iso is not None:
                query += " AND r.timestamp < %s"
                params.append(older_than_iso)
            query += " ORDER BY r.id ASC LIMIT %s"
            params.append(limit)
            cursor.execute(query, tuple(params))
            out = []
            for row in cursor.fetchall():
                data = self._as_json(row[3]) or {}
                out.append({
                    "shadow_run_hash": row[0],
                    "timestamp": row[1].isoformat() if row[1] else None,
                    "cassette_version": row[2],
                    "recommendation_kind": data.get("recommendation_kind"),
                    "subject": data.get("subject"),
                    "inputs": self._as_json(row[4]),
                    "recommendation": self._as_json(row[5]),
                })
            return out
        finally:
            self.pool.putconn(conn)

    def record_outcome_harm_event(self, cassette_version: str,
                                 decision_hash: str, harm_kind: str,
                                 subject_id: str, finding: Dict[str, Any],
                                 discovered_at: Optional[str] = None,
                                 authorized_by: Optional[str] = None,
                                 cassette_hash: Optional[str] = None) -> Dict[str, Any]:
        """Record a HARM event against a closed decision. OutcomeV1.

        The exception carved out of "per-decision outcomes are business
        reporting, not governance". A defaulted loan on a calibrated
        model is expected loss and stays out of the chain; a denial
        reversed on appeal is different in kind, because what it
        establishes is that the decision PROCESS failed, not that the
        odds came in badly. That is a governance fact and it belongs
        where governance facts live.

        Deliberately NOT a decision_supersession. A supersession says
        "a reviewer looked at the decision and replaced its output". A
        harm event says "the world established, later, that this
        decision caused harm" -- possibly with no replacement output at
        all, possibly years afterward, possibly discovered by someone
        with no authority to supersede anything. Filing both under one
        record_kind would leave an examiner unable to count either.

        The decision itself is NOT touched. decision_hash points at the
        row from here; the row keeps pointing nowhere, which is what
        lets it stay closed forever.
        """
        if not cassette_version or not isinstance(cassette_version, str):
            raise ValueError("record_outcome_harm_event requires cassette_version")
        if not decision_hash or not str(decision_hash).strip():
            raise ValueError("record_outcome_harm_event requires the current_hash of "
                             "the decision harmed -- a harm event with nothing to "
                             "point at is an allegation, not a record")
        if not harm_kind or not str(harm_kind).strip():
            raise ValueError("record_outcome_harm_event requires harm_kind (e.g. "
                             "'denial_reversed_on_appeal'); 'harm occurred' is not "
                             "a finding an examiner can act on")
        if not subject_id or not str(subject_id).strip():
            raise ValueError("record_outcome_harm_event requires the subject the "
                             "harm applies to")
        if not isinstance(finding, dict) or not finding:
            raise ValueError("record_outcome_harm_event requires a non-empty finding "
                             "body as a dict")
        finding = json.loads(json.dumps(finding, sort_keys=True, default=str))

        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('ledger_entries'))")
            cursor.execute("""
                SELECT current_hash FROM ledger_entries ORDER BY id DESC LIMIT 1
            """)
            row = cursor.fetchone()
            previous_hash = row[0] if row else "genesis"

            canonical_entry = {
                "record_kind": "outcome_harm_event",
                "cassette_version": cassette_version,
                "harmed_decision": str(decision_hash),
                "harm_kind": str(harm_kind),
                "subject": str(subject_id),
                "discovered_at": str(discovered_at or ""),
                "finding": finding,
                "previous_hash": previous_hash,
            }
            authorized_by_sig = self._authorized_by_sig(
                authorized_by, previous_hash, "outcome_harm_event")
            apply_optional_hashed_fields(canonical_entry, {
                "cassette_hash": cassette_hash,
                "authorized_by": authorized_by,
                _AUTHORIZED_BY_SIG_FIELD: authorized_by_sig,
            })
            current_hash = hashlib.sha256(
                json.dumps(canonical_entry, sort_keys=True, default=str).encode()
            ).hexdigest()

            data = {"record_kind": "outcome_harm_event",
                    "harmed_decision": str(decision_hash),
                    "harm_kind": str(harm_kind), "subject": str(subject_id),
                    "discovered_at": str(discovered_at or ""),
                    "parameter_changed": False}
            cursor.execute("""
                INSERT INTO ledger_entries
                (action_type, node, previous_value, applied_value, reason,
                 previous_hash, current_hash, data,
                 record_kind, cassette_version, decision_output, cassette_hash,
                 authorized_by, authorized_by_sig)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, ("outcome_harm_event", str(harm_kind)[:100], 0.0, 0.0,
                  f"outcome harm event: {str(harm_kind)[:80]} on decision "
                  f"{str(decision_hash)[:16]}",
                  previous_hash, current_hash, json.dumps(data),
                  "outcome_harm_event", cassette_version,
                  json.dumps(finding), cassette_hash, authorized_by,
                  authorized_by_sig))
            conn.commit()
            return {
                "status": "created",
                "cassette_version": cassette_version,
                "harmed_decision": str(decision_hash),
                "harm_kind": str(harm_kind),
                "subject": str(subject_id),
                "current_hash": current_hash,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    # -- Contract compliance attestation --------------------------------
    #
    # Four record kinds, all on the SAME chain as every other event, all
    # hashed through the shared canonical_fields contract. They exist as
    # distinct kinds rather than one "contract_event" with a subtype for
    # the reason record_outcome_harm_event states: filing distinguishable
    # events under one record_kind leaves an examiner unable to count
    # either. "How many egresses went to subcontractors" and "how many
    # deletions were attested late" are different questions and must be
    # different queries.
    #
    # REMINDER, learned the hard way on outcome_harm_event: a new record
    # kind is not done until all THREE recompute sites agree -- the
    # writer here, verify_chain below, and twin_custody.recompute_current_hash.

    _CONTRACT_EGRESS_DECISIONS = ("authorized", "refused")
    _CONTRACT_APPROVAL_STATES = ("granted", "revoked")
    _CONTRACT_DELETION_SCOPES = ("active", "backup")

    def record_contract_ingest(self, contract_version: str, counterparty: str,
                               ingest_id: str, data_scope: str,
                               received_at: str,
                               cassette_hash: Optional[str] = None,
                               authorized_by: Optional[str] = None
                               ) -> Dict[str, Any]:
        """Record that data arrived under a contract, as a chained event.

        Retention cannot be checked without a start date. An ingest row
        is that start date, on the chain, so the clock a deletion is
        measured against is not something the operator can quietly
        restate later. A deletion event points back at ingest_id.

        Deliberately NOT a governance_decision: nothing was decided
        here. Data arrived.
        """
        if not contract_version or not isinstance(contract_version, str):
            raise ValueError("record_contract_ingest requires contract_version")
        if not counterparty or not str(counterparty).strip():
            raise ValueError("record_contract_ingest requires the counterparty "
                             "whose contract governs this data")
        if not ingest_id or not str(ingest_id).strip():
            raise ValueError("record_contract_ingest requires ingest_id -- a "
                             "deletion with nothing to point back at cannot "
                             "retire anything")
        if not data_scope or not str(data_scope).strip():
            raise ValueError("record_contract_ingest requires data_scope")
        if not received_at or not str(received_at).strip():
            raise ValueError("record_contract_ingest requires received_at; the "
                             "retention clock has to start somewhere explicit")

        return self._append_contract_row(
            record_kind="contract_ingest",
            cassette_version=contract_version,
            canonical_extra={
                "counterparty": str(counterparty),
                "ingest_id": str(ingest_id),
                "data_scope": str(data_scope),
                "received_at": str(received_at),
            },
            finding=None,
            node=str(counterparty)[:100],
            reason=f"contract ingest {str(ingest_id)[:60]}",
            cassette_hash=cassette_hash,
            authorized_by=authorized_by,
        )

    def record_contract_egress(self, contract_version: str, counterparty: str,
                               decision: str, data_scope: str, recipient: str,
                               recipient_class: str, purpose: str,
                               occurred_at: str, finding: Dict[str, Any],
                               approval_reference: Optional[str] = None,
                               cassette_hash: Optional[str] = None,
                               authorized_by: Optional[str] = None
                               ) -> Dict[str, Any]:
        """Record one egress authorization decision -- granted or refused.

        Both outcomes are chained. A refusal that left no trace would
        make the log a record of successes, which is the one shape of
        egress log nobody should trust. `decision` is a first-class
        hashed field, so "authorized" cannot be edited to look like a
        refusal or vice versa without breaking the chain.

        HONEST SCOPE, and it must stay stated wherever this data is
        shown: this proves the egress log is complete relative to the
        chokepoint. It cannot prove nothing left by a path that never
        called the chokepoint at all.
        """
        if not contract_version or not isinstance(contract_version, str):
            raise ValueError("record_contract_egress requires contract_version")
        if decision not in self._CONTRACT_EGRESS_DECISIONS:
            raise ValueError(
                f"decision must be one of {list(self._CONTRACT_EGRESS_DECISIONS)}, "
                f"got {decision!r}")
        for name, value in (("counterparty", counterparty),
                            ("data_scope", data_scope),
                            ("recipient", recipient),
                            ("recipient_class", recipient_class),
                            ("purpose", purpose),
                            ("occurred_at", occurred_at)):
            if not value or not str(value).strip():
                raise ValueError(f"record_contract_egress requires {name}")
        if not isinstance(finding, dict) or not finding:
            raise ValueError("record_contract_egress requires a non-empty finding "
                             "body stating the basis for the decision")
        finding = json.loads(json.dumps(finding, sort_keys=True, default=str))

        return self._append_contract_row(
            record_kind="contract_egress",
            cassette_version=contract_version,
            canonical_extra={
                "counterparty": str(counterparty),
                "decision": str(decision),
                "data_scope": str(data_scope),
                "recipient": str(recipient),
                "recipient_class": str(recipient_class),
                "purpose": str(purpose),
                "approval_reference": str(approval_reference or ""),
                "occurred_at": str(occurred_at),
            },
            finding=finding,
            node=str(recipient_class)[:100],
            reason=(f"contract egress {decision}: {str(purpose)[:40]} -> "
                    f"{str(recipient)[:40]}"),
            cassette_hash=cassette_hash,
            authorized_by=authorized_by,
        )

    def record_contract_approval(self, contract_version: str, counterparty: str,
                                 approval_id: str, state: str, approver: str,
                                 recipient: str, recipient_class: str,
                                 scope: str, granted_at: str,
                                 expires_at: Optional[str] = None,
                                 revoked_at: Optional[str] = None,
                                 cassette_hash: Optional[str] = None
                                 ) -> Dict[str, Any]:
        """Record an approval grant or revocation as a chained event.

        Approval is first-class, not an attribute of the egress that
        used it: an egress references an approval, and whether that
        approval was live and unrevoked AT EGRESS TIME is then a
        question answerable from two chained rows rather than from one
        row's self-description.

        Revocation is its own row with state="revoked", never an edit
        of the grant row. The grant happened; the chain says so
        permanently, and the revocation says when that stopped being
        true.

        approver rides in the authorized_by column, the same column
        every other authorizing identity in this ledger uses.
        """
        if not contract_version or not isinstance(contract_version, str):
            raise ValueError("record_contract_approval requires contract_version")
        if state not in self._CONTRACT_APPROVAL_STATES:
            raise ValueError(
                f"state must be one of {list(self._CONTRACT_APPROVAL_STATES)}, "
                f"got {state!r}")
        for name, value in (("counterparty", counterparty),
                            ("approval_id", approval_id),
                            ("approver", approver),
                            ("recipient", recipient),
                            ("recipient_class", recipient_class),
                            ("scope", scope),
                            ("granted_at", granted_at)):
            if not value or not str(value).strip():
                raise ValueError(f"record_contract_approval requires {name}")
        if state == "revoked" and not str(revoked_at or "").strip():
            raise ValueError("a revocation requires revoked_at; 'revoked at some "
                             "point' cannot be compared against an egress time")

        return self._append_contract_row(
            record_kind="contract_approval",
            cassette_version=contract_version,
            canonical_extra={
                "counterparty": str(counterparty),
                "approval_id": str(approval_id),
                "state": str(state),
                "recipient": str(recipient),
                "recipient_class": str(recipient_class),
                "scope": str(scope),
                "granted_at": str(granted_at),
                "expires_at": str(expires_at or ""),
                "revoked_at": str(revoked_at or ""),
            },
            finding=None,
            node=str(recipient_class)[:100],
            reason=f"contract approval {state}: {str(approval_id)[:60]}",
            cassette_hash=cassette_hash,
            authorized_by=str(approver),
        )

    def record_contract_deletion(self, contract_version: str, counterparty: str,
                                 ingest_id: str, deleted_at: str,
                                 scope: str, method: str,
                                 cassette_hash: Optional[str] = None,
                                 authorized_by: Optional[str] = None
                                 ) -> Dict[str, Any]:
        """Record a deletion as a POSITIVE event bound to the ingest it retires.

        Absence is never evidence of deletion. That is the whole reason
        this row exists: without a positive event, "no record of this
        data" and "deleted on time" are indistinguishable, and the
        latter is the one an operator would prefer you assume.

        PROVENANCE. This event is ATTESTED, never verified, and
        contract_retention.py stamps it that way in every report.
        Sentinel does not delete anything and does not watch the
        deletion happen; the processor deletes and then says so. A
        read-back check would still be the operator's own code
        reporting on the operator, which is attestation with extra
        steps. Real verification needs a signed tombstone from the
        storage layer -- outside instrumentation, explicitly out of
        scope. What gives the check teeth is not the stamp on a claim
        the operator did make, it is the treatment of the claim they
        did not: a missing deletion past the horizon is overdue or
        INDETERMINATE, never compliant.

        `method` names how the deletion was performed and is required
        -- an attestation that will not describe its own mechanism is
        the same failure event_v1 refuses for an estimate that will not
        name its method.
        """
        if not contract_version or not isinstance(contract_version, str):
            raise ValueError("record_contract_deletion requires contract_version")
        if scope not in self._CONTRACT_DELETION_SCOPES:
            raise ValueError(
                f"scope must be one of {list(self._CONTRACT_DELETION_SCOPES)}, "
                f"got {scope!r}")
        for name, value in (("counterparty", counterparty),
                            ("ingest_id", ingest_id),
                            ("deleted_at", deleted_at),
                            ("method", method)):
            if not value or not str(value).strip():
                raise ValueError(f"record_contract_deletion requires {name}")

        return self._append_contract_row(
            record_kind="contract_deletion",
            cassette_version=contract_version,
            canonical_extra={
                "counterparty": str(counterparty),
                "ingest_id": str(ingest_id),
                "deleted_at": str(deleted_at),
                "scope": str(scope),
                "method": str(method),
                "stamp": "attested",
            },
            finding=None,
            node=str(scope)[:100],
            reason=f"contract deletion ({scope}) of {str(ingest_id)[:50]}",
            cassette_hash=cassette_hash,
            authorized_by=authorized_by,
        )

    def _append_contract_row(self, record_kind: str, cassette_version: str,
                             canonical_extra: Dict[str, Any],
                             finding: Optional[Dict[str, Any]],
                             node: str, reason: str,
                             cassette_hash: Optional[str],
                             authorized_by: Optional[str]) -> Dict[str, Any]:
        """Shared append path for the four contract record kinds.

        One function rather than four near-identical bodies, because
        four hand-copied canonical-form builders is four chances for
        the writer to drift from verify_chain and the twin. The
        canonical form is always:

            record_kind, cassette_version, <the kind's own fields>,
            [finding, when the kind carries one], previous_hash

        plus the shared optional hashed fields. The kind's own fields
        are stored verbatim in the data JSONB under the same names, so
        both other recompute sites rebuild them by copying keys out of
        data with no per-kind mapping table to get wrong.
        """
        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('ledger_entries'))")
            cursor.execute("""
                SELECT current_hash FROM ledger_entries ORDER BY id DESC LIMIT 1
            """)
            row = cursor.fetchone()
            previous_hash = row[0] if row else "genesis"

            canonical_entry: Dict[str, Any] = {
                "record_kind": record_kind,
                "cassette_version": cassette_version,
            }
            canonical_entry.update(canonical_extra)
            if finding is not None:
                canonical_entry["finding"] = finding
            canonical_entry["previous_hash"] = previous_hash
            authorized_by_sig = self._authorized_by_sig(
                authorized_by, previous_hash, record_kind)
            apply_optional_hashed_fields(canonical_entry, {
                "cassette_hash": cassette_hash,
                "authorized_by": authorized_by,
                _AUTHORIZED_BY_SIG_FIELD: authorized_by_sig,
            })
            current_hash = hashlib.sha256(
                json.dumps(canonical_entry, sort_keys=True, default=str).encode()
            ).hexdigest()

            data = {"record_kind": record_kind, "parameter_changed": False}
            data.update(canonical_extra)
            cursor.execute("""
                INSERT INTO ledger_entries
                (action_type, node, previous_value, applied_value, reason,
                 previous_hash, current_hash, data,
                 record_kind, cassette_version, decision_output, cassette_hash,
                 authorized_by, authorized_by_sig)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (record_kind, node, 0.0, 0.0, reason,
                  previous_hash, current_hash, json.dumps(data),
                  record_kind, cassette_version,
                  json.dumps(finding) if finding is not None else None,
                  cassette_hash, authorized_by, authorized_by_sig))
            conn.commit()
            result = {"status": "created", "record_kind": record_kind,
                      "cassette_version": cassette_version,
                      "current_hash": current_hash}
            result.update(canonical_extra)
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    # The canonical field lists live in canonical_fields.py, imported by
    # all three recompute sites. Bound here as class attributes so the
    # methods below read naturally, NOT redefined -- a second copy is
    # exactly the drift canonical_fields exists to prevent.
    CONTRACT_CANONICAL_FIELDS: Dict[str, Tuple[str, ...]] = CONTRACT_CANONICAL_FIELDS
    CONTRACT_KINDS_WITH_FINDING: Tuple[str, ...] = CONTRACT_KINDS_WITH_FINDING

    def get_contract_rows(self, counterparty: str,
                          record_kinds: Optional[Tuple[str, ...]] = None,
                          limit: int = 5000) -> List[Dict[str, Any]]:
        """Every contract row for ONE counterparty, oldest first.

        Scoped at the query, not filtered after the fact in Python:
        a report generator that had to remember to filter is a report
        generator that will eventually forget. See
        contract_attestation.py for what is built on top of this.
        """
        if not counterparty or not str(counterparty).strip():
            raise ValueError("get_contract_rows requires a counterparty")
        kinds = tuple(record_kinds or tuple(self.CONTRACT_CANONICAL_FIELDS))
        unknown = [k for k in kinds if k not in self.CONTRACT_CANONICAL_FIELDS]
        if unknown:
            raise ValueError(f"unknown contract record kinds: {unknown}")
        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, timestamp, record_kind, cassette_version, data,
                       decision_output, cassette_hash, authorized_by,
                       previous_hash, current_hash
                FROM ledger_entries
                WHERE record_kind = ANY(%s)
                  AND data->>'counterparty' = %s
                ORDER BY id ASC
                LIMIT %s
            """, (list(kinds), str(counterparty), int(limit)))
            rows = []
            for r in cursor.fetchall():
                rows.append({
                    "id": r[0], "timestamp": r[1], "record_kind": r[2],
                    "cassette_version": r[3], "data": self._as_json(r[4]),
                    "decision_output": self._as_json(r[5]),
                    "cassette_hash": r[6], "authorized_by": r[7],
                    "previous_hash": r[8], "current_hash": r[9],
                })
            return rows
        finally:
            self.pool.putconn(conn)

    def get_regulatory_cassette_history(self,
                                        cassette_version: Optional[str] = None,
                                        limit: int = 200) -> List[Dict[str, Any]]:
        """The examiner query: every lens insertion/removal event, in
        chain order -- 'when was the CFPB lens active' read straight
        off record_kind, no inference required."""
        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            if cassette_version:
                cursor.execute("""
                    SELECT id, timestamp, record_kind, cassette_version, data,
                           cassette_hash, cassette_code_hash, authorized_by,
                           current_hash
                    FROM ledger_entries
                    WHERE record_kind IN ('regulatory_cassette_inserted',
                                          'regulatory_cassette_removed')
                      AND cassette_version = %s
                    ORDER BY id ASC LIMIT %s
                """, (cassette_version, limit))
            else:
                cursor.execute("""
                    SELECT id, timestamp, record_kind, cassette_version, data,
                           cassette_hash, cassette_code_hash, authorized_by,
                           current_hash
                    FROM ledger_entries
                    WHERE record_kind IN ('regulatory_cassette_inserted',
                                          'regulatory_cassette_removed')
                    ORDER BY id ASC LIMIT %s
                """, (limit,))
            events = []
            for (row_id, ts, kind, version, data, chash, code_hash,
                 who, cur_hash) in cursor.fetchall():
                d = self._as_json(data)
                events.append({
                    "id": row_id,
                    "timestamp": ts.isoformat() if ts else None,
                    "event": kind,
                    "cassette_version": version,
                    "mode": d.get("mode"),
                    "regulation": d.get("regulation"),
                    "cassette_hash": chash,
                    "cassette_code_hash": code_hash,
                    "authorized_by": who,
                    "current_hash": cur_hash,
                })
            return events
        finally:
            self.pool.putconn(conn)

    def supersede_decision(self, supersedes_id: int, authority: str, reason: str,
                           corrected_output: Dict[str, Any],
                           cassette_version: Optional[str] = None) -> Dict[str, Any]:
        """Item 6: formally supersede a prior decision WITHOUT altering it.

        The original row is immutable and stays exactly as written. A
        supersession is a NEW `decision_supersession` chain row that references
        the original by id AND by its current_hash -- proving the reviewer acted
        on the actual decision, not a tampered copy. The link (supersedes_hash)
        is inside the canonical form, so the reference itself is tamper-evident.

        This is not deletion, amendment, or a retroactive change. It is a new
        piece of evidence: "a human with authority X reviewed decision Y (whose
        hash was Z) and determined the corrected outcome was W."

        `authority` is the authorizing identity (a role/name, never PII) and is
        recorded in `authorized_by` -- reusing the Item 7 identity column, since
        a supersession is the human-initiated case of "authorized action on the
        governance record". It also enters the hash.

        Fail-closed: if the referenced decision does not exist, raises ValueError
        BEFORE appending -- a supersession that points at nothing is refused, not
        recorded as if valid.
        """
        if not isinstance(supersedes_id, int):
            raise ValueError("supersede_decision requires an integer supersedes_id")
        if not authority or not isinstance(authority, str):
            raise ValueError("supersede_decision requires an authority identity")
        if not isinstance(corrected_output, dict) or not corrected_output:
            raise ValueError("supersede_decision requires a non-empty corrected_output")

        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('ledger_entries'))")

            # The original must exist and be a governance decision.
            cursor.execute("""
                SELECT current_hash, cassette_version, record_kind
                FROM ledger_entries WHERE id = %s
            """, (supersedes_id,))
            orig = cursor.fetchone()
            if orig is None:
                conn.rollback()
                raise ValueError(
                    f"Cannot supersede decision id={supersedes_id}: no such row. "
                    "A supersession must reference an existing decision."
                )
            orig_hash, orig_version, orig_kind = orig
            if orig_kind != "governance_decision":
                conn.rollback()
                raise ValueError(
                    f"Cannot supersede row id={supersedes_id}: it is a "
                    f"'{orig_kind}', not a governance_decision."
                )
            # Inherit the original's cassette_version if none supplied -- the
            # supersession is about the same governed matter.
            version = cassette_version or orig_version or "supersession:none:0"

            cursor.execute("""
                SELECT current_hash FROM ledger_entries ORDER BY id DESC LIMIT 1
            """)
            row = cursor.fetchone()
            previous_hash = row[0] if row else "genesis"

            canonical_entry = {
                "record_kind": "decision_supersession",
                "supersedes_id": supersedes_id,
                "cassette_version": version,
                "authority": authority,
                "reason": reason,
                "corrected_output": corrected_output,
                "previous_hash": previous_hash,
            }
            # supersedes_hash (the original's current_hash) + authorized_by enter
            # the hash via the shared contract, so the link and the authorizing
            # identity are both tamper-evident and recompute identically on the twin.
            authorized_by_sig = self._authorized_by_sig(
                authority, previous_hash, "decision_supersession")
            apply_optional_hashed_fields(canonical_entry, {
                "supersedes_hash": orig_hash,
                "authorized_by": authority,
                _AUTHORIZED_BY_SIG_FIELD: authorized_by_sig,
            })
            current_hash = hashlib.sha256(
                json.dumps(canonical_entry, sort_keys=True, default=str).encode()
            ).hexdigest()

            data = {"record_kind": "decision_supersession", "parameter_changed": False}
            cursor.execute("""
                INSERT INTO ledger_entries
                (action_type, node, previous_value, applied_value, reason,
                 previous_hash, current_hash, data,
                 record_kind, cassette_version, decision_output,
                 authorized_by, supersedes_id, supersedes_hash, authorized_by_sig)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, ("decision_supersession", "supersession", 0.0, 0.0, reason,
                  previous_hash, current_hash, json.dumps(data),
                  "decision_supersession", version,
                  json.dumps(corrected_output),
                  authority, supersedes_id, orig_hash, authorized_by_sig))
            conn.commit()
            return {
                "status": "superseded",
                "supersedes_id": supersedes_id,
                "supersedes_hash": orig_hash,
                "authority": authority,
                "current_hash": current_hash,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    @staticmethod
    def _as_json(raw):
        if raw is None:
            return None
        if isinstance(raw, (dict, list)):
            return raw
        if raw:
            return json.loads(raw)
        return {}

    def get_decisions(self, cassette_version: Optional[str] = None,
                      limit: int = 100) -> List[Dict]:
        """Retrieve structured governance decisions, newest first.

        "Show me every decision this cassette version governed" is one
        call (and one SQL query -- see CASSETTE_GOVERNS_INTEGRATION).
        
        NEW: Includes cassette_snapshot and cassette_hash for forensic
        reconstruction. Regulators can call reconstruct_cassette_for_decision()
        on each row to prove the policy."""

        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            query = """
                SELECT id, timestamp, action_type, node, previous_value,
                       applied_value, reason, previous_hash, current_hash,
                       cassette_version, input_data, policy_parameters,
                       decision_output, cassette_snapshot, cassette_hash,
                       ai_cost
                FROM ledger_entries
                WHERE record_kind = 'governance_decision'
            """
            params: list = []
            if cassette_version is not None:
                query += " AND cassette_version = %s"
                params.append(cassette_version)
            query += " ORDER BY id DESC LIMIT %s"
            params.append(limit)
            cursor.execute(query, tuple(params))

            decisions = []
            for row in cursor.fetchall():
                decisions.append({
                    "id": row[0],
                    "timestamp": row[1].isoformat() if row[1] else None,
                    "action_type": row[2],
                    "node": row[3],
                    "previous_value": row[4],
                    "applied_value": row[5],
                    "reasoning": row[6],
                    "previous_hash": row[7],
                    "current_hash": row[8],
                    "cassette_version": row[9],
                    "input_data": self._as_json(row[10]),
                    "policy_parameters": self._as_json(row[11]),
                    "output": self._as_json(row[12]),
                    "cassette_snapshot": self._as_json(row[13]),
                    "cassette_hash": row[14],
                    "ai_cost": self._as_json(row[15]),
                })
            return decisions
        finally:
            self.pool.putconn(conn)

    def get_decisions_by_node_in_window(
            self, node: str, since_iso: str, until_iso: str,
            limit: int = 1000) -> List[Dict]:
        """Real governance_decision rows for one node/queue, timestamp
        BETWEEN [since_iso, until_iso). Built for recommendation_impact.py
        (2026-07-31): pulling real recent-window and baseline-window
        per-queue data to feed decide_healing_bounds/decide_queue_reordering
        with real numbers instead of simulated ones. Deliberately separate
        from get_decisions() (which has no time or node filter) rather than
        overloading that method's existing contract -- this one exists for
        one purpose, aggregation over the result is the caller's job, not
        this method's.
        """
        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, timestamp, node, input_data, decision_output
                FROM ledger_entries
                WHERE record_kind = 'governance_decision'
                  AND node = %s
                  AND timestamp >= %s AND timestamp < %s
                ORDER BY id ASC
                LIMIT %s
            """, (node, since_iso, until_iso, limit))
            rows = []
            for row in cursor.fetchall():
                rows.append({
                    "id": row[0],
                    "timestamp": row[1].isoformat() if row[1] else None,
                    "node": row[2],
                    "input_data": self._as_json(row[3]),
                    "output": self._as_json(row[4]),
                })
            return rows
        finally:
            self.pool.putconn(conn)

    def get_entries(self, limit: int = 100) -> List[Dict]:
        """Retrieve recent entries"""
        
        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, timestamp, action_type, node, previous_value, applied_value,
                       reason, previous_hash, current_hash, data
                FROM ledger_entries
                ORDER BY id DESC
                LIMIT %s
            """, (limit,))
            
            entries = []
            for row in cursor.fetchall():
                # psycopg2 already deserializes JSONB columns into
                # Python objects; calling json.loads on the resulting
                # dict raised TypeError and made every read of the
                # ledger crash. Decode only if the driver hands back a
                # raw string (e.g. a TEXT-typed legacy column).
                raw = row[9]
                if isinstance(raw, (dict, list)):
                    data = raw
                elif raw:
                    data = json.loads(raw)
                else:
                    data = {}
                entries.append({
                    "id": row[0],
                    "timestamp": row[1].isoformat() if row[1] else None,
                    "action_type": row[2],
                    "node": row[3],
                    "previous_value": row[4],
                    "applied_value": row[5],
                    "reason": row[6],
                    "previous_hash": row[7],
                    "current_hash": row[8],
                    "data": data
                })
            return entries
        finally:
            self.pool.putconn(conn)
    
    def get_decision_with_cassette(self, decision_id: int) -> Dict[str, Any]:
        """Retrieve a decision AND reconstruct the cassette that governed it.

        This is the "show me your proof" endpoint for regulators.

        Returns:
        {
            "decision": { ...full decision record... },
            "cassette_proof": {
                "decision_id": <id>,
                "cassette_snapshot": { ...full cassette config... },
                "cassette_hash": <SHA-256>,
                "cassette_version": <domain:name:version>,
                "timestamp": <ISO 8601>,
                "integrity_verified": True/False
            }
        }

        Raises ValueError if the cassette snapshot is missing or corrupted.
        """
        if reconstruct_cassette_for_decision is None:
            raise RuntimeError(
                "cassette_forensics module not available; "
                "cannot reconstruct cassettes"
            )

        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, timestamp, action_type, node, previous_value,
                       applied_value, reason, previous_hash, current_hash,
                       cassette_version, cassette_hash, cassette_snapshot,
                       input_data, policy_parameters, decision_output
                FROM ledger_entries
                WHERE id = %s AND record_kind = 'governance_decision'
            """, (decision_id,))

            row = cursor.fetchone()
            if not row:
                raise ValueError(f"Decision {decision_id} not found")

            decision_dict = {
                "id": row[0],
                "timestamp": row[1].isoformat() if row[1] else None,
                "action_type": row[2],
                "node": row[3],
                "previous_value": row[4],
                "applied_value": row[5],
                "reasoning": row[6],
                "previous_hash": row[7],
                "current_hash": row[8],
                "cassette_version": row[9],
                "cassette_hash": row[10],
                "cassette_snapshot": self._as_json(row[11]),
                "input_data": self._as_json(row[12]),
                "policy_parameters": self._as_json(row[13]),
                "output": self._as_json(row[14]),
            }

            # Reconstruct cassette and verify integrity
            cassette_proof = reconstruct_cassette_for_decision(decision_dict)

            return {
                "decision": decision_dict,
                "cassette_proof": cassette_proof,
            }
        finally:
            self.pool.putconn(conn)

    def validate_cassette_snapshot_chain(self) -> Dict[str, Any]:
        """Audit the ledger to prove all cassette snapshots are
        consistent and uncorrupted.

        Used for regulatory audits: "Prove your cassette snapshots are real."

        Returns:
        {
            "total_decisions": N,
            "snapshots_verified": M,
            "corrupted": [],
            "pre_migration": [],
            "all_ok": True/False
        }
        """
        if reconstruct_cassette_for_decision is None:
            raise RuntimeError(
                "cassette_forensics module not available; "
                "cannot validate cassette snapshots"
            )
        
        if compute_cassette_hash is None:
            raise RuntimeError(
                "cassette_forensics module not available; "
                "cannot compute cassette hashes"
            )

        # Retrieve all decisions
        all_decisions = self.get_decisions(limit=10000)

        result = {
            "total_decisions": len(all_decisions),
            "snapshots_verified": 0,
            "corrupted": [],
            "pre_migration": [],
            "all_ok": True,
        }

        for decision in all_decisions:
            decision_id = decision.get("id")
            stored_cassette_snapshot = decision.get("cassette_snapshot")
            stored_cassette_hash = decision.get("cassette_hash")

            if not stored_cassette_snapshot:
                result["pre_migration"].append(decision_id)
                continue

            try:
                # Reconstruct cassette from decision record
                reconstruct_cassette_for_decision(decision)
                
                # Explicit hash verification: compute hash of stored snapshot
                # and compare against stored cassette_hash
                computed_hash = compute_cassette_hash(stored_cassette_snapshot)
                if computed_hash != stored_cassette_hash:
                    result["corrupted"].append({
                        "decision_id": decision_id,
                        "error": f"cassette_hash mismatch: stored={stored_cassette_hash[:8]}..., computed={computed_hash[:8]}..."
                    })
                    result["all_ok"] = False
                else:
                    result["snapshots_verified"] += 1
                    
            except ValueError as e:
                result["corrupted"].append(
                    {"decision_id": decision_id, "error": str(e)}
                )
                result["all_ok"] = False

        return result

    def verify_chain(self, mode: str = "strict") -> Dict:
        """Verify ledger integrity: chain links AND content hash recomputation.

        Checks both that previous_hash links form an unbroken chain AND that
        each row's current_hash matches a fresh recomputation from its contents.
        Detects in-place tampering (e.g., flipping decision_output.approved).

        When any attestation key is configured (current, PREVIOUS, or
        RETIRED), ALSO checks the keyed attestation on each row's authorized_by
        claim: a row whose authorized_by string was altered after writing is
        reported as a violation even if current_hash was recomputed to keep the
        unkeyed SHA-256 chain self-consistent. A row signed by a key this
        deployment does not hold at all (UNKNOWN_KEY) is ALWAYS a violation; a
        row signed by a deliberately retired key (RETIRED_KEY) is a violation
        only when enforcement is on. This check attests writer-authenticity and
        integrity of the claim string only -- NOT that the named party held the
        authority claimed (see authorized_by_attestation.py). With no key
        configured, this method behaves exactly as before.
        """

        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            _att_keys = attestation_keyset()
            _att_enforced = enforcement_required()
            # Fetch all columns needed to reconstruct canonical forms
            cursor.execute("""
                SELECT id, record_kind, previous_hash, current_hash,
                       action_type, node, previous_value, applied_value, reason,
                       data, cassette_version, input_data, policy_parameters,
                       decision_output, cassette_hash,
                       cassette_code_hash, model_identity, authorized_by,
                       supersedes_id, supersedes_hash, outcome_obligation,
                       replaces_hash, ai_cost, shadow_run_hash, decision_hash,
                       authorized_by_sig
                FROM ledger_entries
                ORDER BY id ASC
            """)
            
            rows = cursor.fetchall()
            
            if not rows:
                return {"ok": True, "entries": 0, "violations": []}
            
            violations = []
            prev_hash = "genesis"
            
            for row in rows:
                (row_id, record_kind, stored_prev, stored_current,
                 action_type, node, previous_value, applied_value, reason,
                 data, cassette_version, input_data, policy_parameters,
                 decision_output, cassette_hash,
                 cassette_code_hash, model_identity, authorized_by,
                 supersedes_id, supersedes_hash, outcome_obligation,
                 replaces_hash, ai_cost, shadow_run_hash, decision_hash,
                 authorized_by_sig) = row
                
                # Check chain link integrity
                if stored_prev != prev_hash:
                    violations.append(f"Entry {row_id}: chain broken (prev_hash mismatch)")
                
                # Reconstruct canonical form based on record type and recompute hash
                try:
                    if record_kind == "governance_decision":
                        # Structured decision path (append_decision)
                        canonical_entry = {
                            "record_kind": "governance_decision",
                            "action_type": action_type,
                            "node": node,
                            "cassette_version": cassette_version,
                            "input_data": self._as_json(input_data),
                            "policy_parameters": self._as_json(policy_parameters),
                            "reasoning": reason,
                            "output": self._as_json(decision_output),
                            "previous_value": previous_value,
                            "applied_value": applied_value,
                            "parameter_changed": self._as_json(data).get("parameter_changed", False),
                            "previous_hash": stored_prev,
                        }
                        # Optional hashed fields (cassette_hash + Phase-2) via the
                        # SAME shared contract the writer and the twin use, so all
                        # three recompute sites stay in lockstep. Absent fields are
                        # omitted -> legacy rows recompute exactly as before.
                        apply_optional_hashed_fields(canonical_entry, {
                            "cassette_hash": cassette_hash,
                            "cassette_code_hash": cassette_code_hash,
                            "model_identity": model_identity,
                            "authorized_by": authorized_by,
                            "supersedes_hash": supersedes_hash,
                            "outcome_obligation": outcome_obligation,
                            "replaces_hash": replaces_hash,
                            "ai_cost": self._as_json(ai_cost),
                        })
                    elif record_kind == "cassette_binding":
                        # Item 2 -- mirrors bind_cassette_version()
                        canonical_entry = {
                            "record_kind": "cassette_binding",
                            "cassette_version": cassette_version,
                            "previous_hash": stored_prev,
                        }
                        apply_optional_hashed_fields(canonical_entry, {
                            "cassette_hash": cassette_hash,
                            "cassette_code_hash": cassette_code_hash,
                            "authorized_by": authorized_by,
                        })
                    elif record_kind in ("regulatory_cassette_inserted",
                                         "regulatory_cassette_removed"):
                        # Mirrors record_regulatory_cassette_event().
                        # mode + regulation were stored in data.
                        d = self._as_json(data)
                        canonical_entry = {
                            "record_kind": record_kind,
                            "cassette_version": cassette_version,
                            "mode": d.get("mode"),
                            "regulation": d.get("regulation"),
                            "previous_hash": stored_prev,
                        }
                        apply_optional_hashed_fields(canonical_entry, {
                            "cassette_hash": cassette_hash,
                            "cassette_code_hash": cassette_code_hash,
                            "authorized_by": authorized_by,
                        })
                    elif record_kind == "regulatory_disclosure":
                        # Mirrors record_regulatory_disclosure(). The
                        # finding body was stored in decision_output;
                        # regulation/check/action/subject in data.
                        d = self._as_json(data)
                        canonical_entry = {
                            "record_kind": "regulatory_disclosure",
                            "cassette_version": cassette_version,
                            "regulation": d.get("regulation"),
                            "check": d.get("check"),
                            "action": d.get("action"),
                            "subject": d.get("subject"),
                            "finding": self._as_json(decision_output),
                            "previous_hash": stored_prev,
                        }
                        apply_optional_hashed_fields(canonical_entry, {
                            "cassette_hash": cassette_hash,
                            "authorized_by": authorized_by,
                        })
                    elif record_kind == "outcome_harm_event":
                        # OutcomeV1 -- mirrors record_outcome_harm_event().
                        # The finding body was stored in decision_output; the
                        # pointer, kind, subject and discovery time in data.
                        # Without this branch a harm event would fall through
                        # to the legacy path and fail its own verification --
                        # a new record kind is not done until all three
                        # recompute sites (writer, this, twin_custody) agree.
                        d = self._as_json(data)
                        canonical_entry = {
                            "record_kind": "outcome_harm_event",
                            "cassette_version": cassette_version,
                            "harmed_decision": d.get("harmed_decision"),
                            "harm_kind": d.get("harm_kind"),
                            "subject": d.get("subject"),
                            "discovered_at": d.get("discovered_at"),
                            "finding": self._as_json(decision_output),
                            "previous_hash": stored_prev,
                        }
                        apply_optional_hashed_fields(canonical_entry, {
                            "cassette_hash": cassette_hash,
                            "authorized_by": authorized_by,
                        })
                    elif record_kind in self.CONTRACT_CANONICAL_FIELDS:
                        # Contract compliance attestation -- mirrors
                        # _append_contract_row(). Every one of the kind's own
                        # fields was stored in data under its canonical name,
                        # so this rebuilds by copying keys in the declared
                        # order rather than by a per-kind mapping that could
                        # drift from the writer. Recompute site 2 of 3.
                        d = self._as_json(data) or {}
                        canonical_entry = {
                            "record_kind": record_kind,
                            "cassette_version": cassette_version,
                        }
                        for key in self.CONTRACT_CANONICAL_FIELDS[record_kind]:
                            canonical_entry[key] = d.get(key)
                        if record_kind in self.CONTRACT_KINDS_WITH_FINDING:
                            canonical_entry["finding"] = self._as_json(decision_output)
                        canonical_entry["previous_hash"] = stored_prev
                        apply_optional_hashed_fields(canonical_entry, {
                            "cassette_hash": cassette_hash,
                            "authorized_by": authorized_by,
                        })
                    elif record_kind == "decision_supersession":
                        # Item 6 -- mirrors supersede_decision(). authority was
                        # stored in authorized_by; corrected_output in decision_output.
                        canonical_entry = {
                            "record_kind": "decision_supersession",
                            "supersedes_id": supersedes_id,
                            "cassette_version": cassette_version,
                            "authority": authorized_by,
                            "reason": reason,
                            "corrected_output": self._as_json(decision_output),
                            "previous_hash": stored_prev,
                        }
                        apply_optional_hashed_fields(canonical_entry, {
                            "supersedes_hash": supersedes_hash,
                            "authorized_by": authorized_by,
                        })
                    elif record_kind == "recommendation_shadow_run":
                        # Mirrors record_recommendation_shadow_run().
                        # recommendation_kind/subject were stored in data;
                        # inputs in input_data; the recommendation itself
                        # in decision_output.
                        d = self._as_json(data)
                        canonical_entry = {
                            "record_kind": "recommendation_shadow_run",
                            "cassette_version": cassette_version,
                            "recommendation_kind": d.get("recommendation_kind"),
                            "subject": d.get("subject"),
                            "inputs": self._as_json(input_data),
                            "recommendation": self._as_json(decision_output),
                            "previous_hash": stored_prev,
                        }
                        apply_optional_hashed_fields(canonical_entry, {
                            "authorized_by": authorized_by,
                        })
                    elif record_kind == "recommendation_shadow_score":
                        # Mirrors record_recommendation_shadow_score(). The
                        # actual outcome was stored in input_data (reusing
                        # that column's existing "real data this row is
                        # about" role, same as every other record kind
                        # here); the computed score in decision_output;
                        # shadow_run_hash has its own dedicated column,
                        # deliberately not a reuse of replaces_hash -- see
                        # canonical_fields.py.
                        canonical_entry = {
                            "record_kind": "recommendation_shadow_score",
                            "actual": self._as_json(input_data),
                            "score": self._as_json(decision_output),
                            "previous_hash": stored_prev,
                        }
                        apply_optional_hashed_fields(canonical_entry, {
                            "authorized_by": authorized_by,
                            "shadow_run_hash": shadow_run_hash,
                        })
                    elif record_kind == "human_selection":
                        # F2 -- mirrors record_human_selection(). The
                        # selection/rationale were stored in data;
                        # recommendation_shown (looked up from the parent
                        # governance_decision at write time, never
                        # caller-supplied) in decision_output;
                        # decision_hash has its own dedicated column, same
                        # posture as shadow_run_hash -- see
                        # canonical_fields.py. Without this branch a
                        # human_selection row would fall through to the
                        # legacy path and fail its own verification -- a
                        # new record kind is not done until all three
                        # recompute sites (writer, this, twin_custody) agree.
                        d = self._as_json(data)
                        canonical_entry = {
                            "record_kind": "human_selection",
                            "cassette_version": cassette_version,
                            "human_selection": d.get("human_selection"),
                            "rationale": d.get("rationale"),
                            "recommendation_shown": self._as_json(decision_output),
                            "previous_hash": stored_prev,
                        }
                        apply_optional_hashed_fields(canonical_entry, {
                            "authorized_by": authorized_by,
                            "decision_hash": decision_hash,
                        })
                    elif record_kind == "observed_event":
                        # The persisted EventV1 stream -- mirrors the
                        # observed_event loop in append_decision(). The event
                        # body is stored verbatim in input_data; the FIXED
                        # canonical form (no optional fields) is built by the
                        # shared observed_event_canonical the writer and the
                        # twin also call, so all three sites stay
                        # byte-identical. Without this branch these rows fall
                        # through to the legacy path and fail their own
                        # verification -- a false DIVERGE indistinguishable
                        # from tampering.
                        canonical_entry = observed_event_canonical(
                            self._as_json(input_data) or {}, stored_prev)
                    else:
                        # Legacy path (append)
                        canonical_entry = {
                            "action_type": action_type,
                            "node": node,
                            "previous_value": previous_value,
                            "applied_value": applied_value,
                            "reason": reason,
                            "data": self._as_json(data),
                            "previous_hash": stored_prev,
                        }

                    # authorized_by_sig rides in EVERY record kind's canonical
                    # form via the shared OPTIONAL_HASHED_FIELDS contract
                    # (D5). Applied here once, at the single recompute point,
                    # rather than threaded through each per-kind source dict
                    # above -- exactly how twin_custody.recompute_current_hash
                    # applies the shared contract to the whole row. Absent
                    # (legacy rows, rows written with no key) -> omitted ->
                    # byte-identical recompute to before this field existed.
                    apply_optional_hashed_fields(
                        canonical_entry,
                        {_AUTHORIZED_BY_SIG_FIELD: authorized_by_sig},
                    )

                    # Recompute hash from canonical form
                    recomputed_hash = hashlib.sha256(
                        json.dumps(canonical_entry, sort_keys=True, default=str).encode()
                    ).hexdigest()

                    # Check for tampering
                    if recomputed_hash != stored_current:
                        violations.append(
                            f"Entry {row_id}: content hash mismatch "
                            f"(stored={stored_current[:8]}..., "
                            f"recomputed={recomputed_hash[:8]}...)"
                        )

                    # Keyed attestation check on the authorized_by claim.
                    # Independent of the SHA-256 chain above: catches an
                    # authorized_by edit even when current_hash was recomputed
                    # to match. Only runs when this deployment holds at least
                    # one attestation key. A NULL signature (unattested row) is
                    # never a violation. INVALID (tampering) and UNKNOWN_KEY
                    # (signed by a key we do not hold in any role) are always
                    # violations; RETIRED_KEY (signed by a deliberately
                    # distrusted key) is a violation only under enforcement.
                    if not _att_keys.is_empty():
                        att_status, att_detail = verify_authorized_by_signature(
                            {
                                "authorized_by": authorized_by,
                                _AUTHORIZED_BY_SIG_FIELD: authorized_by_sig,
                                "previous_hash": stored_prev,
                                "record_kind": record_kind,
                            },
                            _att_keys,
                        )
                        if att_status == _ATT_STATUS_INVALID:
                            violations.append(
                                f"Entry {row_id}: authorized_by attestation "
                                f"invalid ({att_detail})"
                            )
                        elif att_status == _ATT_STATUS_UNKNOWN_KEY:
                            violations.append(
                                f"Entry {row_id}: authorized_by signed by an "
                                f"unrecognised key ({att_detail})"
                            )
                        elif (att_status == _ATT_STATUS_RETIRED_KEY
                              and _att_enforced):
                            violations.append(
                                f"Entry {row_id}: authorized_by signed by a "
                                f"retired key and attestation is enforced "
                                f"({att_detail})"
                            )

                except Exception as e:
                    violations.append(f"Entry {row_id}: hash recomputation failed ({e})")

                prev_hash = stored_current
            
            ok = len(violations) == 0
            
            if mode == "strict" and violations:
                raise Exception(f"Ledger verification failed: {violations}")
            
            return {
                "ok": ok,
                "entries": len(rows),
                "violations": violations
            }
        finally:
            self.pool.putconn(conn)

    def reconstruct_decision(self, row_id: int) -> Dict[str, Any]:
        """Replay a governance_decision from its persisted observed_event
        stream and compare the recomputed assembly against what the row
        recorded. Read-only: opens no write transaction, mutates nothing.

        This is the payoff of persisting the events (append_decision's
        observed_events arg): a decision row on its own can be shown to be
        UNTAMPERED (verify_chain / the twin), but only replaying the
        observations it was built from shows it is REPRODUCIBLE -- that the
        recorded provenance map and estimated-field set are what
        assemble_episode actually produces from those events.

        Verifies the REDUCER layer only. Re-judging tier/score needs the
        governing cassette rebuilt from its snapshot into a judgeable object,
        which is not done here -- see the 'rejudged' key, always False for
        now.

        Returns a dict:
          ok               -- bool: every checked field matched
          reason           -- str: why not, when ok is False
          row_id, episode_id, event_count
          reducer_version_recorded / _current  -- and reducer_drift bool
          checks           -- {field: {"recorded":.., "recomputed":.., "match":bool}}
          rejudged         -- False (documented limitation)
        """
        try:
            from event_v1 import (assemble_episode, make_event,
                                  REDUCER_VERSION)
        except Exception as exc:  # pragma: no cover - import wiring
            return {"ok": False, "reason": f"event_v1 unavailable: {exc}",
                    "row_id": row_id}

        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT record_kind, input_data, cassette_version
                FROM ledger_entries WHERE id = %s
            """, (row_id,))
            drow = cursor.fetchone()
            if drow is None:
                return {"ok": False, "reason": f"no ledger row with id {row_id}",
                        "row_id": row_id}
            record_kind, input_data, cassette_version = drow
            if record_kind != "governance_decision":
                return {"ok": False,
                        "reason": f"row {row_id} is a {record_kind!r}, not a "
                                  f"governance_decision",
                        "row_id": row_id}

            idata = self._as_json(input_data) or {}
            kernel = idata.get("kernel") or {}
            source_events = list(kernel.get("source_events") or [])
            if not source_events:
                return {"ok": False,
                        "reason": "decision row carries no kernel.source_events -- "
                                  "it was written before the observed-event layer, "
                                  "or with observed_events omitted",
                        "row_id": row_id}

            cursor.execute("""
                SELECT input_data FROM ledger_entries
                WHERE record_kind = 'observed_event'
                  AND input_data->>'event_id' = ANY(%s)
            """, (source_events,))
            bodies = [self._as_json(r[0]) or {} for r in cursor.fetchall()]

            missing = set(source_events) - {b.get("event_id") for b in bodies}
            if missing:
                return {"ok": False,
                        "reason": f"observed_event rows missing for "
                                  f"{sorted(missing)!r} -- the stream is incomplete",
                        "row_id": row_id, "event_count": len(bodies)}

            episode_ids = {b.get("episode_id") for b in bodies}
            if len(episode_ids) != 1:
                return {"ok": False,
                        "reason": f"events span episodes {sorted(episode_ids)!r}",
                        "row_id": row_id, "event_count": len(bodies)}
            episode_id = episode_ids.pop()
            domain = (bodies[0].get("domain") if bodies else None) or "unknown"
            reducer_recorded = sorted({b.get("reducer_version") for b in bodies})

            events = [
                make_event(
                    event_id=b["event_id"], episode_id=b["episode_id"],
                    domain=b["domain"], kind=b["kind"],
                    occurred_at=b["occurred_at"], observed_at=b["observed_at"],
                    source=b["source"], provenance=b["provenance"],
                    fields=b.get("fields") or {}, method=b.get("method"),
                    detail=b.get("detail") or {},
                    schema_version=b.get("schema_version", 1),
                    reducer_version=b.get("reducer_version"),
                )
                for b in bodies
            ]

            ep_inputs = kernel.get("episode_inputs") or {}
            assembly = assemble_episode(
                episode_id=episode_id, domain=domain,
                requested=ep_inputs.get("requested") or {},
                events=events,
                outcome_reasons=tuple(ep_inputs.get("outcome_reasons") or ()),
                attributes=ep_inputs.get("attributes") or {},
            )

            checks: Dict[str, Any] = {}
            def _check(name, recorded, recomputed):
                match = recorded == recomputed
                checks[name] = {"recorded": recorded, "recomputed": recomputed,
                                "match": match}
                return match

            _check("source_events",
                   sorted(source_events), sorted(assembly.source_events))
            if "field_provenance" in kernel:
                _check("field_provenance",
                       kernel.get("field_provenance"), assembly.provenance)
            if "estimated_fields" in kernel:
                _check("estimated_fields",
                       sorted(kernel.get("estimated_fields") or []),
                       sorted(assembly.estimated_fields))

            failed = [k for k, v in checks.items() if not v["match"]]
            # An ok verdict requires at least one CONTENT check (not just the
            # event-id list): a row with tampered fields on every event but an
            # intact source_events list would otherwise pass on nothing.
            content_checked = bool({"field_provenance", "estimated_fields"}
                                   & set(checks))
            reducer_drift = (len(reducer_recorded) != 1
                             or reducer_recorded[0] != REDUCER_VERSION)

            if failed:
                ok = False
                reason = (f"replayed assembly diverges from the recorded summary "
                          f"on: {', '.join(failed)}")
            elif not content_checked:
                ok = False
                reason = ("decision summary carries no field_provenance or "
                          "estimated_fields -- the event bodies cannot be "
                          "verified against it, only their ids")
            elif reducer_drift:
                ok = False
                reason = (f"assembly matched, but events were recorded under "
                          f"reducer {reducer_recorded!r} and this build is "
                          f"{REDUCER_VERSION!r} -- recomputed under the newer fold")
            else:
                ok = True
                reason = ""

            return {
                "ok": ok,
                "reason": reason,
                "row_id": row_id,
                "episode_id": episode_id,
                "event_count": len(bodies),
                "content_verified": content_checked and not failed,
                "reducer_version_recorded": reducer_recorded,
                "reducer_version_current": REDUCER_VERSION,
                "reducer_drift": reducer_drift,
                "checks": checks,
                "rejudged": False,
            }
        finally:
            self.pool.putconn(conn)

    def sid_exists(self, call_sid: str) -> bool:
        """Check whether a call with this sid has already been recorded.

        Used by the harness to reject duplicate submissions before any
        processing happens (Option A: hard reject). The partial unique
        index on call_sid (WHERE call_sid IS NOT NULL) is the DB-level
        backstop for races this check can't catch.
        """
        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM ledger_entries WHERE call_sid = %s LIMIT 1;",
                (call_sid,)
            )
            return cursor.fetchone() is not None
        finally:
            self.pool.putconn(conn)

    def episode_decision_exists(self, episode_id: str) -> bool:
        """Check whether a governance_decision for this episode_id has
        already been recorded.

        Same role as sid_exists above, for consumers built on
        GovernanceHarness's domain-agnostic Episode path instead of the
        telephony call_sid column: a redelivery-safe pre-check a caller
        runs BEFORE invoking GovernanceHarness.process() again, so a
        crash-between-commit-and-ack redelivery is recognized as
        already-done rather than written a second time.
        episode_id lives inside the JSONB input_data column
        (GovernanceHarness._write_decision's record shape), not a
        dedicated column the way call_sid is -- there is no unique
        index backstopping this one, so a caller relying on it for
        correctness under real concurrency (not just crash recovery)
        would need one; this is the same pre-check-only posture
        sid_exists documents for its own DB-level backstop.
        """
        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM ledger_entries WHERE record_kind = 'governance_decision' "
                "AND input_data->>'episode_id' = %s LIMIT 1;",
                (episode_id,)
            )
            return cursor.fetchone() is not None
        finally:
            self.pool.putconn(conn)

    def close(self):
        """Close connection pool"""
        self.pool.closeall()
