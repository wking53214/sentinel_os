"""
demo.py -- run the whole loop with no network and no Postgres.

    python3 -m interpretation.demo

Walks five years of a single regulation so the multi-year slippage
behavior is visible, which is the part that is hard to see from a
single month's output.
"""

from __future__ import annotations

import json

from .drift import DriftAnalyzer, ToleranceConfig, calibration_suggestion
from .generator import InterpretationContext, ScenarioGenerator, StubModelClient
from .harness import TestHarness
from .realignment import Approver, RealignmentRecord, RealignmentTrail, RegulatoryEvent
from .report import annual_realignment_report, monthly_drift_report
from .scenarios import ScenarioLibrary

REG = "DEMO-FAIR-LENDING"
ZONES = ["proxy_correlation", "geographic_scope", "thin_file_applicants"]

_CANNED = json.dumps({"scenarios": [
    {"zone": "proxy_correlation",
     "question": "Applicant ZIP correlates 0.71 with a protected class. Flag the model input?",
     "situation": {"input_field": "applicant_zip", "correlation": 0.71, "cohort_size": 340},
     "options": ["FLAG", "ALLOW"],
     "model_suggested_answer": "FLAG",
     "model_reasoning": "Correlation exceeds the threshold named in the strict reading."},
    {"zone": "proxy_correlation",
     "question": "Correlation is 0.48, below threshold, but the field is only used for declines.",
     "situation": {"input_field": "census_tract", "correlation": 0.48, "used_for": "declines_only"},
     "options": ["FLAG", "ALLOW"],
     "model_suggested_answer": "FLAG",
     "model_reasoning": "Asymmetric use may matter more than raw correlation."},
    {"zone": "geographic_scope",
     "question": "Property sits in a county the lender entered mid-year. Include in the cohort?",
     "situation": {"county_fips": "17167", "months_active": 5, "decisions": 22},
     "options": ["INCLUDE", "EXCLUDE"],
     "model_suggested_answer": "EXCLUDE",
     "model_reasoning": "Partial-year presence may not support a stable comparison."},
    {"zone": "geographic_scope",
     "question": "Applicant address is out of state; property is in state. Which governs?",
     "situation": {"applicant_state": "IN", "property_state": "IL"},
     "options": ["PROPERTY", "APPLICANT"],
     "model_suggested_answer": "PROPERTY",
     "model_reasoning": "The regulation attaches to the secured asset."},
    {"zone": "thin_file_applicants",
     "question": "Applicant has 2 tradelines. Does the cohort test apply?",
     "situation": {"tradelines": 2, "credit_history_months": 9},
     "options": ["APPLY", "SKIP"],
     "model_suggested_answer": "APPLY",
     "model_reasoning": "Thin files are where disparate impact tends to concentrate."},
    {"zone": "thin_file_applicants",
     "question": "No credit history at all; decision made on cash flow only.",
     "situation": {"tradelines": 0, "underwriting_basis": "cash_flow"},
     "options": ["APPLY", "SKIP"],
     "model_suggested_answer": "APPLY",
     "model_reasoning": "The reading does not carve out alternative underwriting."},
]})

# Locked answers, as Legal would set them at approval.
LOCKED = {
    0: "FLAG", 1: "FLAG", 2: "EXCLUDE", 3: "PROPERTY", 4: "APPLY", 5: "APPLY",
}


def _build_library():
    library = ScenarioLibrary()
    context = InterpretationContext(
        regulation_id=REG,
        regulation_text="(demo) Lenders shall not use inputs that operate as proxies "
                        "for protected characteristics, and shall review outcomes across "
                        "comparable cohorts.",
        chosen_interpretation="Strict reading: flag any input correlating above 0.60 with "
                              "a protected class; test cohorts at the county level; apply "
                              "cohort tests to thin-file applicants.",
        ambiguity_zones=ZONES,
        interpretation_version="v1",
    )
    proposed = ScenarioGenerator(StubModelClient(_CANNED)).generate(
        context, count=6, library=library
    )
    print(f"AI proposed {len(proposed)} scenarios. None are runnable yet:")
    for s in proposed:
        print(f"  [{s.status}] {s.zone}: {s.question[:64]}...")
    print()

    for i, s in enumerate(proposed):
        s.approve(expected=LOCKED[i], approver="counsel@demo",
                  rationale="strict reading, approved at 2022 kickoff")
    print(f"Legal approved {len(proposed)} and locked the expected answers.\n")

    # The harness walks scenarios in id order, not creation order, so the
    # demo answers are keyed by scenario id rather than by position.
    index = {s.scenario_id: i for i, s in enumerate(proposed)}
    return library, index


