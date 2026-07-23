"""
test_c2_input_side_checkers -- proof suite for the C2 input-side
checkers built this session (input-authorization tier screen,
narrative-legitimacy screen) and the C2 AND-rollup that combines
findings across dimensions.

Pure logic, no ledger: DecisionMaterial is a plain dataclass and every
function under test here is deterministic and side-effect-free, so
this suite needs no Postgres and no lens registry -- unlike
test_regulatory_cassettes.py, which proves the framework end to end
against the real ledger. Run: pytest Tests/test_c2_input_side_checkers.py -q

Sections:
1. Input-authorization tier ladder (T0-T6), confidence scale, floor
2. Prohibited-inputs override and jurisdiction-conflict resolution
3. Narrative-legitimacy screen, Phase A (missing narrative) and
   Phase B (flagged language + deviation + unexplained reason)
4. C2 rollup: PASS / FLAG / INDETERMINATE, INDETERMINATE precedence
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from regulatory_cassette_interface import ACTION_FLAG, DecisionMaterial
from regulatory_checks import (
    C2_DIMENSIONS,
    C2_FLAG,
    C2_INDETERMINATE,
    C2_PASS,
    CONFIDENCE_ATTESTED_ACCOUNTABLE_EVIDENCED,
    CONFIDENCE_ATTESTED_ACCOUNTABLE_UNSUPPORTED,
    CONFIDENCE_ATTESTED_UNSUPPORTED,
    CONFIDENCE_UNDECLARED,
    CONFIDENCE_VERIFIED,
    DIMENSION_INPUT_AUTHORIZATION_TIER,
    DIMENSION_KNOWN_BAD_VARIABLE_NAMES,
    DIMENSION_NARRATIVE_LEGITIMACY,
    DIMENSION_STATISTICAL_OUTCOME_EQUITY,
    RegulationCheckProfile,
    T0_PROHIBITED,
    T1_FILED,
    T2_PERMITTED,
    T3_INTERNAL,
    T4_PENDING,
    T5_UNDECLARED,
    T6_OPAQUE,
    TierDeclaration,
    assess_input_authorization,
    check_input_authorization_tier,
    check_narrative_legitimacy,
    check_proxy_variables,
    resolve_tier_conflict,
    rollup_c2_bias_identification,
)


def _material(subject_id="D-1", input_fields=None, mismatched_fields=(),
              reasons=(), domain="lending"):
    return DecisionMaterial(
        subject_id=subject_id, domain=domain, reasons=tuple(reasons),
        input_fields=dict(input_fields or {}),
        mismatched_fields=tuple(mismatched_fields), outcome={}, source="ledger",
    )


# ==========================================================================
# 1. Input-authorization tier ladder
# ==========================================================================

_INSURANCE_PROFILE = RegulationCheckProfile(
    regulation="NAIC illustrative filed-rating-variable regime",
    authorized_inputs={
        r"credit_based_score": TierDeclaration(tier=T1_FILED,
                                               authorized_by="actuarial-lead",
                                               approval_date="2025-03-01",
                                               justification="filing #IL-2025-114"),
        r"prior_claims_count": TierDeclaration(tier=T2_PERMITTED),
        r"agent_override_flag": TierDeclaration(tier=T3_INTERNAL,
                                                authorized_by="uw-director"),
        r"pending_telematics_score": TierDeclaration(tier=T4_PENDING),
        r"third_party_risk_index": TierDeclaration(tier=T6_OPAQUE),
    },
    tier_floor=T2_PERMITTED,
)


def test_undeclared_variable_flags_t5():
    material = _material(input_fields={"mystery_field": 1})
    findings = check_input_authorization_tier(material, _INSURANCE_PROFILE)
    assert len(findings) == 1
    assert findings[0].classification == "undeclared_input"
    assert findings[0].evidence["tier"] == T5_UNDECLARED
    assert findings[0].evidence["confidence"] == CONFIDENCE_UNDECLARED
    assert findings[0].score == 1.0


def test_filed_tier_at_evidenced_confidence_passes():
    material = _material(input_fields={"credit_based_score": 700})
    findings = check_input_authorization_tier(material, _INSURANCE_PROFILE)
    assert findings == []
    assessment = assess_input_authorization("credit_based_score", _INSURANCE_PROFILE)
    assert assessment["flagged"] is False
    assert assessment["tier"] == T1_FILED
    assert assessment["confidence"] == CONFIDENCE_ATTESTED_ACCOUNTABLE_EVIDENCED


def test_bare_declaration_still_passes_floor_but_carries_low_confidence():
    """T2 with no owner/evidence at all -- meets the default floor
    (tier_acceptable) but assess_* shows the bare confidence rather
    than silently reading the same as a verified claim."""
    material = _material(input_fields={"prior_claims_count": 2})
    assert check_input_authorization_tier(material, _INSURANCE_PROFILE) == []
    assessment = assess_input_authorization("prior_claims_count", _INSURANCE_PROFILE)
    assert assessment["flagged"] is False
    assert assessment["confidence"] == CONFIDENCE_ATTESTED_UNSUPPORTED


def test_named_owner_without_evidence_is_accountable_unsupported():
    assessment = assess_input_authorization("agent_override_flag", _INSURANCE_PROFILE)
    assert assessment["confidence"] == CONFIDENCE_ATTESTED_ACCOUNTABLE_UNSUPPORTED
    # T3 is below the T2 floor (worse rank) -- flags on tier alone,
    # independent of confidence.
    assert assessment["flagged"] is True
    assert assessment["classification"] == "below_tier_floor"


def test_pending_tier_below_floor_flags():
    material = _material(input_fields={"pending_telematics_score": 0.4})
    findings = check_input_authorization_tier(material, _INSURANCE_PROFILE)
    assert len(findings) == 1
    assert findings[0].classification == "below_tier_floor"
    assert findings[0].evidence["tier"] == T4_PENDING


def test_opaque_vendor_tier_always_flags_even_though_never_below_floor():
    """T6 is categorical: it must never silently pass regardless of
    tier_floor, because there is no way to rank an undisclosed list
    against a floor at all."""
    material = _material(input_fields={"third_party_risk_index": 0.9})
    findings = check_input_authorization_tier(material, _INSURANCE_PROFILE)
    assert len(findings) == 1
    assert findings[0].classification == "opaque_input"
    assert findings[0].evidence["tier"] == T6_OPAQUE


def test_verified_claim_reports_verified_confidence():
    profile = RegulationCheckProfile(
        regulation="illustrative",
        authorized_inputs={
            r"filed_rate_factor": TierDeclaration(
                tier=T1_FILED, authorized_by="actuarial-lead",
                approval_date="2025-01-01", justification="filing #X",
                verified=True,
            ),
        },
    )
    assessment = assess_input_authorization("filed_rate_factor", profile)
    assert assessment["confidence"] == CONFIDENCE_VERIFIED
    assert assessment["flagged"] is False


def test_empty_profile_reports_everything_undeclared_not_a_crash():
    """An industry with no filed-variable list and no blacklist at
    all is a valid, honest configuration -- every variable reports
    T5_UNDECLARED rather than the checker silently passing or
    erroring on missing configuration."""
    empty = RegulationCheckProfile(regulation="no regime declared")
    material = _material(input_fields={"anything": 1, "something_else": 2})
    findings = check_input_authorization_tier(material, empty)
    assert len(findings) == 2
    assert all(f.classification == "undeclared_input" for f in findings)


# ==========================================================================
# 2. Prohibited-inputs override and jurisdiction-conflict resolution
# ==========================================================================

def test_prohibited_input_wins_over_declared_better_tier():
    profile = RegulationCheckProfile(
        regulation="illustrative",
        authorized_inputs={
            r"applicant_race": TierDeclaration(tier=T1_FILED,
                                               authorized_by="someone",
                                               verified=True),
        },
        prohibited_inputs=(r"race",),
    )
    material = _material(input_fields={"applicant_race": "x"})
    findings = check_input_authorization_tier(material, profile)
    assert len(findings) == 1
    assert findings[0].classification == "prohibited_input"
    assert findings[0].evidence["tier"] == T0_PROHIBITED
    # The T0 override carries no confidence grade -- it isn't a claim
    # being graded, it's a categorical bar.
    assert findings[0].evidence["confidence"] is None


def test_resolve_tier_conflict_stricter_wins():
    assert resolve_tier_conflict(T1_FILED, T4_PENDING) == T4_PENDING
    assert resolve_tier_conflict(T4_PENDING, T1_FILED) == T4_PENDING
    assert resolve_tier_conflict(T2_PERMITTED, T2_PERMITTED) == T2_PERMITTED


def test_resolve_tier_conflict_categorical_beats_ranked():
    assert resolve_tier_conflict(T1_FILED, T0_PROHIBITED) == T0_PROHIBITED
    assert resolve_tier_conflict(T6_OPAQUE, T3_INTERNAL) == T6_OPAQUE


def test_resolve_tier_conflict_prohibited_beats_opaque():
    assert resolve_tier_conflict(T0_PROHIBITED, T6_OPAQUE) == T0_PROHIBITED
    assert resolve_tier_conflict(T6_OPAQUE, T0_PROHIBITED) == T0_PROHIBITED


# ==========================================================================
# 3. Narrative-legitimacy screen
# ==========================================================================

_NARRATIVE_PROFILE = RegulationCheckProfile(
    regulation="illustrative narrative-expecting regulation",
    narrative_field="reviewer_notes",
    narrative_flag_phrases=("single mom", "empathize", "she's"),
)

_NO_NARRATIVE_PROFILE = RegulationCheckProfile(regulation="no narrative expected")


def test_no_narrative_expectation_is_zero_findings_not_a_gap():
    material = _material(input_fields={"amount": 100}, mismatched_fields=("approved",))
    assert check_narrative_legitimacy(material, _NO_NARRATIVE_PROFILE) == []


def test_declared_narrative_field_missing_from_decision_flags_not_screened():
    material = _material(input_fields={"amount": 100})  # no reviewer_notes at all
    findings = check_narrative_legitimacy(material, _NARRATIVE_PROFILE)
    assert len(findings) == 1
    assert findings[0].classification == "not_screened"
    assert findings[0].evidence["narrative_field"] == "reviewer_notes"


def test_declared_narrative_field_blank_also_flags_not_screened():
    material = _material(input_fields={"reviewer_notes": "   "})
    findings = check_narrative_legitimacy(material, _NARRATIVE_PROFILE)
    assert [f.classification for f in findings] == ["not_screened"]


def test_flagged_language_with_no_deviation_is_clean():
    """Flagged phrases present, but requested == actual (no
    mismatch): nothing to launder a reason for."""
    material = _material(
        input_fields={"reviewer_notes": "Approved; she's a long-time customer, "
                                        "I can empathize with the request."},
        mismatched_fields=(),
    )
    assert check_narrative_legitimacy(material, _NARRATIVE_PROFILE) == []


def test_deviation_plus_flagged_language_plus_unexplained_reason_flags():
    """Wm's own scenario: a reviewer approves a refund because 'she's
    a single mom, I can empathize' against a stated no-refunds policy
    -- the outcome deviated from what was requested, the narrative
    carries flagged language, and the stated reason never mentions it."""
    material = _material(
        input_fields={"reviewer_notes": "She's a single mom, I can empathize."},
        mismatched_fields=("refund_issued",),
        reasons=("Approved per manager discretion.",),
    )
    findings = check_narrative_legitimacy(material, _NARRATIVE_PROFILE)
    assert len(findings) == 1
    assert findings[0].classification == "possible_laundered_reason"
    assert findings[0].action == ACTION_FLAG
    assert set(findings[0].evidence["flagged_phrases"]) == {"empathize", "single mom",
                                                              "she's"}


def test_deviation_plus_flagged_language_but_reason_discloses_it_is_clean():
    """Same deviation and language, but the stated reason itself
    names the flagged content -- transparent, not laundered."""
    material = _material(
        input_fields={"reviewer_notes": "She's a single mom, I can empathize."},
        mismatched_fields=("refund_issued",),
        reasons=("Approved as a goodwill exception; noted she's a single mom "
                 "per manager sign-off.",),
    )
    assert check_narrative_legitimacy(material, _NARRATIVE_PROFILE) == []


# ==========================================================================
# 4. C2 rollup
# ==========================================================================

def test_rollup_all_clean_is_pass():
    rollup = rollup_c2_bias_identification({
        DIMENSION_KNOWN_BAD_VARIABLE_NAMES: [],
        DIMENSION_INPUT_AUTHORIZATION_TIER: [],
        DIMENSION_NARRATIVE_LEGITIMACY: [],
    })
    assert rollup.status == C2_PASS
    assert rollup.not_evaluated_dimensions == ()
    assert rollup.flagged_dimensions == ()


def test_rollup_any_finding_is_flag():
    material = _material(input_fields={"zip_code": "60601"})
    proxy_findings = check_proxy_variables(
        material,
        RegulationCheckProfile(regulation="x", proxy_variables={r"zip": "race proxy"}),
    )
    rollup = rollup_c2_bias_identification({
        DIMENSION_KNOWN_BAD_VARIABLE_NAMES: proxy_findings,
        DIMENSION_INPUT_AUTHORIZATION_TIER: [],
    })
    assert rollup.status == C2_FLAG
    assert rollup.flagged_dimensions == (DIMENSION_KNOWN_BAD_VARIABLE_NAMES,)
    assert len(rollup.findings) == 1


def test_rollup_dimension_4_always_present_forces_indeterminate():
    """Dimension 4 is permanently not-yet-evaluated until it is
    separately built -- full C2 status must stay INDETERMINATE
    regardless of how clean 1-3 come back."""
    rollup = rollup_c2_bias_identification({
        DIMENSION_KNOWN_BAD_VARIABLE_NAMES: [],
        DIMENSION_INPUT_AUTHORIZATION_TIER: [],
        DIMENSION_NARRATIVE_LEGITIMACY: [],
        DIMENSION_STATISTICAL_OUTCOME_EQUITY: None,
    })
    assert rollup.status == C2_INDETERMINATE
    assert rollup.not_evaluated_dimensions == (DIMENSION_STATISTICAL_OUTCOME_EQUITY,)


def test_rollup_indeterminate_takes_precedence_over_flag():
    """Even when 1-3 turned up real findings, an unevaluated dimension
     keeps the overall status honestly INDETERMINATE -- the flagged
    findings remain visible in the rollup, they just don't get to
    claim a resolved FLAG status while dimension 4 is still blocked."""
    material = _material(input_fields={"zip_code": "60601"})
    proxy_findings = check_proxy_variables(
        material,
        RegulationCheckProfile(regulation="x", proxy_variables={r"zip": "race proxy"}),
    )
    rollup = rollup_c2_bias_identification({
        DIMENSION_KNOWN_BAD_VARIABLE_NAMES: proxy_findings,
        DIMENSION_STATISTICAL_OUTCOME_EQUITY: None,
    })
    assert rollup.status == C2_INDETERMINATE
    assert rollup.flagged_dimensions == (DIMENSION_KNOWN_BAD_VARIABLE_NAMES,)
    assert len(rollup.findings) == 1


def test_rollup_dimension_not_present_at_all_is_excluded_not_indeterminate():
    """A dimension the caller never mentions (not applicable to this
    decision) must not itself force INDETERMINATE -- only a dimension
    explicitly passed as None (applicable but pending) does."""
    rollup = rollup_c2_bias_identification({
        DIMENSION_KNOWN_BAD_VARIABLE_NAMES: [],
    })
    assert rollup.status == C2_PASS
    assert rollup.evaluated_dimensions == (DIMENSION_KNOWN_BAD_VARIABLE_NAMES,)
    assert rollup.not_evaluated_dimensions == ()


def test_c2_dimensions_vocabulary_has_all_four():
    assert set(C2_DIMENSIONS) == {
        DIMENSION_KNOWN_BAD_VARIABLE_NAMES,
        DIMENSION_INPUT_AUTHORIZATION_TIER,
        DIMENSION_NARRATIVE_LEGITIMACY,
        DIMENSION_STATISTICAL_OUTCOME_EQUITY,
    }
