"""
test_mortgage_cassette -- proves the first outcome_obligation-enabling
cassette: manifest/schema validity, the loan_performance@3y maturation
rule, classify_outcome's resolution vocabulary, and the decision-
process-integrity judgment surface (see cassettes/mortgage_cassette.py
module docstring for the locked domain decisions this proves).
"""

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from cassette_capabilities import (
    CAPABILITY_OUTCOME_OBLIGATION,
    require_capabilities,
)
from cassette_interface import CassetteRegistry
from cassette_schema import cassette_version_of, validate_cassette
from cassettes.mortgage_cassette import (
    INVOLUNTARY_CLOSURE_MECHANISMS,
    PROPERTY_ADDRESS_FIELD,
    RESOLUTION_INVOLUNTARY_CLOSURE,
    RESOLUTION_PAID_IN_FULL,
    RESOLUTION_TYPES,
    MortgageCassette,
)
from episode import EpisodeIntegrityError, explain_episode, judge_episode, make_episode
from outcome_v1 import MaturationRule


# ---------------------------------------------------------------------------
# Manifest / schema validity
# ---------------------------------------------------------------------------

def test_manifest_is_outcome_obligation_only():
    """Kernel-only plus outcome_obligation -- no call-center surface
    at all, matching this domain's honest capability set."""
    assert MortgageCassette.CAPABILITIES == (CAPABILITY_OUTCOME_OBLIGATION,)


def test_config_identity():
    config = MortgageCassette().get_config()
    assert config.name == "mortgage-v1"
    assert config.domain == "mortgage"
    assert cassette_version_of(MortgageCassette()) == "mortgage:mortgage-v1:1.0.0"


def test_validates_cleanly_against_full_schema():
    params = validate_cassette(MortgageCassette())
    assert params.capabilities == (CAPABILITY_OUTCOME_OBLIGATION,)
    # Exactly kernel + outcome_obligation's own parameter -- nothing
    # owned by a capability this cassette doesn't enable (the
    # anti-placeholder rule; a stray owned-but-unenabled param would
    # have already failed validate_cassette above).
    assert params.names() == ["governance_trigger", "outcome_horizon_days"]


def test_governance_trigger_declared_as_kernel_int():
    params = validate_cassette(MortgageCassette())
    assert params.int_value("governance_trigger") == 1


def test_outcome_horizon_declared_as_1095_days():
    params = validate_cassette(MortgageCassette())
    assert params.int_value("outcome_horizon_days") == 1095


def test_registers_in_the_cassette_registry():
    registry = CassetteRegistry()
    registry.register(MortgageCassette())
    assert registry.get("mortgage").get_config().name == "mortgage-v1"


def test_a_pipeline_requiring_outcome_obligation_accepts_it():
    """The capability this cassette exists to prove out: an engine
    that requires outcome_obligation does not refuse mortgage the way
    it refuses IVR (see test_cassette_capabilities for that negative)."""
    require_capabilities(MortgageCassette(), (CAPABILITY_OUTCOME_OBLIGATION,),
                         consumer="outcome_tracker")


# ---------------------------------------------------------------------------
# Maturation rule -- the 3-year (1095-day) horizon
# ---------------------------------------------------------------------------

def test_maturation_rule_kind_and_horizon():
    rule = MortgageCassette().get_maturation_rule()
    assert rule.kind == "loan_performance"
    assert rule.horizon_seconds == 1095 * 86400.0


def test_maturation_rule_declaration_round_trips_as_3y():
    """1095 days is exactly 3*365 -- the declaration should emit the
    largest exact unit, proving the horizon was chosen as a real
    3-year figure and not an off-by-a-few-days approximation."""
    rule = MortgageCassette().get_maturation_rule()
    assert rule.declaration() == "loan_performance@3y"
    assert MaturationRule.parse(rule.declaration()) == rule


def test_maturation_rule_agrees_with_the_governance_parameter():
    """The hashed declaration (get_maturation_rule) and the governance
    snapshot (outcome_horizon_days) must state the same horizon --
    this is exactly what validate() self-checks at load time."""
    cassette = MortgageCassette()
    params = validate_cassette(cassette)
    declared_days = params.int_value("outcome_horizon_days")
    rule = cassette.get_maturation_rule()
    assert rule.horizon_seconds == declared_days * 86400.0


def test_validate_self_check_passes():
    assert MortgageCassette().validate() is True


def test_validate_self_check_catches_a_diverged_declaration():
    """Prove the self-check actually catches drift, not just asserts
    the happy path -- a subclass that lies about outcome_horizon_days
    relative to the rule it emits must fail validate()."""

    class DriftedMortgageCassette(MortgageCassette):
        def get_governance_parameters(self):
            params = copy.deepcopy(self._GOVERNANCE_PARAMETERS)
            params["outcome_horizon_days"]["value"] = 730  # lies: rule still says 1095
            return params

    assert DriftedMortgageCassette().validate() is False


