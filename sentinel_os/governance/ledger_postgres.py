"""
PostgreSQL Ledger Adapter - Production-grade persistent audit trail

Replaces LocalDiskAdapter with real database: transactions, durability, ACID
"""

import json
import hashlib
from typing import List, Dict, Optional
import psycopg2
from psycopg2.pool import SimpleConnectionPool

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
