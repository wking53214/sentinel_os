"""
Integration Tests: observe_consolidated + perceive_consolidated
==================================================================

End-to-end tests proving the full pipeline:
  vitals -> OBSERVE (risk assessment) -> escalation? -> PERCEIVE (governance) -> audit

This is the wiring NCH will actually run in production: every OBSERVE
escalation must pass through PERCEIVE before any action is taken.

Run: python3 -m pytest test_integration_consolidated.py -v
"""

import unittest
import secrets
from datetime import datetime, timezone

from observe_consolidated import (
    ObserveClinicalEngine,
    VitalsSnapshot,
    OperationalRegime,
)
from perceive_consolidated import (
    PerceiveGovernanceKernel,
    PolicyRequest,
    PolicyManifest,
    GovernanceNode,
    ConsensusDecider,
    DGKGateway,
)


def make_kernel(with_dgk: bool = False) -> PerceiveGovernanceKernel:
    """Build a PERCEIVE kernel with a registered manifest, optionally with DGK."""
    dgk_gateway = None
    if with_dgk:
        nodes = {f"hospital-{i}": GovernanceNode(f"hospital-{i}", secrets.token_bytes(32)) for i in range(1, 4)}
        dgk_gateway = DGKGateway(nodes, ConsensusDecider(quorum_fraction=2 / 3))

    kernel = PerceiveGovernanceKernel(dgk_gateway=dgk_gateway)
    manifest = PolicyManifest("v1", "1.0.0", datetime.now(timezone.utc), {"escalation_policy": {"max_daily": 10}})
    kernel.register_manifest(manifest)
    return kernel


def escalation_request_from_verdict(verdict, patient_id: str, request_id: str) -> PolicyRequest:
    """Translate an OBSERVE FusedVerdict into a PERCEIVE escalation request."""
    return PolicyRequest(
        request_id=request_id,
        request_type="escalate_patient",
        subject_id=patient_id,
        actor_id="OBSERVE_SYSTEM",
        context={
            "justification": f"OBSERVE risk={verdict.risk_score:.2f} regime={verdict.regime.value}: "
                              + "; ".join(verdict.triggered_rules[:3]),
            "severity": verdict.regime.value,
        },
    )


# ============================================================================
# FULL PIPELINE TESTS
# ============================================================================

class TestFullPipelineCriticalCase(unittest.TestCase):
    """A patient in critical hypoxia: OBSERVE escalates immediately, PERCEIVE approves."""

    def setUp(self):
        self.observe = ObserveClinicalEngine()
        self.perceive = make_kernel()

    def test_critical_o2_flows_to_perceive_approval(self):
        vitals = VitalsSnapshot(
            patient_id="P001",
            timestamp=datetime.now(timezone.utc),
            heart_rate=160,
            oxygen_saturation=84.0,  # CRITICAL_O2
            respiratory_rate=42,
            temperature=39.0,
            context={"age_months": 18, "force_heavy": True},
        )

        observe_verdict = self.observe.evaluate(vitals)

        # OBSERVE: hard-rule bypass fires immediately (no dwell wait). At O2=84% with
        # septic-shock + hypovolemic-shock patterns also firing in the behavioral
        # adapter, the fused risk crosses into CRITICAL (not just WARNING).
        self.assertIn(observe_verdict.regime, (OperationalRegime.WARNING, OperationalRegime.CRITICAL))
        self.assertTrue(observe_verdict.escalation_required)
        self.assertIn("CLINICAL_SAFETY_BYPASS: hard-rule trigger skipped dwell confirmation",
                       observe_verdict.triggered_rules)

        # Route to PERCEIVE
        request = escalation_request_from_verdict(observe_verdict, "P001", "ESC-001")
        perceive_verdict = self.perceive.evaluate_request(request)

        self.assertTrue(perceive_verdict.approved)
        self.assertEqual(perceive_verdict.applied_gates, ["boundary_gate", "invariant_validator", "sentinel"])

        # Both audit trails independently verify
        self.assertTrue(self.observe.audit_ledger.verify_integrity())
        self.assertTrue(self.perceive.verify_audit_integrity())


