"""
Tests: compliance_exporters.py
===============================

Verifies HIPAA de-identification, FDA report generation, SOX decision
logging, GDPR transparency records, and the combined compliance summary.

Focus areas:
  - PII never leaks (raw patient_id absent, timestamps coarsened)
  - Pseudonymization is stable and non-reversible
  - Reports reflect actual audit content
  - Tamper in the underlying ledger surfaces as INVALID in the FDA report

Run: python3 -m pytest test_compliance_exporters.py -v
"""

import unittest
import csv
import io
from datetime import datetime, timezone

from observe_consolidated import VitalsSnapshot
from clinical_governance_system import build_single_hospital_system
from compliance_exporters import (
    deidentify_timestamp,
    pseudonymize_patient_id,
    HIPAAExporter,
    FDAExporter,
    SOXExporter,
    GDPRExporter,
    ComplianceSummary,
)


def build_traffic():
    """Run sample traffic and return (system, observe_entries, perceive_entries, audits)."""
    system = build_single_hospital_system()
    cases = [
        ("PATIENT-SECRET-001", 110, 98.0, 24, 37.0, 24),  # stable; raw ID is sensitive
        ("PATIENT-SECRET-002", 168, 83.0, 46, 39.5, 12),  # critical
        ("PATIENT-SECRET-003", 172, 81.0, 48, 39.8, 6),   # critical
    ]
    for pid, hr, o2, rr, temp, age in cases:
        system.process_vitals(VitalsSnapshot(pid, datetime.now(timezone.utc), hr, o2, rr, temp,
                                             context={"age_months": age, "force_heavy": True}))
    observe_entries = [
        {"patient_id": e["patient_id"], "timestamp": e["timestamp"], "action": e["action"],
         "data": e["data"], "immutable_hash": e["immutable_hash"]}
        for e in system.observe.audit_ledger.entries
    ]
    perceive_entries = system.export_perceive_audit()
    audits = system.verify_all_audits()
    return system, observe_entries, perceive_entries, audits


# ============================================================================
# DE-IDENTIFICATION PRIMITIVES
# ============================================================================

class TestDeidentification(unittest.TestCase):

    def test_timestamp_coarsened_to_hour(self):
        out = deidentify_timestamp("2026-06-14T13:47:32.123456+00:00")
        self.assertEqual(out, "2026-06-14 13:00")
        self.assertNotIn("47", out)  # minutes stripped
        self.assertNotIn("32", out)  # seconds stripped

    def test_timestamp_invalid_redacted(self):
        self.assertEqual(deidentify_timestamp("not-a-timestamp"), "REDACTED")

    def test_pseudonym_stable(self):
        a = pseudonymize_patient_id("PATIENT-001")
        b = pseudonymize_patient_id("PATIENT-001")
        self.assertEqual(a, b)  # same input -> same pseudonym

    def test_pseudonym_differs_per_patient(self):
        self.assertNotEqual(pseudonymize_patient_id("PATIENT-001"),
                            pseudonymize_patient_id("PATIENT-002"))

    def test_pseudonym_does_not_contain_raw_id(self):
        raw = "PATIENT-SECRET-001"
        pseudo = pseudonymize_patient_id(raw)
        self.assertNotIn(raw, pseudo)
        self.assertNotIn("SECRET", pseudo)

    def test_pseudonym_salt_changes_output(self):
        self.assertNotEqual(
            pseudonymize_patient_id("PATIENT-001", salt="SALT_A"),
            pseudonymize_patient_id("PATIENT-001", salt="SALT_B"),
        )


# ============================================================================
# HIPAA EXPORTER
# ============================================================================

class TestHIPAAExporter(unittest.TestCase):

    def test_no_raw_pii_in_output(self):
        _, observe_entries, _, _ = build_traffic()
        csv_text = HIPAAExporter.export_csv(observe_entries)
        # Raw patient IDs must NOT appear anywhere in the export
        self.assertNotIn("PATIENT-SECRET-001", csv_text)
        self.assertNotIn("PATIENT-SECRET-002", csv_text)
        self.assertNotIn("SECRET", csv_text)

    def test_pseudonyms_present(self):
        _, observe_entries, _, _ = build_traffic()
        csv_text = HIPAAExporter.export_csv(observe_entries)
        self.assertIn("PT-", csv_text)  # pseudonym prefix

    def test_csv_well_formed_with_expected_columns(self):
        _, observe_entries, _, _ = build_traffic()
        csv_text = HIPAAExporter.export_csv(observe_entries)
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)
        self.assertEqual(len(rows), len(observe_entries))
        for col in ["pseudonym", "date_hour", "action", "risk_score", "regime", "escalation_required", "audit_hash"]:
            self.assertIn(col, reader.fieldnames)

    def test_timestamps_coarsened_in_output(self):
        _, observe_entries, _, _ = build_traffic()
        csv_text = HIPAAExporter.export_csv(observe_entries)
        reader = csv.DictReader(io.StringIO(csv_text))
        for row in reader:
            # date_hour ends in ":00" (no sub-hour precision)
            self.assertTrue(row["date_hour"].endswith(":00") or row["date_hour"] == "REDACTED")


