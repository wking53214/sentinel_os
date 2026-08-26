"""
Sentinel OS → PERCEIVE Integration

Wires Sentinel artifacts into the central PERCEIVE governance orchestrator.
"""

import sys
import os

# Add observe-perceive to path for import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../..', 'observe-perceive'))

from governance_orchestrator import GovernanceOrchestrator
from sentinel_os.conservation.gateway import SentinelConservationGateway
from sentinel_os.conservation.types import SentinelArtifact


class SentinelPerceiveIntegration:
    """Integrates Sentinel artifacts with PERCEIVE governance."""

    def __init__(self, orchestrator: GovernanceOrchestrator):
        """
        Initialize integration with orchestrator.

        Args:
            orchestrator: The central GovernanceOrchestrator
        """
        self.orchestrator = orchestrator

    def govern_sentinel_artifact(
        self,
        artifact: SentinelArtifact,
        operation_type: str,
        gsa815_operation_func,
        vitals_snapshot=None,
        context=None
    ) -> dict:
        """
        Route Sentinel artifact through PERCEIVE governance.

        Args:
            artifact: SentinelArtifact to govern
            operation_type: "escalate", "modify", "export", "override"
            gsa815_operation_func: Function to execute if approved
            vitals_snapshot: Optional vitals for OBSERVE monitoring
            context: Additional context

        Returns:
            Complete governance decision with audit chain
        """
        return self.orchestrator.orchestrate_request(
            artifact,
            operation_type,
            gsa815_operation_func,
            vitals_snapshot,
            context
        )

    def validate_governance_chain(self, result: dict) -> bool:
        """
        Validate complete governance chain.

        Args:
            result: Result from governance flow

        Returns:
            True if audit chain is valid
        """
        if result["status"] != "APPROVED_AND_EXECUTED":
            return False

        forensic_proof = result.get("forensic_proof")
        if not forensic_proof:
            return False

        return forensic_proof.get("chain_valid", False)
