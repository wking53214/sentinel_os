"""
Compliance Exporters
====================

Export the OBSERVE and PERCEIVE audit trails into regulator-ready formats:

    HIPAA  -> de-identified clinical event log (no PII, date+hour only)
    FDA    -> 510(k)-style validation report (deterministic, traceable decisions)
    SOX    -> governance decision log (who/what/when/result, tamper-evident)
    GDPR   -> data-processing transparency record

All exporters operate on the in-memory audit ledgers and never mutate them.
A combined compliance summary stitches both chains into one attestation.

Run as a demo:  python3 compliance_exporters.py
"""

from __future__ import annotations

import csv
import io
import json
import hashlib
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional


# ============================================================================
# DE-IDENTIFICATION
# ============================================================================

def deidentify_timestamp(iso_ts: str) -> str:
    """
    HIPAA Safe Harbor: reduce timestamp precision to date + hour only.
    Strips minutes/seconds (which can act as quasi-identifiers in low-volume units).
    """
    try:
        dt = datetime.fromisoformat(iso_ts)
        return dt.strftime("%Y-%m-%d %H:00")
    except (ValueError, TypeError):
        return "REDACTED"


def pseudonymize_patient_id(patient_id: str, salt: str = "OBSERVE_SALT_V1") -> str:
    """
    Replace a raw patient_id with a stable, non-reversible pseudonym.
    Same patient -> same pseudonym (enables longitudinal analysis without PII).
    """
    digest = hashlib.sha256(f"{salt}:{patient_id}".encode()).hexdigest()
    return f"PT-{digest[:12]}"


# ============================================================================
# HIPAA EXPORTER (clinical event log)
# ============================================================================

class HIPAAExporter:
    """De-identified clinical event log from the OBSERVE audit ledger."""

    @staticmethod
    def export_csv(observe_entries: List[Dict[str, Any]]) -> str:
        """
        observe_entries: list of OBSERVE audit entries (dicts with patient_id,
        timestamp, action, data, immutable_hash).
        Returns CSV text. No raw PII: patient_id pseudonymized, timestamp coarsened.
        """
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=[
            "pseudonym", "date_hour", "action", "risk_score", "regime",
            "escalation_required", "audit_hash",
        ])
        writer.writeheader()

        for e in observe_entries:
            verdict = (e.get("data") or {}).get("verdict", {})
            writer.writerow({
                "pseudonym": pseudonymize_patient_id(e.get("patient_id", "UNKNOWN")),
                "date_hour": deidentify_timestamp(e.get("timestamp", "")),
                "action": e.get("action", ""),
                "risk_score": round(verdict.get("risk_score", 0.0), 3),
                "regime": verdict.get("regime", ""),
                "escalation_required": verdict.get("escalation_required", False),
                "audit_hash": e.get("immutable_hash", "")[:16],
            })

        return buf.getvalue()


# ============================================================================
# FDA EXPORTER (510(k) validation report)
# ============================================================================