# ============================================================================
# FDA EXPORTER
# ============================================================================

class TestFDAExporter(unittest.TestCase):

    def test_report_reflects_assessment_count(self):
        _, observe_entries, _, audits = build_traffic()
        report = FDAExporter.export_report(observe_entries, audits["observe_chain_valid"])
        self.assertIn(f"Total assessments recorded: {len(observe_entries)}", report)

    def test_valid_chain_shown_as_valid(self):
        _, observe_entries, _, audits = build_traffic()
        report = FDAExporter.export_report(observe_entries, chain_valid=True)
        self.assertIn("VALID", report)
        self.assertNotIn("INVALID", report)

    def test_invalid_chain_flagged(self):
        _, observe_entries, _, _ = build_traffic()
        report = FDAExporter.export_report(observe_entries, chain_valid=False)
        self.assertIn("INVALID", report)
        self.assertIn("INVESTIGATE", report)

    def test_engine_utilization_listed(self):
        _, observe_entries, _, audits = build_traffic()
        report = FDAExporter.export_report(observe_entries, audits["observe_chain_valid"])
        self.assertIn("ENGINE UTILIZATION", report)
        self.assertIn("heuristic", report)
        # The 7th engine should appear too
        self.assertIn("physiological_reserve", report)

    def test_determinism_attestation_present(self):
        _, observe_entries, _, audits = build_traffic()
        report = FDAExporter.export_report(observe_entries, audits["observe_chain_valid"])
        self.assertIn("DETERMINISM ATTESTATION", report)


# ============================================================================
# SOX EXPORTER
# ============================================================================

class TestSOXExporter(unittest.TestCase):

    def test_logs_governance_decisions(self):
        _, _, perceive_entries, _ = build_traffic()
        csv_text = SOXExporter.export_csv(perceive_entries)
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)
        self.assertEqual(len(rows), len(perceive_entries))

    def test_includes_actor_and_result(self):
        _, _, perceive_entries, _ = build_traffic()
        csv_text = SOXExporter.export_csv(perceive_entries)
        reader = csv.DictReader(io.StringIO(csv_text))
        for col in ["timestamp", "actor", "request_type", "approved", "immutable_hash"]:
            self.assertIn(col, reader.fieldnames)
        # The orchestrator escalates as OBSERVE_SYSTEM
        self.assertIn("OBSERVE_SYSTEM", csv_text)


# ============================================================================
# GDPR EXPORTER
# ============================================================================

class TestGDPRExporter(unittest.TestCase):

    def test_report_includes_volumes(self):
        report = GDPRExporter.export_report(observe_entry_count=10, perceive_entry_count=3)
        self.assertIn("Clinical assessments: 10", report)
        self.assertIn("Governance decisions: 3", report)

    def test_report_states_lawful_basis(self):
        report = GDPRExporter.export_report(5, 2)
        self.assertIn("Lawful basis", report)
        self.assertIn("vital interests", report)

    def test_export_modes_listed(self):
        report = GDPRExporter.export_report(5, 2, export_types_used=["synthetic_only"])
        self.assertIn("synthetic_only", report)


# ============================================================================
# COMBINED SUMMARY
# ============================================================================

class TestComplianceSummary(unittest.TestCase):

    def test_summary_reports_both_chains(self):
        _, observe_entries, perceive_entries, audits = build_traffic()
        summary = ComplianceSummary.generate(
            observe_entries, perceive_entries,
            audits["observe_chain_valid"], audits["perceive_chain_valid"],
        )
        self.assertEqual(summary["observe"]["total_assessments"], len(observe_entries))
        self.assertEqual(summary["perceive"]["total_decisions"], len(perceive_entries))
        self.assertTrue(summary["overall_compliant"])

    def test_overall_compliant_false_if_either_chain_invalid(self):
        summary = ComplianceSummary.generate([], [], observe_chain_valid=True, perceive_chain_valid=False)
        self.assertFalse(summary["overall_compliant"])

    def test_frameworks_listed(self):
        summary = ComplianceSummary.generate([], [], True, True)
        for fw in ["HIPAA", "FDA 510(k)", "SOX", "GDPR"]:
            self.assertIn(fw, summary["frameworks_supported"])

    def test_to_json_roundtrip(self):
        import json
        summary = ComplianceSummary.generate([], [], True, True)
        parsed = json.loads(ComplianceSummary.to_json(summary))
        self.assertEqual(parsed["overall_compliant"], True)


if __name__ == "__main__":
    unittest.main()
