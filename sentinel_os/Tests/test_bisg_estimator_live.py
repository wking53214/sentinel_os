"""
test_bisg_estimator_live -- proves CensusBISGEstimator produces real
estimates from real, live Census data (geocoding, ACS, and the
downloaded surname list). Skips cleanly -- never fails the suite -- when
CENSUS_API_KEY isn't set or the live services aren't reachable, same
posture as this repo's own _pg_available()-style optional-infrastructure
tests. This is deliberately kept separate from test_bisg_estimator.py:
that file proves the CHECKER/parsing logic deterministically and always
runs; this file is the one place the "not fabricated, genuinely real"
claim about CensusBISGEstimator itself is proven, and it needs live
network access to do that honestly.

Run: pytest Tests/test_bisg_estimator_live.py -v
Requires CENSUS_API_KEY in the environment and outbound internet access.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from bisg_estimator import AcsRaceTable, CensusBISGEstimator, CensusGeocoder

_KNOWN_ADDRESS = "4600 Silver Hill Rd, Washington, DC"


def _live_available() -> bool:
    if not os.environ.get("CENSUS_API_KEY"):
        return False
    try:
        geo = CensusGeocoder().geocode_to_tract(_KNOWN_ADDRESS)
        if geo is None:
            return False
        return AcsRaceTable().national_totals() is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _live_available(),
    reason="CENSUS_API_KEY not set, or Census geocoding/ACS services unreachable",
)


def test_geocoder_resolves_a_real_address_to_a_real_tract():
    geo = CensusGeocoder().geocode_to_tract(_KNOWN_ADDRESS)
    assert geo is not None
    assert geo["state"] == "24"  # Maryland
    assert geo["county"] == "033"  # Prince George's County
    assert len(geo["tract"]) == 6


def test_geocoder_returns_none_for_a_nonsense_address():
    geo = CensusGeocoder().geocode_to_tract("asdkjfhaskdjfh not a real address 99999999")
    assert geo is None


def test_acs_national_totals_are_real_and_plausible():
    totals = AcsRaceTable().national_totals()
    assert totals is not None
    # US population is on the order of 300-350M; a sanity floor/ceiling,
    # not an exact pin (ACS 5-year estimates update over time).
    assert 250_000_000 < totals["total"] < 400_000_000
    assert sum(totals[r] for r in ("white", "black", "aian", "api",
                                   "mult_other", "hispanic")) == pytest.approx(
        totals["total"], rel=0.01)


def test_full_estimate_for_a_hispanic_associated_surname_skews_hispanic():
    """Real, live, end-to-end: a name with a strong Hispanic-population
    association, combined with a real tract's real demographics,
    produces a posterior that genuinely reflects both signals -- not
    asserting an exact number (live data), but a directionally correct,
    plausible outcome."""
    estimator = CensusBISGEstimator()
    result = estimator.estimate("Garcia", _KNOWN_ADDRESS)
    assert result.is_determinate, result.indeterminate_reason
    assert result.precision == "tract"
    assert sum(result.distribution.values()) == pytest.approx(1.0, abs=0.01)
    assert result.distribution["hispanic"] > 0.5


def test_full_estimate_missing_surname_or_address_is_indeterminate():
    estimator = CensusBISGEstimator()
    result = estimator.estimate(None, _KNOWN_ADDRESS)
    assert not result.is_determinate
    assert "required" in result.indeterminate_reason

    result2 = estimator.estimate("Garcia", None)
    assert not result2.is_determinate


def test_full_estimate_unresolvable_address_is_indeterminate_not_fabricated():
    estimator = CensusBISGEstimator()
    result = estimator.estimate("Garcia", "not a real address at all 00000")
    assert not result.is_determinate
    assert result.distribution is None
    assert "geocode" in result.indeterminate_reason
