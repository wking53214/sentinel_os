"""
test_c2_correlation_based_proxy_detection -- proof suite for the C2
finding #2 mitigation (regulatory_checks.check_correlation_based_proxy_
detection): screening numeric/boolean input VALUES for correlation with
estimated group membership, so a renamed proxy variable no longer
defeats detection on name alone.

Pure logic, no ledger, no sealed channel -- same posture as
test_c2_statistical_outcome_equity.py: this file tests the statistical
check itself against already-assembled CohortInputDecision records,
not the sealed-channel plumbing that would assemble them in practice.
"""

import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from regulatory_checks import (
    CORRELATION_FLAG_THRESHOLD,
    MIN_COHORT_SIZE_FOR_STATISTICAL_TEST,
    CohortInputDecision,
    RegulationCheckProfile,
    check_correlation_based_proxy_detection,
)

_PROFILE = RegulationCheckProfile(regulation="test-finding-2-mitigation")
_N = MIN_COHORT_SIZE_FOR_STATISTICAL_TEST


def _rng():
    return random.Random(1234)


# ==========================================================================
# Cohort-size gate (same posture as dimension 4)
# ==========================================================================

def test_below_minimum_cohort_size_is_indeterminate_not_a_false_pass():
    small = [
        CohortInputDecision(f"s{i}", input_fields={"x": float(i)},
                             group_distribution={"black": 1.0})
        for i in range(_N - 5)
    ]
    findings = check_correlation_based_proxy_detection(small, _PROFILE)
    assert len(findings) == 1
    assert findings[0].classification == "indeterminate_insufficient_cohort"
    assert findings[0].evidence["cohort_size"] == len(small)
    assert findings[0].evidence["minimum_required"] == _N


def test_empty_cohort_is_indeterminate():
    findings = check_correlation_based_proxy_detection([], _PROFILE)
    assert len(findings) == 1
    assert findings[0].classification == "indeterminate_insufficient_cohort"


# ==========================================================================
# Renamed proxy detection -- the actual mitigation
# ==========================================================================

def test_renamed_numeric_proxy_is_caught_by_value_not_name():
    """A variable with an innocuous name that check_proxy_variables would
    never match on its name alone, but whose VALUE is planted to track
    group membership, still gets flagged."""
    rng = _rng()
    cohort = []
    for i in range(60):
        is_black = 1.0 if i % 3 == 0 else 0.0
        noise = rng.uniform(-0.03, 0.03)
        prob = min(max(is_black + noise, 0.0), 1.0)
        cohort.append(CohortInputDecision(
            subject_id=f"s{i}",
            input_fields={
                "zx_field_9": is_black * 100.0 + rng.uniform(-1, 1),
                "unrelated_amount": rng.uniform(0, 1000),
            },
            group_distribution={"black": prob, "white": 1 - prob},
        ))
    findings = check_correlation_based_proxy_detection(cohort, _PROFILE)
    flagged_vars = {f.evidence["variable"] for f in findings}
    assert "zx_field_9" in flagged_vars
    assert "unrelated_amount" not in flagged_vars
    hit = next(f for f in findings if f.evidence["variable"] == "zx_field_9"
               and f.evidence["group"] == "black")
    assert hit.classification == "correlation_based_proxy_signal"
    assert abs(hit.evidence["correlation"]) >= CORRELATION_FLAG_THRESHOLD
    assert hit.score == pytest.approx(abs(hit.evidence["correlation"]), abs=1e-9)
    assert hit.evidence["sample_size"] == 60


def test_renamed_boolean_proxy_is_caught():
    """Boolean input variables are in scope, coerced to 1.0/0.0 -- a
    binary flag is a very plausible shape for a renamed proxy."""
    cohort = []
    for i in range(60):
        flag = (i % 4 == 0)
        prob = 0.9 if flag else 0.1
        cohort.append(CohortInputDecision(
            subject_id=f"s{i}",
            input_fields={"in_target_segment": flag},
            group_distribution={"hispanic": prob, "white": 1 - prob},
        ))
    findings = check_correlation_based_proxy_detection(cohort, _PROFILE)
    assert any(f.evidence["variable"] == "in_target_segment"
               and f.evidence["group"] == "hispanic" for f in findings)


