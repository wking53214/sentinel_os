"""OutcomeV1: the Provenance Rule made testable.

Every test here is one of two claims: an unknown must say WHY it is
unknown and WHAT WOULD CLOSE IT, and a known must say how it came to be
known. There is no third kind of assertion in this file.
"""

import pytest

from event_v1 import PROVENANCE_ESTIMATED, PROVENANCE_VERIFIED
from outcome_v1 import (ABANDONED_REASONS, OPEN_REASONS, OUTCOME_ABANDONED,
                        OUTCOME_OPEN, OUTCOME_RESOLVED, MaturationRule,
                        OutcomeIntegrityError, OutcomeObligation, abandon,
                        derive_open_obligations, horizon_honored, is_overdue,
                        open_obligation, resolve, stay_open,
                        REASON_DATA_SOURCE_UNREACHABLE,
                        REASON_DECISION_SUPERSEDED, REASON_GENUINELY_AMBIGUOUS,
                        REASON_INSUFFICIENT_COHORT, REASON_NOT_YET_DUE,
                        to_cohort_decision)

NOW = 1_700_000_000.0
DAY = 86400.0
RULE = MaturationRule.parse("loan_performance@24mo")


def _open():
    return open_obligation("o1", "decisionhash", "lending", RULE, opened_at=NOW,
                           subject_id="subject-1")


# ---------------------------------------------------------------------------
# Maturation declarations: the string that hashes into the decision row.
# ---------------------------------------------------------------------------

def test_declaration_round_trips_in_canonical_form():
    for text in ("loan_performance@24mo", "claim_cost@5y", "repair@10d",
                 "settlement@12h"):
        assert MaturationRule.parse(text).declaration() == text


def test_equivalent_durations_normalize_to_the_largest_exact_unit():
    """A consequence of the fixed-length approximation, asserted rather
    than hidden: 30d and 1mo are the same horizon, and both come back as
    the larger unit. Anyone reading a declaration is reading seconds."""
    assert MaturationRule.parse("repair@30d").declaration() == "repair@1mo"
    assert (MaturationRule.parse("repair@30d").horizon_seconds
            == MaturationRule.parse("repair@1mo").horizon_seconds)


def test_months_and_years_are_fixed_length_and_documented_as_such():
    assert MaturationRule.parse("x@24mo").horizon_seconds == 720 * DAY
    assert MaturationRule.parse("x@1y").horizon_seconds == 365 * DAY


def test_unparseable_declaration_is_refused_not_defaulted():
    for bad in ("loan_performance", "@24mo", "loan@24weeks", "loan@mo", ""):
        with pytest.raises(ValueError):
            MaturationRule.parse(bad)


def test_a_zero_horizon_is_refused():
    with pytest.raises(ValueError) as exc:
        MaturationRule.parse("loan@0d")
    assert "already closed" in str(exc.value)


def test_horizon_is_computed_from_the_rule_never_passed_in():
    o = _open()
    assert o.expected_by == o.opened_at + RULE.horizon_seconds
    assert o.obligation_kind == "loan_performance"


# ---------------------------------------------------------------------------
# The unknown must be typed. No flat indeterminate flag.
# ---------------------------------------------------------------------------

def test_a_new_obligation_opens_on_not_yet_due():
    assert _open().state == OUTCOME_OPEN
    assert _open().reason_code == REASON_NOT_YET_DUE


def test_open_with_an_untyped_reason_is_refused():
    with pytest.raises(OutcomeIntegrityError) as exc:
        stay_open(_open(), "we're not sure yet")
    assert any("flat indeterminate flag" in v for v in exc.value.violations)


def test_open_with_no_reason_at_all_is_refused():
    with pytest.raises(OutcomeIntegrityError):
        stay_open(_open(), "")


@pytest.mark.parametrize("reason", OPEN_REASONS)
def test_every_open_reason_in_the_vocabulary_validates(reason):
    assert stay_open(_open(), reason).reason_code == reason


def test_the_open_vocabulary_distinguishes_waiting_from_stuck():
    """An auditor is entitled to know which one they are looking at."""
    assert REASON_NOT_YET_DUE in OPEN_REASONS
    assert REASON_DATA_SOURCE_UNREACHABLE in OPEN_REASONS
    assert REASON_INSUFFICIENT_COHORT in OPEN_REASONS
    assert REASON_GENUINELY_AMBIGUOUS in OPEN_REASONS


