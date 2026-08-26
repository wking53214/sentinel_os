"""
Artifact Factory for Governance Decisions

Converts Sentinel governance decisions into Conservation Kernel artifacts.
This factory ensures governance decisions are represented as properly typed,
verifiable artifacts with full provenance information.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from governance.ledger_postgres import GovernanceDecisionRecord
from conservation.types import SentinelArtifact, ArtifactMetadata, EpistemicStatus, OriginStatus


class ArtifactFactory:
    """
    Factory for creating artifacts from governance decisions.

    Transforms GovernanceDecisionRecord (Sentinel's governance model)
    into SentinelArtifact (Conservation Kernel's model).
    """

    @staticmethod
    def create_governance_artifact(
        decision_record: GovernanceDecisionRecord,
        artifact_id: Optional[str] = None,
        parent_artifact_ids: Optional[list] = None,
        governance_context: Optional[Dict[str, Any]] = None,
    ) -> SentinelArtifact:
        """
        Create an artifact from a governance decision.

        Args:
            decision_record: The GovernanceDecisionRecord
            artifact_id: Override artifact ID (default: generated from decision)
            parent_artifact_ids: Parent artifacts this decision depends on
            governance_context: Additional governance metadata

        Returns:
            SentinelArtifact representing the governance decision
        """
        # Generate deterministic artifact ID if not provided
        if artifact_id is None:
            artifact_id = ArtifactFactory._generate_artifact_id(decision_record)

        # Build artifact content from decision
        content = ArtifactFactory._build_artifact_content(decision_record)

        # Determine authority source from decision
        authority_source = ArtifactFactory._determine_authority_source(decision_record)

        # Determine epistemic status from decision
        epistemic_status = ArtifactFactory._determine_epistemic_status(decision_record)

        # Build metadata
        metadata = ArtifactMetadata(
            producer=decision_record.node or "sentinel-governance",
            epistemic_status=epistemic_status,
            authority_source=authority_source,
            lineage=parent_artifact_ids or [],
            evidence_refs=[]  # Could be populated from decision if available
        )

        artifact = SentinelArtifact(
            artifact_id=artifact_id,
            content=content,
            metadata=metadata,
            governance_context=governance_context or {}
        )

        return artifact

    @staticmethod
    def _generate_artifact_id(decision: GovernanceDecisionRecord) -> str:
        """
        Generate deterministic artifact ID from decision.

        Ensures same decision always produces same ID (idempotent).
        """
        import hashlib

        # Use decision components that are immutable
        components = [
            str(decision.node or ""),
            str(decision.cassette_version or ""),
            str(decision.output or {}),
            str(decision.reasoning or ""),
        ]

        combined = "|".join(components)
        hash_digest = hashlib.sha256(combined.encode()).hexdigest()

        return f"governance-{hash_digest[:16]}"

    @staticmethod
    def _build_artifact_content(decision: GovernanceDecisionRecord) -> Dict[str, Any]:
        """
        Build artifact content from governance decision.

        This is the actual "governed state" - what was decided and why.
        """
        return {
            "decision_type": decision.action_type or "governance_decision",
            "node": decision.node,
            "cassette_version": decision.cassette_version,
            "input_data": decision.input_data or {},
            "policy_parameters": decision.policy_parameters or {},
            "reasoning": decision.reasoning or "",
            "output": decision.output or {},
            "model_identity": decision.model_identity,
            "ai_cost": decision.ai_cost,
            "outcome_obligation": decision.outcome_obligation,
            "decision_timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _determine_authority_source(decision: GovernanceDecisionRecord) -> str:
        """
        Determine authority source from governance decision.

        Maps decision components to authority identity.
        """
        # If explicitly authorized by someone, use that
        if decision.authorized_by:
            return decision.authorized_by

        # If model made the decision (Claude API)
        if decision.model_identity:
            return "governor_claude_api"

        # Default: system governance
        return "sentinel-governance"

    @staticmethod
    def _determine_epistemic_status(decision: GovernanceDecisionRecord) -> EpistemicStatus:
        """
        Determine epistemic status of decision.

        Reflects confidence in the decision.
        """
        # If decision came from model (Claude), it's estimated
        if decision.model_identity:
            return EpistemicStatus.ESTIMATED

        # If it came from a regulatory system, it's verified
        if decision.authorized_by and "regulatory" in decision.authorized_by.lower():
            return EpistemicStatus.VERIFIED

        # Default: estimated (safe assumption for governance)
        return EpistemicStatus.ESTIMATED


def create_governance_artifact_from_decision(
    decision_record: GovernanceDecisionRecord,
    artifact_id: Optional[str] = None,
    parent_ids: Optional[list] = None,
) -> SentinelArtifact:
    """
    Convenience function to create artifact from decision.

    Args:
        decision_record: The governance decision
        artifact_id: Optional artifact ID override
        parent_ids: Optional parent artifact IDs

    Returns:
        Artifact representation of the decision
    """
    return ArtifactFactory.create_governance_artifact(
        decision_record=decision_record,
        artifact_id=artifact_id,
        parent_artifact_ids=parent_ids
    )
