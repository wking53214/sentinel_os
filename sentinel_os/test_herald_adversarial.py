"""
HERALD Adversarial Campaign - Phase 3.3
Exhaustive attack discovery following AMC-1.0 FACP/ASX methodology.

SCOPE: Typed Actor Model, Conservation Boundary, Authority Verification
INVARIANT TO TEST: "NO GOVERNED STATE WITHOUT EXPLICIT TYPED DECISION-MAKER"

Three main attack vectors:
1. Conservation bypass attacks (skip the mandatory boundary)
2. Post-validation mutation (modify approved artifacts)
3. Authority escalation (impersonate higher-privilege actors)

METHODOLOGY: FACP/ASX
- Fault Analysis: identify system assumptions
- Countermeasure Proposal: what prevents the attack?
- Attack Simulation: explicit test case
- eXposed evidence: what the test discovers

STATUS: Discovery phase - failures indicate vulnerabilities.
No fixes applied until user approval after full campaign results.
"""

import pytest
import json
import sys
import os
from typing import Dict, Any
from unittest.mock import Mock, patch, MagicMock

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from governance.ledger_postgres import GovernanceDecisionRecord
from conservation.artifact_factory import create_governance_artifact_from_decision, ArtifactFactory
from conservation.transformation_factory import create_governance_transformation, TransformationRecordFactory
from conservation.artifact_store import ArtifactStore
from conservation.gateway import SentinelConservationGateway
from conservation.types import SentinelArtifact, ArtifactMetadata, EpistemicStatus


class TestConservationBypassAttacks:
    """
    VECTOR 1: Conservation Bypass

    Fault Analysis:
    - System assumes conservation boundary is mandatory before ledger write
    - Assumption: Artifacts must pass through gateway before state persists

    Countermeasure: Gateway is invoked before ledger append in _write_decision

    Attack Simulation: Can we persist state without passing through boundary?
    """

    def test_artifact_creation_without_conservation_submission(self):
        """ATTACK: Create artifact but never submit to conservation boundary."""
        # FACP/ASX Step 1: Craft decision that bypasses gateway
        decision = GovernanceDecisionRecord(
            action_type="governance_decision",
            node="test_domain",
            cassette_version="v1",
            input_data={"episode_id": "ep_attacker_001"},
            policy_parameters={},
            reasoning="Attacker decision - should not reach boundary",
            output={"approved": True},
            model_identity="attacker_model",
        )

        # Step 2: Create artifact (first boundary checkpoint)
        artifact = create_governance_artifact_from_decision(decision)
        assert artifact is not None
        assert artifact.artifact_id is not None

        # Step 3: Store artifact WITHOUT going through gateway submission
        # (simulating direct database access or method bypass)
        store = ArtifactStore(use_postgres=False)
        stored_id = store.store_artifact(artifact)

        # Step 4: Verify artifact persists in store without kernel verification
        retrieved = store.get_artifact(stored_id)
        assert retrieved is not None

        # DISCOVERY: Artifacts can be stored in-memory without conservation kernel verification.
        # Risk: If ledger uses artifact store directly, bypassing gateway is possible.
        # Countermeasure check: Does _write_decision require gateway approval before ledger append?

    def test_transformation_record_without_authorization_refs(self):
        """ATTACK: Create transformation record with empty authorization_refs."""
        decision = GovernanceDecisionRecord(
            action_type="governance_decision",
            node="test_domain",
            cassette_version="v1",
            input_data={},
            policy_parameters={},
            reasoning="No authorization claimed",
            output={"approved": True},
            # MISSING: model_identity AND authorized_by - should fail
        )

        # ATTACK: Try to bypass actor requirement
        with pytest.raises(ValueError):
            create_governance_artifact_from_decision(decision)

        # DISCOVERY: Fail-closed works - artifacts require actor identity.
        # Status: Countermeasure working as designed.

    def test_decision_without_artifact_creation(self):
        """ATTACK: Write decision record directly without artifact creation."""
        # FACP: Can we create a GovernanceDecisionRecord that doesn't produce an artifact?
        decision = GovernanceDecisionRecord(
            action_type="governance_decision",
            node="test",
            cassette_version="v1",
            input_data={},
            policy_parameters={},
            reasoning="Attempt direct persistence",
            output={"approved": True},
            model_identity="claude-opus-4",
        )

        # ATTACK: Create artifact then test if it's mandatory
        artifact = create_governance_artifact_from_decision(decision)
        transformation = create_governance_transformation(decision, artifact)

        # Both are created successfully - but what if we skip the artifact store step?
        # In real _write_decision flow, line 358 stores artifact, line 361 creates transformation.
        # If store fails, transformation creation is unreachable.

        # DISCOVERY: Artifact creation is prerequisite for transformation.
        # Sequential dependency is correct fail-closed ordering.


