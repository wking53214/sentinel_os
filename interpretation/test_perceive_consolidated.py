"""
Tests: perceive_consolidated.py
=================================

Unit + integration tests for the consolidated PERCEIVE governance kernel,
including the 6 policy gates, consensus engine, audit ledger, and DGK
multi-node consensus layer.

Run: python3 -m pytest test_perceive_consolidated.py -v
"""

import unittest
import secrets
from datetime import datetime, timezone

from perceive_consolidated import (
    PolicyRequest,
    PolicyManifest,
    PolicyGates,
    ConsensusEngine,
    PerceiveGovernanceKernel,
    ManifestRegistry,
    EventStore,
    ImmutableAuditLedger,
    EscalationPolicy,
    RuleModificationPolicy,
    DataExportPolicy,
    EmergencyOverridePolicy,
    Signer,
    Proposal,
    GovernanceNode,
    ConsensusDecider,
    DGKGateway,
    DGKAwareVerdict,
)


# ============================================================================
# POLICY RULE TESTS
# ============================================================================

class TestEscalationPolicy(unittest.TestCase):
    def test_daily_limit_enforced(self):
        approved, violations = EscalationPolicy.can_escalate("critical", 10, 1, 30)
        self.assertFalse(approved)
        self.assertTrue(any("Daily" in v for v in violations))

    def test_hourly_limit_enforced(self):
        approved, violations = EscalationPolicy.can_escalate("critical", 5, 3, 30)
        self.assertFalse(approved)
        self.assertTrue(any("Hourly" in v for v in violations))

    def test_cooldown_enforced(self):
        approved, violations = EscalationPolicy.can_escalate("critical", 5, 1, 5)
        self.assertFalse(approved)
        self.assertTrue(any("cooldown" in v for v in violations))

    def test_valid_escalation_approved(self):
        approved, violations = EscalationPolicy.can_escalate("critical", 3, 1, 20)
        self.assertTrue(approved)
        self.assertEqual(violations, [])


class TestRuleModificationPolicy(unittest.TestCase):
    def test_critical_requires_dual_approval(self):
        approved, violations = RuleModificationPolicy.can_modify("critical", 1, 25)
        self.assertFalse(approved)
        self.assertTrue(any("Insufficient approvals" in v for v in violations))

    def test_critical_temporal_lock(self):
        approved, violations = RuleModificationPolicy.can_modify("critical", 2, 12)
        self.assertFalse(approved)
        self.assertTrue(any("Temporal lock" in v for v in violations))

    def test_safety_critical_requires_three_approvals(self):
        approved, violations = RuleModificationPolicy.can_modify("safety_critical", 2, 100)
        self.assertFalse(approved)

    def test_valid_modification_approved(self):
        approved, violations = RuleModificationPolicy.can_modify("critical", 2, 25)
        self.assertTrue(approved)


class TestDataExportPolicy(unittest.TestCase):
    def test_pii_requires_consent(self):
        approved, violations = DataExportPolicy.can_export("pii_included", False, True, True)
        self.assertFalse(approved)
        self.assertTrue(any("consent" in v for v in violations))

    def test_pii_requires_encryption(self):
        approved, violations = DataExportPolicy.can_export("pii_included", True, True, False)
        self.assertFalse(approved)
        self.assertTrue(any("Encryption" in v for v in violations))

    def test_synthetic_export_approved_without_consent(self):
        approved, violations = DataExportPolicy.can_export("synthetic_only", False, True, False)
        self.assertTrue(approved)

    def test_unknown_export_type_rejected(self):
        approved, violations = DataExportPolicy.can_export("unknown_type", True, True, True)
        self.assertFalse(approved)


