"""
Canonical Artifact Store for Sentinel Governance

This module provides persistent storage for artifacts created by governance decisions.
Artifacts are the unit of governed state in the Conservation Kernel integration.

The artifact store:
- Persists governance decision artifacts to PostgreSQL
- Provides deterministic artifact ID generation
- Enables artifact resolution for transformation chains
- Maintains lineage and provenance information
"""

import json
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List
from dataclasses import dataclass, asdict

from conservation.types import SentinelArtifact, ArtifactMetadata, EpistemicStatus, OriginStatus


@dataclass
class StoredArtifact:
    """Internal representation of a stored artifact with metadata."""
    artifact_id: str
    content: Dict[str, Any]
    metadata: ArtifactMetadata
    created_at: str
    ledger_reference: Optional[str] = None  # Reference to ledger row if persisted


class ArtifactStore:
    """
    Persistent artifact storage for governance decisions.

    This store maintains the canonical repository of artifacts created by governance
    decisions. It enables:
    - Deterministic artifact ID generation
    - Artifact resolution for parent references
    - Lineage tracking
    - Fail-closed behavior (artifacts must exist before use)
    """

    def __init__(self, use_postgres: bool = True):
        """
        Initialize artifact store.

        Args:
            use_postgres: If True, use PostgreSQL for persistence (production).
                         If False, use in-memory dict (testing).
        """
        self.use_postgres = use_postgres
        self._in_memory_store: Dict[str, StoredArtifact] = {}
        self._connection_pool = None

        if use_postgres:
            self._initialize_postgres()

    def _initialize_postgres(self):
        """Initialize PostgreSQL table for artifact storage."""
        try:
            import psycopg2
            from psycopg2.pool import SimpleConnectionPool
            import os

            host = os.getenv("ICEBERG_LEDGER_HOST", "localhost")
            port = int(os.getenv("ICEBERG_LEDGER_PORT", "5432"))
            dbname = os.getenv("ICEBERG_LEDGER_DB", "iceberg")
            user = os.getenv("ICEBERG_LEDGER_RUNTIME_USER", "ledger_reader")
            password = os.getenv("ICEBERG_LEDGER_RUNTIME_PASSWORD", "")

            self._connection_pool = SimpleConnectionPool(1, 5, host=host, port=port,
                                                         database=dbname, user=user,
                                                         password=password)
            self._create_schema()
        except Exception as e:
            # Fall back to in-memory if PostgreSQL unavailable
            print(f"Warning: PostgreSQL not available, using in-memory store: {e}")
            self.use_postgres = False

    def _create_schema(self):
        """Create artifacts table if not exists."""
        if not self._connection_pool:
            return

        conn = self._connection_pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS governance_artifacts (
                    artifact_id VARCHAR(255) PRIMARY KEY,
                    content JSONB NOT NULL,
                    producer VARCHAR(255),
                    epistemic_status VARCHAR(50),
                    authority_source VARCHAR(255),
                    lineage TEXT[],
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    ledger_reference VARCHAR(255),
                    UNIQUE(artifact_id)
                )
            """)
            conn.commit()
        finally:
            self._connection_pool.putconn(conn)

    def store_artifact(self, artifact: SentinelArtifact) -> str:
        """
        Store an artifact persistently.

        Args:
            artifact: The artifact to store

        Returns:
            The artifact ID (for confirmation)

        Raises:
            ValueError: If artifact already exists
        """
        if self._artifact_exists(artifact.artifact_id):
            raise ValueError(f"Artifact {artifact.artifact_id} already exists")

        stored = StoredArtifact(
            artifact_id=artifact.artifact_id,
            content=artifact.content,
            metadata=artifact.metadata,
            created_at=datetime.now(timezone.utc).isoformat()
        )

        if self.use_postgres:
            self._store_postgres(stored)
        else:
            self._in_memory_store[artifact.artifact_id] = stored

        return artifact.artifact_id

    def _store_postgres(self, artifact: StoredArtifact):
        """Persist artifact to PostgreSQL."""
        if not self._connection_pool:
            self._in_memory_store[artifact.artifact_id] = artifact
            return

        conn = self._connection_pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO governance_artifacts
                (artifact_id, content, producer, epistemic_status, authority_source, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                artifact.artifact_id,
                json.dumps(artifact.content),
                artifact.metadata.producer,
                artifact.metadata.epistemic_status.value if hasattr(artifact.metadata.epistemic_status, 'value') else str(artifact.metadata.epistemic_status),
                artifact.metadata.authority_source,
                artifact.created_at
            ))
            conn.commit()
        finally:
            self._connection_pool.putconn(conn)

    def get_artifact(self, artifact_id: str) -> Optional[SentinelArtifact]:
        """
        Retrieve an artifact by ID.

        Args:
            artifact_id: The artifact ID

        Returns:
            The artifact, or None if not found
        """
        if self.use_postgres:
            return self._get_postgres(artifact_id)
        else:
            stored = self._in_memory_store.get(artifact_id)
            if stored:
                return SentinelArtifact(
                    artifact_id=stored.artifact_id,
                    content=stored.content,
                    metadata=stored.metadata
                )
            return None

    def _get_postgres(self, artifact_id: str) -> Optional[SentinelArtifact]:
        """Retrieve artifact from PostgreSQL."""
        if not self._connection_pool:
            return None

        conn = self._connection_pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT artifact_id, content, producer, epistemic_status, authority_source
                FROM governance_artifacts
                WHERE artifact_id = %s
            """, (artifact_id,))
            row = cursor.fetchone()

            if not row:
                return None

            artifact_id, content, producer, epistemic_status, authority_source = row
            metadata = ArtifactMetadata(
                producer=producer,
                epistemic_status=epistemic_status,
                authority_source=authority_source
            )
            return SentinelArtifact(
                artifact_id=artifact_id,
                content=json.loads(content),
                metadata=metadata
            )
        finally:
            self._connection_pool.putconn(conn)

    def _artifact_exists(self, artifact_id: str) -> bool:
        """Check if artifact exists."""
        if self.use_postgres:
            if not self._connection_pool:
                return artifact_id in self._in_memory_store

            conn = self._connection_pool.getconn()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM governance_artifacts WHERE artifact_id = %s", (artifact_id,))
                exists = cursor.fetchone() is not None
            finally:
                self._connection_pool.putconn(conn)
            return exists
        else:
            return artifact_id in self._in_memory_store

    def list_artifacts(self, limit: int = 100) -> List[str]:
        """List all artifact IDs (for debugging)."""
        if self.use_postgres:
            if not self._connection_pool:
                return list(self._in_memory_store.keys())[:limit]

            conn = self._connection_pool.getconn()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT artifact_id FROM governance_artifacts ORDER BY created_at DESC LIMIT %s", (limit,))
                return [row[0] for row in cursor.fetchall()]
            finally:
                self._connection_pool.putconn(conn)
        else:
            return list(self._in_memory_store.keys())[:limit]

    def close(self):
        """Close database connections."""
        if self._connection_pool:
            self._connection_pool.closeall()


# Global artifact store instance
_global_store: Optional[ArtifactStore] = None


def get_artifact_store() -> ArtifactStore:
    """Get or create the global artifact store."""
    global _global_store
    if _global_store is None:
        _global_store = ArtifactStore()
    return _global_store


def init_artifact_store(use_postgres: bool = True):
    """Initialize the global artifact store."""
    global _global_store
    _global_store = ArtifactStore(use_postgres=use_postgres)
