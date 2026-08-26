"""Type bridges between Sentinel artifacts and Conservation Kernel model."""

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional, List
from enum import Enum
from datetime import datetime
import hashlib
import json


class EpistemicStatus(str, Enum):
    """Epistemic status of a proposition or artifact."""
    VERIFIED = "verified"
    ESTIMATED = "estimated"
    INFERRED = "inferred"
    UNCERTAIN = "uncertain"


class AuthorityStatus(str, Enum):
    """Authority status for an artifact."""
    AUTHORIZED = "authorized"
    UNAUTHORIZED = "unauthorized"
    PROVISIONAL = "provisional"
    INHERITED = "inherited"


class OriginStatus(str, Enum):
    """Origin status of an artifact."""
    NATIVE = "native"
    IMPORTED = "imported"
    RECONSTRUCTED = "reconstructed"
    UNKNOWN = "unknown"


@dataclass
class ArtifactMetadata:
    """Metadata about an artifact from Sentinel perspective."""
    artifact_id: str
    producer: str  # e.g., "Sentinel"
    created_at: str  # ISO8601
    content_hash: str  # SHA256 of content
    authority_source: str
    epistemic_status: EpistemicStatus = EpistemicStatus.ESTIMATED
    origin_status: OriginStatus = OriginStatus.NATIVE
    provenance: str = "sentinel_governed"
    evidence_refs: List[str] = field(default_factory=list)
    lineage: List[str] = field(default_factory=list)
    temporal_scope: str = "instant"
    uncertainty: Optional[float] = None  # 0-1, None = not specified

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_sentinel_artifact(
        artifact_id: str,
        content: Any,
        authority_source: str,
        epistemic_status: str = "estimated",
        evidence_refs: Optional[List[str]] = None,
        lineage: Optional[List[str]] = None,
    ) -> "ArtifactMetadata":
        """Create metadata from a Sentinel-produced artifact."""
        content_bytes = json.dumps(content, sort_keys=True, default=str).encode('utf-8')
        content_hash = hashlib.sha256(content_bytes).hexdigest()

        return ArtifactMetadata(
            artifact_id=artifact_id,
            producer="Sentinel",
            created_at=datetime.utcnow().isoformat() + "Z",
            content_hash=content_hash,
            authority_source=authority_source,
            epistemic_status=EpistemicStatus(epistemic_status),
            evidence_refs=evidence_refs or [],
            lineage=lineage or [],
        )


@dataclass
class SentinelArtifact:
    """Artifact as produced by Sentinel, ready for conservation."""
    artifact_id: str
    content: Dict[str, Any]
    metadata: ArtifactMetadata
    governance_context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "content": self.content,
            "metadata": self.metadata.to_dict(),
            "governance_context": self.governance_context,
        }

    def content_hash(self) -> str:
        """Recompute content hash to verify integrity."""
        content_bytes = json.dumps(self.content, sort_keys=True, default=str).encode('utf-8')
        return hashlib.sha256(content_bytes).hexdigest()

    def verify_hash(self) -> bool:
        """Verify that recorded hash matches current content."""
        return self.content_hash() == self.metadata.content_hash