class TestEmergencyOverridePolicy(unittest.TestCase):
    def test_patient_safety_allowed_with_approval(self):
        approved, violations = EmergencyOverridePolicy.can_override(
            "patient_safety", "Child's life at immediate risk", True, True
        )
        self.assertTrue(approved)

    def test_patient_safety_requires_physician_approval(self):
        approved, violations = EmergencyOverridePolicy.can_override(
            "patient_safety", "Child's life at immediate risk", False, True
        )
        self.assertFalse(approved)
        self.assertTrue(any("Physician approval" in v for v in violations))

    def test_regulatory_exception_never_allowed(self):
        approved, violations = EmergencyOverridePolicy.can_override(
            "regulatory_exception", "Policy change requested", True, True
        )
        self.assertFalse(approved)
        self.assertTrue(any("not permitted" in v for v in violations))

    def test_empty_justification_rejected(self):
        approved, violations = EmergencyOverridePolicy.can_override(
            "patient_safety", "", True, True
        )
        self.assertFalse(approved)
        self.assertTrue(any("Justification required" in v for v in violations))


# ============================================================================
# POLICY GATE TESTS
# ============================================================================

class TestBoundaryGate(unittest.TestCase):
    def test_valid_request_approved(self):
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001", {})
        out = PolicyGates.boundary_gate(req)
        self.assertTrue(out.approved)

    def test_missing_actor_id_rejected(self):
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "", {})
        out = PolicyGates.boundary_gate(req)
        self.assertFalse(out.approved)
        self.assertTrue(any("actor_id" in v for v in out.violation_details))

    def test_unknown_request_type_rejected(self):
        req = PolicyRequest("REQ-1", "delete_everything", "P001", "DR-001", {})
        out = PolicyGates.boundary_gate(req)
        self.assertFalse(out.approved)


class TestCitadelGate(unittest.TestCase):
    def test_sufficient_justification_with_context_approved(self):
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001",
                             {"justification": "Patient showing signs of deterioration", "severity": "high"})
        out = PolicyGates.citadel(req)
        self.assertTrue(out.approved)

    def test_short_justification_rejected(self):
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001",
                             {"justification": "bad", "severity": "high"})
        out = PolicyGates.citadel(req)
        self.assertFalse(out.approved)

    def test_hedging_language_rejected(self):
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001",
                             {"justification": "Patient might maybe be deteriorating", "severity": "high"})
        out = PolicyGates.citadel(req)
        self.assertFalse(out.approved)

    def test_missing_required_context_rejected(self):
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001",
                             {"justification": "Patient showing signs of deterioration"})
        out = PolicyGates.citadel(req)
        self.assertFalse(out.approved)


class TestFortressGate(unittest.TestCase):
    def test_normal_request_approved(self):
        req = PolicyRequest("REQ-1", "modify_rule", "P001", "DR-001", {"changes": {"threshold": 0.5}})
        out = PolicyGates.fortress(req)
        self.assertTrue(out.approved)

    def test_disable_audit_blocked(self):
        req = PolicyRequest("REQ-1", "modify_rule", "P001", "DR-001",
                             {"changes": {"disable_audit": True}})
        out = PolicyGates.fortress(req)
        self.assertFalse(out.approved)
        self.assertTrue(any("audit" in v for v in out.violation_details))

    def test_disable_gates_blocked(self):
        req = PolicyRequest("REQ-1", "modify_rule", "P001", "DR-001",
                             {"changes": {"disable_gates": True}})
        out = PolicyGates.fortress(req)
        self.assertFalse(out.approved)

    def test_bypass_approval_blocked(self):
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001", {"bypass_approval": True})
        out = PolicyGates.fortress(req)
        self.assertFalse(out.approved)


class TestInvariantValidatorGate(unittest.TestCase):
    def test_always_approved_when_invariants_hold(self):
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001", {})
        out = PolicyGates.invariant_validator(req)
        self.assertTrue(out.approved)
        self.assertEqual(out.confidence, 0.99)


class TestSentinelGate(unittest.TestCase):
    def test_normal_activity_approved(self):
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001", {"operation_count_today": 5})
        out = PolicyGates.sentinel(req)
        self.assertTrue(out.approved)

    def test_high_frequency_rejected(self):
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001", {"operation_count_today": 60})
        out = PolicyGates.sentinel(req)
        self.assertFalse(out.approved)

    def test_admin_privilege_without_justification_rejected(self):
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001",
                             {"requested_privilege_level": "admin"})
        out = PolicyGates.sentinel(req)
        self.assertFalse(out.approved)

    def test_admin_privilege_with_justification_approved(self):
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001",
                             {"requested_privilege_level": "admin", "admin_justification": "Required for audit export"})
        out = PolicyGates.sentinel(req)
        self.assertTrue(out.approved)

    def test_rapid_retry_rejected(self):
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001",
                             {"is_retry": True, "time_since_last_attempt_seconds": 1})
        out = PolicyGates.sentinel(req)
        self.assertFalse(out.approved)

    def test_large_data_volume_rejected(self):
        req = PolicyRequest("REQ-1", "export_data", "P001", "DR-001", {"data_volume_gb": 150})
        out = PolicyGates.sentinel(req)
        self.assertFalse(out.approved)


