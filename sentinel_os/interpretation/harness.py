"""
harness.py -- the monthly run: pose approved scenarios to Sentinel,
record what it answered.

WHAT THIS DOES AND DOES NOT PROVE
----------------------------------
It proves consistency, not correctness. A 100% pass rate means Sentinel
is still applying the reading Legal locked in. It does not mean that
reading is legally right. Only Legal can say that, and the annual
realignment is where they say it again.

That distinction matters because the failure mode of a governance test
suite is a green dashboard that means nothing. This one has a narrow,
honest claim: "we still do what we said we would do."

FOUR OUTCOMES, NOT TWO
-----------------------
  MATCH        -- Sentinel answered as Legal locked in.
  MISMATCH     -- Sentinel answered a different valid option. This is
                  drift, and it is the whole point of the exercise.
  INDETERMINATE-- Sentinel declined to answer. Under this system's own
                  rules that is correct behavior when the scenario
                  genuinely falls outside the declared reading, so it
                  is counted separately and never scored as a pass or
                  a fail.
  ERROR        -- The scenario could not be run at all: hash mismatch,
                  resolver raised, option not recognized.

Collapsing INDETERMINATE into either pass or fail is how you get a
metric that lies. A system that answers "I don't know" to everything
would score 100% if refusals counted as passes, and 0% if they counted
as failures. Neither number describes reality, so it gets its own
column.

THE RESOLVER
-------------
The harness does not know how Sentinel answers a question. It is handed
a resolver: anything callable that takes a scenario and returns an
option string, or None to decline. In production that wraps the live
cassette. In tests it is a dict lookup. Keeping it injected is what
lets the same harness probe a proposed interpretation in shadow before
anyone deploys it.

WIRING A LIVE CASSETTE (the kernel/capability seam)
-----------------------------------------------------
run() takes any Resolver, but a cassette is not automatically one: a
domain cassette (cassette_interface.Cassette) must OPT IN by enabling
cassette_capabilities.CAPABILITY_INTERPRETATION_TESTABLE and
implementing resolve_scenario(scenario) -> Optional[str] -- same
signature as the Resolver protocol above, so a testable cassette's
bound method is a drop-in resolver. Opt-in is the default because most
domains have no regulation-reading a drift-check needs to probe, BUT a
cassette that enables CAPABILITY_OUTCOME_OBLIGATION, or that declares a
non-empty REGULATORY_BINDINGS, MUST enable this capability -- a domain
whose outcomes mature later, or that a regulatory lens reviews, is
exactly the kind of decision this drift-check exists to catch, and
cassette_schema.validate_governance_parameters refuses to load such a
cassette otherwise (see cassette_capabilities' module docstring).

run_against_cassette() is the capability-aware entry point: it checks
cassette_is_scenario_testable() first and, when the cassette does not
enable the capability, reports every candidate scenario in `skipped`
with a not-testable reason instead of calling resolve_scenario at all
-- "cannot be scenario-tested" is a reported fact, same NO SILENT SKIPS
posture as an unapproved scenario, never a crash and never a
disappearing denominator. Only cassettes that opt in ever reach
resolve_scenario.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Protocol

from .scenarios import Scenario, ScenarioIntegrityError, ScenarioLibrary

RESULT_MATCH = "MATCH"
RESULT_MISMATCH = "MISMATCH"
RESULT_INDETERMINATE = "INDETERMINATE"
RESULT_ERROR = "ERROR"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def cassette_is_scenario_testable(cassette: Any) -> bool:
    """Whether `cassette` enables cassette_capabilities.
    CAPABILITY_INTERPRETATION_TESTABLE and can therefore be probed by
    this harness's resolver seam (see the module docstring's WIRING A
    LIVE CASSETTE section). Imported lazily so this package does not
    take a hard, load-time dependency on the kernel/capability module
    -- the same posture cassette_interface.Cassette.capabilities()
    already takes on cassette_capabilities.

    A cassette that cannot even state its manifest (CapabilityError)
    is treated as not testable rather than propagating the error here
    -- run_against_cassette reports that as a skip, the caller finds
    out the real reason when it tries to load the cassette anywhere
    else.
    """
    from cassette_capabilities import (
        CAPABILITY_INTERPRETATION_TESTABLE,
        CapabilityError,
        enabled_capabilities,
    )

    try:
        enabled = enabled_capabilities(cassette)
    except CapabilityError:
        return False
    return CAPABILITY_INTERPRETATION_TESTABLE in enabled


class Resolver(Protocol):
    """How Sentinel answers one scenario.

    Returns the chosen option, or None to decline. Declining is a
    first-class answer here, not a failure to answer.
    """

    def __call__(self, scenario: Scenario) -> Optional[str]:  # pragma: no cover
        ...


@dataclass
class ScenarioResult:
    scenario_id: str
    regulation_id: str
    zone: str
    expected: Optional[str]
    actual: Optional[str]
    result: str
    detail: Optional[str] = None
    ran_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TestRun:
    """One monthly execution against one interpretation version."""

    # Not a pytest test class despite the name.
    __test__ = False

    run_id: str
    regulation_id: str
    interpretation_version: str
    ran_at: str
    results: List[ScenarioResult]
    skipped: List[Dict[str, str]] = field(default_factory=list)

    # -- counts --------------------------------------------------------

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def matched(self) -> int:
        return sum(1 for r in self.results if r.result == RESULT_MATCH)

    @property
    def mismatched(self) -> int:
        return sum(1 for r in self.results if r.result == RESULT_MISMATCH)

    @property
    def indeterminate(self) -> int:
        return sum(1 for r in self.results if r.result == RESULT_INDETERMINATE)

    @property
    def errored(self) -> int:
        return sum(1 for r in self.results if r.result == RESULT_ERROR)

    @property
    def decided(self) -> int:
        """Scenarios Sentinel actually took a position on.

        Alignment is measured against this, not against total, because
        a refusal is not a wrong answer.
        """
        return self.matched + self.mismatched

    @property
    def alignment(self) -> Optional[float]:
        """Share of decided scenarios answered as Legal locked in.

        None when nothing was decided. Returning 1.0 for an empty run
        would report perfect agreement from zero evidence, which is the
        single most dangerous number this module could produce.
        """
        if self.decided == 0:
            return None
        return self.matched / self.decided

    def by_zone(self) -> Dict[str, List[ScenarioResult]]:
        grouped: Dict[str, List[ScenarioResult]] = {}
        for result in self.results:
            grouped.setdefault(result.zone, []).append(result)
        return grouped

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "regulation_id": self.regulation_id,
            "interpretation_version": self.interpretation_version,
            "ran_at": self.ran_at,
            "totals": {
                "total": self.total,
                "matched": self.matched,
                "mismatched": self.mismatched,
                "indeterminate": self.indeterminate,
                "errored": self.errored,
                "decided": self.decided,
                "alignment": self.alignment,
            },
            "results": [r.to_dict() for r in self.results],
            "skipped": self.skipped,
        }


class TestHarness:
    """Runs a library's approved scenarios against a resolver."""

    # Not a pytest test class despite the name.
    __test__ = False

    def __init__(self, library: ScenarioLibrary):
        self._library = library

    def run(
        self,
        resolver: Resolver,
        regulation_id: str,
        interpretation_version: str,
        run_id: Optional[str] = None,
    ) -> TestRun:
        results: List[ScenarioResult] = []
        skipped: List[Dict[str, str]] = []

        # Everything for this regulation, not just the runnable ones, so
        # that unapproved and retired scenarios appear in the skipped
        # list with a reason instead of vanishing from the denominator.
        candidates = [s for s in self._library.all() if s.regulation_id == regulation_id]

        for scenario in candidates:
            if not scenario.is_runnable:
                skipped.append({
                    "scenario_id": scenario.scenario_id,
                    "zone": scenario.zone,
                    "reason": f"not runnable (status={scenario.status})",
                })
                continue

            try:
                scenario.verify_hash()
            except ScenarioIntegrityError as exc:
                results.append(ScenarioResult(
                    scenario_id=scenario.scenario_id,
                    regulation_id=scenario.regulation_id,
                    zone=scenario.zone,
                    expected=scenario.expected,
                    actual=None,
                    result=RESULT_ERROR,
                    detail=str(exc),
                ))
                continue

            try:
                answer = resolver(scenario)
            except Exception as exc:  # resolver failure is evidence, not a crash
                results.append(ScenarioResult(
                    scenario_id=scenario.scenario_id,
                    regulation_id=scenario.regulation_id,
                    zone=scenario.zone,
                    expected=scenario.expected,
                    actual=None,
                    result=RESULT_ERROR,
                    detail=f"resolver raised {type(exc).__name__}: {exc}",
                ))
                continue

            results.append(self._score(scenario, answer))

        return TestRun(
            run_id=run_id or f"run-{uuid.uuid4().hex[:12]}",
            regulation_id=regulation_id,
            interpretation_version=interpretation_version,
            ran_at=_utc_now(),
            results=results,
            skipped=skipped,
        )

    def run_against_cassette(
        self,
        cassette: Any,
        regulation_id: str,
        interpretation_version: str,
        run_id: Optional[str] = None,
    ) -> TestRun:
        """Run this library's approved scenarios against a domain
        cassette's own resolve_scenario -- but only if the cassette
        enables cassette_capabilities.CAPABILITY_INTERPRETATION_TESTABLE
        (see the module docstring's WIRING A LIVE CASSETTE section).

        A cassette that does not enable the capability cannot be
        scenario-tested: every candidate scenario for this regulation
        is reported in the returned TestRun's `skipped` list with a
        not-testable reason, and resolve_scenario is never called.
        This is a report, not an error -- the same NO SILENT SKIPS
        posture scenarios.py already takes with unapproved scenarios,
        applied to a cassette that was never obligated to answer in
        the first place.
        """
        if not cassette_is_scenario_testable(cassette):
            from cassette_schema import cassette_version_of

            label = cassette_version_of(cassette)
            candidates = [s for s in self._library.all() if s.regulation_id == regulation_id]
            return TestRun(
                run_id=run_id or f"run-{uuid.uuid4().hex[:12]}",
                regulation_id=regulation_id,
                interpretation_version=interpretation_version,
                ran_at=_utc_now(),
                results=[],
                skipped=[{
                    "scenario_id": s.scenario_id,
                    "zone": s.zone,
                    "reason": (
                        f"not testable: cassette '{label}' does not enable "
                        f"'interpretation_testable'"
                    ),
                } for s in candidates],
            )
        return self.run(cassette.resolve_scenario, regulation_id,
                        interpretation_version, run_id)

    @staticmethod
    def _score(scenario: Scenario, answer: Optional[str]) -> ScenarioResult:
        common = {
            "scenario_id": scenario.scenario_id,
            "regulation_id": scenario.regulation_id,
            "zone": scenario.zone,
            "expected": scenario.expected,
            "actual": answer,
        }
        if answer is None:
            return ScenarioResult(**common, result=RESULT_INDETERMINATE,
                                  detail="resolver declined to answer")
        if answer not in scenario.options:
            return ScenarioResult(**common, result=RESULT_ERROR,
                                  detail=f"answer {answer!r} not among options {scenario.options}")
        if answer == scenario.expected:
            return ScenarioResult(**common, result=RESULT_MATCH)
        return ScenarioResult(**common, result=RESULT_MISMATCH,
                              detail="answered a different valid option than Legal locked in")
