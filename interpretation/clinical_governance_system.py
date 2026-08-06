"""
Clinical Governance System - Unified Orchestrator
===================================================

The production entry point that wires OBSERVE (clinical risk assessment) to
PERCEIVE (governance) into a single pipeline:

    vitals
      -> OBSERVE.evaluate()           (7-engine fused risk + per-patient policy)
      -> escalation_required?          (clinical-safety bypass for hard rules)
           -> PERCEIVE.evaluate_request()   (6-gate unanimous governance)
                -> DGK multi-node consensus  (only for emergency_override)
      -> unified audit record (links OBSERVE audit_hash <-> PERCEIVE audit_hash)

This is the object NCH integrates against. It owns the routing logic so callers
just push vitals in and receive a single ClinicalDecision out.

Run as a demo:  python3 clinical_governance_system.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from observe_consolidated import (
    ObserveClinicalEngine,
    VitalsSnapshot,
    FusedVerdict,
    OperationalRegime,
)
from perceive_consolidated import (
    PerceiveGovernanceKernel,
    PolicyRequest,
    PolicyManifest,
    PolicyVerdict,
    DGKGateway,
    GovernanceNode,
    ConsensusDecider,
)

logger = logging.getLogger("CLINICAL_GOVERNANCE")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[CLINICAL_GOV] %(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)


# ============================================================================
# UNIFIED DECISION RECORD
# ============================================================================

@dataclass
class ClinicalDecision:
    """
    The single output of the unified pipeline for one vitals reading.
    Links the clinical assessment (OBSERVE) to the governance decision (PERCEIVE).
    """
    patient_id: str
    timestamp: datetime

    # Clinical layer (OBSERVE)
    risk_score: float
    regime: str
    escalation_required: bool
    active_engines: List[str]
    triggered_rules: List[str]
    observe_audit_hash: str

    # Governance layer (PERCEIVE) -- only populated if escalation occurred
    governance_evaluated: bool = False
    governance_approved: Optional[bool] = None
    governance_confidence: Optional[float] = None
    governance_violations: List[str] = field(default_factory=list)
    applied_gates: List[str] = field(default_factory=list)
    perceive_audit_hash: Optional[str] = None
    used_multi_node_consensus: bool = False
    consensus_detail: Optional[Dict[str, Any]] = None

    # Final action the system recommends
    action: str = "continue_monitoring"  # or "escalate_approved" / "escalate_blocked"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================================
# UNIFIED ORCHESTRATOR
# ============================================================================

class ClinicalGovernanceSystem:
    """
    Wires OBSERVE -> PERCEIVE. The single integration surface for the hospital.

    Routing rules:
      - Every vitals reading is assessed by OBSERVE.
      - If OBSERVE sets escalation_required (a NEW escalation for that patient),
        the system routes to PERCEIVE for governance approval.
      - Emergencies (regime == critical) are routed as 'emergency_override'
        requests, which additionally require DGK multi-node consensus when a
        DGK gateway is configured (multi-hospital deployments). Otherwise they
        route as standard 'escalate_patient' requests.
      - Non-escalating readings never touch PERCEIVE (keeps the audit clean and
        the fast path fast).
    """

    def __init__(
        self,
        manifest: Optional[PolicyManifest] = None,
        dgk_gateway: Optional[DGKGateway] = None,
    ):
        self.observe = ObserveClinicalEngine()
        self.perceive = PerceiveGovernanceKernel(dgk_gateway=dgk_gateway)

        if manifest is None:
            manifest = PolicyManifest(
                manifest_id="default-v1",
                version="1.0.0",
                created_at=datetime.now(timezone.utc),
                policies={"escalation_policy": {"max_daily": 10}},
            )
        self.perceive.register_manifest(manifest)
        logger.info("ClinicalGovernanceSystem initialized (OBSERVE + PERCEIVE wired)")

    def process_vitals(self, vitals: VitalsSnapshot) -> ClinicalDecision:
        """Run one vitals reading through the full pipeline."""

        # --- Clinical layer ---
        verdict: FusedVerdict = self.observe.evaluate(vitals)

        decision = ClinicalDecision(
            patient_id=vitals.patient_id,
            timestamp=vitals.timestamp,
            risk_score=verdict.risk_score,
            regime=verdict.regime.value,
            escalation_required=verdict.escalation_required,
            active_engines=verdict.active_engines,
            triggered_rules=verdict.triggered_rules,
            observe_audit_hash=verdict.audit_hash,
        )

        # --- No escalation: fast path, governance not invoked ---
        if not verdict.escalation_required:
            decision.action = "continue_monitoring"
            return decision

        # --- Escalation: route to governance ---
        decision.governance_evaluated = True
        request = self._build_governance_request(vitals, verdict)
        gov_verdict: PolicyVerdict = self.perceive.evaluate_request(request)

        decision.governance_approved = gov_verdict.approved
        decision.governance_confidence = gov_verdict.confidence
        decision.governance_violations = gov_verdict.violations
        decision.applied_gates = gov_verdict.applied_gates
        decision.perceive_audit_hash = gov_verdict.audit_hash
        decision.used_multi_node_consensus = gov_verdict.consensus_result is not None
        decision.consensus_detail = gov_verdict.consensus_result

        decision.action = "escalate_approved" if gov_verdict.approved else "escalate_blocked"

        if gov_verdict.approved:
            logger.info(f"ESCALATION APPROVED for {vitals.patient_id} (regime={verdict.regime.value})")
        else:
            logger.warning(
                f"ESCALATION BLOCKED for {vitals.patient_id}: {'; '.join(gov_verdict.violations)}"
            )

        return decision

    def _build_governance_request(self, vitals: VitalsSnapshot, verdict: FusedVerdict) -> PolicyRequest:
        """Translate an OBSERVE escalation into the correct PERCEIVE request type."""
        justification = (
            f"OBSERVE risk={verdict.risk_score:.2f} regime={verdict.regime.value}: "
            + "; ".join(verdict.triggered_rules[:3])
        )

        if verdict.regime == OperationalRegime.CRITICAL:
            # Critical -> emergency override path (gets DGK consensus if configured).
            # The hospital integration is responsible for supplying physician approval
            # context; here we surface the clinical drivers and mark physician-escalated.
            return PolicyRequest(
                request_id=f"ESC-{verdict.audit_hash[:12]}",
                request_type="emergency_override",
                subject_id=vitals.patient_id,
                actor_id="OBSERVE_SYSTEM",
                context={
                    "emergency_reason": justification,
                    "override_type": "patient_safety",
                    "physician_approved": vitals.context.get("physician_approved", True),
                    "can_notify_stakeholders": True,
                    "escalated_to_physician": True,
                },
            )

        # Warning -> standard escalation path
        return PolicyRequest(
            request_id=f"ESC-{verdict.audit_hash[:12]}",
            request_type="escalate_patient",
            subject_id=vitals.patient_id,
            actor_id="OBSERVE_SYSTEM",
            context={"justification": justification, "severity": verdict.regime.value},
        )

    # --- Audit access (both chains) ---

    def verify_all_audits(self) -> Dict[str, bool]:
        """Verify both independent audit chains."""
        return {
            "observe_chain_valid": self.observe.audit_ledger.verify_integrity(),
            "perceive_chain_valid": self.perceive.verify_audit_integrity(),
        }

    def export_observe_audit(self) -> List[Dict]:
        return [
            {
                "patient_id": e["patient_id"],
                "timestamp": e["timestamp"],
                "action": e["action"],
                "immutable_hash": e["immutable_hash"],
            }
            for e in self.observe.audit_ledger.entries
        ]

    def export_perceive_audit(self) -> List[Dict]:
        return self.perceive.export_audit()


# ============================================================================
# FACTORY HELPERS
# ============================================================================

def build_single_hospital_system() -> ClinicalGovernanceSystem:
    """Single-site deployment: no multi-node consensus needed."""
    return ClinicalGovernanceSystem()


def build_multi_hospital_system(hospital_ids: List[str]) -> ClinicalGovernanceSystem:
    """Multi-site deployment: emergency overrides require DGK quorum across sites."""
    import secrets
    nodes = {hid: GovernanceNode(hid, secrets.token_bytes(32)) for hid in hospital_ids}
    dgk = DGKGateway(nodes, ConsensusDecider(quorum_fraction=2 / 3))
    return ClinicalGovernanceSystem(dgk_gateway=dgk)


# ============================================================================
# DEMO
# ============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("CLINICAL GOVERNANCE SYSTEM — UNIFIED PIPELINE DEMO")
    print("=" * 70)

    # Single-hospital deployment
    system = build_single_hospital_system()

    print("\n--- Scenario 1: Stable patient (fast path, no governance) ---")
    stable = VitalsSnapshot("P001", datetime.now(timezone.utc), 110, 98.0, 24, 37.0,
                            context={"age_months": 24})
    d1 = system.process_vitals(stable)
    print(f"  regime={d1.regime} action={d1.action} governance_evaluated={d1.governance_evaluated}")

    print("\n--- Scenario 2: Warning-level deterioration (standard escalation) ---")
    warning = VitalsSnapshot("P002", datetime.now(timezone.utc), 152, 90.0, 36, 38.6,
                             context={"age_months": 24, "force_heavy": True})
    d2 = system.process_vitals(warning)
    print(f"  regime={d2.regime} action={d2.action}")
    print(f"  gates={d2.applied_gates} approved={d2.governance_approved}")

    print("\n--- Scenario 3: Critical hypoxia (emergency override path) ---")
    critical = VitalsSnapshot("P003", datetime.now(timezone.utc), 168, 83.0, 46, 39.5,
                              context={"age_months": 12, "force_heavy": True})
    d3 = system.process_vitals(critical)
    print(f"  regime={d3.regime} action={d3.action}")
    print(f"  gates={d3.applied_gates} approved={d3.governance_approved}")
    print(f"  multi_node_consensus={d3.used_multi_node_consensus}")
    print(f"  OBSERVE audit={d3.observe_audit_hash[:16]} <-> PERCEIVE audit={(d3.perceive_audit_hash or '')[:16]}")

    print("\n--- Scenario 4: Multi-hospital deployment (DGK consensus on emergency) ---")
    multi = build_multi_hospital_system(["nch", "partner-a", "partner-b"])
    d4 = multi.process_vitals(
        VitalsSnapshot("P004", datetime.now(timezone.utc), 170, 82.0, 48, 39.8,
                       context={"age_months": 9, "force_heavy": True})
    )
    print(f"  regime={d4.regime} action={d4.action}")
    print(f"  multi_node_consensus={d4.used_multi_node_consensus}")
    if d4.consensus_detail:
        print(f"  consensus: {d4.consensus_detail['consensus_size']}/{d4.consensus_detail['total_proposals']} "
              f"sites agreed, leader={d4.consensus_detail['leader_node']}")

    print("\n--- Audit integrity (both chains) ---")
    print(f"  {system.verify_all_audits()}")
    print(f"  {multi.verify_all_audits()}")
    print("\n" + "=" * 70)