class TestMicropatchGate(unittest.TestCase):
    def test_non_emergency_passthrough(self):
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001", {})
        out = PolicyGates.micropatch(req)
        self.assertTrue(out.approved)
        self.assertIn("not applicable", out.violation_details[0].lower())

    def test_patient_safety_with_full_context_approved(self):
        req = PolicyRequest("REQ-1", "emergency_override", "P001", "DR-001", {
            "emergency_reason": "Critical desaturation",
            "override_type": "patient_safety",
            "physician_approved": True,
            "can_notify_stakeholders": True,
            "escalated_to_physician": True,
        })
        out = PolicyGates.micropatch(req)
        self.assertTrue(out.approved)

    def test_patient_safety_without_physician_escalation_rejected(self):
        req = PolicyRequest("REQ-1", "emergency_override", "P001", "DR-001", {
            "emergency_reason": "Critical desaturation",
            "override_type": "patient_safety",
            "physician_approved": True,
            "can_notify_stakeholders": True,
            "escalated_to_physician": False,
        })
        out = PolicyGates.micropatch(req)
        self.assertFalse(out.approved)

    def test_regulatory_exception_rejected(self):
        req = PolicyRequest("REQ-1", "emergency_override", "P001", "DR-001", {
            "emergency_reason": "Policy update",
            "override_type": "regulatory_exception",
            "physician_approved": True,
            "can_notify_stakeholders": True,
        })
        out = PolicyGates.micropatch(req)
        self.assertFalse(out.approved)


# ============================================================================
# CONSENSUS ENGINE TESTS
# ============================================================================

class TestConsensusEngine(unittest.TestCase):
    def test_all_approved_yields_approval(self):
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001", {})
        outputs = [PolicyGates.boundary_gate(req), PolicyGates.invariant_validator(req)]
        approved, confidence, violations = ConsensusEngine.evaluate(outputs)
        self.assertTrue(approved)
        self.assertEqual(violations, [])

    def test_single_rejection_blocks_approval(self):
        req_bad = PolicyRequest("REQ-1", "unknown_type", "P001", "DR-001", {})
        outputs = [PolicyGates.boundary_gate(req_bad), PolicyGates.invariant_validator(req_bad)]
        approved, confidence, violations = ConsensusEngine.evaluate(outputs)
        self.assertFalse(approved)
        self.assertGreater(len(violations), 0)

    def test_confidence_is_geometric_mean(self):
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001", {})
        outputs = [PolicyGates.boundary_gate(req), PolicyGates.invariant_validator(req)]
        approved, confidence, _ = ConsensusEngine.evaluate(outputs)
        expected = (outputs[0].confidence * outputs[1].confidence) ** 0.5
        self.assertAlmostEqual(confidence, expected, places=6)

    def test_no_gates_rejected(self):
        approved, confidence, violations = ConsensusEngine.evaluate([])
        self.assertFalse(approved)
        self.assertEqual(confidence, 0.0)


# ============================================================================
# EVENT SOURCING TESTS
# ============================================================================

class TestEventStore(unittest.TestCase):
    def test_append_and_replay(self):
        store = EventStore()
        for i in range(3):
            store.append_event("policy_evaluation", {"id": i}, "DR-001")
        events = store.replay()
        self.assertEqual(len(events), 3)

    def test_replay_up_to_index(self):
        store = EventStore()
        for i in range(5):
            store.append_event("policy_evaluation", {"id": i}, "DR-001")
        events = store.replay(up_to_index=2)
        self.assertEqual(len(events), 3)


# ============================================================================
# MANIFEST REGISTRY TESTS
# ============================================================================