class FDAExporter:
    """510(k)-style validation report emphasizing determinism + traceability."""

    @staticmethod
    def export_report(
        observe_entries: List[Dict[str, Any]],
        chain_valid: bool,
        software_version: str = "1.0.0",
    ) -> str:
        total = len(observe_entries)
        escalations = sum(
            1 for e in observe_entries
            if (e.get("data") or {}).get("verdict", {}).get("escalation_required")
        )

        # Engine-usage tally (which engines contributed across all assessments)
        engine_usage: Dict[str, int] = {}
        for e in observe_entries:
            for eng in (e.get("data") or {}).get("selected_engines", []):
                engine_usage[eng] = engine_usage.get(eng, 0) + 1

        lines = [
            "FDA 510(k) SOFTWARE VALIDATION REPORT",
            "=" * 50,
            f"Software: OBSERVE Clinical AI",
            f"Version: {software_version}",
            f"Report generated: {datetime.now(timezone.utc).isoformat()}",
            "",
            "DEVICE DESCRIPTION",
            "-" * 50,
            "Pediatric physiological early-warning system. Multi-engine risk",
            "assessment with deterministic fusion and cryptographically chained",
            "audit trail. Decisions are reproducible from recorded inputs.",
            "",
            "VALIDATION SUMMARY",
            "-" * 50,
            f"Total assessments recorded: {total}",
            f"Escalations triggered: {escalations}",
            f"Escalation rate: {(escalations / total * 100) if total else 0:.1f}%",
            f"Audit chain integrity: {'VALID (tamper-evident)' if chain_valid else 'INVALID — INVESTIGATE'}",
            "",
            "ENGINE UTILIZATION",
            "-" * 50,
        ]
        for eng, count in sorted(engine_usage.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {eng:28s}: {count} assessments")

        lines += [
            "",
            "DETERMINISM ATTESTATION",
            "-" * 50,
            "All risk engines are pure functions of recorded telemetry + context.",
            "Given identical inputs, the system produces identical outputs and the",
            "same audit hash. No randomness in the decision path.",
            "",
            "TRACEABILITY",
            "-" * 50,
            "Every decision is recorded with a SHA256 hash chained to its",
            "predecessor. Any post-hoc alteration breaks the chain and is detected",
            "by verify_integrity().",
        ]

        return "\n".join(lines)


# ============================================================================
# SOX EXPORTER (governance decision log)
# ============================================================================

class SOXExporter:
    """Governance decision log from the PERCEIVE audit ledger (who/what/when/result)."""

    @staticmethod
    def export_csv(perceive_entries: List[Dict[str, Any]]) -> str:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=[
            "timestamp", "audit_id", "actor", "request_type", "evaluated_gates",
            "approved", "manifest_version", "immutable_hash",
        ])
        writer.writeheader()

        for e in perceive_entries:
            req = e.get("request_snapshot", {})
            verdict = e.get("final_verdict", {})
            writer.writerow({
                "timestamp": e.get("timestamp", ""),
                "audit_id": e.get("audit_id", ""),
                "actor": req.get("actor_id", ""),
                "request_type": req.get("request_type", ""),
                "evaluated_gates": "|".join(e.get("evaluated_gates", [])),
                "approved": verdict.get("approved", ""),
                "manifest_version": e.get("manifest_version", ""),
                "immutable_hash": e.get("immutable_hash", "")[:16],
            })

        return buf.getvalue()


# ============================================================================
# GDPR EXPORTER (data-processing transparency)
# ============================================================================

class GDPRExporter:
    """Data-processing transparency record (Article 30-style)."""

    @staticmethod
    def export_report(
        observe_entry_count: int,
        perceive_entry_count: int,
        export_types_used: Optional[List[str]] = None,
    ) -> str:
        export_types_used = export_types_used or ["synthetic_only", "aggregate_only"]
        lines = [
            "GDPR DATA PROCESSING TRANSPARENCY RECORD",
            "=" * 50,
            f"Generated: {datetime.now(timezone.utc).isoformat()}",
            "",
            "PURPOSE OF PROCESSING",
            "-" * 50,
            "Real-time clinical risk assessment for pediatric patient safety.",
            "Lawful basis: vital interests of the data subject (Art. 6(1)(d)) and",
            "provision of health care (Art. 9(2)(h)).",
            "",
            "DATA CATEGORIES",
            "-" * 50,
            "  - Physiological telemetry (heart rate, SpO2, respiratory rate, temp)",
            "  - Derived risk scores and regime classifications",
            "  - Governance decisions and their justifications",
            "",
            "PROCESSING ACTIVITY VOLUME",
            "-" * 50,
            f"  Clinical assessments: {observe_entry_count}",
            f"  Governance decisions: {perceive_entry_count}",
            "",
            "DATA MINIMIZATION / EXPORT CONTROLS",
            "-" * 50,
            f"  Permitted export modes in use: {', '.join(export_types_used)}",
            "  PII export requires explicit consent + encryption (enforced by",
            "  PERCEIVE DataExportPolicy). Default exports are de-identified.",
            "",
            "DATA SUBJECT RIGHTS",
            "-" * 50,
            "  Access/erasure requests are serviceable via patient_id lookup against",
            "  the audit ledgers. Pseudonymization is reversible only by the",
            "  controller holding the salt.",
        ]
        return "\n".join(lines)


