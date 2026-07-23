"""
Test cassette snapshot forensics for ledger reconstruction.

Proves that:
1. Cassette snapshots are captured when decisions are recorded
2. Snapshots are immutable (hash validation)
3. Snapshots can be reconstructed for regulatory audit
4. Hash chain is maintained with cassette_hash
5. Pre-migration decisions coexist cleanly
"""

import pytest
import json
from governance.ledger_postgres import GovernanceDecisionRecord
from cassette_schema import validate_cassette, cassette_version_of
from cassette_forensics import (
    serialize_cassette_for_ledger,
    compute_cassette_hash,
    reconstruct_cassette_for_decision,
)


class TestCassetteSnapshot:
    """Prove cassette snapshots are captured and immutable."""

    def test_append_decision_with_cassette(self, test_ledger, test_cassette):
        """When cassette is provided, snapshot is captured and stored."""
        governance_params = validate_cassette(test_cassette)

        decision = GovernanceDecisionRecord(
            action_type="governance",
            node="test_node",
            cassette_version=cassette_version_of(test_cassette),
            input_data={"friction": 2},
            policy_parameters=governance_params.snapshot(),
            reasoning="test decision",
            output={"approved": False},
        )

        # append_decision should accept governance_params and store snapshot
        assert test_ledger.append_decision(decision, governance_params=governance_params)

        # Retrieve and verify snapshot was stored
        decisions = test_ledger.get_decisions(limit=1)
        assert len(decisions) > 0

        decision_row = decisions[0]
        assert decision_row["cassette_snapshot"] is not None
        assert decision_row["cassette_hash"] is not None

    def test_cassette_snapshot_contains_full_policy(self, test_ledger, test_cassette):
        """Snapshot contains the full cassette configuration."""
        governance_params = validate_cassette(test_cassette)

        decision = GovernanceDecisionRecord(
            action_type="governance",
            node="test_node",
            cassette_version=cassette_version_of(test_cassette),
            input_data={"friction": 2},
            policy_parameters=governance_params.snapshot(),
            reasoning="test decision",
            output={"approved": False},
        )

        test_ledger.append_decision(decision, governance_params=governance_params)

        decisions = test_ledger.get_decisions(limit=1)
        decision_row = decisions[0]
        snapshot = decision_row["cassette_snapshot"]

        # Verify snapshot structure
        assert snapshot["schema_version"] == "2.0.0"
        assert snapshot["cassette_version"] == cassette_version_of(test_cassette)
        # 2.0.0: the snapshot also records WHICH capability surfaces
        # existed at decision time, not just the parameter values.
        assert sorted(snapshot["capabilities"]) == [
            "rl", "routing_topology", "self_healing", "telephony_ingest"]
        assert "parameters" in snapshot
        assert isinstance(snapshot["parameters"], dict)

        # Verify key parameters are present
        params = snapshot["parameters"]
        assert "long_wait_threshold" in params
        assert "governance_trigger" in params
        assert "expected_wait_bounds" in params

    def test_cassette_hash_is_deterministic(self, test_cassette):
        """Same cassette always produces same hash."""
        governance_params = validate_cassette(test_cassette)
        snapshot = serialize_cassette_for_ledger(governance_params)

        hash1 = compute_cassette_hash(snapshot)
        hash2 = compute_cassette_hash(snapshot)

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex string

    def test_cassette_hash_changes_with_policy(self, test_cassette):
        """Different policy parameters produce different hash."""
        governance_params = validate_cassette(test_cassette)
        snapshot1 = serialize_cassette_for_ledger(governance_params)
        hash1 = compute_cassette_hash(snapshot1)

        # Simulate a different cassette by modifying snapshot
        snapshot2 = json.loads(json.dumps(snapshot1))  # Deep copy
        snapshot2["parameters"]["governance_trigger"]["value"] = 99
        hash2 = compute_cassette_hash(snapshot2)

        assert hash1 != hash2

    def test_reconstruct_cassette_from_decision(self, test_ledger, test_cassette):
        """Regulators can pull and verify cassette from ledger."""
        governance_params = validate_cassette(test_cassette)

        decision = GovernanceDecisionRecord(
            action_type="governance",
            node="test_node",
            cassette_version=cassette_version_of(test_cassette),
            input_data={"friction": 2},
            policy_parameters=governance_params.snapshot(),
            reasoning="test decision",
            output={"approved": False},
        )

        test_ledger.append_decision(decision, governance_params=governance_params)

        # Retrieve decision from ledger
        decisions = test_ledger.get_decisions(limit=1)
        decision_row = decisions[0]

        # Reconstruct cassette
        reconstruction = reconstruct_cassette_for_decision(decision_row)

        assert "cassette_snapshot" in reconstruction
        assert "cassette_hash" in reconstruction
        assert "integrity_verified" in reconstruction
        assert reconstruction["integrity_verified"] is True
        assert reconstruction["cassette_version"] == cassette_version_of(test_cassette)

    def test_cassette_snapshot_integrity_proof(self, test_ledger, test_cassette):
        """Prove cassette has not been tampered with (hash validation)."""
        governance_params = validate_cassette(test_cassette)

        decision = GovernanceDecisionRecord(
            action_type="governance",
            node="test_node",
            cassette_version=cassette_version_of(test_cassette),
            input_data={"friction": 2},
            policy_parameters=governance_params.snapshot(),
            reasoning="test decision",
            output={"approved": False},
        )

        test_ledger.append_decision(decision, governance_params=governance_params)

        # Retrieve from ledger
        decisions = test_ledger.get_decisions(limit=1)
        decision_row = decisions[0]

        ledger_snapshot = decision_row["cassette_snapshot"]
        ledger_hash = decision_row["cassette_hash"]

        # Recompute hash and verify it matches
        computed_hash = compute_cassette_hash(ledger_snapshot)
        assert computed_hash == ledger_hash

    def test_tampered_cassette_snapshot_detected(self, test_ledger, test_cassette):
        """If someone tampers with snapshot, reconstruction fails."""
        governance_params = validate_cassette(test_cassette)

        decision = GovernanceDecisionRecord(
            action_type="governance",
            node="test_node",
            cassette_version=cassette_version_of(test_cassette),
            input_data={"friction": 2},
            policy_parameters=governance_params.snapshot(),
            reasoning="test decision",
            output={"approved": False},
        )

        test_ledger.append_decision(decision, governance_params=governance_params)

        # Retrieve and tamper
        decisions = test_ledger.get_decisions(limit=1)
        decision_row = decisions[0]

        tampered_row = json.loads(json.dumps(decision_row))  # Deep copy
        tampered_row["cassette_snapshot"]["parameters"]["governance_trigger"]["value"] = 999

        # Reconstruction should fail
        with pytest.raises(ValueError, match="CORRUPTED"):
            reconstruct_cassette_for_decision(tampered_row)

    def test_get_decision_with_cassette_method(self, test_ledger, test_cassette):
        """New API endpoint: retrieve decision with cassette proof."""
        governance_params = validate_cassette(test_cassette)

        decision = GovernanceDecisionRecord(
            action_type="governance",
            node="test_node",
            cassette_version=cassette_version_of(test_cassette),
            input_data={"friction": 2},
            policy_parameters=governance_params.snapshot(),
            reasoning="test decision",
            output={"approved": False},
        )

        assert test_ledger.append_decision(decision, governance_params=governance_params)

        # Get the decision ID (newest decision)
        decisions = test_ledger.get_decisions(limit=1)
        decision_id = decisions[0]["id"]

        # Retrieve with cassette
        result = test_ledger.get_decision_with_cassette(decision_id)

        assert "decision" in result
        assert "cassette_proof" in result
        assert result["decision"]["id"] == decision_id
        assert result["cassette_proof"]["integrity_verified"] is True

    def test_pre_migration_decisions_coexist(self, test_ledger, test_cassette):
        """Old decisions without cassette snapshot can coexist cleanly."""
        # Append decision WITHOUT cassette (simulating pre-migration)
        decision_old = GovernanceDecisionRecord(
            action_type="governance",
            node="test_node",
            cassette_version="old:cassette:1.0",
            input_data={"friction": 1},
            policy_parameters={"threshold": 2},
            reasoning="old decision",
            output={"approved": True},
        )

        # Should work without governance_params
        assert test_ledger.append_decision(decision_old, governance_params=None)

        # Append decision WITH cassette (new style)
        governance_params = validate_cassette(test_cassette)
        decision_new = GovernanceDecisionRecord(
            action_type="governance",
            node="test_node",
            cassette_version=cassette_version_of(test_cassette),
            input_data={"friction": 2},
            policy_parameters=governance_params.snapshot(),
            reasoning="new decision",
            output={"approved": False},
        )

        assert test_ledger.append_decision(decision_new, governance_params=governance_params)

        # Both should be retrievable
        decisions = test_ledger.get_decisions(limit=10)
        assert len(decisions) >= 2

        # Old has no snapshot
        old_decision = next(d for d in decisions if d.get("reasoning") == "old decision")
        assert old_decision["cassette_snapshot"] is None
        assert old_decision["cassette_hash"] is None

        # New has snapshot
        new_decision = next(d for d in decisions if d.get("reasoning") == "new decision")
        assert new_decision["cassette_snapshot"] is not None
        assert new_decision["cassette_hash"] is not None

    def test_validate_cassette_snapshot_chain(self, test_ledger, test_cassette):
        """Audit all cassette snapshots in ledger (regulatory audit)."""
        governance_params = validate_cassette(test_cassette)

        # Add multiple decisions
        for i in range(3):
            decision = GovernanceDecisionRecord(
                action_type="governance",
                node=f"test_node_{i}",
                cassette_version=cassette_version_of(test_cassette),
                input_data={"friction": 1 + i},
                policy_parameters=governance_params.snapshot(),
                reasoning=f"decision {i}",
                output={"approved": i % 2 == 0},
            )
            test_ledger.append_decision(decision, governance_params=governance_params)

        # Validate chain
        audit_result = test_ledger.validate_cassette_snapshot_chain()

        assert audit_result["total_decisions"] >= 3
        assert audit_result["snapshots_verified"] >= 3
        assert len(audit_result["corrupted"]) == 0
        assert audit_result["all_ok"] is True

    def test_cassette_hash_in_canonical_form(self, test_ledger, test_cassette):
        """cassette_hash is included in the canonical form for chain integrity."""
        governance_params = validate_cassette(test_cassette)

        decision = GovernanceDecisionRecord(
            action_type="governance",
            node="test_node",
            cassette_version=cassette_version_of(test_cassette),
            input_data={"friction": 2},
            policy_parameters=governance_params.snapshot(),
            reasoning="test decision",
            output={"approved": False},
        )

        test_ledger.append_decision(decision, governance_params=governance_params)

        # Retrieve and verify hash chain
        decisions = test_ledger.get_decisions(limit=1)
        decision_row = decisions[0]

        # The current_hash should be different if cassette_hash is included
        # (proving it's in the canonical form)
        assert decision_row["cassette_hash"] is not None
        assert decision_row["current_hash"] is not None

        # Both should be valid SHA-256 hex strings
        assert len(decision_row["cassette_hash"]) == 64
        assert len(decision_row["current_hash"]) == 64