class TestManifestRegistry(unittest.TestCase):
    def test_register_and_retrieve(self):
        registry = ManifestRegistry()
        manifest = PolicyManifest("v1", "1.0.0", datetime.now(timezone.utc), {"policy1": {}})
        registry.register_manifest(manifest)
        current = registry.get_current_manifest()
        self.assertEqual(current.version, "1.0.0")
        self.assertNotEqual(current.manifest_hash, "")

    def test_versioning(self):
        registry = ManifestRegistry()
        m1 = PolicyManifest("v1", "1.0.0", datetime.now(timezone.utc), {})
        m2 = PolicyManifest("v2", "1.1.0", datetime.now(timezone.utc), {})
        registry.register_manifest(m1)
        registry.register_manifest(m2)
        self.assertEqual(registry.get_current_manifest().version, "1.1.0")
        self.assertEqual(registry.get_manifest("1.0.0").version, "1.0.0")


# ============================================================================
# AUDIT LEDGER TESTS
# ============================================================================

class TestImmutableAuditLedger(unittest.TestCase):
    def test_append_creates_valid_hash(self):
        ledger = ImmutableAuditLedger()
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001", {})
        outputs = [PolicyGates.boundary_gate(req)]
        entry = ledger.append_decision({"id": "REQ-1"}, ["boundary_gate"], outputs,
                                        {"approved": True, "confidence": 0.9}, "1.0.0", "abc")
        self.assertEqual(len(entry.immutable_hash), 64)

    def test_chain_integrity_valid(self):
        ledger = ImmutableAuditLedger()
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001", {})
        outputs = [PolicyGates.boundary_gate(req)]
        for i in range(5):
            ledger.append_decision({"id": f"REQ-{i}"}, ["boundary_gate"], outputs,
                                    {"approved": True}, "1.0.0", "abc")
        self.assertTrue(ledger.verify_chain_integrity())

    def test_tamper_detection(self):
        ledger = ImmutableAuditLedger()
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001", {})
        outputs = [PolicyGates.boundary_gate(req)]
        ledger.append_decision({"id": "REQ-1"}, ["boundary_gate"], outputs,
                                {"approved": True}, "1.0.0", "abc")
        # Tamper with the final_verdict after the fact
        ledger.entries[0].final_verdict["approved"] = False
        self.assertFalse(ledger.verify_chain_integrity())

    def test_export_json(self):
        ledger = ImmutableAuditLedger()
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001", {})
        outputs = [PolicyGates.boundary_gate(req)]
        ledger.append_decision({"id": "REQ-1"}, ["boundary_gate"], outputs,
                                {"approved": True}, "1.0.0", "abc")
        exported = ledger.export_json()
        self.assertIn("REQ-1", exported)


# ============================================================================
# KERNEL INTEGRATION TESTS
# ============================================================================