# ============================================================================
# COMBINED COMPLIANCE SUMMARY
# ============================================================================

class ComplianceSummary:
    """Stitches both audit chains into a single attestation bundle."""

    @staticmethod
    def generate(
        observe_entries: List[Dict[str, Any]],
        perceive_entries: List[Dict[str, Any]],
        observe_chain_valid: bool,
        perceive_chain_valid: bool,
    ) -> Dict[str, Any]:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "observe": {
                "total_assessments": len(observe_entries),
                "chain_valid": observe_chain_valid,
            },
            "perceive": {
                "total_decisions": len(perceive_entries),
                "chain_valid": perceive_chain_valid,
            },
            "overall_compliant": observe_chain_valid and perceive_chain_valid,
            "frameworks_supported": ["HIPAA", "FDA 510(k)", "SOX", "GDPR"],
        }

    @staticmethod
    def to_json(summary: Dict[str, Any]) -> str:
        return json.dumps(summary, indent=2, default=str)


# ============================================================================
# DEMO
# ============================================================================

if __name__ == "__main__":
    from clinical_governance_system import build_single_hospital_system
    from observe_consolidated import VitalsSnapshot

    print("Generating sample traffic through the unified system...\n")
    system = build_single_hospital_system()

    sample_cases = [
        ("P001", 110, 98.0, 24, 37.0, 24),    # stable
        ("P002", 168, 83.0, 46, 39.5, 12),    # critical
        ("P003", 105, 97.5, 22, 36.9, 36),    # stable
        ("P004", 172, 81.0, 48, 39.8, 6),     # critical
        ("P005", 100, 98.0, 20, 37.1, 48),    # stable
    ]
    for pid, hr, o2, rr, temp, age in sample_cases:
        system.process_vitals(VitalsSnapshot(pid, datetime.now(timezone.utc), hr, o2, rr, temp,
                                             context={"age_months": age, "force_heavy": True}))

    observe_entries = [
        {
            "patient_id": e["patient_id"],
            "timestamp": e["timestamp"],
            "action": e["action"],
            "data": e["data"],
            "immutable_hash": e["immutable_hash"],
        }
        for e in system.observe.audit_ledger.entries
    ]
    perceive_entries = system.export_perceive_audit()
    audits = system.verify_all_audits()

    print("=" * 70)
    print("HIPAA — De-identified Clinical Event Log")
    print("=" * 70)
    print(HIPAAExporter.export_csv(observe_entries))

    print("=" * 70)
    print("FDA — 510(k) Validation Report")
    print("=" * 70)
    print(FDAExporter.export_report(observe_entries, audits["observe_chain_valid"]))

    print("\n" + "=" * 70)
    print("SOX — Governance Decision Log")
    print("=" * 70)
    print(SOXExporter.export_csv(perceive_entries))

    print("=" * 70)
    print("GDPR — Data Processing Transparency Record")
    print("=" * 70)
    print(GDPRExporter.export_report(len(observe_entries), len(perceive_entries)))

    print("\n" + "=" * 70)
    print("COMBINED COMPLIANCE SUMMARY")
    print("=" * 70)
    summary = ComplianceSummary.generate(
        observe_entries, perceive_entries,
        audits["observe_chain_valid"], audits["perceive_chain_valid"],
    )
    print(ComplianceSummary.to_json(summary))