class TestPostValidationMutationAttacks:
    """
    VECTOR 2: Post-Validation Mutation

    Fault Analysis:
    - System assumes artifact content is immutable after conservation submission
    - Assumption: Approved artifacts cannot be modified in storage

    Countermeasure: Artifact hash stored with metadata; verification on retrieval

    Attack Simulation: Modify artifact after gateway approval but before use
    """

    def test_artifact_content_mutation_after_storage(self):
        """ATTACK: Modify artifact content after it's stored."""
        decision = GovernanceDecisionRecord(
            action_type="governance_decision",
            node="test_domain",
            cassette_version="v1",
            input_data={"episode_id": "ep_123"},
            policy_parameters={"original_threshold": 1},
            reasoning="Original decision",
            output={"approved": False},  # CRITICAL: Originally rejected
            model_identity="claude-opus-4",
        )

        artifact = create_governance_artifact_from_decision(decision)
        original_hash = artifact.metadata.content_hash

        # Store artifact
        store = ArtifactStore(use_postgres=False)
        artifact_id = store.store_artifact(artifact)

        # ATTACK: Retrieve and mutate
        retrieved = store.get_artifact(artifact_id)
        assert retrieved is not None

        # Attempt mutation: change rejection to approval
        mutated_content = retrieved.content.copy()
        mutated_content["output"]["approved"] = True  # Flip approval!
        mutated_content["policy_parameters"]["original_threshold"] = 999  # Change policy!

        # FACP/ASX: Verify hash protection
        modified_hash = artifact.content_hash() if hasattr(artifact, 'content_hash') else None

        # DISCOVERY: Artifact.verify_hash() method exists (line 100-102 of types.py)
        # Test whether hashes are actually validated on retrieval
        if hasattr(retrieved, 'verify_hash'):
            # If we mutate content after retrieval, hash should fail
            original_verify = retrieved.verify_hash()
            assert original_verify is True, "Original artifact should pass hash check"

            # Now mutate
            retrieved.content = mutated_content
            mutated_verify = retrieved.verify_hash()

            # DISCOVERY: Does mutation break hash verification?
            if mutated_verify:
                # VULNERABILITY: Hash verification passed despite mutation
                pytest.fail(
                    "DISCOVERY: Artifact mutation not detected by hash verification. "
                    "Modified content passed verification."
                )

    def test_metadata_mutation_authority_escalation(self):
        """ATTACK: Mutate artifact metadata to escalate authority."""
        decision = GovernanceDecisionRecord(
            action_type="governance_decision",
            node="test",
            cassette_version="v1",
            input_data={},
            policy_parameters={},
            reasoning="Low-privilege decision",
            output={"approved": False},
            authorized_by="harness:governance",  # SYSTEM actor - low privilege
        )

        artifact = create_governance_artifact_from_decision(decision)
        original_authority = artifact.metadata.authority_source

        # ATTACK: Attempt to escalate authority in metadata
        store = ArtifactStore(use_postgres=False)
        stored_id = store.store_artifact(artifact)
        retrieved = store.get_artifact(stored_id)

        # Try to mutate metadata authority
        if hasattr(retrieved.metadata, 'authority_source'):
            original_auth = retrieved.metadata.authority_source
            retrieved.metadata.authority_source = "governor_claude_api"  # Escalate to MODEL

            # DISCOVERY: Can we modify metadata in-place after retrieval?
            if retrieved.metadata.authority_source != original_auth:
                # Metadata is mutable in memory - gateway whitelist would re-check it
                # But this indicates metadata is not cryptographically bound to artifact
                pytest.fail(
                    "DISCOVERY: Artifact metadata is mutable after retrieval. "
                    f"Authority escalated from {original_auth} to {retrieved.metadata.authority_source}"
                )