class TestPerceiveKernelIntegration(unittest.TestCase):
    def setUp(self):
        self.kernel = PerceiveGovernanceKernel()
        manifest = PolicyManifest("v1", "1.0.0", datetime.now(timezone.utc), {"escalation_policy": {"max_daily": 5}})
        self.kernel.register_manifest(manifest)

    def test_escalate_patient_approved(self):
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001",
                             {"justification": "Patient showing rapid deterioration", "severity": "high"})
        verdict = self.kernel.evaluate_request(req)
        self.assertTrue(verdict.approved)
        self.assertEqual(verdict.applied_gates, ["boundary_gate", "invariant_validator", "sentinel"])
        self.assertIsNone(verdict.consensus_result)  # no DGK gateway configured

    def test_missing_manifest_rejected(self):
        kernel = PerceiveGovernanceKernel()  # no manifest registered
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001", {})
        verdict = kernel.evaluate_request(req)
        self.assertFalse(verdict.approved)
        self.assertIn("No policy manifest available", verdict.violations)

    def test_modify_rule_gate_selection(self):
        req = PolicyRequest("REQ-1", "modify_rule", "P001", "DR-001",
                             {"justification": "Adjusting threshold per clinical review", "rule_id": "RULE-42",
                              "changes": {"threshold": 0.6}})
        verdict = self.kernel.evaluate_request(req)
        self.assertEqual(set(verdict.applied_gates), {"boundary_gate", "fortress", "citadel", "invariant_validator"})

    def test_export_data_gate_selection(self):
        req = PolicyRequest("REQ-1", "export_data", "P001", "DR-001",
                             {"justification": "Quarterly compliance export", "export_type": "synthetic_only"})
        verdict = self.kernel.evaluate_request(req)
        self.assertEqual(set(verdict.applied_gates), {"boundary_gate", "sentinel"})

    def test_emergency_override_gate_selection(self):
        req = PolicyRequest("REQ-1", "emergency_override", "P001", "DR-001", {
            "emergency_reason": "Critical desaturation requiring immediate action",
            "override_type": "patient_safety",
            "physician_approved": True,
            "can_notify_stakeholders": True,
            "escalated_to_physician": True,
        })
        verdict = self.kernel.evaluate_request(req)
        self.assertEqual(set(verdict.applied_gates), {"boundary_gate", "micropatch", "sentinel"})
        self.assertTrue(verdict.approved)

    def test_audit_trail_grows_and_verifies(self):
        req = PolicyRequest("REQ-1", "escalate_patient", "P001", "DR-001",
                             {"justification": "Patient showing rapid deterioration", "severity": "high"})
        self.kernel.evaluate_request(req)
        self.kernel.evaluate_request(req)
        self.assertEqual(len(self.kernel.export_audit()), 2)
        self.assertTrue(self.kernel.verify_audit_integrity())

    def test_rejected_request_still_audited(self):
        # modify_rule selects citadel, which requires justification (>=10 chars) + rule_id context.
        # escalate_patient does NOT select citadel (per _select_gates), so an empty-context
        # escalate_patient request would actually pass -- this test uses modify_rule instead.
        req = PolicyRequest("REQ-1", "modify_rule", "P001", "DR-001", {})  # missing justification/rule_id
        verdict = self.kernel.evaluate_request(req)
        self.assertFalse(verdict.approved)
        self.assertEqual(len(self.kernel.export_audit()), 1)
        self.assertTrue(self.kernel.verify_audit_integrity())


# ============================================================================
# DGK MULTI-NODE CONSENSUS TESTS
# ============================================================================

class TestDGKSigning(unittest.TestCase):
    def test_sign_and_verify_roundtrip(self):
        key = secrets.token_bytes(32)
        payload = {"node_id": "n1", "decision": {"approved": True}, "timestamp": "2026-01-01T00:00:00+00:00"}
        sig = Signer.sign(payload, key)
        payload["_sig"] = sig
        self.assertTrue(Signer.verify(payload, key))

    def test_verify_fails_with_wrong_key(self):
        key = secrets.token_bytes(32)
        wrong_key = secrets.token_bytes(32)
        payload = {"node_id": "n1", "decision": {"approved": True}, "timestamp": "2026-01-01T00:00:00+00:00"}
        payload["_sig"] = Signer.sign(payload, key)
        self.assertFalse(Signer.verify(payload, wrong_key))


class TestDGKConsensus(unittest.TestCase):
    def setUp(self):
        self.nodes = {f"hospital-{i}": GovernanceNode(f"hospital-{i}", secrets.token_bytes(32)) for i in range(1, 4)}
        self.decider = ConsensusDecider(quorum_fraction=2 / 3)

    def test_unanimous_consensus(self):
        decision = {"approved": True, "confidence": 0.9}
        proposals = [node.propose(decision) for node in self.nodes.values()]
        key_map = {n.node_id: n.key for n in self.nodes.values()}
        result = self.decider.decide(proposals, key_map)
        self.assertEqual(result["consensus_size"], 3)
        self.assertEqual(result["total_proposals"], 3)

    def test_quorum_with_one_dissenter(self):
        nodes = list(self.nodes.values())
        p1 = nodes[0].propose({"approved": True, "confidence": 0.9})
        p2 = nodes[1].propose({"approved": True, "confidence": 0.9})
        p3 = nodes[2].propose({"approved": False, "confidence": 0.5})  # dissent
        key_map = {n.node_id: n.key for n in nodes}
        result = self.decider.decide([p1, p2, p3], key_map)
        self.assertEqual(result["consensus_size"], 2)

    def test_no_quorum_raises(self):
        nodes = list(self.nodes.values())
        p1 = nodes[0].propose({"approved": True})
        p2 = nodes[1].propose({"approved": False})
        p3 = nodes[2].propose({"decision": "something_else"})
        key_map = {n.node_id: n.key for n in nodes}
        with self.assertRaises(RuntimeError):
            self.decider.decide([p1, p2, p3], key_map)

    def test_unknown_node_rejected(self):
        nodes = list(self.nodes.values())
        proposals = [n.propose({"approved": True}) for n in nodes]
        key_map = {nodes[0].node_id: nodes[0].key}  # missing keys for other nodes
        with self.assertRaises(RuntimeError):
            self.decider.decide(proposals, key_map)


