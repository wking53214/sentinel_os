"""
interpretation -- how Sentinel keeps applying the reading it was given.

Regulations are not black and white. Where one is genuinely open, the
business picks a reading. This package is what makes that reading real:
it probes Sentinel with concrete situations every month and reports
whether the answers still match what Legal locked in.

The loop:

  1. An AI writes candidate scenarios on the boundaries of the declared
     ambiguity zones.                                    (generator.py)
  2. Legal approves or rejects each one, and in approving it LOCKS the
     correct answer. Nothing runs unapproved.            (scenarios.py)
  3. Monthly, approved scenarios are posed to Sentinel and every
     outcome is recorded, including refusals.              (harness.py)
  4. Results are grouped by zone and compared to tolerance the business
     set, so drift is localized rather than vague.            (drift.py)
  5. Annually, humans decide whether the reading still holds, and the
     decision is sealed with its evidence.             (realignment.py)
  6. Everything renders as documents people can read.        (report.py)

The load-bearing constraint: AI generates and humans decide. A model
suggestion is never an authoritative answer, and no scenario is scored
against a machine-supplied expectation.
"""

from .scenarios import (
    STATUS_APPROVED,
    STATUS_PROPOSED,
    STATUS_REJECTED,
    STATUS_RETIRED,
    Scenario,
    ScenarioIntegrityError,
    ScenarioLibrary,
)
from .generator import (
    InterpretationContext,
    ModelClient,
    ScenarioGenerator,
    StubModelClient,
)
from .harness import (
    RESULT_ERROR,
    RESULT_INDETERMINATE,
    RESULT_MATCH,
    RESULT_MISMATCH,
    ScenarioResult,
    TestHarness,
    TestRun,
    cassette_is_scenario_testable,
)
from .drift import (
    STATE_BREACH,
    STATE_OK,
    STATE_UNKNOWN,
    STATE_WATCH,
    STRICT,
    DriftAnalyzer,
    DriftReport,
    ToleranceConfig,
    ZoneDrift,
    ZoneTolerance,
    calibration_suggestion,
)
from .realignment import (
    DECISION_KEEP,
    DECISION_RETIRE,
    DECISION_REVIEW,
    DECISION_UPDATE,
    Approver,
    RealignmentRecord,
    RealignmentTrail,
    RegulatoryEvent,
    VersionChange,
)
from .report import annual_realignment_report, monthly_drift_report

__all__ = [
    "Scenario", "ScenarioLibrary", "ScenarioIntegrityError",
    "STATUS_PROPOSED", "STATUS_APPROVED", "STATUS_REJECTED", "STATUS_RETIRED",
    "ScenarioGenerator", "InterpretationContext", "ModelClient", "StubModelClient",
    "TestHarness", "TestRun", "ScenarioResult", "cassette_is_scenario_testable",
    "RESULT_MATCH", "RESULT_MISMATCH", "RESULT_INDETERMINATE", "RESULT_ERROR",
    "DriftAnalyzer", "DriftReport", "ZoneDrift", "ZoneTolerance", "ToleranceConfig",
    "calibration_suggestion", "STRICT",
    "STATE_OK", "STATE_WATCH", "STATE_BREACH", "STATE_UNKNOWN",
    "RealignmentRecord", "RealignmentTrail", "Approver", "RegulatoryEvent", "VersionChange",
    "DECISION_KEEP", "DECISION_UPDATE", "DECISION_RETIRE", "DECISION_REVIEW",
    "monthly_drift_report", "annual_realignment_report",
]