def test_an_open_obligation_cannot_already_be_favorable():
    bad = OutcomeObligation(
        obligation_id="o", decision_hash="h", domain="lending",
        obligation_kind="loan_performance", opened_at=NOW,
        expected_by=NOW + DAY, state=OUTCOME_OPEN,
        reason_code=REASON_NOT_YET_DUE, favorable=True)
    with pytest.raises(OutcomeIntegrityError) as exc:
        from outcome_v1 import validate_obligation
        validate_obligation(bad)
    assert any("without a resolution behind it" in v for v in exc.value.violations)


def test_a_horizon_that_matures_before_it_opens_is_refused():
    from outcome_v1 import validate_obligation
    with pytest.raises(OutcomeIntegrityError):
        validate_obligation(OutcomeObligation(
            obligation_id="o", decision_hash="h", domain="d",
            obligation_kind="k", opened_at=NOW, expected_by=NOW - 1,
            state=OUTCOME_OPEN, reason_code=REASON_NOT_YET_DUE))


# ---------------------------------------------------------------------------
# The known must say how it came to be known.
# ---------------------------------------------------------------------------

def test_resolution_must_carry_a_provenance_stamp():
    with pytest.raises(OutcomeIntegrityError) as exc:
        resolve(_open(), resolved_at=NOW + DAY, resolved_value={"s": "paid"},
                provenance="probably", favorable=True)
    assert any("not interchangeable" in v for v in exc.value.violations)


def test_an_estimated_resolution_must_name_its_method():
    with pytest.raises(OutcomeIntegrityError) as exc:
        resolve(_open(), resolved_at=NOW + DAY, resolved_value={"s": "paid"},
                provenance=PROVENANCE_ESTIMATED, favorable=True)
    assert any("how it was estimated" in v for v in exc.value.violations)


def test_an_estimated_resolution_with_a_method_validates():
    r = resolve(_open(), resolved_at=NOW + DAY, resolved_value={"s": "paid"},
                provenance=PROVENANCE_ESTIMATED, favorable=True,
                method="servicer_file_join_on_account_id")
    assert r.state == OUTCOME_RESOLVED
    assert r.resolution_method == "servicer_file_join_on_account_id"


def test_resolving_with_nothing_recorded_is_refused():
    with pytest.raises(OutcomeIntegrityError) as exc:
        resolve(_open(), resolved_at=NOW + DAY, resolved_value={},
                provenance=PROVENANCE_VERIFIED, favorable=True)
    assert any("nothing established" in v for v in exc.value.violations)


def test_a_resolved_obligation_carries_no_open_reason():
    r = resolve(_open(), resolved_at=NOW + DAY, resolved_value={"s": "paid"},
                provenance=PROVENANCE_VERIFIED, favorable=True)
    assert r.reason_code is None


# ---------------------------------------------------------------------------
# Abandonment is recorded, not deleted.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reason", ABANDONED_REASONS)
def test_every_abandon_reason_validates(reason):
    a = abandon(_open(), reason, at=NOW + DAY)
    assert a.state == OUTCOME_ABANDONED and a.reason_code == reason


def test_abandoning_with_an_open_reason_is_refused():
    """The two vocabularies are not interchangeable either."""
    with pytest.raises(OutcomeIntegrityError):
        abandon(_open(), REASON_NOT_YET_DUE, at=NOW + DAY)


def test_abandoned_has_no_favorability():
    assert abandon(_open(), REASON_DECISION_SUPERSEDED, at=NOW).favorable is None


# ---------------------------------------------------------------------------
# Overdue is arithmetic, never a stored flag.
# ---------------------------------------------------------------------------

def test_overdue_is_computed_from_the_two_timestamps():
    o = _open()
    assert is_overdue(o, NOW + DAY) is False
    assert is_overdue(o, o.expected_by + 1) is True


def test_a_closed_obligation_is_never_overdue():
    r = resolve(_open(), resolved_at=NOW + DAY, resolved_value={"s": "paid"},
                provenance=PROVENANCE_VERIFIED, favorable=True)
    assert is_overdue(r, r.expected_by + 10 * DAY) is False


def test_there_is_no_overdue_field_to_set():
    assert not hasattr(_open(), "overdue")


