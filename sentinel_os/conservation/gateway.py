"""
Sentinel-side gateway for Conservation Kernel integration.

This adapter translates Sentinel artifacts into Conservation Kernel format,
submits them for verification, and returns a ConservationReceipt for downstream
use.

Fail-closed: If the Kernel rejects an artifact, the receipt will indicate
rejection and downstream systems MUST NOT accept the artifact.
"""

from typing import Any, Dict, List, Optional, Union
import json
import hashlib
from datetime import datetime

from .types import SentinelArtifact, ArtifactMetadata, AuthorityStatus, EpistemicStatus, OriginStatus
from .receipt import ConservationReceipt, VerificationStatus


class ConservationGatewayError(Exception):
    """Raised when conservation gateway fails."""
    pass


class SentinelConservationGateway:
    """
    Gateway managing artifact flow through Conservation Kernel.

    Responsible for:
    - Converting Sentinel artifacts to Kernel format
    - Invoking ConservationKernel.submit()
    - Wrapping Kernel result in ConservationReceipt
    - Failing closed on rejection
    """

    def __init__(self, kernel_instance: Optional[Any] = None):
        """
        Initialize gateway.

        Args:
            kernel_instance: ConservationKernel instance. If None, will be
                           lazily imported on first use.
        """
        self._kernel = kernel_instance
        self._kernel_lazy_loaded = False

    @property
    def kernel(self) -> Any:
        """Lazy-load Conservation Kernel on first use."""
        if self._kernel is None and not self._kernel_lazy_loaded:
            try:
                from conservation_kernel import ConservationKernel
                self._kernel = ConservationKernel()
                self._kernel_lazy_loaded = True
            except ImportError as e:
                raise ConservationGatewayError(
                    "Conservation Kernel not installed. "
                    "Install with: pip install conservation-kernel"
                ) from e
        return self._kernel

    def submit_artifact(
        self,
        artifact_id: str,
        content: Dict[str, Any],
        authority_source: str,
        epistemic_status: str = "estimated",
        evidence_refs: Optional[List[str]] = None,
        lineage: Optional[List[str]] = None,
        input_artifact_ids: Optional[List[str]] = None,
        transformation_declared: Optional[str] = None,
    ) -> ConservationReceipt:
        """
        Submit a Sentinel artifact through Conservation Kernel.

        This is the primary integration point for Sentinel → Kernel flow.

        Args:
            artifact_id: Unique identifier for this artifact
            content: The artifact content (dict)
            authority_source: System/person authorizing this artifact
            epistemic_status: "verified", "estimated", "inferred", "uncertain"
            evidence_refs: References to supporting evidence
            lineage: Sequence of predecessor artifact IDs
            input_artifact_ids: IDs of artifacts used to create this one
            transformation_declared: Description of transformation applied

        Returns:
            ConservationReceipt suitable for downstream handoff

        Raises:
            ConservationGatewayError: If kernel submission fails
        """
        try:
            metadata = ArtifactMetadata.from_sentinel_artifact(
                artifact_id=artifact_id,
                content=content,
                authority_source=authority_source,
                epistemic_status=epistemic_status,
                evidence_refs=evidence_refs,
                lineage=lineage,
            )

            artifact = SentinelArtifact(
                artifact_id=artifact_id,
                content=content,
                metadata=metadata,
                governance_context={
                    "transformation_declared": transformation_declared,
                    "input_artifact_ids": input_artifact_ids or [],
                },
            )

            # Convert to Conservation Kernel format
            kernel_artifact = self._to_kernel_artifact(artifact)
            kernel_record = self._to_transformation_record(
                artifact_id=artifact_id,
                input_ids=input_artifact_ids or [],
                transformation_declared=transformation_declared,
            )

            # Submit to Kernel
            kernel_result = self.kernel.submit(
                input_artifacts=None,  # Will be provided by input_artifact_ids
                output=kernel_artifact,
                record=kernel_record,
            )

            # Wrap result in receipt
            receipt = self._result_to_receipt(kernel_result, artifact_id)

            return receipt

        except Exception as e:
            raise ConservationGatewayError(
                f"Failed to submit artifact {artifact_id} to Conservation Kernel: {e}"
            ) from e

    def validate_receipt(self, receipt: ConservationReceipt) -> tuple[bool, str]:
        """
        Validate a receipt before downstream handoff.

        Returns:
            (is_valid, reason) tuple
        """
        return receipt.validate_for_handoff()

    def _to_kernel_artifact(self, artifact: SentinelArtifact) -> Dict[str, Any]:
        """Convert SentinelArtifact to Conservation Kernel Artifact format."""
        return {
            "artifact_id": artifact.artifact_id,
            "content": artifact.content,
            "origin": artifact.metadata.origin_status.value,
            "authority": artifact.metadata.authority_source,
            "epistemic_status": artifact.metadata.epistemic_status.value,
            "uncertainty": artifact.metadata.uncertainty,
            "temporal_scope": artifact.metadata.temporal_scope,
            "provenance": artifact.metadata.provenance,
            "lineage": artifact.metadata.lineage,
            "evidence_refs": artifact.metadata.evidence_refs,
            "content_hash": artifact.metadata.content_hash,
            "produced_by": artifact.metadata.producer,
            "created_at": artifact.metadata.created_at,
        }

    def _to_transformation_record(
        self,
        artifact_id: str,
        input_ids: List[str],
        transformation_declared: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a TransformationRecord for Kernel submission."""
        return {
            "transformation_id": f"sentinel-{artifact_id}-{datetime.utcnow().timestamp()}",
            "input_artifact_ids": input_ids,
            "output_artifact_id": artifact_id,
            "transformation_kind": "sentinel_governance",
            "declared_changes": transformation_declared or "artifact produced by Sentinel",
            "evidence_provided": True,
            "actor": "Sentinel",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def _result_to_receipt(
        self,
        kernel_result: Any,
        artifact_id: str,
    ) -> ConservationReceipt:
        """Convert Kernel VerificationResult to ConservationReceipt."""
        # Normalize kernel result to dict if it's an object
        if hasattr(kernel_result, "to_dict"):
            result_dict = kernel_result.to_dict()
        elif isinstance(kernel_result, dict):
            result_dict = kernel_result
        else:
            result_dict = {
                "status": getattr(kernel_result, "status", "fail"),
                "transformation_id": getattr(kernel_result, "transformation_id", ""),
                "input_artifact_ids": getattr(kernel_result, "input_artifact_ids", []),
                "observed_changes": [],
                "violations": [],
                "checked_dimensions": [],
            }

        return ConservationReceipt.from_kernel_result(
            result_dict,
            artifact_id=artifact_id,
            produced_by="Sentinel",
        )


# Module-level convenience function for Sentinel integration
_gateway: Optional[SentinelConservationGateway] = None


def get_gateway() -> SentinelConservationGateway:
    """Get or create the global Sentinel conservation gateway."""
    global _gateway
    if _gateway is None:
        _gateway = SentinelConservationGateway()
    return _gateway


def submit_for_conservation(
    artifact_id: str,
    content: Dict[str, Any],
    authority_source: str,
    **kwargs,
) -> ConservationReceipt:
    """
    Convenience function: submit artifact for conservation.

    Usage:
        receipt = submit_for_conservation(
            artifact_id="my-artifact-1",
            content={"key": "value"},
            authority_source="sentinel_governance",
        )
        if not receipt.accepted:
            raise ValueError(f"Artifact rejected: {receipt.violations}")
    """
    gateway = get_gateway()
    return gateway.submit_artifact(
        artifact_id=artifact_id,
        content=content,
        authority_source=authority_source,
        **kwargs,
    )