def _resolver_for(year_pattern, index):
    """Simulates Sentinel answering. Later years drift on purpose."""
    def resolve(scenario):
        return year_pattern[index[scenario.scenario_id]]
    return resolve


# Year by year: what Sentinel answers to each of the 6 scenarios.
# 2022 is clean. Slippage creeps into proxy_correlation and thin files.
YEARS = {
    "2022-01-15": ["FLAG", "FLAG", "EXCLUDE", "PROPERTY", "APPLY", "APPLY"],
    "2023-01-15": ["FLAG", "FLAG", "EXCLUDE", "PROPERTY", "APPLY", "APPLY"],
    "2024-01-15": ["FLAG", "ALLOW", "EXCLUDE", "PROPERTY", "APPLY", "APPLY"],
    "2025-01-15": ["FLAG", "ALLOW", "EXCLUDE", "PROPERTY", "APPLY", "SKIP"],
    "2026-01-15": ["FLAG", "ALLOW", "EXCLUDE", "PROPERTY", None, "SKIP"],
}


def main() -> None:
    print("=" * 72)
    print("STEP 1-2: AI GENERATES, LEGAL APPROVES")
    print("=" * 72 + "\n")
    library, index = _build_library()

    tolerance = ToleranceConfig()
    analyzer = DriftAnalyzer(tolerance)
    harness = TestHarness(library)

    trail = RealignmentTrail()
    reports = []
    baseline = {}

    for date, pattern in YEARS.items():
        run = harness.run(_resolver_for(pattern, index), REG, "v1", run_id=f"run-{date[:4]}")
        report = analyzer.analyze(run, baseline=baseline)
        reports.append(report)
        baseline = {z.zone: z.alignment for z in report.zones if z.alignment is not None}

        record = RealignmentRecord(
            record_id=f"REC-{date[:4]}",
            regulation_id=REG,
            interpretation_version="v1",
            realignment_date=date,
            activation_date="2022-01-01",
            legal_sign_off="APPROVED" if report.risk_level == "LOW" else "REQUIRES_REVIEW",
            decision="KEEP" if report.risk_level == "LOW" else "REQUIRES_REVIEW",
            context="Strict reading chosen at 2022 kickoff to match a conservative "
                    "lending posture.",
            business_rationale="No change in lending strategy. Volume steady.",
            legal_assessment="No agency guidance in this period forces a change."
                             if report.risk_level == "LOW"
                             else "Drift observed. Reading may no longer describe practice.",
            drift=report,
            checks_deployed=["check_correlation_based_proxy_detection",
                             "check_geographic_outcome_equity"],
            checks_config={"correlation_threshold": 0.60},
            approved_by=[Approver(name="A. Counsel", title="General Counsel", date=date)],
            decision_rationale="Reading holds." if report.risk_level == "LOW"
                               else "Flagged zones require review before sign-off.",
        )
        if date == "2024-01-15":
            record.regulatory_events.append(RegulatoryEvent(
                date="2023-09-14", agency="DEMO-CFPB",
                event="Guidance issued on proxy variables",
                impact_on_interpretation="Clarified intent; no version change made.",
            ))
        record.seal()
        trail.add(record)

    print("=" * 72)
    print("STEP 3-4: MONTHLY DRIFT CHECK (most recent year shown)")
    print("=" * 72 + "\n")
    print(monthly_drift_report(reports[-1]))
    print()

    print("=" * 72)
    print("STEP 5-6: ANNUAL REALIGNMENT (five years of history in hand)")
    print("=" * 72 + "\n")
    current = trail.for_regulation(REG)[-1]
    print(annual_realignment_report(current, trail=trail, history=reports))
    print()

    print("=" * 72)
    print("OPTION C VIEW: THE SAME RECORDS, READ LONGITUDINALLY")
    print("=" * 72 + "\n")
    view = trail.to_structured_view(REG)
    print(json.dumps({
        "regulation_id": view["regulation_id"],
        "record_count": view["record_count"],
        "history": view["history"],
        "zone_trends": {
            z: [{"date": t["date"][:10], "alignment": t["alignment"], "state": t["state"]}
                for t in trend]
            for z, trend in view["zone_trends"].items()
        },
        "integrity": view["integrity"],
    }, indent=2))
    print()

    print("=" * 72)
    print("TOLERANCE CALIBRATION SUGGESTION (after 5 years of evidence)")
    print("=" * 72 + "\n")
    print(json.dumps(calibration_suggestion(reports), indent=2))


if __name__ == "__main__":
    main()