# ---------------------------------------------------------------------------
# classify_outcome -- the resolution vocabulary
# ---------------------------------------------------------------------------

def test_paid_in_full_is_favorable():
    assert MortgageCassette().classify_outcome(
        {"resolution_type": RESOLUTION_PAID_IN_FULL}) is True


def test_involuntary_closure_is_unfavorable():
    assert MortgageCassette().classify_outcome(
        {"resolution_type": RESOLUTION_INVOLUNTARY_CLOSURE}) is False


@pytest.mark.parametrize("mechanism", INVOLUNTARY_CLOSURE_MECHANISMS)
def test_involuntary_closure_mechanism_is_an_optional_subdetail_not_required(mechanism):
    """Foreclosure, short sale, and deed-in-lieu are all ONE bucket --
    classify_outcome reads only resolution_type; the mechanism sub-
    detail (when a customer turns it on) does not change the verdict."""
    evidence = {"resolution_type": RESOLUTION_INVOLUNTARY_CLOSURE,
               "detail": {"mechanism": mechanism}}
    assert MortgageCassette().classify_outcome(evidence) is False


def test_unrecognized_resolution_type_is_genuinely_ambiguous():
    assert MortgageCassette().classify_outcome(
        {"resolution_type": "something_novel"}) is None


def test_missing_resolution_type_is_genuinely_ambiguous():
    assert MortgageCassette().classify_outcome({}) is None


def test_resolution_types_are_exactly_the_two_locked_paths():
    assert set(RESOLUTION_TYPES) == {RESOLUTION_PAID_IN_FULL,
                                     RESOLUTION_INVOLUNTARY_CLOSURE}


# ---------------------------------------------------------------------------
# Property address field -- the resolved input_fields schema question
# ---------------------------------------------------------------------------

def test_property_address_field_name_is_locked():
    assert PROPERTY_ADDRESS_FIELD == "loan_property_address"


# ---------------------------------------------------------------------------
# judge() / explain() -- decision-process integrity
# ---------------------------------------------------------------------------

def test_matched_episode_scores_excellent():
    cassette = MortgageCassette()
    episode = make_episode(
        "M-1", "mortgage",
        requested={"outcome": "approved", "amount": 300000.0},
        actual={"outcome": "approved", "amount": 300000.0},
        attributes={PROPERTY_ADDRESS_FIELD: "123 Main St, Baltimore, MD 21201"},
    )
    result = judge_episode(cassette, episode)
    assert result.score == 1.0
    assert result.tier == "excellent"


def test_single_mismatch_with_substantive_reason_scores_good():
    cassette = MortgageCassette()
    episode = make_episode(
        "M-2", "mortgage",
        requested={"amount": 300000.0},
        actual={"amount": 250000.0},
        outcome_reasons=["approved amount reduced to 250000 based on updated appraisal value"],
    )
    result = judge_episode(cassette, episode)
    assert result.tier == "good"
    assert 0.60 < result.score < 0.85


def test_full_denial_with_thin_reason_scores_worse_than_substantive_reason():
    cassette = MortgageCassette()
    thin = make_episode(
        "M-3a", "mortgage",
        requested={"outcome": "approved", "amount": 300000.0},
        actual={"outcome": "denied", "amount": 0.0},
        outcome_reasons=["denied"],
    )
    substantive = make_episode(
        "M-3b", "mortgage",
        requested={"outcome": "approved", "amount": 300000.0},
        actual={"outcome": "denied", "amount": 0.0},
        outcome_reasons=["credit score 574 is below the 620 minimum required for this loan amount"],
    )
    thin_result = judge_episode(cassette, thin)
    substantive_result = judge_episode(cassette, substantive)
    assert thin_result.score < substantive_result.score
    assert thin_result.tier in ("poor", "failed")


def test_kernel_refuses_a_mismatch_with_no_reason_before_judge_ever_runs():
    """The kernel's own invariant (episode.validate_episode), not this
    cassette, guarantees a reason exists on any mismatch. Proves the
    cassette's judge() never has to defend against this case itself."""
    cassette = MortgageCassette()
    episode = make_episode(
        "M-4", "mortgage",
        requested={"outcome": "approved"},
        actual={"outcome": "denied"},
        # no outcome_reasons
    )
    with pytest.raises(EpisodeIntegrityError):
        judge_episode(cassette, episode)