def test_uncorrelated_numeric_variable_is_clean():
    rng = _rng()
    cohort = [
        CohortInputDecision(
            subject_id=f"s{i}",
            input_fields={"loan_amount": rng.uniform(1000, 50000)},
            group_distribution={"asian": rng.uniform(0, 1), "white": rng.uniform(0, 1)},
        )
        for i in range(60)
    ]
    findings = check_correlation_based_proxy_detection(cohort, _PROFILE)
    assert findings == []


def test_constant_variable_is_skipped_not_flagged_or_indeterminate():
    """No variance to correlate -- statistics.correlation raises
    StatisticsError; the check must swallow that per-pair, not crash
    or manufacture a finding."""
    cohort = [
        CohortInputDecision(
            subject_id=f"s{i}",
            input_fields={"always_100": 100.0},
            group_distribution={"black": 1.0 if i % 2 == 0 else 0.0,
                                "white": 0.0 if i % 2 == 0 else 1.0},
        )
        for i in range(60)
    ]
    assert check_correlation_based_proxy_detection(cohort, _PROFILE) == []


def test_sparse_variable_skipped_without_dedicated_finding():
    """A variable present in far fewer than MIN_COHORT_SIZE_FOR_STATISTICAL_TEST
    decisions is skipped silently -- the overall cohort-size gate is
    what protects against too-little-data findings, not a per-variable
    indeterminate for every sparse field."""
    rng = _rng()
    cohort = []
    for i in range(60):
        fields = {"common_field": rng.uniform(0, 1)}
        if i < 3:
            fields["rare_field"] = 999.0
        cohort.append(CohortInputDecision(
            subject_id=f"s{i}", input_fields=fields,
            group_distribution={"black": rng.uniform(0, 1)},
        ))
    findings = check_correlation_based_proxy_detection(cohort, _PROFILE)
    assert all(f.evidence.get("variable") != "rare_field" for f in findings)


def test_string_and_none_values_are_never_treated_as_numeric():
    rng = _rng()
    cohort = [
        CohortInputDecision(
            subject_id=f"s{i}",
            input_fields={"neighborhood_cluster": f"cluster-{i % 5}",
                          "notes": None,
                          "amount": rng.uniform(0, 100)},
            group_distribution={"black": rng.uniform(0, 1)},
        )
        for i in range(60)
    ]
    findings = check_correlation_based_proxy_detection(cohort, _PROFILE)
    flagged_vars = {f.evidence["variable"] for f in findings}
    assert "neighborhood_cluster" not in flagged_vars
    assert "notes" not in flagged_vars


def test_missing_field_in_some_decisions_only_uses_present_pairs():
    rng = _rng()
    cohort = []
    for i in range(70):
        fields = {}
        is_black = 1.0 if i % 3 == 0 else 0.0
        if i < 65:  # present in 65/70, still >= MIN_COHORT_SIZE
            fields["sometimes_present"] = is_black * 50.0 + rng.uniform(-1, 1)
        prob = min(max(is_black + rng.uniform(-0.03, 0.03), 0.0), 1.0)
        cohort.append(CohortInputDecision(
            subject_id=f"s{i}", input_fields=fields,
            group_distribution={"black": prob, "white": 1 - prob},
        ))
    findings = check_correlation_based_proxy_detection(cohort, _PROFILE)
    hit = next((f for f in findings if f.evidence["variable"] == "sometimes_present"
                and f.evidence["group"] == "black"), None)
    assert hit is not None
    assert hit.evidence["sample_size"] == 65


def test_findings_carry_screening_disclaimer_shaped_evidence():
    """Not a strict SCREENING_DISCLAIMER string match (that's the
    lens/deck's job) -- just that every finding is self-evidently a
    signal, not a verdict, per this module's stated posture."""
    cohort = []
    for i in range(60):
        is_black = 1.0 if i % 2 == 0 else 0.0
        cohort.append(CohortInputDecision(
            subject_id=f"s{i}",
            input_fields={"planted": is_black * 10.0},
            group_distribution={"black": is_black, "white": 1 - is_black},
        ))
    findings = check_correlation_based_proxy_detection(cohort, _PROFILE)
    assert findings
    for f in findings:
        assert "score_meaning" in f.evidence
        assert "not itself evidence of intent" in f.evidence["score_meaning"]
        assert f.action == "flag"
        assert 0.0 <= f.score <= 1.0