def test_horizon_honored_counts_states_reasons_and_lateness():
    o = _open()
    stuck = stay_open(o, REASON_DATA_SOURCE_UNREACHABLE)
    done = resolve(o, resolved_at=NOW + DAY, resolved_value={"s": "paid"},
                   provenance=PROVENANCE_VERIFIED, favorable=True)
    counts = horizon_honored([o, stuck, done], o.expected_by + 1)
    assert counts[OUTCOME_OPEN] == 2
    assert counts[OUTCOME_RESOLVED] == 1
    assert counts["overdue"] == 2
    assert counts[f"open:{REASON_DATA_SOURCE_UNREACHABLE}"] == 1
    assert counts[f"open:{REASON_NOT_YET_DUE}"] == 1


# ---------------------------------------------------------------------------
# Independent derivation: the twin computes what is owed, unaided.
# ---------------------------------------------------------------------------

def test_derivation_reads_the_declaration_off_the_decision_feed():
    rows = [{"current_hash": "h1", "timestamp": NOW, "domain": "lending",
             "outcome_obligation": "loan_performance@24mo"}]
    derived = derive_open_obligations(rows)
    assert len(derived) == 1
    assert derived[0].decision_hash == "h1"
    assert derived[0].obligation_kind == "loan_performance"
    assert derived[0].expected_by == NOW + 720 * DAY


def test_a_decision_declaring_nothing_owes_nothing():
    """IVR settles at hangup. Inventing an obligation for it would be
    fabricating a debt."""
    rows = [{"current_hash": "h1", "timestamp": NOW, "outcome_obligation": None},
            {"current_hash": "h2", "timestamp": NOW}]
    assert derive_open_obligations(rows) == []


def test_an_unreadable_declaration_is_raised_not_skipped():
    with pytest.raises(ValueError):
        derive_open_obligations([{"current_hash": "h", "timestamp": NOW,
                                  "outcome_obligation": "loan@forever"}])


def test_derivation_is_deterministic_for_the_same_feed():
    rows = [{"current_hash": "h1", "timestamp": NOW, "domain": "lending",
             "outcome_obligation": "loan_performance@24mo"}]
    assert ([o.obligation_id for o in derive_open_obligations(rows)]
            == [o.obligation_id for o in derive_open_obligations(rows)])


# ---------------------------------------------------------------------------
# The cohort return path: what C2 dimension 4 has been waiting for.
# ---------------------------------------------------------------------------

def test_a_resolved_outcome_becomes_a_cohort_decision():
    r = resolve(_open(), resolved_at=NOW + DAY, resolved_value={"s": "charged_off"},
                provenance=PROVENANCE_VERIFIED, favorable=False)
    cohort = to_cohort_decision(r, {"black": 0.7, "white": 0.3})
    assert cohort.subject_id == "subject-1"
    assert cohort.favorable_outcome is False
    assert cohort.group_distribution == {"black": 0.7, "white": 0.3}


def test_an_open_obligation_is_never_quietly_a_false():
    with pytest.raises(OutcomeIntegrityError) as exc:
        to_cohort_decision(_open(), {"black": 1.0})
    assert any("unmeasured" in v for v in exc.value.violations)


def test_a_genuinely_ambiguous_outcome_is_not_coerced_to_a_bool():
    """Forcing it either way fabricates the input to a fairness statistic."""
    r = resolve(_open(), resolved_at=NOW + DAY, resolved_value={"s": "unclear"},
                provenance=PROVENANCE_VERIFIED, favorable=None)
    with pytest.raises(OutcomeIntegrityError) as exc:
        to_cohort_decision(r, {"black": 1.0})
    assert any("fabricates" in v for v in exc.value.violations)


def test_an_ambiguous_outcome_can_instead_stay_open_on_its_own_reason():
    o = stay_open(_open(), REASON_GENUINELY_AMBIGUOUS)
    assert o.state == OUTCOME_OPEN
    assert o.reason_code == REASON_GENUINELY_AMBIGUOUS


def test_the_state_vocabulary_is_not_the_twins_verdict_vocabulary():
    """twin_detector's PENDING means 'inside the transport SLA' and its
    EXTRA means 'wiped from the primary'. Outcome lag is neither."""
    from outcome_v1 import OUTCOME_STATES
    assert set(OUTCOME_STATES).isdisjoint(
        {"MATCH", "DIVERGE", "MISSING", "PENDING", "EXTRA"})
