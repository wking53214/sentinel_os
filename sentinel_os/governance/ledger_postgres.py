"""
PostgreSQL Ledger Adapter - Production-grade persistent audit trail

Replaces LocalDiskAdapter with real database: transactions, durability, ACID
"""

import json
import hashlib
import os
from canonical_fields import apply_optional_hashed_fields
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
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
    # Item 7: resolved authorizing identity -- an API-key NAME or service
    #   identity (e.g. "harness:production"), never a raw key and never PII.
    authorized_by: Optional[str] = None
    # Item 6: for a supersession row, the current_hash of the row it
    #   supersedes -- proving the reviewer saw the actual decision. NULL on
    #   ordinary governance_decision rows.
    supersedes_hash: Optional[str] = None

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
        """
        from psycopg2 import sql
        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            try:
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
    
    def _initialize_schema(self):
        """Create ledger table if not exists"""
        
        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ledger_entries (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
            # In-place migration for structured governance decisions.
            # Legacy rows keep their shape (columns stay NULL); new
            # decision rows fill them. The hash chain is shared: legacy
            # append() and structured append_decision() interleave on
            # one chain, each hashing its own canonical form.
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
            # Idempotency: store the raw Twilio sid so duplicate
            # submissions can be rejected before processing. UNIQUE
            # constraint on the column itself is the last-resort guard
            # (catches races the application-level check can't); the
            # normal path rejects earlier via sid_exists(). Nullable
            # so legacy/non-Twilio rows don't collide.
            cursor.execute("""
                ALTER TABLE ledger_entries
                    ADD COLUMN IF NOT EXISTS call_sid VARCHAR(100);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_call_sid
                    ON ledger_entries(call_sid)
                    WHERE call_sid IS NOT NULL;
            """)
            conn.commit()
        finally:
            self.pool.putconn(conn)

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
                       governance_params: Optional[Any] = None) -> bool:
        """Append one structured governance decision (transaction).

        TRIPWIRE: a decision without a cassette_version is an error,
        not a warning. The whole point of the record is "which policy
        governed this" -- a row that cannot answer that is refused
        before it ever touches the chain. Same for the policy snapshot
        itself.

        NEW: governance_params (GovernanceParameters from cassette_schema.py)
        is required. We serialize and hash the cassette at decision time
        so regulators can reconstruct it and prove it hasn't been changed.

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
            optional_source = {
                "cassette_hash": cassette_hash,
                "cassette_code_hash": record.cassette_code_hash,
                "model_identity": record.model_identity,
                "authorized_by": record.authorized_by,
                "supersedes_hash": record.supersedes_hash,
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
                 supersedes_id, supersedes_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s)
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
                  getattr(record, "supersedes_id", None), record.supersedes_hash))
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
            apply_optional_hashed_fields(canonical_entry, {
                "cassette_hash": cassette_hash,
                "cassette_code_hash": cassette_code_hash,
                "authorized_by": authorized_by,
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
                 authorized_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, ("cassette_binding", cassette_version, 0.0, 0.0,
                  "cassette version->content binding",
                  previous_hash, current_hash, json.dumps(data),
                  "cassette_binding", cassette_version, cassette_hash,
                  cassette_code_hash, authorized_by))
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
            apply_optional_hashed_fields(canonical_entry, {
                "supersedes_hash": orig_hash,
                "authorized_by": authority,
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
                 authorized_by, supersedes_id, supersedes_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, ("decision_supersession", "supersession", 0.0, 0.0, reason,
                  previous_hash, current_hash, json.dumps(data),
                  "decision_supersession", version,
                  json.dumps(corrected_output),
                  authority, supersedes_id, orig_hash))
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
                       decision_output, cassette_snapshot, cassette_hash
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
                })
            return decisions
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
        """
        
        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            # Fetch all columns needed to reconstruct canonical forms
            cursor.execute("""
                SELECT id, record_kind, previous_hash, current_hash,
                       action_type, node, previous_value, applied_value, reason,
                       data, cassette_version, input_data, policy_parameters,
                       decision_output, cassette_hash,
                       cassette_code_hash, model_identity, authorized_by,
                       supersedes_id, supersedes_hash
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
                 supersedes_id, supersedes_hash) = row
                
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

    def close(self):
        """Close connection pool"""
        self.pool.closeall()
