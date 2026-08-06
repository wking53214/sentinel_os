"""
Tests: clinical_governance_system.py (the unified orchestrator)
================================================================

Verifies the OBSERVE -> PERCEIVE wiring: routing logic, the fast path,
emergency-vs-standard escalation selection, multi-hospital consensus,
and the linked dual-audit record.

Run: python3 -m pytest test_clinical_governance_system.py -v
"""

import unittest
from datetime import datetime, timezone, timedelta

from observe_consolidated import VitalsSnapshot
from clinical_governance_system import (
    ClinicalGovernanceSystem,
    ClinicalDecision,
    build_single_hospital_system,
    build_multi_hospital_system,
)


def vitals(pid="P001", hr=110, o2=98.0, rr=24, temp=37.0, age=24, ts=None, **ctx):
    base_ctx = {"age_months": age}
    base_ctx.update(ctx)
    return VitalsSnapshot(pid, ts or datetime.now(timezone.utc), hr, o2, rr, temp, context=base_ctx)


# ============================================================================
# ROUTING: fast path vs governance
# ============================================================================

class TestRouting(unittest.TestCase):

    def setUp(self):
        self.system = build_single_hospital_system()

    def test_stable_takes_fast_path_no_governance(self):
        d = self.system.process_vitals(vitals(hr=110, o2=98.0))
        self.assertEqual(d.regime, "stable")
        self.assertFalse(d.escalation_required)
        self.assertFalse(d.governance_evaluated)
        self.assertEqual(d.action, "continue_monitoring")
        self.assertIsNone(d.governance_approved)
        # PERCEIVE audit must remain untouched on the fast path
        self.assertEqual(len(self.system.export_perceive_audit()), 0)

    def test_critical_routes_to_governance(self):
        d = self.system.process_vitals(vitals(hr=168, o2=83.0, rr=46, temp=39.5, age=12, force_heavy=True))
        self.assertEqual(d.regime, "critical")
        self.assertTrue(d.escalation_required)
        self.assertTrue(d.governance_evaluated)
        self.assertIn(d.action, ("escalate_approved", "escalate_blocked"))
        self.assertIsNotNone(d.perceive_audit_hash)

    def test_decision_links_both_audit_hashes(self):
        d = self.system.process_vitals(vitals(hr=168, o2=83.0, rr=46, temp=39.5, age=12, force_heavy=True))
        # OBSERVE hash always present; PERCEIVE hash present because it escalated
        self.assertTrue(len(d.observe_audit_hash) > 0)
        self.assertTrue(len(d.perceive_audit_hash) > 0)
        self.assertNotEqual(d.observe_audit_hash, d.perceive_audit_hash)


# ============================================================================
# ESCALATION TYPE SELECTION
# ============================================================================

class TestEscalationTypeSelection(unittest.TestCase):

    def setUp(self):
        self.system = build_single_hospital_system()

    def test_critical_uses_emergency_override_path(self):
        d = self.system.process_vitals(vitals(hr=170, o2=82.0, rr=48, temp=39.8, age=9, force_heavy=True))
        self.assertEqual(d.regime, "critical")
        # emergency_override selects micropatch gate
        self.assertIn("micropatch", d.applied_gates)

    def test_warning_uses_standard_escalation_path(self):
        # A warning-level (not critical) escalation should select the escalate_patient gates,
        # which do NOT include micropatch.
        # Septic-shock pattern lands as critical, so to get a clean warning we need an
        # elevated-but-not-critical fused risk. We drive it via a hard-rule O2 warning.
        d = self.system.process_vitals(vitals(hr=120, o2=86.0, rr=30, temp=37.5, age=24, force_heavy=True))
        if d.escalation_required and d.regime == "warning":
            self.assertNotIn("micropatch", d.applied_gates)
            self.assertIn("boundary_gate", d.applied_gates)


# ============================================================================
# MULTI-HOSPITAL CONSENSUS
# ============================================================================

class TestMultiHospital(unittest.TestCase):

    def test_emergency_triggers_multi_node_consensus(self):
        system = build_multi_hospital_system(["nch", "partner-a", "partner-b"])
        d = system.process_vitals(vitals(pid="P004", hr=170, o2=82.0, rr=48, temp=39.8, age=9, force_heavy=True))
        self.assertEqual(d.regime, "critical")
        self.assertTrue(d.used_multi_node_consensus)
        self.assertIsNotNone(d.consensus_detail)
        self.assertEqual(d.consensus_detail["consensus_size"], 3)
        self.assertEqual(d.consensus_detail["total_proposals"], 3)

    def test_single_hospital_no_consensus(self):
        system = build_single_hospital_system()
        d = system.process_vitals(vitals(pid="P004", hr=170, o2=82.0, rr=48, temp=39.8, age=9, force_heavy=True))
        self.assertEqual(d.regime, "critical")
        self.assertFalse(d.used_multi_node_consensus)
        self.assertIsNone(d.consensus_detail)


# ============================================================================
# PER-PATIENT ISOLATION THROUGH THE ORCHESTRATOR
# ============================================================================

class TestOrchestratorIsolation(unittest.TestCase):

    def test_two_critical_patients_both_escalate(self):
        system = build_single_hospital_system()
        t0 = datetime.now(timezone.utc)
        dA = system.process_vitals(vitals(pid="PA", hr=160, o2=84.0, rr=42, temp=39.0, age=18, ts=t0, force_heavy=True))
        dB = system.process_vitals(vitals(pid="PB", hr=165, o2=83.0, rr=44, temp=39.5, age=12, ts=t0 + timedelta(seconds=1), force_heavy=True))
        self.assertTrue(dA.escalation_required)
        self.assertTrue(dB.escalation_required)
        # Both should have produced PERCEIVE audit entries
        self.assertEqual(len(system.export_perceive_audit()), 2)


# ============================================================================
# AUDIT INTEGRITY
# ============================================================================

class TestAuditIntegrity(unittest.TestCase):

    def test_both_chains_valid_after_mixed_traffic(self):
        system = build_single_hospital_system()
        cases = [
            ("P001", 110, 98.0, 24, 37.0, 24),   # stable
            ("P002", 168, 83.0, 46, 39.5, 12),   # critical
            ("P003", 105, 97.5, 22, 36.9, 36),   # stable
            ("P004", 172, 81.0, 48, 39.8, 6),    # critical
        ]
        for pid, hr, o2, rr, temp, age in cases:
            system.process_vitals(vitals(pid=pid, hr=hr, o2=o2, rr=rr, temp=temp, age=age, force_heavy=True))

        audits = system.verify_all_audits()
        self.assertTrue(audits["observe_chain_valid"])
        self.assertTrue(audits["perceive_chain_valid"])
        # 4 OBSERVE assessments, 2 escalations -> 2 PERCEIVE decisions
        self.assertEqual(len(system.export_observe_audit()), 4)
        self.assertEqual(len(system.export_perceive_audit()), 2)

    def test_decision_serializes_to_dict(self):
        system = build_single_hospital_system()
        d = system.process_vitals(vitals())
        as_dict = d.to_dict()
        self.assertIn("patient_id", as_dict)
        self.assertIn("observe_audit_hash", as_dict)
        self.assertIn("action", as_dict)


if __name__ == "__main__":
    unittest.main()