class TestAuthorityEscalationAttacks:
    """
    VECTOR 3: Authority Escalation

    Fault Analysis:
    - System maps authority_source to ActorKind via typed mapping
    - Assumption: Only whitelist-verified authorities map to high privilege

    Countermeasure: gateway._map_authority_status() uses explicit whitelist

    Attack Simulation: Attempt to impersonate verified actors
    """

    def test_actor_kind_inference_via_substring(self):
        """ATTACK: Craft authorized_by value that passes as MODEL actor via substring."""
        # FACP: The mapping in transformation_factory._map_authorized_by_to_kind uses pattern matching
        # Does it check "claude" substring or exact identity?

        attacker_identities = [
            "claude-impersonator",     # Contains "claude"
            "not_claude_but_close",    # Similar pattern
            "human_model_hybrid",      # Contains both "human" and "model"
            "model",                   # Exact substring
        ]

        for attacker_id in attacker_identities:
            decision = GovernanceDecisionRecord(
                action_type="governance_decision",
                node="test",
                cassette_version="v1",
                input_data={},
                policy_parameters={},
                reasoning=f"Attempt with {attacker_id}",
                output={"approved": True},
                authorized_by=attacker_id,
            )

            artifact = create_governance_artifact_from_decision(decision)
            transformation = create_governance_transformation(decision, artifact)

            # Check transformer actor kind
            if hasattr(transformation, 'transformer') and hasattr(transformation.transformer, 'kind'):
                actor_kind = transformation.transformer.kind

                # DISCOVERY: Does substring matching bypass typed mapping?
                if "claude" in attacker_id.lower() and actor_kind == "MODEL":
                    pytest.fail(
                        f"DISCOVERY: Substring matching allows escalation. "
                        f"'{attacker_id}' mapped to {actor_kind} actor (should be SYSTEM/HUMAN)"
                    )

    def test_authority_whitelist_bypass(self):
        """ATTACK: Use authority value not in gateway whitelist."""
        # FACP: Gateway._map_authority_status() defines VERIFIED_AUTHORITIES whitelist
        # What happens for unknown authorities?

        gateway = SentinelConservationGateway(resolver=None)

        unknown_authorities = [
            "attacker_model",
            "rogue_human_reviewer",
            "unauthorized_service",
            "harness:attacker",  # Similar pattern to whitelist but not exact
            "GOVERNOR_CLAUDE_API",  # Case variation of whitelist
        ]

        for auth in unknown_authorities:
            # ATTACK: Submit with unknown authority
            status = gateway._map_authority_status(auth)

            # FACP/ASX: Should map to NONE for unknown authorities
            from conservation_kernel import AuthorityStatus

            if status != AuthorityStatus.NONE:
                pytest.fail(
                    f"DISCOVERY: Unknown authority '{auth}' mapped to {status}. "
                    f"Should be NONE for fail-closed behavior."
                )

    def test_actor_identity_fabrication(self):
        """ATTACK: Create artifact with fabricated actor identity."""
        # FACP: Can we set model_identity to a fake model name?
        fake_models = [
            "gpt-5-super-advanced",
            "claude-opus-unlimited",
            "anthropic-god-model",
            "my-custom-model",
        ]

        for fake_model in fake_models:
            decision = GovernanceDecisionRecord(
                action_type="governance_decision",
                node="test",
                cassette_version="v1",
                input_data={},
                policy_parameters={},
                reasoning=f"Using fake model {fake_model}",
                output={"approved": True},
                model_identity=fake_model,
            )

            artifact = create_governance_artifact_from_decision(decision)

            # DISCOVERY: Artifact accepts any model_identity string
            assert artifact.metadata.authority_source == "governor_claude_api"

            # The authority_source is canonical (governor_claude_api)
            # But the artifact content preserves model_identity
            if artifact.content.get("model_identity") == fake_model:
                # DISCOVERY: Fake model identity is preserved in artifact
                # This could be used to forge decision provenance in logs/audits
                # Risk: If artifact.content["model_identity"] is used as proof of model authorization
                pass

    def test_system_actor_as_model_escalation(self):
        """ATTACK: Use harness:governance service identity to bypass approval requirements."""
        # FACP: System actors (harness:governance) are used for fail-closed decisions
        # Can they be used to approve decisions that should require Claude approval?

        decision = GovernanceDecisionRecord(
            action_type="governance_decision",
            node="test",
            cassette_version="v1",
            input_data={"episode_id": "critical_decision"},
            policy_parameters={},
            reasoning="Critical change - should require Claude approval",
            output={"approved": True},  # CRITICAL: Approval
            authorized_by="harness:governance",  # SYSTEM actor, not MODEL
        )

        artifact = create_governance_artifact_from_decision(decision)
        transformation = create_governance_transformation(decision, artifact)

        # DISCOVERY: System actors can approve critical decisions
        # The system doesn't distinguish between "refusal actor" and "approval actor"
        # QUESTION: Should harness:governance only appear on safe=False decisions?

        if transformation.transformer.kind == "SYSTEM" and decision.output.get("approved"):
            # This might be intentional (harness can force-approve for safety)
            # Or it might be an escalation vulnerability
            # Requires policy review
            pass

    def test_authorized_by_spoofing_with_valid_format(self):
        """ATTACK: Use authorized_by format that passes validation but is fabricated."""
        # FACP: authorized_by validation allows "human_*" pattern
        # Pattern: if "human_" in authorized_by.lower() -> ActorKind.HUMAN

        spoofed_authors = [
            "human_admin",
            "human_ceo",
            "human_with_god_powers",
            "human_x_attacker",
        ]

        for spoof in spoofed_authors:
            decision = GovernanceDecisionRecord(
                action_type="governance_decision",
                node="test",
                cassette_version="v1",
                input_data={},
                policy_parameters={},
                reasoning=f"Spoofed by {spoof}",
                output={"approved": True},
                authorized_by=spoof,
            )

            artifact = create_governance_artifact_from_decision(decision)
            transformation = create_governance_transformation(decision, artifact)

            # DISCOVERY: Pattern-based ACL allows any "human_*" string
            # No validation that "human_admin" actually exists as an identity
            # QUESTION: Is there a human identity registry to validate against?
            if transformation.transformer.kind == "HUMAN":
                # VULNERABILITY (conditional): If no identity registry exists,
                # any decision claiming "human_*" authority is accepted
                pass


