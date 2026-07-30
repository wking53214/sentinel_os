"""
test_c2_geographic_outcome_equity -- proof suite for C2 dimension 6
(regulatory_checks.check_geographic_outcome_equity): the ZIP/county
redlining-style regional-equity screen.

Pure logic only -- no ledger, no sealed channel, no geocoder (that's
obligation_sweep's job to wire together; see Tests/test_obligation_sweep.py
for the end-to-end geocoding path). This file tests the STATISTICAL
check itself against already-assembled GeographicCohortDecision records,
same posture as test_c2_statistical_outcome_equity.py for dimension 4.

Not wired into CFPBRegBLens.c2_rollup() yet (deliberately left as a
follow-up decision, same posture dimension 5/correlation had between
being built and being wired in) -- so there is no rollup-wiring section
here yet, unlike test_c2_statistical_outcome_equity.py's.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from regulatory_checks import (
    FOUR_FIFTHS_THRESHOLD,
    GEOGRAPHY_TIER_COUNTY,
    GEOGRAPHY_TIER_ZIP,
    MIN_COHORT_SIZE_FOR_STATISTICAL_TEST,
    MIN_OBSERVATIONS_PER_GEOGRAPHY_GROUP,
    GeographicCohortDecision,
    RegulationCheckProfile,
    check_geographic_outcome_equity,
)

_PROFILE = RegulationCheckProfile(regulation="test-dimension-6")


def _cohort_for_tier(n_per_group: int, rate_a: float, rate_b: float,
                     tier: str = GEOGRAPHY_TIER_ZIP):
    """n_per_group decisions each for two groups 'zip-a'/'zip-b' (or
    'county-a'/'county-b'), hard-assigned -- one group per record, not
    a distribution. Mirrors test_c2_statistical_outcome_equity._cohort
    but for the hard-assigned shape."""
    cohort = []
    n_favorable_a = round(n_per_group * rate_a)
    n_favorable_b = round(n_per_group * rate_b)
    for i in range(n_per_group):
        kwargs = dict(subject_id=f"a{i}", favorable_outcome=(i < n_favorable_a),
                     zip_code=None, county_fips=None)
        if tier == GEOGRAPHY_TIER_ZIP:
            kwargs["zip_code"] = "62701"
        else:
            kwargs["county_fips"] = "17167"
        cohort.append(GeographicCohortDecision(**kwargs))
    for i in range(n_per_group):
        kwargs = dict(subject_id=f"b{i}", favorable_outcome=(i < n_favorable_b),
                     zip_code=None, county_fips=None)
        if tier == GEOGRAPHY_TIER_ZIP:
            kwargs["zip_code"] = "62704"
        else:
            kwargs["county_fips"] = "17201"
        cohort.append(GeographicCohortDecision(**kwargs))
    return cohort


# ==========================================================================
# ZIP tier
# ==========================================================================

def test_clear_adverse_impact_flags_at_zip_tier():
    # 90% vs 50%: ratio 0.556, well under the 0.8 threshold.
    cohort = _cohort_for_tier(20, rate_a=0.9, rate_b=0.5, tier=GEOGRAPHY_TIER_ZIP)
    findings = check_geographic_outcome_equity(cohort, _PROFILE)
    zip_findings = [f for f in findings if f.evidence.get("tier") == GEOGRAPHY_TIER_ZIP]
    assert len(zip_findings) == 1
    f = zip_findings[0]
    assert f.classification == "four_fifths_adverse_impact"
    assert f.evidence["group"] == "62704"
    assert f.evidence["ratio"] == pytest.approx(0.5 / 0.9, abs=0.01)
    assert f.evidence["ratio"] < FOUR_FIFTHS_THRESHOLD


def test_equal_rates_across_zips_is_clean():
    cohort = _cohort_for_tier(20, rate_a=0.75, rate_b=0.75, tier=GEOGRAPHY_TIER_ZIP)
    findings = check_geographic_outcome_equity(cohort, _PROFILE)
    assert not any(f.evidence.get("tier") == GEOGRAPHY_TIER_ZIP and
                   f.classification == "four_fifths_adverse_impact" for f in findings)


# ==========================================================================
# County tier -- same math, different attribute, run independently
# ==========================================================================

def test_clear_adverse_impact_flags_at_county_tier():
    cohort = _cohort_for_tier(20, rate_a=0.9, rate_b=0.5, tier=GEOGRAPHY_TIER_COUNTY)
    findings = check_geographic_outcome_equity(cohort, _PROFILE)
    county_findings = [f for f in findings
                       if f.evidence.get("tier") == GEOGRAPHY_TIER_COUNTY]
    assert len(county_findings) == 1
    assert county_findings[0].evidence["group"] == "17201"


def test_rates_within_four_fifths_band_is_clean_at_county_tier():
    # 80% vs 65%: ratio = 0.8125, just above the 0.8 threshold.
    cohort = _cohort_for_tier(20, rate_a=0.80, rate_b=0.65, tier=GEOGRAPHY_TIER_COUNTY)
    findings = check_geographic_outcome_equity(cohort, _PROFILE)
    assert not any(f.evidence.get("tier") == GEOGRAPHY_TIER_COUNTY and
                   f.classification == "four_fifths_adverse_impact" for f in findings)


# ==========================================================================
# Both tiers run together, independently, off the SAME cohort
# ==========================================================================

def test_both_tiers_evaluated_from_one_cohort_independently():
    """A record's zip and county can disagree about which side of a
    disparity it's on -- e.g. two ZIPs in the same county with very
    different rates should be able to flag at the ZIP tier while the
    county tier (pooling both ZIPs together) stays clean."""
    cohort = []
    # ZIP 62701 (county 17167): 90% favorable, 20 loans.
    for i in range(20):
        cohort.append(GeographicCohortDecision(
            f"a{i}", favorable_outcome=(i < 18), zip_code="62701", county_fips="17167"))
    # ZIP 62702 (SAME county 17167): 50% favorable, 20 loans.
    for i in range(20):
        cohort.append(GeographicCohortDecision(
            f"b{i}", favorable_outcome=(i < 10), zip_code="62702", county_fips="17167"))
    findings = check_geographic_outcome_equity(cohort, _PROFILE)
    zip_flags = [f for f in findings if f.evidence.get("tier") == GEOGRAPHY_TIER_ZIP
                and f.classification == "four_fifths_adverse_impact"]
    county_flags = [f for f in findings if f.evidence.get("tier") == GEOGRAPHY_TIER_COUNTY
                   and f.classification == "four_fifths_adverse_impact"]
    assert len(zip_flags) == 1  # the two ZIPs disagree -- flags
    assert len(county_flags) == 0  # pooled into one county -- nothing to compare


# ==========================================================================
# Cohort-size / group-coverage gates
# ==========================================================================

def test_below_minimum_cohort_size_is_indeterminate_not_a_false_pass():
    small = _cohort_for_tier(5, rate_a=1.0, rate_b=0.0)  # would obviously flag if evaluated
    assert len(small) < MIN_COHORT_SIZE_FOR_STATISTICAL_TEST
    findings = check_geographic_outcome_equity(small, _PROFILE)
    assert len(findings) == 1  # one indeterminate for the WHOLE cohort, not per-tier
    assert findings[0].classification == "indeterminate_insufficient_cohort"
    assert findings[0].evidence["cohort_size"] == len(small)


def test_single_group_per_tier_is_indeterminate_insufficient_coverage():
    cohort = [
        GeographicCohortDecision(f"a{i}", favorable_outcome=(i % 2 == 0),
                                 zip_code="62701", county_fips="17167")
        for i in range(MIN_COHORT_SIZE_FOR_STATISTICAL_TEST + 5)
    ]
    findings = check_geographic_outcome_equity(cohort, _PROFILE)
    # every record shares the same zip AND county -- both tiers indeterminate
    assert len(findings) == 2
    assert {f.evidence["tier"] for f in findings} == {GEOGRAPHY_TIER_ZIP, GEOGRAPHY_TIER_COUNTY}
    assert all(f.classification == "indeterminate_insufficient_group_coverage"
              for f in findings)


def test_group_below_minimum_observations_excluded_from_comparison():
    """Unlike dimension 4's >= 1.0 EFFECTIVE weight floor (built for
    probability-weighted BISG estimates), hard-assigned geography needs
    a real sample per group -- MIN_OBSERVATIONS_PER_GEOGRAPHY_GROUP,
    not 1."""
    cohort = _cohort_for_tier(MIN_COHORT_SIZE_FOR_STATISTICAL_TEST, rate_a=0.9, rate_b=0.9,
                              tier=GEOGRAPHY_TIER_ZIP)
    # Add a THIRD zip with only 2 loans -- below the floor of 5.
    assert MIN_OBSERVATIONS_PER_GEOGRAPHY_GROUP > 2
    cohort.append(GeographicCohortDecision("c0", favorable_outcome=True,
                                           zip_code="62799", county_fips=None))
    cohort.append(GeographicCohortDecision("c1", favorable_outcome=False,
                                           zip_code="62799", county_fips=None))
    findings = check_geographic_outcome_equity(cohort, _PROFILE)
    assert not any(f.evidence.get("group") == "62799" for f in findings)


# ==========================================================================
# Partial geography: a record can carry only one tier's value
# ==========================================================================

def test_record_with_only_zip_contributes_to_zip_tier_only():
    cohort = _cohort_for_tier(20, rate_a=0.9, rate_b=0.5, tier=GEOGRAPHY_TIER_ZIP)
    # None of these records have a county_fips at all.
    assert all(d.county_fips is None for d in cohort)
    findings = check_geographic_outcome_equity(cohort, _PROFILE)
    # county tier: no groups have ANY observations -- indeterminate, not a flag
    county_findings = [f for f in findings if f.evidence.get("tier") == GEOGRAPHY_TIER_COUNTY]
    assert len(county_findings) == 1
    assert county_findings[0].classification == "indeterminate_insufficient_group_coverage"
