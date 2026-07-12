"""
PostgreSQL Ledger Adapter - Production-grade persistent audit trail

Replaces LocalDiskAdapter with real database: transactions, durability, ACID
"""

import json
import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import psycopg2
from psycopg2.pool import SimpleConnectionPool


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

class PostgreSQLLedger:
    """Production ledger backed by PostgreSQL"""
    
    def __init__(self, host: str = "localhost", port: int = 5432, 
                 dbname: str = "iceberg", user: str = "iceberg", 
                 password: str = "iceberg", min_connections: int = 1, max_connections: int = 10):
        """Initialize connection pool"""
        
        self.pool = SimpleConnectionPool(
            min_connections, max_connections,
            host=host, port=port, database=dbname,
            user=user, password=password
        )
        self._initialize_schema()
    
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
            conn.commit()
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
    
    def append_decision(self, record: GovernanceDecisionRecord) -> bool:
        """Append one structured governance decision (transaction).

        TRIPWIRE: a decision without a cassette_version is an error,
        not a warning. The whole point of the record is "which policy
        governed this" -- a row that cannot answer that is refused
        before it ever touches the chain. Same for the policy snapshot
        itself.

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
            current_hash = hashlib.sha256(
                json.dumps(canonical_entry, sort_keys=True, default=str).encode()
            ).hexdigest()

            cursor.execute("""
                INSERT INTO ledger_entries
                (action_type, node, previous_value, applied_value, reason,
                 previous_hash, current_hash, data,
                 record_kind, cassette_version, input_data, policy_parameters,
                 decision_output)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (record.action_type, record.node, record.previous_value,
                  record.applied_value, record.reasoning,
                  previous_hash, current_hash, json.dumps(data),
                  "governance_decision", record.cassette_version,
                  json.dumps(record.input_data),
                  json.dumps(record.policy_parameters),
                  json.dumps(record.output)))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    @staticmethod
    def _as_json(raw):
        if isinstance(raw, (dict, list)):
            return raw
        if raw:
            return json.loads(raw)
        return {}

    def get_decisions(self, cassette_version: Optional[str] = None,
                      limit: int = 100) -> List[Dict]:
        """Retrieve structured governance decisions, newest first.

        "Show me every decision this cassette version governed" is one
        call (and one SQL query -- see CASSETTE_GOVERNS_INTEGRATION)."""

        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            query = """
                SELECT id, timestamp, action_type, node, previous_value,
                       applied_value, reason, previous_hash, current_hash,
                       cassette_version, input_data, policy_parameters,
                       decision_output
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
    
    def verify_chain(self, mode: str = "strict") -> Dict:
        """Verify ledger integrity (hash chain validation)"""
        
        conn = self.pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, previous_hash, current_hash
                FROM ledger_entries
                ORDER BY id ASC
            """)
            
            rows = cursor.fetchall()
            
            if not rows:
                return {"ok": True, "entries": 0, "violations": []}
            
            violations = []
            prev_hash = "genesis"
            
            for row_id, stored_prev, stored_current in rows:
                if stored_prev != prev_hash:
                    violations.append(f"Entry {row_id}: chain broken")
                
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
    
    def close(self):
        """Close connection pool"""
        self.pool.closeall()