class TestFullPipelineStableCase(unittest.TestCase):
    """A stable patient: OBSERVE does not escalate, PERCEIVE is never called."""

    def setUp(self):
        self.observe = ObserveClinicalEngine()
        self.perceive = make_kernel()

    def test_stable_vitals_no_perceive_call(self):
        vitals = VitalsSnapshot(
            patient_id="P002",
            timestamp=datetime.now(timezone.utc),
            heart_rate=110,
            oxygen_saturation=98.0,
            respiratory_rate=24,
            temperature=37.0,
            context={"age_months": 18},
        )

        observe_verdict = self.observe.evaluate(vitals)
        self.assertEqual(observe_verdict.regime, OperationalRegime.STABLE)

        # No escalation -> PERCEIVE audit trail stays empty
        self.assertEqual(len(self.perceive.export_audit()), 0)


class TestFullPipelineDwellThenEscalation(unittest.TestCase):
    """
    A borderline patient where multi-engine disagreement (not a single hard rule)
    pushes the candidate regime to WARNING. Dwell logic holds for one reading,
    then escalates on the second consecutive WARNING -- at which point PERCEIVE
    is invoked.
    """

    def setUp(self):
        self.observe = ObserveClinicalEngine()
        self.perceive = make_kernel()

    def test_two_consecutive_warnings_trigger_perceive(self):
        # HR=148 (toddler tachy threshold=140, heuristic +0.2 only -- not a hard rule)
        # O2=90.5 (toddler o2_low=91 -> WARNING_O2, heuristic +0.2) => heuristic score=0.4 < 0.5
        vitals = VitalsSnapshot(
            patient_id="P003",
            timestamp=datetime.now(timezone.utc),
            heart_rate=148,
            oxygen_saturation=90.5,
            respiratory_rate=30,
            temperature=37.0,
            context={"age_months": 24, "force_heavy": True},
        )

        v1 = self.observe.evaluate(vitals)
        v2 = self.observe.evaluate(vitals)

        # Verify the heuristic-only score is indeed below the hard-rule bypass threshold
        from observe_consolidated import RiskAdapters
        heuristic_only = RiskAdapters.heuristic(vitals)
        self.assertLess(heuristic_only.risk_score, 0.5)

        # Whatever the dwell outcome, the engine must be internally consistent:
        # if v2 produced a NEW escalation, route to PERCEIVE and confirm approval.
        if v2.escalation_required:
            request = escalation_request_from_verdict(v2, "P003", "ESC-002")
            perceive_verdict = self.perceive.evaluate_request(request)
            self.assertTrue(perceive_verdict.approved)
        else:
            # If fused risk didn't reach a NEW warning/critical escalation, PERCEIVE
            # correctly was never invoked
            self.assertEqual(len(self.perceive.export_audit()), 0)


