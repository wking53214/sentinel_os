"""
test_c2_statistical_outcome_equity -- proof suite for C2 dimension 4
(regulatory_checks.check_statistical_outcome_equity): the four-fifths
disparate-impact screen, the consent_model field it's configured
alongside, and CFPBRegBLens.c2_rollup()'s optional wiring of a
precomputed dimension-4 result.

Pure logic + one lens-wiring section -- no ledger, no sealed channel
(that's Tests/test_sealed_demographic_channel.py's job; this file tests
the STATISTICAL check itself against already-assembled CohortDecision
records, and the rollup wiring against already-computed findings lists).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from regulatory_cassette_interface import DecisionMaterial
from regulatory_cassettes.cfpb_reg_b import CFPBRegBLens
from regulatory_checks import (
    C2_FLAG,
    C2_INDETERMINATE,
    CONSENT_OPT_IN_REQUIRED,
    CONSENT_OPT_OUT_PERMITTED,
    DIMENSION_STATISTICAL_OUTCOME_EQUITY,
    FOUR_FIFTHS_THRESHOLD,
    MIN_COHORT_SIZE_FOR_STATISTICAL_TEST,
    CohortDecision,
    RegulationCheckProfile,
    check_statistical_outcome_equity,
)


def _material(inputs=None):
    return DecisionMaterial(
        subject_id="D-1", domain="lending", reasons=(),
        input_fields=dict(inputs or {}), mismatched_fields=(), outcome={}, source="ledger",
    )


# ==========================================================================
# consent_model
# ==========================================================================

def test_consent_model_defaults_to_opt_in_required():
    profile = RegulationCheckProfile(regulation="x")
    assert profile.consent_model == CONSENT_OPT_IN_REQUIRED


def test_consent_model_accepts_opt_out_permitted():
    profile = RegulationCheckProfile(regulation="x", consent_model=CONSENT_OPT_OUT_PERMITTED)
    assert profile.consent_model == CONSENT_OPT_OUT_PERMITTED


def test_consent_model_rejects_unknown_value():
    with pytest.raises(ValueError, match="consent_model"):
        RegulationCheckProfile(regulation="x", consent_model="just_guess_it")


def test_consent_model_rides_in_as_dict():
    profile = RegulationCheckProfile(regulation="x", consent_model=CONSENT_OPT_OUT_PERMITTED)
    assert profile.as_dict()["consent_model"] == CONSENT_OPT_OUT_PERMITTED


# ==========================================================================
# check_statistical_outcome_equity -- four-fifths rule
# ==========================================================================

_PROFILE = RegulationCheckProfile(regulation="test-dimension-4")


def _cohort(n_per_group: int, rate_a: float, rate_b: float):
    """n_per_group decisions each for two single-category groups 'a'
    and 'b', each with an independently-set favorable rate."""
    cohort = []
    n_favorable_a = round(n_per_group * rate_a)
    n_favorable_b = round(n_per_group * rate_b)
    for i in range(n_per_group):
        cohort.append(CohortDecision(
            f"a{i}", favorable_outcome=(i < n_favorable_a), group_distribution={"white": 1.0},
        ))
    for i in range(n_per_group):
        cohort.append(CohortDecision(
            f"b{i}", favorable_outcome=(i < n_favorable_b), group_distribution={"black": 1.0},
        ))
    return cohort


def test_clear_adverse_impact_flags():
    # 90% vs 50%: ratio 0.556, well under the 0.8 threshold.
    cohort = _cohort(20, rate_a=0.9, rate_b=0.5)
    findings = check_statistical_outcome_equity(cohort, _PROFILE)
    assert len(findings) == 1
    f = findings[0]
    assert f.classification == "four_fifths_adverse_impact"
    assert f.evidence["group"] == "black"
    assert f.evidence["ratio"] == pytest.approx(0.5 / 0.9, abs=0.01)
    assert f.evidence["ratio"] < FOUR_FIFTHS_THRESHOLD


def test_equal_rates_across_groups_is_clean():
    cohort = _cohort(20, rate_a=0.75, rate_b=0.75)
    assert check_statistical_outcome_equity(cohort, _PROFILE) == []


def test_rates_within_four_fifths_band_is_clean():
    # 80% vs 65%: ratio = 0.8125, just above the 0.8 threshold.
    cohort = _cohort(20, rate_a=0.80, rate_b=0.65)
    assert check_statistical_outcome_equity(cohort, _PROFILE) == []


def test_below_minimum_cohort_size_is_indeterminate_not_a_false_pass():
    small = _cohort(5, rate_a=1.0, rate_b=0.0)  # would obviously flag if evaluated
    assert len(small) < MIN_COHORT_SIZE_FOR_STATISTICAL_TEST
    findings = check_statistical_outcome_equity(small, _PROFILE)
    assert len(findings) == 1
    assert findings[0].classification == "indeterminate_insufficient_cohort"
    assert findings[0].evidence["cohort_size"] == len(small)


def test_single_group_cohort_is_indeterminate_insufficient_coverage():
    cohort = [
        CohortDecision(f"a{i}", favorable_outcome=(i % 2 == 0), group_distribution={"white": 1.0})
        for i in range(MIN_COHORT_SIZE_FOR_STATISTICAL_TEST + 5)
    ]
    findings = check_statistical_outcome_equity(cohort, _PROFILE)
    assert len(findings) == 1
    assert findings[0].classification == "indeterminate_insufficient_group_coverage"


def test_bisg_weighted_probabilities_combine_correctly():
    """Probability-weighted, not hard-assigned: a decision with a 60/40
    BISG split contributes 0.6 to white's weight and 0.4 to black's,
    not a full point to whichever is higher."""
    cohort = []
    # 20 decisions, 60% white / 40% black estimated, all favorable.
    for i in range(20):
        cohort.append(CohortDecision(
            f"w{i}", favorable_outcome=True, group_distribution={"white": 0.6, "black": 0.4},
        ))
    # 20 decisions, same split, all UNfavorable.
    for i in range(20):
        cohort.append(CohortDecision(
            f"u{i}", favorable_outcome=False, group_distribution={"white": 0.6, "black": 0.4},
        ))
    # Both groups get identical weighted rates (50% each, since the
    # split is uniform across favorable/unfavorable) -- clean.
    assert check_statistical_outcome_equity(cohort, _PROFILE) == []


def test_negligible_weight_group_excluded_from_comparison():
    """A group with less than 1.0 total effective weight in the cohort
    doesn't get compared -- not enough signal to say anything about it
    here, and it must not produce a spurious flag from a near-zero
    denominator."""
    cohort = _cohort(MIN_COHORT_SIZE_FOR_STATISTICAL_TEST, rate_a=0.9, rate_b=0.9)
    # Add one decision with a tiny sliver of a third group -- total
    # weight 0.05, well under the 1.0 floor.
    cohort.append(CohortDecision("c0", favorable_outcome=True,
                                 group_distribution={"aian": 0.05, "white": 0.95}))
    findings = check_statistical_outcome_equity(cohort, _PROFILE)
    assert not any(f.evidence.get("group") == "aian" for f in findings)


# ==========================================================================
# CFPBRegBLens.c2_rollup() -- optional dimension-4 passthrough
# ==========================================================================

def test_c2_rollup_default_still_indeterminate_when_no_result_passed():
    """Unchanged behavior: a caller that doesn't pass a dimension-4
    result gets exactly the same INDETERMINATE-forcing None as before
    dimension 4 existed."""
    lens = CFPBRegBLens(version="1.0.0-d4-a")
    rollup = lens.c2_rollup(_material())
    assert rollup.status == C2_INDETERMINATE
    assert DIMENSION_STATISTICAL_OUTCOME_EQUITY in rollup.not_evaluated_dimensions


def test_c2_rollup_accepts_clean_precomputed_dimension_4_result():
    lens = CFPBRegBLens(version="1.0.0-d4-b")
    rollup = lens.c2_rollup(_material(), statistical_outcome_equity_findings=[])
    assert DIMENSION_STATISTICAL_OUTCOME_EQUITY in rollup.evaluated_dimensions
    assert DIMENSION_STATISTICAL_OUTCOME_EQUITY not in rollup.not_evaluated_dimensions


def test_c2_rollup_flags_when_precomputed_dimension_4_result_flagged():
    lens = CFPBRegBLens(version="1.0.0-d4-c")
    cohort = _cohort(20, rate_a=0.9, rate_b=0.5)
    d4_findings = check_statistical_outcome_equity(cohort, _PROFILE)
    rollup = lens.c2_rollup(_material(), statistical_outcome_equity_findings=d4_findings)
    assert rollup.status == C2_FLAG
    assert DIMENSION_STATISTICAL_OUTCOME_EQUITY in rollup.flagged_dimensions


def test_c2_rollup_can_reach_pass_only_when_dimension_4_actually_clean():
    """The one scenario that could not happen before this session: a
    non-INDETERMINATE PASS, when dimension 4 genuinely ran clean and no
    other dimension flagged."""
    lens = CFPBRegBLens(version="1.0.0-d4-d")
    material = _material(inputs={"income": 90000})  # nothing proxy-shaped
    rollup = lens.c2_rollup(material, statistical_outcome_equity_findings=[])
    from regulatory_checks import C2_PASS
    assert rollup.status == C2_PASS