class TestDGKGateway(unittest.TestCase):
    def setUp(self):
        self.nodes = {f"hospital-{i}": GovernanceNode(f"hospital-{i}", secrets.token_bytes(32)) for i in range(1, 4)}
        self.gateway = DGKGateway(self.nodes, ConsensusDecider(quorum_fraction=2 / 3))

    def test_routine_escalation_no_consensus_required(self):
        self.assertFalse(self.gateway.require_consensus("escalate_patient"))

    def test_emergency_override_requires_consensus(self):
        self.assertTrue(self.gateway.require_consensus("emergency_override"))

    def test_reach_consensus_full_agreement(self):
        result = self.gateway.reach_consensus({"request_id": "REQ-1", "approved": True, "confidence": 0.9})
        self.assertEqual(result["consensus_size"], 3)
        self.assertIn(result["leader_node"], self.nodes)


class TestKernelWithDGK(unittest.TestCase):
    """Integration: PERCEIVE kernel with DGK gateway for critical decisions."""

    def setUp(self):
        nodes = {f"hospital-{i}": GovernanceNode(f"hospital-{i}", secrets.token_bytes(32)) for i in range(1, 4)}
        self.dgk = DGKGateway(nodes, ConsensusDecider(quorum_fraction=2 / 3))
        self.kernel = PerceiveGovernanceKernel(dgk_gateway=self.dgk)
        manifest = PolicyManifest("v1", "1.0.0", datetime.now(timezone.utc), {})
        self.kernel.register_manifest(manifest)

    def test_emergency_override_runs_dgk_consensus(self):
        req = PolicyRequest("REQ-1", "emergency_override", "P001", "DR-001", {
            "emergency_reason": "Critical desaturation requiring immediate action",
            "override_type": "patient_safety",
            "physician_approved": True,
            "can_notify_stakeholders": True,
            "escalated_to_physician": True,
        })
        verdict = self.kernel.evaluate_request(req)
        self.assertTrue(verdict.approved)
        self.assertIsNotNone(verdict.consensus_result)
        self.assertEqual(verdict.consensus_result["consensus_size"], 3)

    def test_routine_escalation_skips_dgk(self):
        req = PolicyRequest("REQ-2", "escalate_patient", "P001", "DR-001",
                             {"justification": "Patient showing rapid deterioration", "severity": "high"})
        verdict = self.kernel.evaluate_request(req)
        self.assertTrue(verdict.approved)
        self.assertIsNone(verdict.consensus_result)

    def test_gate_rejection_skips_dgk_entirely(self):
        # Missing required context -> citadel/gate rejection -> DGK should not run
        req = PolicyRequest("REQ-3", "emergency_override", "P001", "DR-001", {
            "override_type": "patient_safety",
            # no emergency_reason, no physician approval
        })
        verdict = self.kernel.evaluate_request(req)
        self.assertFalse(verdict.approved)
        self.assertIsNone(verdict.consensus_result)


class TestDGKAwareVerdict(unittest.TestCase):
    def test_is_consensus_decision_flag(self):
        v_no_consensus = DGKAwareVerdict("REQ-1", True, 0.9, consensus_result=None)
        v_with_consensus = DGKAwareVerdict("REQ-2", True, 0.9, consensus_result={"consensus_size": 3})
        self.assertFalse(v_no_consensus.is_consensus_decision)
        self.assertTrue(v_with_consensus.is_consensus_decision)


if __name__ == "__main__":
    unittest.main()