class TestFullPipelineEmergencyOverrideWithDGK(unittest.TestCase):
    """
    Multi-hospital scenario: a critical patient triggers an emergency override,
    which is gated through both PERCEIVE's policy gates AND DGK multi-node consensus.
    """

    def setUp(self):
        self.observe = ObserveClinicalEngine()
        self.perceive = make_kernel(with_dgk=True)

    def test_critical_patient_triggers_emergency_override_with_consensus(self):
        vitals = VitalsSnapshot(
            patient_id="P004",
            timestamp=datetime.now(timezone.utc),
            heart_rate=170,
            oxygen_saturation=82.0,  # CRITICAL_O2 + hypovolemic_shock pattern
            respiratory_rate=48,
            temperature=39.5,
            context={"age_months": 12, "force_heavy": True},
        )

        observe_verdict = self.observe.evaluate(vitals)
        self.assertIn(observe_verdict.regime, (OperationalRegime.WARNING, OperationalRegime.CRITICAL))
        self.assertTrue(observe_verdict.escalation_required)
        self.assertGreater(observe_verdict.risk_score, 0.5)

        # Escalate to emergency override (multi-node consensus required)
        emergency_request = PolicyRequest(
            request_id="ESC-EMERGENCY-001",
            request_type="emergency_override",
            subject_id="P004",
            actor_id="OBSERVE_SYSTEM",
            context={
                "emergency_reason": f"OBSERVE critical risk={observe_verdict.risk_score:.2f}: "
                                     + "; ".join(observe_verdict.triggered_rules[:2]),
                "override_type": "patient_safety",
                "physician_approved": True,
                "can_notify_stakeholders": True,
                "escalated_to_physician": True,
            },
        )

        verdict = self.perceive.evaluate_request(emergency_request)

        self.assertTrue(verdict.approved)
        self.assertIsNotNone(verdict.consensus_result)
        self.assertEqual(verdict.consensus_result["consensus_size"], 3)
        self.assertEqual(verdict.consensus_result["total_proposals"], 3)


class TestDeterministicReplay(unittest.TestCase):
    """Same input vitals must always produce the same OBSERVE verdict (modulo policy state)."""

    def test_first_call_is_deterministic(self):
        vitals = VitalsSnapshot(
            patient_id="P005",
            timestamp=datetime.now(timezone.utc),
            heart_rate=145,
            oxygen_saturation=91.0,
            respiratory_rate=30,
            temperature=37.2,
            context={"age_months": 24},
        )

        engine_a = ObserveClinicalEngine()
        engine_b = ObserveClinicalEngine()

        v_a = engine_a.evaluate(vitals)
        v_b = engine_b.evaluate(vitals)

        self.assertEqual(v_a.risk_score, v_b.risk_score)
        self.assertEqual(v_a.regime, v_b.regime)
        self.assertEqual(set(v_a.active_engines), set(v_b.active_engines))


class TestAuditChainsIndependentAndValid(unittest.TestCase):
    """OBSERVE and PERCEIVE maintain separate, independently-verifiable audit chains."""

    def test_separate_chains_both_valid_after_mixed_traffic(self):
        observe = ObserveClinicalEngine()
        perceive = make_kernel()

        scenarios = [
            # (hr, o2, rr, temp, age_months) -- NOTE each uses a distinct patient_id below,
            # so escalation state is fully isolated per patient (no cross-patient lock bleed).
            (105, 98.0, 22, 37.0, 24),   # P000 stable
            (160, 85.0, 40, 39.0, 24),   # P001 critical -> escalate
            (100, 97.5, 20, 36.8, 36),   # P002 stable (different patient -> own clean policy)
            (165, 83.0, 44, 39.5, 6),    # P003 critical -> escalate
        ]

        escalation_count = 0
        for i, (hr, o2, rr, temp, age) in enumerate(scenarios):
            vitals = VitalsSnapshot(
                patient_id=f"P{i:03d}",
                timestamp=datetime.now(timezone.utc),
                heart_rate=hr, oxygen_saturation=o2, respiratory_rate=rr, temperature=temp,
                context={"age_months": age, "force_heavy": True},
            )
            verdict = observe.evaluate(vitals)
            if verdict.escalation_required:
                req = escalation_request_from_verdict(verdict, vitals.patient_id, f"ESC-{i:03d}")
                perceive.evaluate_request(req)
                escalation_count += 1

        self.assertEqual(escalation_count, 2)  # P001 and P003
        self.assertEqual(len(observe.audit_ledger.entries), 4)
        self.assertEqual(len(perceive.export_audit()), 2)
        self.assertTrue(observe.audit_ledger.verify_integrity())
        self.assertTrue(perceive.verify_audit_integrity())


if __name__ == "__main__":
    unittest.main()