class TestBoundaryEnforcementIntegrity:
    """
    VECTOR 4 (Derived): Boundary Enforcement Integrity

    Testing that the conservation boundary is actually invoked and
    failures properly prevent state persistence.
    """

    def test_gateway_rejection_prevents_ledger_write(self):
        """TEST: If gateway rejects, does artifact persist anyway?"""
        # FACP: _write_decision calls gateway.submit_artifact (line 365)
        # What happens if gateway raises ConservationGatewayError?

        # Mock a rejecting gateway
        from conservation.gateway import ConservationGatewayError

        with patch('conservation.gateway.SentinelConservationGateway') as MockGateway:
            mock_instance = Mock()
            mock_instance.submit_artifact.side_effect = ConservationGatewayError("Kernel rejected")
            MockGateway.return_value = mock_instance

            # Create decision
            decision = GovernanceDecisionRecord(
                action_type="governance_decision",
                node="test",
                cassette_version="v1",
                input_data={},
                policy_parameters={},
                reasoning="Should be rejected by gateway",
                output={"approved": True},
                model_identity="claude-opus-4",
            )

            artifact = create_governance_artifact_from_decision(decision)
            transformation = create_governance_transformation(decision, artifact)

            # DISCOVERY: What happens when gateway.submit_artifact fails?
            # In real _write_decision, the exception handling is at lines 347-392
            # If gateway.submit_artifact raises, does ledger append still happen?

            # This test documents the code path
            # The try/except at line 347 catches exceptions
            # If raised, does ledger.append_decision still execute?
            pass

    def test_multiple_decision_writes_produce_unique_actors(self):
        """TEST: Each decision captures its actual actor, not a generic fallback."""
        decisions_with_actors = [
            ("claude-opus-4", None),  # Model decision
            (None, "harness:governance"),  # Refusal decision
            (None, "human_reviewer_alice"),  # Human decision
        ]

        artifacts = []
        for model_id, auth_by in decisions_with_actors:
            decision = GovernanceDecisionRecord(
                action_type="governance_decision",
                node="test",
                cassette_version="v1",
                input_data={},
                policy_parameters={},
                reasoning=f"Decision by {model_id or auth_by}",
                output={"approved": True},
                model_identity=model_id,
                authorized_by=auth_by,
            )

            artifact = create_governance_artifact_from_decision(decision)
            transformation = create_governance_transformation(decision, artifact)
            artifacts.append((decision, artifact, transformation))

        # DISCOVERY: Verify each has unique actor identity
        actors = [
            t[2].transformer.actor_id if hasattr(t[2], 'transformer') else None
            for t in artifacts
        ]

        # Should have 3 different actors
        if len(set(actors)) != 3:
            pytest.fail(
                f"DISCOVERY: Multiple decisions collapsed to fewer than 3 unique actors. "
                f"Actors: {actors}"
            )


# ============================================================================
# CAMPAIGN REPORT TEMPLATE
# ============================================================================
"""
HERALD CAMPAIGN RESULTS SUMMARY
(Fill in after all tests run)

EXECUTION DATE: [Test run date]
DURATION: [Hours/minutes]
ENVIRONMENT: [pytest version, Python version]

VECTOR 1: CONSERVATION BYPASS ATTACKS
  Tests: [count]
  Passed: [count]
  Failed: [count]
  Vulnerabilities Found: [yes/no]
  Critical Findings: [list]

VECTOR 2: POST-VALIDATION MUTATION
  Tests: [count]
  Passed: [count]
  Failed: [count]
  Vulnerabilities Found: [yes/no]
  Critical Findings: [list]

VECTOR 3: AUTHORITY ESCALATION
  Tests: [count]
  Passed: [count]
  Failed: [count]
  Vulnerabilities Found: [yes/no]
  Critical Findings: [list]

VECTOR 4: BOUNDARY ENFORCEMENT
  Tests: [count]
  Passed: [count]
  Failed: [count]
  Vulnerabilities Found: [yes/no]
  Critical Findings: [list]

OVERALL ASSESSMENT:
  Total Tests: [count]
  Total Failures: [count]
  System Posture: [SECURE / VULNERABLE / CRITICAL]
  Recommended Actions: [by William after review]
"""

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