def test_explain_reports_reason_substance_factor_on_a_thin_denial():
    cassette = MortgageCassette()
    episode = make_episode(
        "M-5", "mortgage",
        requested={"outcome": "approved"},
        actual={"outcome": "denied"},
        outcome_reasons=["no"],
    )
    factors = explain_episode(cassette, episode)
    substance_factors = [f for f in factors if f.get("factor") == "reason_substance"]
    assert len(substance_factors) == 1
    assert substance_factors[0]["value"] == 1  # one thin reason
    assert substance_factors[0]["contribution"] < 0


def test_explain_includes_kernel_prepended_mismatch_findings():
    """episode.explain_episode prepends outcome_mismatch findings ahead
    of the cassette's own factors -- proves this cassette doesn't need
    to (and doesn't) duplicate that raw listing itself."""
    cassette = MortgageCassette()
    episode = make_episode(
        "M-6", "mortgage",
        requested={"amount": 300000.0},
        actual={"amount": 250000.0},
        outcome_reasons=["reduced based on updated appraisal value on file"],
    )
    factors = explain_episode(cassette, episode)
    kernel_findings = [f for f in factors if f.get("factor") == "outcome_mismatch"]
    assert len(kernel_findings) == 1
    assert kernel_findings[0]["field"] == "amount"


# ---------------------------------------------------------------------------
# judge()/explain() reason-gap fix (2026-08-01) -- two ways an episode
# could previously score excellent with nothing actually checked.
# ---------------------------------------------------------------------------

def test_no_requested_fields_cannot_score_excellent():
    """An episode recording no requested fields has nothing for
    outcome_mismatches to compare `actual` against -- that used to
    silently take the SAME "no mismatch" path as a genuinely verified
    clean match and score 1.0. Now it's a distinct, penalized,
    unverifiable state that cannot reach the top tier."""
    cassette = MortgageCassette()
    episode = make_episode(
        "M-7", "mortgage",
        requested={},
        actual={"outcome": "approved", "amount": 300000.0},
    )
    result = judge_episode(cassette, episode)
    assert result.score == pytest.approx(0.75)
    assert result.tier == "good"
    factors = explain_episode(cassette, episode)
    unverifiable = [f for f in factors
                    if f.get("factor") == "requested_vs_actual_unverifiable"]
    assert len(unverifiable) == 1
    assert unverifiable[0]["contribution"] == pytest.approx(-0.25)


def test_genuinely_clean_match_is_distinct_from_unverifiable():
    """The unverifiable penalty must not leak onto a real verified
    match -- requested recorded AND matching actual still scores 1.0
    and gets the ordinary requested_vs_actual factor, not the
    unverifiable one."""
    cassette = MortgageCassette()
    episode = make_episode(
        "M-8", "mortgage",
        requested={"outcome": "approved", "amount": 300000.0},
        actual={"outcome": "approved", "amount": 300000.0},
    )
    result = judge_episode(cassette, episode)
    assert result.score == 1.0
    assert result.tier == "excellent"
    factors = explain_episode(cassette, episode)
    assert [f["factor"] for f in factors] == ["requested_vs_actual"]


def test_reason_substance_is_examined_even_with_no_mismatch():
    """A thin/placeholder reason attached to a decision that never
    triggers a mismatch (e.g. the recorded outcome matched what was
    requested) used to never be examined at all, since the substance
    check only ran inside the mismatch branch. Now it's checked
    regardless of mismatch status."""
    cassette = MortgageCassette()
    episode = make_episode(
        "M-9", "mortgage",
        requested={"outcome": "denied"},
        actual={"outcome": "denied"},
        outcome_reasons=["no"],
    )
    result = judge_episode(cassette, episode)
    assert result.score < 1.0
    factors = explain_episode(cassette, episode)
    substance = [f for f in factors if f.get("factor") == "reason_substance"]
    assert len(substance) == 1
    assert substance[0]["contribution"] < 0


def test_reason_substance_examined_with_no_mismatch_stays_clean_if_substantive():
    """The reverse of the above: a genuinely substantive reason
    attached with no mismatch is examined and passes -- proves the
    fix checks substance, it doesn't just penalize presence."""
    cassette = MortgageCassette()
    episode = make_episode(
        "M-10", "mortgage",
        requested={"outcome": "denied"},
        actual={"outcome": "denied"},
        outcome_reasons=["denial confirmed and documented per underwriting policy 4.2"],
    )
    result = judge_episode(cassette, episode)
    assert result.score == 1.0
    assert result.tier == "excellent"
    factors = explain_episode(cassette, episode)
    substance = [f for f in factors if f.get("factor") == "reason_substance"]
    assert len(substance) == 1
    assert substance[0]["contribution"] == 0.0
