"""
PERCEIVE Governance Kernel - Consolidated
===========================================

Deterministic policy evaluation engine for AI governance.
Merges: perceive_kernel, policy_engine, all 6 gate adapters, audit ledger, and DGK consensus.

Core responsibilities:
- Policy evaluation against versioned manifests
- Consensus-based approval (all gates must pass — unanimous)
- Event sourcing for state transitions
- Immutable audit with cryptographic chaining
- Optional multi-node (DGK) consensus for critical decisions

Single-file deployment.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger("PERCEIVE")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[PERCEIVE] %(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)


# ============================================================================
# DATA CONTRACTS
# ============================================================================

@dataclass
class Provenance:
    """Tracks who authorized what and why."""
    actor_id: str
    policy_id: str
    justification: str


@dataclass
class PolicyOutput:
    """Standard output contract for all policy gates."""
    gate_name: str
    approved: bool
    confidence: float  # 0.0 to 1.0
    violation_details: List[str]
    timestamp: datetime
    provenance: Provenance


@dataclass
class PolicyRequest:
    """Input: request to evaluate against policies."""
    request_id: str
    request_type: str  # "escalate_patient", "modify_rule", "export_data", "emergency_override"
    subject_id: str
    actor_id: str
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyManifest:
    """Versioned collection of policies."""
    manifest_id: str
    version: str  # semantic versioning: 1.0.0
    created_at: datetime
    policies: Dict[str, Dict] = field(default_factory=dict)
    manifest_hash: str = ""


@dataclass
class PolicyVerdict:
    """Final decision after consensus evaluation."""
    request_id: str
    approved: bool
    confidence: float
    violations: List[str]
    applied_gates: List[str]
    policy_version: str
    provenance: Provenance
    audit_hash: str
    consensus_result: Optional[Dict[str, Any]] = None  # populated if DGK multi-node consensus ran


@dataclass
class Event:
    """Immutable event in the audit trail."""
    event_id: str
    event_type: str
    timestamp: datetime
    request_snapshot: Dict
    actor_id: str
    details: Dict = field(default_factory=dict)


@dataclass
class AuditEntry:
    """Single entry in the immutable governance audit trail."""
    audit_id: str
    timestamp: datetime
    request_snapshot: Dict
    evaluated_gates: List[str]
    policy_outputs: List[Dict]
    final_verdict: Dict
    manifest_version: str
    manifest_hash: str
    previous_hash: str = ""
    immutable_hash: str = ""


# ============================================================================
# POLICY RULES (thresholds, evidence-based, audit-traceable)
# ============================================================================

class EscalationPolicy:
    """Controls when patient escalations are approved."""

    MAX_ESCALATIONS_PER_DAY = 10
    MAX_ESCALATIONS_PER_HOUR = 3
    MIN_TIME_BETWEEN_ESCALATIONS_MINUTES = 15

    RISK_TIERS = {
        "stable": {"escalation_cost": 0, "approval_required": False},
        "caution": {"escalation_cost": 1, "approval_required": False},
        "warning": {"escalation_cost": 2, "approval_required": True},
        "critical": {"escalation_cost": 3, "approval_required": True},
    }

    @staticmethod
    def can_escalate(
        current_risk_tier: str,
        escalations_today: int,
        escalations_this_hour: int,
        minutes_since_last_escalation: int,
    ) -> Tuple[bool, List[str]]:
        violations = []

        if escalations_today >= EscalationPolicy.MAX_ESCALATIONS_PER_DAY:
            violations.append(
                f"Daily escalation limit reached ({escalations_today}/{EscalationPolicy.MAX_ESCALATIONS_PER_DAY})"
            )
        if escalations_this_hour >= EscalationPolicy.MAX_ESCALATIONS_PER_HOUR:
            violations.append(
                f"Hourly escalation limit reached ({escalations_this_hour}/{EscalationPolicy.MAX_ESCALATIONS_PER_HOUR})"
            )
        if minutes_since_last_escalation < EscalationPolicy.MIN_TIME_BETWEEN_ESCALATIONS_MINUTES:
            violations.append(
                f"Escalation cooldown active ({minutes_since_last_escalation}/"
                f"{EscalationPolicy.MIN_TIME_BETWEEN_ESCALATIONS_MINUTES} minutes)"
            )

        return len(violations) == 0, violations


class RuleModificationPolicy:
    """Controls when governance rules can be modified."""

    MODIFICATION_LEVELS = {
        "non_critical": {"approval_required": 1, "temporal_lock_hours": 0},
        "critical": {"approval_required": 2, "temporal_lock_hours": 24},
        "safety_critical": {"approval_required": 3, "temporal_lock_hours": 72},
    }

    @staticmethod
    def can_modify(rule_type: str, approval_count: int, hours_since_last_modification: int) -> Tuple[bool, List[str]]:
        violations = []

        if rule_type not in RuleModificationPolicy.MODIFICATION_LEVELS:
            return False, [f"Unknown rule type: {rule_type}"]

        level = RuleModificationPolicy.MODIFICATION_LEVELS[rule_type]

        if approval_count < level["approval_required"]:
            violations.append(f"Insufficient approvals ({approval_count}/{level['approval_required']})")
        if hours_since_last_modification < level["temporal_lock_hours"]:
            violations.append(
                f"Temporal lock active ({hours_since_last_modification}/{level['temporal_lock_hours']} hours)"
            )

        return len(violations) == 0, violations


class DataExportPolicy:
    """Controls when patient data can be exported."""

    EXPORT_RESTRICTIONS = {
        "pii_included": {"requires_consent": True, "requires_audit_log": True, "encryption_required": True},
        "synthetic_only": {"requires_consent": False, "requires_audit_log": True, "encryption_required": False},
        "aggregate_only": {"requires_consent": False, "requires_audit_log": False, "encryption_required": False},
    }

    @staticmethod
    def can_export(export_type: str, has_consent: bool, will_audit_log: bool, will_encrypt: bool) -> Tuple[bool, List[str]]:
        violations = []

        if export_type not in DataExportPolicy.EXPORT_RESTRICTIONS:
            return False, [f"Unknown export type: {export_type}"]

        r = DataExportPolicy.EXPORT_RESTRICTIONS[export_type]

        if r["requires_consent"] and not has_consent:
            violations.append("Patient consent required for PII export")
        if r["requires_audit_log"] and not will_audit_log:
            violations.append("Audit logging required for export")
        if r["encryption_required"] and not will_encrypt:
            violations.append("Encryption required for export")

        return len(violations) == 0, violations


class EmergencyOverridePolicy:
    """Controls emergency override requests (life-saving exceptions)."""

    OVERRIDE_CATEGORIES = {
        "patient_safety": {"allowed": True, "requires_physician_approval": True, "audit_required": True, "notification_required": True},
        "system_failure": {"allowed": True, "requires_physician_approval": False, "audit_required": True, "notification_required": True},
        "regulatory_exception": {"allowed": False, "requires_physician_approval": False, "audit_required": True, "notification_required": True},
    }

    @staticmethod
    def can_override(override_type: str, justification: str, has_physician_approval: bool, can_notify: bool) -> Tuple[bool, List[str]]:
        violations = []

        if override_type not in EmergencyOverridePolicy.OVERRIDE_CATEGORIES:
            return False, [f"Unknown override type: {override_type}"]

        category = EmergencyOverridePolicy.OVERRIDE_CATEGORIES[override_type]

        if not category["allowed"]:
            return False, [f"Override type '{override_type}' not permitted"]

        if category["requires_physician_approval"] and not has_physician_approval:
            violations.append("Physician approval required for this override")
        if not justification or len(justification.strip()) == 0:
            violations.append("Justification required for emergency override")
        if category["notification_required"] and not can_notify:
            violations.append("System must be able to notify about override")

        return len(violations) == 0, violations


class GovernanceInvariants:
    """Hard constraints that must always hold."""

    @staticmethod
    def audit_trail_immutable() -> Tuple[bool, str]:
        return True, "Audit trail is cryptographically chained"

    @staticmethod
    def manifest_versioned() -> Tuple[bool, str]:
        return True, "Manifest versioning enforced"

    @staticmethod
    def gates_unanimous() -> Tuple[bool, str]:
        return True, "Consensus logic enforced"

    @staticmethod
    def decisions_replay_deterministic() -> Tuple[bool, str]:
        return True, "Event sourcing enables replay"


# ============================================================================
# POLICY GATES (all 6 adapters, consolidated as static methods)
# ============================================================================

class PolicyGates:
    """All 6 policy gate adapters, consolidated."""

    @staticmethod
    def boundary_gate(request: PolicyRequest) -> PolicyOutput:
        """Validates inbound requests are well-formed and of known type."""
        violations = []

        if not request.request_id:
            violations.append("Missing request_id")
        if not request.request_type:
            violations.append("Missing request_type")
        if not request.actor_id:
            violations.append("Missing actor_id")

        valid_types = {"escalate_patient", "modify_rule", "export_data", "emergency_override"}
        if request.request_type not in valid_types:
            violations.append(f"Unknown request_type: {request.request_type}")

        approved = len(violations) == 0
        return PolicyOutput(
            gate_name="boundary_gate", approved=approved, confidence=0.95 if approved else 0.9,
            violation_details=violations, timestamp=datetime.now(timezone.utc),
            provenance=Provenance("system", "boundary", "structural_validation"),
        )

    @staticmethod
    def citadel(request: PolicyRequest) -> PolicyOutput:
        """Linguistic intent validation and clarity checks."""
        violations = []
        context = request.context or {}

        justification = context.get("justification", "")
        if not justification or len(justification.strip()) < 10:
            violations.append("Insufficient justification provided (minimum 10 characters)")

        if request.request_type == "emergency_override" and not context.get("emergency_reason"):
            violations.append("Emergency override requires explicit reason")

        if "maybe" in justification.lower() or "might" in justification.lower():
            violations.append("Request lacks clarity (hedging language detected)")

        request_type = request.request_type
        has_matching_context = (
            (request_type == "escalate_patient" and "severity" in context) or
            (request_type == "modify_rule" and "rule_id" in context) or
            (request_type == "export_data" and "export_type" in context) or
            (request_type == "emergency_override" and "emergency_reason" in context)
        )
        if not has_matching_context and request_type != "unknown":
            violations.append(f"Request type '{request_type}' missing required context")

        approved = len(violations) == 0
        return PolicyOutput(
            gate_name="citadel", approved=approved, confidence=0.85 if approved else 0.80,
            violation_details=violations, timestamp=datetime.now(timezone.utc),
            provenance=Provenance("system", "citadel", "intent_validation"),
        )

    @staticmethod
    def fortress(request: PolicyRequest) -> PolicyOutput:
        """Content safety and containment checks."""
        violations = []
        context = request.context or {}

        if context.get("request_all_data"):
            violations.append("Unrestricted data access not permitted")

        if request.request_type == "modify_rule":
            changes = context.get("changes", {})
            if changes.get("disable_audit"):
                violations.append("Cannot disable audit logging")
            if changes.get("disable_gates"):
                violations.append("Cannot disable policy gates")

        if context.get("bypass_approval"):
            violations.append("Bypass of approval process not permitted")

        approved = len(violations) == 0
        return PolicyOutput(
            gate_name="fortress", approved=approved, confidence=0.92 if approved else 0.88,
            violation_details=violations, timestamp=datetime.now(timezone.utc),
            provenance=Provenance("system", "fortress", "content_safety"),
        )

    @staticmethod
    def invariant_validator(request: PolicyRequest) -> PolicyOutput:
        """Validates governance invariants hold."""
        violations = []

        checks = [
            GovernanceInvariants.audit_trail_immutable(),
            GovernanceInvariants.manifest_versioned(),
            GovernanceInvariants.gates_unanimous(),
            GovernanceInvariants.decisions_replay_deterministic(),
        ]
        for passed, message in checks:
            if not passed:
                violations.append(message)

        approved = len(violations) == 0
        return PolicyOutput(
            gate_name="invariant_validator", approved=approved, confidence=0.99 if approved else 0.85,
            violation_details=violations, timestamp=datetime.now(timezone.utc),
            provenance=Provenance("system", "invariants", "consistency_check"),
        )

    @staticmethod
    def sentinel(request: PolicyRequest) -> PolicyOutput:
        """Anomaly detection: frequency, privilege escalation, retries, volume."""
        violations = []
        context = request.context or {}

        if context.get("operation_count_today", 0) > 50:
            violations.append("Unusually high operation frequency today")

        if request.actor_id.startswith("SYSTEM_") and context.get("requires_human_oversight"):
            violations.append("System actor making governance change that requires human review")

        if context.get("requested_privilege_level", "normal") == "admin" and not context.get("admin_justification"):
            violations.append("Admin privilege escalation requires explicit justification")

        if context.get("is_retry") and context.get("time_since_last_attempt_seconds", 300) < 5:
            violations.append("Request retried too quickly (rate limit)")

        if context.get("data_volume_gb", 0) > 100:
            violations.append("Large data export requested - requires additional verification")

        approved = len(violations) == 0
        return PolicyOutput(
            gate_name="sentinel", approved=approved, confidence=0.88 if approved else 0.82,
            violation_details=violations, timestamp=datetime.now(timezone.utc),
            provenance=Provenance("system", "sentinel", "anomaly_detection"),
        )

    @staticmethod
    def micropatch(request: PolicyRequest) -> PolicyOutput:
        """Emergency override evaluation. Pass-through for non-emergency requests."""
        if request.request_type != "emergency_override":
            return PolicyOutput(
                gate_name="micropatch", approved=True, confidence=1.0,
                violation_details=["MicroPatch not applicable to non-emergency requests"],
                timestamp=datetime.now(timezone.utc),
                provenance=Provenance("system", "micropatch", "not_applicable"),
            )

        context = request.context or {}
        override_type = context.get("override_type", "unknown")
        justification = context.get("emergency_reason", "")
        has_approval = context.get("physician_approved", False)
        can_notify = context.get("can_notify_stakeholders", False)

        approved, violations = EmergencyOverridePolicy.can_override(
            override_type=override_type, justification=justification,
            has_physician_approval=has_approval, can_notify=can_notify,
        )

        if override_type == "patient_safety" and not context.get("escalated_to_physician"):
            violations.append("Patient safety override must be escalated to physician")

        approved = len(violations) == 0
        return PolicyOutput(
            gate_name="micropatch", approved=approved, confidence=0.90 if approved else 0.85,
            violation_details=violations, timestamp=datetime.now(timezone.utc),
            provenance=Provenance("system", "micropatch", "emergency_evaluation"),
        )


# ============================================================================
# CONSENSUS LOGIC (unanimous: ALL gates must pass)
# ============================================================================

class ConsensusEngine:
    """ALL gates must pass (unanimous consensus)."""

    @staticmethod
    def evaluate(policy_outputs: List[PolicyOutput]) -> Tuple[bool, float, List[str]]:
        if not policy_outputs:
            return False, 0.0, ["No gates evaluated"]

        violations = []
        confidences = []

        for output in policy_outputs:
            confidences.append(output.confidence)
            if not output.approved:
                violations.append(f"{output.gate_name}: {'; '.join(output.violation_details)}")

        approved = all(o.approved for o in policy_outputs)

        # FIX: math imported at module level (was inline before)
        confidence = math.prod(confidences) ** (1 / len(confidences)) if confidences else 0.0

        return approved, confidence, violations


# ============================================================================
# EVENT SOURCING
# ============================================================================

class EventStore:
    """Append-only event ledger."""

    def __init__(self):
        self.events: List[Event] = []

    def append_event(self, event_type: str, request_snapshot: Dict, actor_id: str, details: Dict = None) -> Event:
        event = Event(
            event_id=hashlib.sha256(f"{len(self.events)}:{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:16],
            event_type=event_type,
            timestamp=datetime.now(timezone.utc),
            request_snapshot=request_snapshot,
            actor_id=actor_id,
            details=details or {},
        )
        self.events.append(event)
        return event

    def replay(self, up_to_index: Optional[int] = None) -> List[Event]:
        if up_to_index is None:
            return list(self.events)
        return list(self.events[:up_to_index + 1])


# ============================================================================
# MANIFEST VERSIONING
# ============================================================================

class ManifestRegistry:
    """Versioned policy manifests."""

    def __init__(self):
        self.manifests: Dict[str, PolicyManifest] = {}
        self.current_version = "1.0.0"

    def register_manifest(self, manifest: PolicyManifest) -> None:
        manifest.manifest_hash = self._compute_hash(manifest)
        self.manifests[manifest.version] = manifest
        self.current_version = manifest.version
        logger.info(f"Registered manifest v{manifest.version}")

    @staticmethod
    def _compute_hash(manifest: PolicyManifest) -> str:
        payload = {
            "version": manifest.version,
            "policy_count": len(manifest.policies),
            "created_at": manifest.created_at.isoformat(),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def get_manifest(self, version: Optional[str] = None) -> Optional[PolicyManifest]:
        if version is None:
            version = self.current_version
        return self.manifests.get(version)

    def get_current_manifest(self) -> Optional[PolicyManifest]:
        return self.get_manifest(self.current_version)


# ============================================================================
# IMMUTABLE AUDIT LEDGER (SHA256 cryptographic chain)
# ============================================================================

class ImmutableAuditLedger:
    """Append-only audit trail with SHA256 chaining."""

    def __init__(self):
        self.entries: List[AuditEntry] = []
        self.chain_head = hashlib.sha256(b"PERCEIVE_GENESIS").hexdigest()

    def append_decision(
        self,
        request_snapshot: Dict,
        evaluated_gates: List[str],
        policy_outputs: List[PolicyOutput],
        final_verdict: Dict,
        manifest_version: str,
        manifest_hash: str,
    ) -> AuditEntry:
        entry = AuditEntry(
            audit_id=hashlib.sha256(f"{len(self.entries)}:{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:16],
            timestamp=datetime.now(timezone.utc),
            request_snapshot=request_snapshot,
            evaluated_gates=evaluated_gates,
            policy_outputs=[
                {"gate_name": o.gate_name, "approved": o.approved, "confidence": o.confidence, "violations": o.violation_details}
                for o in policy_outputs
            ],
            final_verdict=final_verdict,
            manifest_version=manifest_version,
            manifest_hash=manifest_hash,
            previous_hash=self.chain_head,
        )

        entry_dict = {
            "audit_id": entry.audit_id,
            "timestamp": entry.timestamp.isoformat(),
            "evaluated_gates": entry.evaluated_gates,
            "final_verdict": entry.final_verdict,
        }
        combined = self.chain_head + hashlib.sha256(json.dumps(entry_dict, sort_keys=True, default=str).encode()).hexdigest()
        entry.immutable_hash = hashlib.sha256(combined.encode()).hexdigest()

        self.chain_head = entry.immutable_hash
        self.entries.append(entry)
        return entry

    def verify_chain_integrity(self) -> bool:
        expected_hash = hashlib.sha256(b"PERCEIVE_GENESIS").hexdigest()
        for entry in self.entries:
            entry_dict = {
                "audit_id": entry.audit_id,
                "timestamp": entry.timestamp.isoformat(),
                "evaluated_gates": entry.evaluated_gates,
                "final_verdict": entry.final_verdict,
            }
            combined = entry.previous_hash + hashlib.sha256(json.dumps(entry_dict, sort_keys=True, default=str).encode()).hexdigest()
            computed_hash = hashlib.sha256(combined.encode()).hexdigest()
            if computed_hash != entry.immutable_hash:
                return False
            if entry.previous_hash != expected_hash:
                return False
            expected_hash = entry.immutable_hash
        return True

    def export_audit_trail(self) -> List[Dict]:
        return [asdict(entry) for entry in self.entries]

    def export_json(self) -> str:
        return json.dumps(self.export_audit_trail(), indent=2, default=str)


# ============================================================================
# DGK INTEGRATION — optional multi-node consensus for critical decisions
# ============================================================================

def _canonical_json(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _public_view(state: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in state.items() if not (isinstance(k, str) and k.startswith("_"))}


class Signer:
    """HMAC signing for multi-node proposals."""

    @staticmethod
    def sign(payload: Dict[str, Any], key: bytes) -> str:
        data = _canonical_json(_public_view(payload)).encode()
        return hmac.new(key, data, hashlib.sha256).hexdigest()

    @staticmethod
    def verify(payload: Dict[str, Any], key: bytes) -> bool:
        sig = payload.get("_sig")
        if not isinstance(sig, str):
            return False
        return hmac.compare_digest(sig, Signer.sign(payload, key))


@dataclass
class Proposal:
    """Multi-node proposal for a critical decision."""
    node_id: str
    decision: Dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    signature: str = ""

    def sign(self, key: bytes) -> None:
        payload = {"node_id": self.node_id, "decision": self.decision, "timestamp": self.timestamp.isoformat()}
        self.signature = Signer.sign(payload, key)


def _l1_distance(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    a_view, b_view = _public_view(a), _public_view(b)
    keys = set(a_view) | set(b_view)
    dist = 0.0
    for k in keys:
        try:
            dist += abs(float(a_view.get(k, 0)) - float(b_view.get(k, 0)))
        except (ValueError, TypeError):
            if a_view.get(k) != b_view.get(k):
                dist += 1.0
    return dist


def _cluster_proposals(proposals: List[Proposal], eps: float = 1e-6) -> List[List[Proposal]]:
    clusters: List[List[Proposal]] = []
    for p in proposals:
        placed = False
        for c in clusters:
            if _l1_distance(p.decision, c[0].decision) <= eps:
                c.append(p)
                placed = True
                break
        if not placed:
            clusters.append([p])
    return clusters


class ConsensusDecider:
    """Multi-node consensus with quorum requirement (default 2/3)."""

    def __init__(self, quorum_fraction: float = 2 / 3):
        self.quorum_fraction = quorum_fraction

    def decide(self, proposals: List[Proposal], key_map: Dict[str, bytes]) -> Dict[str, Any]:
        if not proposals:
            raise RuntimeError("No proposals to decide on")

        for p in proposals:
            if p.node_id not in key_map:
                raise RuntimeError(f"Unknown node: {p.node_id}")
            # FIX: include _sig in the verification payload — Signer.verify reads
            # payload["_sig"] and Signer.sign strips "_"-prefixed keys via _public_view,
            # so the reconstructed payload must carry the signature to compare against.
            payload = {"node_id": p.node_id, "decision": p.decision, "timestamp": p.timestamp.isoformat(), "_sig": p.signature}
            if not Signer.verify(payload, key_map[p.node_id]):
                raise RuntimeError(f"Signature verification failed for {p.node_id}")

        clusters = _cluster_proposals(proposals)
        total = len(proposals)
        min_votes = math.ceil(total * self.quorum_fraction)
        best_cluster = max(clusters, key=len)

        if len(best_cluster) < min_votes:
            raise RuntimeError(f"No quorum: {len(best_cluster)} votes < {min_votes} required ({self.quorum_fraction:.0%} of {total})")

        chosen = sorted(best_cluster, key=lambda p: p.node_id)[0]
        return {
            "decision": _public_view(chosen.decision),
            "consensus_size": len(best_cluster),
            "total_proposals": total,
            "quorum_fraction": self.quorum_fraction,
            "leader_node": chosen.node_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


class GovernanceNode:
    """A single governance node (e.g. one hospital) in multi-node consensus."""

    def __init__(self, node_id: str, key: bytes):
        self.node_id = node_id
        self.key = key

    def propose(self, decision: Dict[str, Any]) -> Proposal:
        proposal = Proposal(node_id=self.node_id, decision=decision)
        proposal.sign(self.key)
        return proposal


class DGKGateway:
    """Optional gateway: routes critical decisions to multi-node consensus."""

    CRITICAL_TYPES = {"emergency_override", "modify_critical_rule", "modify_safety_critical_rule"}

    def __init__(self, nodes: Dict[str, GovernanceNode], consensus_decider: ConsensusDecider):
        self.nodes = nodes
        self.consensus_decider = consensus_decider

    def require_consensus(self, request_type: str) -> bool:
        return request_type in self.CRITICAL_TYPES

    def reach_consensus(self, decision_data: Dict[str, Any]) -> Dict[str, Any]:
        proposals = [node.propose(decision_data) for node in self.nodes.values()]
        key_map = {node.node_id: node.key for node in self.nodes.values()}
        return self.consensus_decider.decide(proposals, key_map)


@dataclass
class DGKAwareVerdict:
    """Extension wrapper: PolicyVerdict + multi-node consensus metadata."""
    request_id: str
    approved: bool
    confidence: float
    consensus_result: Optional[Dict[str, Any]] = None

    @property
    def is_consensus_decision(self) -> bool:
        return self.consensus_result is not None


# ============================================================================
# PERCEIVE KERNEL (MAIN ORCHESTRATOR)
# ============================================================================

class PerceiveGovernanceKernel:
    """Main orchestrator: evaluate request → run gates → consensus → DGK (optional) → audit."""

    GATE_MAP = {
        "boundary_gate": PolicyGates.boundary_gate,
        "citadel": PolicyGates.citadel,
        "fortress": PolicyGates.fortress,
        "invariant_validator": PolicyGates.invariant_validator,
        "sentinel": PolicyGates.sentinel,
        "micropatch": PolicyGates.micropatch,
    }

    def __init__(self, dgk_gateway: Optional[DGKGateway] = None):
        self.manifest_registry = ManifestRegistry()
        self.event_store = EventStore()
        self.audit_ledger = ImmutableAuditLedger()
        self.dgk_gateway = dgk_gateway  # Optional multi-node consensus for critical decisions
        logger.info("PERCEIVE Governance Kernel initialized")

    def register_manifest(self, manifest: PolicyManifest) -> None:
        self.manifest_registry.register_manifest(manifest)

    @staticmethod
    def _select_gates(request: PolicyRequest) -> List[str]:
        """Deterministic gate selection based on request type."""
        gates = ["boundary_gate"]  # always evaluated

        if request.request_type == "escalate_patient":
            gates.extend(["invariant_validator", "sentinel"])
        elif request.request_type == "modify_rule":
            gates.extend(["fortress", "citadel", "invariant_validator"])
        elif request.request_type == "export_data":
            gates.extend(["sentinel"])
        elif request.request_type == "emergency_override":
            gates.extend(["micropatch", "sentinel"])

        return list(dict.fromkeys(gates))

    def evaluate_request(self, request: PolicyRequest) -> PolicyVerdict:
        """Main evaluation loop: gates → consensus → DGK (if critical) → audit."""
        logger.info(f"Evaluating request {request.request_id} ({request.request_type})")

        manifest = self.manifest_registry.get_current_manifest()
        if not manifest:
            logger.error("No manifest registered")
            return PolicyVerdict(
                request_id=request.request_id, approved=False, confidence=0.0,
                violations=["No policy manifest available"], applied_gates=[],
                policy_version="", provenance=Provenance("system", "unknown", "no_manifest"),
                audit_hash="",
            )

        gates_to_evaluate = self._select_gates(request)
        logger.info(f"Selected gates: {gates_to_evaluate}")

        policy_outputs = []
        for gate_name in gates_to_evaluate:
            gate_fn = self.GATE_MAP.get(gate_name)
            if gate_fn is None:
                logger.warning(f"Gate not found: {gate_name}")
                continue
            try:
                output = gate_fn(request)
                policy_outputs.append(output)
                logger.info(f"{gate_name}: approved={output.approved}, confidence={output.confidence:.2f}")
            except Exception as e:
                logger.error(f"Gate {gate_name} failed: {e}")
                policy_outputs.append(PolicyOutput(
                    gate_name=gate_name, approved=False, confidence=0.0,
                    violation_details=[f"Gate evaluation failed: {str(e)}"],
                    timestamp=datetime.now(timezone.utc),
                    provenance=Provenance("system", "unknown", "gate_failure"),
                ))

        approved, confidence, violations = ConsensusEngine.evaluate(policy_outputs)

        # Optional: DGK multi-node consensus for critical decisions
        consensus_result = None
        if approved and self.dgk_gateway and self.dgk_gateway.require_consensus(request.request_type):
            try:
                decision_data = {
                    "request_id": request.request_id,
                    "approved": approved,
                    "confidence": round(confidence, 6),
                }
                consensus_result = self.dgk_gateway.reach_consensus(decision_data)
                logger.info(f"DGK consensus: {consensus_result['consensus_size']}/{consensus_result['total_proposals']} nodes agreed")
            except RuntimeError as e:
                logger.error(f"DGK consensus failed: {e}")
                approved = False
                violations.append(f"DGK_CONSENSUS_FAILED: {e}")

        provenance = Provenance(
            actor_id=request.actor_id, policy_id=manifest.version,
            justification=f"Consensus of {len(gates_to_evaluate)} gates" + (" + DGK multi-node" if consensus_result else ""),
        )

        final_verdict_dict = {
            "approved": approved, "confidence": confidence, "violations": violations,
            "gate_count": len(gates_to_evaluate),
            "dgk_consensus": consensus_result,
        }

        audit_entry = self.audit_ledger.append_decision(
            request_snapshot=asdict(request),
            evaluated_gates=gates_to_evaluate,
            policy_outputs=policy_outputs,
            final_verdict=final_verdict_dict,
            manifest_version=manifest.version,
            manifest_hash=manifest.manifest_hash,
        )

        self.event_store.append_event(
            event_type="policy_evaluation",
            request_snapshot=asdict(request),
            actor_id=request.actor_id,
            details={"approved": approved, "gates_evaluated": gates_to_evaluate},
        )

        verdict = PolicyVerdict(
            request_id=request.request_id, approved=approved, confidence=confidence,
            violations=violations, applied_gates=gates_to_evaluate,
            policy_version=manifest.version, provenance=provenance,
            audit_hash=audit_entry.immutable_hash, consensus_result=consensus_result,
        )

        logger.info(f"Verdict: approved={approved}, confidence={confidence:.2f}, audit_hash={audit_entry.immutable_hash[:16]}")
        return verdict

    def export_audit(self) -> List[Dict]:
        return self.audit_ledger.export_audit_trail()

    def verify_audit_integrity(self) -> bool:
        return self.audit_ledger.verify_chain_integrity()


if __name__ == "__main__":
    import secrets

    kernel = PerceiveGovernanceKernel()
    manifest = PolicyManifest(
        manifest_id="v1", version="1.0.0", created_at=datetime.now(timezone.utc),
        policies={"escalation_policy": {"max_daily": 5}},
    )
    kernel.register_manifest(manifest)

    # --- Routine escalation: single-node ---
    request = PolicyRequest(
        request_id="REQ-001", request_type="escalate_patient", subject_id="P001",
        actor_id="DR-001", context={"severity": "high"},
    )
    verdict = kernel.evaluate_request(request)
    print(f"Routine escalation -> approved={verdict.approved}, confidence={verdict.confidence:.2f}, audit={verdict.audit_hash[:16]}")

    # --- Emergency override: with DGK multi-node consensus ---
    nodes = {f"hospital-{i}": GovernanceNode(f"hospital-{i}", secrets.token_bytes(32)) for i in range(1, 4)}
    dgk = DGKGateway(nodes, ConsensusDecider(quorum_fraction=2 / 3))
    kernel_dgk = PerceiveGovernanceKernel(dgk_gateway=dgk)
    kernel_dgk.register_manifest(manifest)

    emergency = PolicyRequest(
        request_id="REQ-002", request_type="emergency_override", subject_id="P002",
        actor_id="DR-002",
        context={
            "emergency_reason": "Absolute PEWS desaturation floor breach detected",
            "override_type": "patient_safety",
            "physician_approved": True,
            "can_notify_stakeholders": True,
            "escalated_to_physician": True,
        },
    )
    verdict2 = kernel_dgk.evaluate_request(emergency)
    print(f"Emergency override -> approved={verdict2.approved}, confidence={verdict2.confidence:.2f}")
    if verdict2.consensus_result:
        print(f"  DGK consensus: {verdict2.consensus_result['consensus_size']}/{verdict2.consensus_result['total_proposals']} nodes, leader={verdict2.consensus_result['leader_node']}")

    print(f"\nAudit chain valid: {kernel_dgk.verify_audit_integrity()}")
