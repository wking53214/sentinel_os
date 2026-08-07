"""
test_cassette_resolver_seam -- proves the kernel/capability seam this
harness uses to probe a live domain cassette (see harness.py's module
docstring, WIRING A LIVE CASSETTE section):

  - a cassette that enables CAPABILITY_INTERPRETATION_TESTABLE is
    scenario-testable, and its resolve_scenario is exactly what
    run_against_cassette calls as the resolver
  - a cassette that does not enable it is reported not-testable in
    the run's `skipped` list -- never an error, never a crash
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cassettes.banking_cassette import BankingCassette
from cassettes.mortgage_cassette import (
    ZONE_ADVERSE_ACTION_REASON_SPECIFICITY,
    MortgageCassette,
)
from interpretation import Scenario, ScenarioLibrary, TestHarness, cassette_is_scenario_testable
from interpretation.harness import RESULT_ERROR, RESULT_INDETERMINATE, RESULT_MATCH, RESULT_MISMATCH

REG = "cfpb-ecoa-reg-b"


def _reason_scenario(candidate_reason, expected, scenario_id=None):
    s = Scenario(
        regulation_id=REG,
        zone=ZONE_ADVERSE_ACTION_REASON_SPECIFICITY,
        question="Is this adverse-action reason specific enough under Reg B?",
        situation={"candidate_reason": candidate_reason},
        options=["specific", "generic"],
        **({"scenario_id": scenario_id} if scenario_id else {}),
    )
    s.approve(expected=expected, approver="counsel@example",
              rationale="locked reading of the specificity zone")
    return s


# ---------------------------------------------------------------------
# Capability declared -> testable
# ---------------------------------------------------------------------

def test_mortgage_cassette_is_scenario_testable():
    assert cassette_is_scenario_testable(MortgageCassette()) is True


def test_run_against_a_testable_cassette_scores_normally():
    """No skips, resolve_scenario is actually consulted, and results
    come back scored MATCH/MISMATCH like any other resolver."""
    matching = _reason_scenario(
        "credit score 574 is below the 620 minimum required for this loan amount",
        expected="specific",
    )
    drifted = _reason_scenario("denied", expected="specific")  # cassette will say generic
    lib = ScenarioLibrary([matching, drifted])

    run = TestHarness(lib).run_against_cassette(MortgageCassette(), REG, "v1")

    assert run.skipped == []
    assert run.total == 2
    results_by_id = {r.scenario_id: r for r in run.results}
    assert results_by_id[matching.scenario_id].result == RESULT_MATCH
    assert results_by_id[drifted.scenario_id].result == RESULT_MISMATCH


def test_resolve_scenario_declines_outside_its_recognized_zone():
    """A scenario outside the one zone this cassette knows how to
    answer is declined (None), which the harness scores INDETERMINATE
    -- a first-class, non-error outcome, not silently dropped."""
    outside_zone = Scenario(
        regulation_id=REG, zone="some_other_zone",
        question="unrelated question", situation={}, options=["A", "B"],
    )
    outside_zone.approve(expected="A", approver="counsel@example", rationale="n/a")
    lib = ScenarioLibrary([outside_zone])

    run = TestHarness(lib).run_against_cassette(MortgageCassette(), REG, "v1")

    assert run.skipped == []
    assert run.total == 1
    assert run.results[0].result == RESULT_INDETERMINATE


# ---------------------------------------------------------------------
# Capability not declared -> reported not-testable, never an error
# ---------------------------------------------------------------------

def test_banking_cassette_is_not_scenario_testable():
    assert cassette_is_scenario_testable(BankingCassette()) is False


def test_run_against_a_non_testable_cassette_skips_every_candidate():
    s1 = _reason_scenario("denied", expected="generic", scenario_id="scn-a")
    s2 = _reason_scenario(
        "credit score 574 is below the 620 minimum required", expected="specific",
        scenario_id="scn-b",
    )
    lib = ScenarioLibrary([s1, s2])

    run = TestHarness(lib).run_against_cassette(BankingCassette(), REG, "v1")

    # No results at all -- resolve_scenario is never called, and this
    # is reported, not an ERROR result and not a crash.
    assert run.results == []
    assert run.total == 0
    assert len(run.skipped) == 2
    reasons = {row["scenario_id"]: row["reason"] for row in run.skipped}
    assert "not testable" in reasons["scn-a"]
    assert "interpretation_testable" in reasons["scn-a"]
    assert set(reasons) == {"scn-a", "scn-b"}
    # No RESULT_ERROR anywhere in this run -- "cannot be tested" is not
    # "something went wrong".
    assert all(r.result != RESULT_ERROR for r in run.results)


def test_non_testable_cassette_never_reaches_resolve_scenario():
    """BankingCassette has no resolve_scenario at all -- proves
    run_against_cassette's capability check runs BEFORE any attempt to
    call it, rather than failing over into an AttributeError."""
    assert not hasattr(BankingCassette(), "resolve_scenario")
    lib = ScenarioLibrary([_reason_scenario("denied", expected="generic")])
    run = TestHarness(lib).run_against_cassette(BankingCassette(), REG, "v1")
    assert run.total == 0
    assert len(run.skipped) == 1
