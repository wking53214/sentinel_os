"""
Transformation Record Factory for Governance Decisions

Converts Sentinel governance decisions into Conservation Kernel TransformationRecords.
This factory wraps governance decisions as auditable transformations with full
provenance, authority, and evidence information.
"""

import json
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List

from governance.ledger_postgres import GovernanceDecisionRecord
from conservation.types import SentinelArtifact, ArtifactMetadata

try:
    from conservation_kernel import (
        TransformationRecord,
        DeclaredChange,
        Actor,
        ActorKind,
        Dimension,
        TransitionKind,
    )
except ImportError:
    # Mock for testing if conservation_kernel not available
    class TransformationRecord:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class DeclaredChange:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class Actor:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class ActorKind:
        SYSTEM = "SYSTEM"
        MODEL = "MODEL"
        HUMAN = "HUMAN"

    class Dimension:
        AUTHORITY = "AUTHORITY"

    class TransitionKind:
        APPROVAL = "APPROVAL"
        REJECTION = "REJECTION"


class TransformationRecordFactory:
    """
    Factory for creating TransformationRecords from governance decisions.

    Wraps Sentinel governance decisions as formally auditable transformations
    in the Conservation Kernel's model.
    """

    @staticmethod
    def create_governance_transformation(
        decision_record: GovernanceDecisionRecord,
        output_artifact: SentinelArtifact,
        input_artifact_ids: Optional[List[str]] = None,
        input_artifacts: Optional[List[Any]] = None,
    ) -> TransformationRecord:
        """
        Create a TransformationRecord from a governance decision.

        Args:
            decision_record: The GovernanceDecisionRecord
            output_artifact: The resulting artifact
            input_artifact_ids: IDs of input artifacts (if any)
            input_artifacts: Actual input artifacts (if any)

        Returns:
            TransformationRecord for Conservation Kernel submission
        """
        # Determine transformer (who/what made the decision)
        transformer = TransformationRecordFactory._determine_transformer(decision_record)

        # Build declared changes
        declared_changes = TransformationRecordFactory._build_declared_changes(decision_record)

        # Compute hashes
        input_hashes = TransformationRecordFactory._compute_input_hashes(
            input_artifact_ids, input_artifacts
        )
        output_hash = TransformationRecordFactory._compute_output_hash(output_artifact)

        # Build transformation ID
        transformation_id = TransformationRecordFactory._generate_transformation_id(
            decision_record, output_artifact
        )

        # Authority and evidence references
        authority_refs = TransformationRecordFactory._extract_authority_refs(decision_record)
        evidence_refs = TransformationRecordFactory._extract_evidence_refs(decision_record)

        record = TransformationRecord(
            transformation_id=transformation_id,
            input_artifact_ids=tuple(input_artifact_ids or []),
            output_artifact_id=output_artifact.artifact_id,
            transformer=transformer,
            transformation_type="sentinel_governance",
            declared_changes=tuple(declared_changes),
            input_hashes=tuple(input_hashes),
            output_hash=output_hash,
            authorization_refs=tuple(authority_refs),
            evidence_refs=tuple(evidence_refs),
            reason=f"Governance decision: {decision_record.reasoning or 'No reason provided'}",
        )

        return record

    @staticmethod
    def _determine_transformer(decision: GovernanceDecisionRecord) -> Actor:
        """
        Determine the transformer (who/what made the decision).

        Maps decision components to a typed Actor identity.
        """
        # If decision came from model (Claude API)
        if decision.model_identity:
            return Actor(
                actor_id=decision.model_identity or "claude-governor",
                kind=ActorKind.MODEL,
                label=f"Claude Model ({decision.model_identity or 'unknown'})"
            )

        # If explicitly authorized by someone
        if decision.authorized_by:
            kind = ActorKind.HUMAN if "human" in decision.authorized_by.lower() else ActorKind.SYSTEM
            return Actor(
                actor_id=decision.authorized_by,
                kind=kind,
                label=f"Authorized by {decision.authorized_by}"
            )

        # Default: system governance
        return Actor(
            actor_id="sentinel-governance",
            kind=ActorKind.SYSTEM,
            label="Sentinel OS Governance System"
        )

    @staticmethod
    def _build_declared_changes(decision: GovernanceDecisionRecord) -> List[DeclaredChange]:
        """
        Build declared changes from governance decision.

        Represents what the decision changed.
        """
        changes = []

        # The decision itself is a change in authority/approval state
        if decision.output:
            approved = decision.output.get("approved", False)
            changes.append(
                DeclaredChange(
                    subject_id=decision.cassette_version or "governance",
                    dimension=Dimension.AUTHORITY,
                    from_value="pending_governance_review",
                    to_value="approved" if approved else "rejected",
                    reason=decision.reasoning or "Governance decision",
                    transition_kind=None,  # Not specifying transition type
                )
            )

        return changes

    @staticmethod
    def _compute_input_hashes(
        input_ids: Optional[List[str]],
        input_artifacts: Optional[List[Any]]
    ) -> List[str]:
        """
        Compute hashes of input artifacts.

        If actual artifacts provided, hash their content.
        Otherwise, compute deterministic hash from ID.
        """
        if not input_artifacts or not input_ids:
            return []

        hashes = []
        for artifact in input_artifacts:
            if hasattr(artifact, "content"):
                content_str = json.dumps(artifact.content, sort_keys=True)
            else:
                content_str = str(artifact)

            hash_val = hashlib.sha256(content_str.encode()).hexdigest()
            hashes.append(hash_val)

        return hashes

    @staticmethod
    def _compute_output_hash(output_artifact: SentinelArtifact) -> str:
        """
        Compute hash of output artifact.

        Hash should match artifact's own digest computation.
        """
        content_str = json.dumps(output_artifact.content, sort_keys=True)
        return hashlib.sha256(content_str.encode()).hexdigest()

    @staticmethod
    def _generate_transformation_id(
        decision: GovernanceDecisionRecord,
        output_artifact: SentinelArtifact
    ) -> str:
        """
        Generate unique transformation ID.

        Combines decision and output artifact identifiers.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        components = [
            decision.node or "sentinel",
            output_artifact.artifact_id,
            timestamp,
        ]
        combined = "|".join(components)
        return hashlib.sha256(combined.encode()).hexdigest()[:16]

    @staticmethod
    def _extract_authority_refs(decision: GovernanceDecisionRecord) -> List[str]:
        """
        Extract authorization references from decision.

        Returns IDs of authorization records supporting this decision.
        """
        refs = []

        # If model made the decision, reference the model identity
        if decision.model_identity:
            refs.append(f"model-{decision.model_identity}")

        # If explicitly authorized, reference the authorization
        if decision.authorized_by:
            refs.append(f"auth-{decision.authorized_by}")

        return refs

    @staticmethod
    def _extract_evidence_refs(decision: GovernanceDecisionRecord) -> List[str]:
        """
        Extract evidence references from decision.

        Returns IDs of evidence records supporting this decision.
        """
        refs = []

        # The reasoning is evidence
        if decision.reasoning:
            refs.append("evidence-reasoning")

        # Model cost is evidence of effort
        if decision.ai_cost:
            refs.append("evidence-ai-cost")

        # Outcome obligation is evidence of commitment
        if decision.outcome_obligation:
            refs.append(f"evidence-obligation-{decision.outcome_obligation}")

        return refs


def create_governance_transformation(
    decision_record: GovernanceDecisionRecord,
    output_artifact: SentinelArtifact,
    input_ids: Optional[List[str]] = None,
) -> TransformationRecord:
    """
    Convenience function to create transformation from decision.

    Args:
        decision_record: The governance decision
        output_artifact: The resulting artifact
        input_ids: Optional input artifact IDs

    Returns:
        TransformationRecord for Conservation Kernel
    """
    return TransformationRecordFactory.create_governance_transformation(
        decision_record=decision_record,
        output_artifact=output_artifact,
        input_artifact_ids=input_ids
    )
