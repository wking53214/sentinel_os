"""
Conservation receipt issued by the Conservation Kernel.

A receipt proves that an artifact has passed through the Conservation Kernel
and that protected distinctions (origin, authority, epistemic status, etc.)
have been verified to be intact.
"""

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum
from datetime import datetime


class VerificationStatus(str, Enum):
    """Result of Conservation Kernel verification."""
    PASS = "pass"
    PASS_WITH_DECLARED_TRANSFORMATION = "pass_with_declared_transformation"
    FAIL = "fail"


@dataclass(frozen=True)
class ObservedChange:
    """A change observed between input and output artifacts."""
    subject_id: str
    dimension: str  # e.g., "content", "authority", "epistemic_status"
    before: Any
    after: Any

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "dimension": self.dimension,
            "before": self.before,
            "after": self.after,
        }


@dataclass(frozen=True)
class Violation:
    """A violation of a protected distinction."""
    code: str  # e.g., "silent_authority_change"
    dimension: Optional[str]
    subject_id: Optional[str]
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "dimension": self.dimension,
            "subject_id": self.subject_id,
            "detail": self.detail,
        }


@dataclass
class ConservationReceipt:
    """
    Proof that an artifact has passed through Conservation Kernel verification.

    This receipt MUST accompany any artifact passed from Sentinel to GSA-815.
    GSA-815 MUST validate this receipt before accepting the artifact.
    """
    # Artifact identity
    artifact_id: str
    input_artifact_ids: Tuple[str, ...] = field(default_factory=tuple)

    # Transformation identity
    transformation_id: str = ""

    # Verification result
    verification_status: VerificationStatus = VerificationStatus.FAIL

    # Kernel identity
    kernel_version: str = "0.1.0"

    # Protected distinctions verified
    origin: str = "unknown"
    authority: str = "unknown"
    epistemic_status: str = "unknown"
    uncertainty: Optional[float] = None
    temporal_scope: str = "instant"

    # Provenance & lineage
    provenance: str = ""
    lineage: Tuple[str, ...] = field(default_factory=tuple)
    evidence_refs: Tuple[str, ...] = field(default_factory=tuple)

    # Content integrity
    content_hash: str = ""
    artifact_content: str = ""  # Actual artifact content (for return path)

    # Producer information
    produced_by: str = "Sentinel"  # System that produced the artifact
    producer: str = "sentinel"  # Actor ID of the original producer
    conserved_at: str = ""  # ISO8601 when kernel processed it

    # Verification details
    observed_changes: Tuple[ObservedChange, ...] = field(default_factory=tuple)
    violations: Tuple[Violation, ...] = field(default_factory=tuple)
    checked_dimensions: Tuple[str, ...] = field(default_factory=tuple)
    unverifiable_properties: Tuple[str, ...] = field(default_factory=tuple)

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        """Whether the kernel accepted this transformation."""
        return self.verification_status in {
            VerificationStatus.PASS,
            VerificationStatus.PASS_WITH_DECLARED_TRANSFORMATION,
        }

    @property
    def rejected(self) -> bool:
        """Whether the kernel rejected this transformation."""
        return self.verification_status == VerificationStatus.FAIL

    @property
    def has_violations(self) -> bool:
        """Whether any violations were detected."""
        return len(self.violations) > 0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize receipt to JSON-safe dict."""
        return {
            "artifact_id": self.artifact_id,
            "input_artifact_ids": list(self.input_artifact_ids),
            "transformation_id": self.transformation_id,
            "verification_status": self.verification_status.value,
            "kernel_version": self.kernel_version,
            "origin": self.origin,
            "authority": self.authority,
            "epistemic_status": self.epistemic_status,
            "uncertainty": self.uncertainty,
            "temporal_scope": self.temporal_scope,
            "provenance": self.provenance,
            "lineage": list(self.lineage),
            "evidence_refs": list(self.evidence_refs),
            "content_hash": self.content_hash,
            "artifact_content": self.artifact_content,
            "produced_by": self.produced_by,
            "producer": self.producer,
            "conserved_at": self.conserved_at,
            "observed_changes": [c.to_dict() for c in self.observed_changes],
            "violations": [v.to_dict() for v in self.violations],
            "checked_dimensions": list(self.checked_dimensions),
            "unverifiable_properties": list(self.unverifiable_properties),
            "metadata": self.metadata,
        }

    def validate_for_handoff(self) -> tuple[bool, str]:
        """
        Validate that this receipt is valid for artifact handoff.

        Returns (is_valid, reason)
        """
        if self.rejected:
            return False, f"Kernel rejected this transformation: {self.violations}"

        if not self.accepted:
            return False, f"Kernel verification status is {self.verification_status.value}"

        if not self.content_hash:
            return False, "Receipt missing content hash"

        if not self.artifact_id:
            return False, "Receipt missing artifact ID"

        return True, "Receipt is valid for handoff"

    @staticmethod
    def from_kernel_result(
        kernel_result: Dict[str, Any],
        artifact_id: str,
        produced_by: str = "Sentinel",
        artifact_content: str = "",
        producer: str = "sentinel",
    ) -> "ConservationReceipt":
        """
        Create a receipt from a ConservationKernel.submit() result.

        Args:
            kernel_result: VerificationResult from Kernel.submit()
            artifact_id: ID of the artifact being conserved
            produced_by: System that produced the original artifact

        Returns:
            ConservationReceipt ready for downstream handoff
        """
        status_str = kernel_result.get("status", "fail")
        status_map = {
            "pass": VerificationStatus.PASS,
            "pass_with_declared_transformation": VerificationStatus.PASS_WITH_DECLARED_TRANSFORMATION,
            "fail": VerificationStatus.FAIL,
        }
        status = status_map.get(status_str, VerificationStatus.FAIL)

        observed_changes = tuple(
            ObservedChange(**change)
            for change in kernel_result.get("observed_changes", [])
        )

        violations = tuple(
            Violation(**violation)
            for violation in kernel_result.get("violations", [])
        )

        return ConservationReceipt(
            artifact_id=artifact_id,
            input_artifact_ids=tuple(kernel_result.get("input_artifact_ids", [])),
            transformation_id=kernel_result.get("transformation_id", ""),
            verification_status=status,
            kernel_version=kernel_result.get("kernel_version", "0.1.0"),
            origin=kernel_result.get("origin", "unknown"),
            authority=kernel_result.get("authority", "unknown"),
            epistemic_status=kernel_result.get("epistemic_status", "unknown"),
            uncertainty=kernel_result.get("uncertainty"),
            temporal_scope=kernel_result.get("temporal_scope", "instant"),
            provenance=kernel_result.get("provenance", ""),
            lineage=tuple(kernel_result.get("lineage", [])),
            evidence_refs=tuple(kernel_result.get("evidence_refs", [])),
            content_hash=kernel_result.get("content_hash", ""),
            artifact_content=artifact_content,  # Actual artifact content for return path
            produced_by=produced_by,
            producer=producer,  # Original producer actor ID
            conserved_at=datetime.utcnow().isoformat() + "Z",
            observed_changes=observed_changes,
            violations=violations,
            checked_dimensions=tuple(kernel_result.get("checked_dimensions", [])),
            unverifiable_properties=tuple(kernel_result.get("unverifiable_properties", [])),
            metadata=kernel_result.get("metadata", {}),
        )
