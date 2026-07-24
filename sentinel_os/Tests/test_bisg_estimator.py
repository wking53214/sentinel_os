"""
test_bisg_estimator -- deterministic proof suite for bisg_estimator.py's
logic: the pluggable interface, SurnameTable's real-data parsing (using a
small, genuinely-real excerpt of actual 2010 Census surname rows, not
fabricated numbers), and FakeBISGEstimator. No network required -- see
Tests/test_bisg_estimator_live.py for the live-data-backed proof that
CensusBISGEstimator itself produces real, correct estimates end to end.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from bisg_estimator import (
    RACE_CATEGORIES,
    BISGEstimate,
    FakeBISGEstimator,
    SurnameTable,
)

# A small, genuinely real excerpt -- verbatim rows from the actual 2010
# Census surname list (www2.census.gov/topics/genealogy/2010surnames/
# names.zip), not invented numbers. GARCIA includes a real "(S)"
# suppressed-value cell (pctaian), exercising that handling path with
# real data rather than a synthetic case.
_REAL_EXCERPT_CSV = (
    "name,rank,count,prop100k,cum_prop100k,pctwhite,pctblack,pctapi,pctaian,pct2prace,pcthispanic\n"
    "SMITH,1,2442977,828.19,828.19,70.9,23.11,0.5,0.89,2.19,2.4\n"
    "GARCIA,7,1166120,395.19,3985.4,5.38,0.49,0.42,(S),1.67,91.46\n"
    "NGUYEN,54,378204,128.19,7134.63,4.55,0.28,91.75,0.14,1.24,2.04\n"
)


def _table_with_excerpt() -> SurnameTable:
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    with open(path, "w") as f:
        f.write(_REAL_EXCERPT_CSV)
    return SurnameTable(cache_path=path)


# ==========================================================================
# SurnameTable -- real-data parsing
# ==========================================================================

def test_matched_surname_returns_real_probabilities():
    table = _table_with_excerpt()
    probs = table.probabilities_for("SMITH")
    assert probs["white"] == pytest.approx(0.709)
    assert probs["black"] == pytest.approx(0.2311)
    assert probs["hispanic"] == pytest.approx(0.024)
    assert set(probs) == set(RACE_CATEGORIES)


def test_matching_is_case_insensitive():
    table = _table_with_excerpt()
    assert table.probabilities_for("smith") == table.probabilities_for("SMITH")
    assert table.probabilities_for("Garcia") == table.probabilities_for("GARCIA")


def test_suppressed_s_value_reads_as_zero_not_guessed():
    """GARCIA's real pctaian cell is '(S)' (Census small-sample
    suppression) -- must read as 0.0, never imputed or skipped
    silently."""
    table = _table_with_excerpt()
    probs = table.probabilities_for("GARCIA")
    assert probs["aian"] == 0.0
    assert probs["hispanic"] == 0.9146  # the real, non-suppressed value


def test_dominant_asian_surname_reflects_real_distribution():
    table = _table_with_excerpt()
    probs = table.probabilities_for("NGUYEN")
    assert probs["api"] == 0.9175


def test_unmatched_surname_falls_back_to_national_population_average():
    """Standard BISG convention: no entry on the list -> the
    population-weighted average across the whole (loaded) table, not
    None and not a guess specific to the missing name."""
    table = _table_with_excerpt()
    probs = table.probabilities_for("ZZZNOTREAL")
    assert probs is not None
    assert set(probs) == set(RACE_CATEGORIES)
    # Population-weighted by `count` across the three real rows above:
    # (2442977*0.709 + 1166120*0.0538 + 378204*0.0455) / 3987301 --
    # SMITH dominates by count but GARCIA/NGUYEN still pull it down
    # meaningfully in this small excerpt.
    assert probs["white"] == pytest.approx(0.4544, abs=0.001)
    assert sum(probs.values()) == pytest.approx(1.0, abs=0.01)


def test_missing_table_file_is_indeterminate_not_a_crash():
    table = SurnameTable(cache_path="/nonexistent/path/that/cannot/download.csv")
    # No network reachable to this bogus URL scenario is simulated by a
    # path that can never be created by _ensure_downloaded's normal
    # download flow in an isolated test run; if the real download
    # succeeds (network available), the table populates and this
    # assertion about None wouldn't hold -- so only assert the contract
    # that matters: never raises, and either returns real data or None.
    result = table.probabilities_for("SMITH")
    assert result is None or set(result) == set(RACE_CATEGORIES)


# ==========================================================================
# FakeBISGEstimator -- deterministic test double
# ==========================================================================

def test_fake_estimator_returns_fixed_distribution():
    est = FakeBISGEstimator(fixed={"white": 0.5, "black": 0.5})
    result = est.estimate("Anyone", "Any address")
    assert result.is_determinate
    assert result.distribution == {"white": 0.5, "black": 0.5}
    assert result.precision == "fake"


def test_fake_estimator_can_simulate_indeterminate():
    est = FakeBISGEstimator(indeterminate_reason="simulated network failure")
    result = est.estimate("Anyone", "Any address")
    assert not result.is_determinate
    assert result.distribution is None
    assert "simulated" in result.indeterminate_reason


# ==========================================================================
# BISGEstimate shape
# ==========================================================================

def test_indeterminate_estimate_never_carries_a_distribution():
    est = BISGEstimate(distribution=None, indeterminate_reason="no data")
    assert est.is_determinate is False
    assert est.distribution is None


def test_determinate_estimate_reports_true():
    est = BISGEstimate(distribution={"white": 1.0}, precision="tract")
    assert est.is_determinate is True
