"""Adversarial contract tests for the prospective action-authorization gate.

These tests use in-memory provider doubles. They exercise the gate contract,
not PostgreSQL, ledger hash-chain, twin, or production action-boundary behavior.
"""

import pytest

from governance.action_authorization_v1 import (
    ActionAuthorizationGate,
    AuthorizationStatus,
    AuthorizationTarget,
    DecisionState,
    PolicyAssessment,
    ReviewEvidence,
    ReviewRequirement,
    TargetType,
    VerificationResult,
)
from governance.human_selection_v1 import (
    HUMAN_SELECTION_CONCUR,
    HUMAN_SELECTION_ESCALATE,
    HUMAN_SELECTION_OVERRIDE,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
_UNSET = object()


def _target(target_type=TargetType.GOVERNANCE_DECISION, evidence_hash=HASH_A):
    return AuthorizationTarget(target_type, "ledger-record-17", evidence_hash)


class _DecisionProvider:
    def __init__(self, state=None, error=None):
        self.state = state
        self.error = error

    def load(self, target):
        if self.error:
            raise self.error
        return self.state


class _Policy:
    def __init__(self, assessment=None, error=None):
        self.assessment = assessment
        self.error = error

    def assess(self, state, action):
        if self.error:
            raise self.error
        return self.assessment


class _ReviewProvider:
    def __init__(self, evidence=None, error=None):
        self.evidence = evidence
        self.error = error

    def load(self, target):
        if self.error:
            raise self.error
        return self.evidence


class _Verifier:
    def __init__(self, result=None, error=None):
        self.result = result or VerificationResult(True, True, True, "verified")
        self.error = error
        self.calls = []

    def verify(self, evidence, target, action, assessment):
        if self.error:
            raise self.error
        self.calls.append((evidence, target, action, assessment))
        return self.result


def _gate(target=None, *, requirement=ReviewRequirement.REQUIRED,
          action_block=False, permitted=(HUMAN_SELECTION_CONCUR,), evidence=None,
          verification=None, action_block_override_policy_permitted=False,
          policy_id=None, policy_version=None, state=_UNSET, policy_error=None,
          decision_error=None, review_error=None, verifier_error=None):
    target = target or _target()
    if state is _UNSET:
        state = DecisionState(target=target, action_block=action_block)
    return ActionAuthorizationGate(
        decisions=_DecisionProvider(state, decision_error),
        policy=_Policy(PolicyAssessment(
            target, "release", requirement, permitted,
            action_block_override_policy_permitted,
            policy_id=policy_id, policy_version=policy_version),
                       policy_error),
        reviews=_ReviewProvider(evidence, review_error),
        verifier=_Verifier(verification, verifier_error),
    )


def _evidence(target=None, selection=HUMAN_SELECTION_CONCUR,
              reviewer="person:reviewer-1"):
    return ReviewEvidence("review-row-23", target or _target(), selection,
                          reviewer)


def test_target_requires_stable_id_and_sha256_digest():
    with pytest.raises(ValueError, match="stable_id"):
        AuthorizationTarget(TargetType.GOVERNANCE_DECISION, " ", HASH_A)
    with pytest.raises(ValueError, match="SHA-256"):
        AuthorizationTarget(TargetType.GOVERNANCE_DECISION, "row-1", "not-a-hash")


def test_review_is_bound_to_exact_target_type_identifier_and_hash():
    target = _target()
    for substituted in (
        AuthorizationTarget(TargetType.REGULATORY_DISCLOSURE_FINDING,
                            target.stable_id, target.evidence_hash),
        AuthorizationTarget(target.target_type, "other-record", target.evidence_hash),
        AuthorizationTarget(target.target_type, target.stable_id, HASH_B),
    ):
        result = _gate(target, evidence=_evidence(substituted)).authorize(
            target, "release")
        assert result.status is AuthorizationStatus.DENIED
        assert result.reason == "review_evidence_malformed_or_mismatched"


@pytest.mark.parametrize("evidence", [None, object()])
def test_missing_or_malformed_review_evidence_fails_closed(evidence):
    result = _gate(evidence=evidence).authorize(_target(), "release")
    assert result.status in (AuthorizationStatus.REVIEW_REQUIRED,
                             AuthorizationStatus.DENIED)
    assert result.permitted is False


def test_stale_decision_state_and_missing_decision_do_not_authorize():
    stale_state = DecisionState(_target(evidence_hash=HASH_B))
    stale = _gate(state=stale_state).authorize(_target(), "release")
    missing = _gate(state=None).authorize(_target(), "release")
    assert stale.status is AuthorizationStatus.INDETERMINATE
    assert stale.reason == "decision_target_mismatch"
    assert missing.status is AuthorizationStatus.DENIED
    assert missing.reason == "target_not_found"


def test_decision_lookup_exception_is_indeterminate_and_denies():
    result = _gate(decision_error=RuntimeError("ledger unavailable")).authorize(
        _target(), "release")
    assert result.status is AuthorizationStatus.INDETERMINATE
    assert result.permitted is False


@pytest.mark.parametrize("verification", [
    VerificationResult(False, True, True, "bad chain"),
    VerificationResult(True, False, True, "unverified reviewer identity"),
    VerificationResult(True, True, False, "reviewer role missing"),
    VerificationResult(1, True, True, "non-boolean verification"),
    object(),
])
def test_integrity_identity_authority_or_ambiguous_verification_fails_closed(
        verification):
    result = _gate(evidence=_evidence(), verification=verification).authorize(
        _target(), "release")
    assert result.status in (AuthorizationStatus.DENIED,
                             AuthorizationStatus.INDETERMINATE)
    assert not result.permitted


def test_policy_and_review_verifier_exceptions_fail_closed():
    policy_failure = _gate(policy_error=RuntimeError("policy unavailable"))
    verifier_failure = _gate(evidence=_evidence(),
                             verifier_error=RuntimeError("verification unavailable"))
    assert policy_failure.authorize(_target(), "release").status is \
        AuthorizationStatus.INDETERMINATE
    assert verifier_failure.authorize(_target(), "release").status is \
        AuthorizationStatus.INDETERMINATE


def test_action_block_is_denied_without_explicit_release_policy():
    target = _target(TargetType.REGULATORY_DISCLOSURE_FINDING)
    result = _gate(target, action_block=True,
                   evidence=_evidence(target)).authorize(target, "release")
    assert result.status is AuthorizationStatus.DENIED
    assert result.reason == "action_block_release_not_authorized"


@pytest.mark.parametrize("selection", [
    HUMAN_SELECTION_CONCUR,
    HUMAN_SELECTION_ESCALATE,
])
def test_concur_and_escalate_preserve_action_block_even_if_listed_by_policy(selection):
    """These selections cannot release a block, even under an overly broad
    policy assessment. The assessment/verifier here are mocks, not deployed
    policy or reviewer authority.
    """
    target = _target(TargetType.REGULATORY_DISCLOSURE_FINDING)
    result = _gate(
        target=target,
        action_block=True,
        permitted=(HUMAN_SELECTION_CONCUR, HUMAN_SELECTION_OVERRIDE,
                   HUMAN_SELECTION_ESCALATE),
        action_block_override_policy_permitted=True,
        policy_id="test-only-policy",
        policy_version="test-only-version",
        evidence=_evidence(target, selection),
    ).authorize(target, "release")
    assert result.status is AuthorizationStatus.DENIED
    assert result.reason == "action_block_selection_preserves_block"


def test_override_requires_explicit_identified_policy_and_verified_reviewer():
    """This uses mock adapters to exercise the conditional OVERRIDE path; it
    does not assert that a production release policy or identity provider exists.
    """
    target = _target(TargetType.REGULATORY_DISCLOSURE_FINDING)
    gate = _gate(
        target=target,
        action_block=True,
        permitted=(HUMAN_SELECTION_OVERRIDE,),
        action_block_override_policy_permitted=True,
        policy_id="test-only-explicit-override-policy",
        policy_version="test-only-version",
        evidence=_evidence(target, HUMAN_SELECTION_OVERRIDE),
        verification=VerificationResult(True, True, True,
                                        "mocked exact-target reviewer verification"),
    )
    result = gate.authorize(target, "release")
    assert result.status is AuthorizationStatus.PERMITTED
    assert result.reason == "policy_valid_review_verified"
    evidence, verified_target, verified_action, assessment = gate._verifier.calls[0]
    assert evidence.target == verified_target == target
    assert verified_action == "release"
    assert assessment.target == target
    assert assessment.policy_id == "test-only-explicit-override-policy"


def test_override_without_policy_identity_is_denied():
    target = _target(TargetType.REGULATORY_DISCLOSURE_FINDING)
    result = _gate(
        target=target,
        action_block=True,
        permitted=(HUMAN_SELECTION_OVERRIDE,),
        action_block_override_policy_permitted=True,
        evidence=_evidence(target, HUMAN_SELECTION_OVERRIDE),
    ).authorize(target, "release")
    assert result.status is AuthorizationStatus.DENIED
    assert result.reason == "action_block_release_policy_unidentified"


def test_api_key_label_alone_cannot_establish_human_authority():
    evidence = _evidence(reviewer="configured-api-key-name")
    result = _gate(
        evidence=evidence,
        verification=VerificationResult(True, False, False,
                                        "API key label is not a human identity"),
    ).authorize(_target(), "release")
    assert result.status is AuthorizationStatus.DENIED
    assert "reviewer_authority" in result.reason


def test_policy_that_requires_no_review_preserves_non_block_behavior():
    result = _gate(requirement=ReviewRequirement.NOT_REQUIRED).authorize(
        _target(), "release")
    assert result.status is AuthorizationStatus.PERMITTED
    assert result.reason == "policy_does_not_require_review"


def test_review_required_selection_must_be_explicitly_permitted_by_policy():
    for selection in (HUMAN_SELECTION_OVERRIDE, HUMAN_SELECTION_ESCALATE):
        result = _gate(evidence=_evidence(selection=selection)).authorize(
            _target(), "release")
        assert result.status is AuthorizationStatus.DENIED
        assert result.reason == "selection_not_permitted_by_policy"


def test_verified_policy_permitted_review_can_yield_permitted_result():
    result = _gate(evidence=_evidence()).authorize(_target(), "release")
    assert result.status is AuthorizationStatus.PERMITTED
    assert result.reason == "policy_valid_review_verified"
    assert result.evidence_id == "review-row-23"


def test_direct_call_cannot_pass_approval_boolean_or_forged_evidence():
    gate = _gate(evidence=None)
    with pytest.raises(TypeError):
        gate.authorize(_target(), "release", approved=True)
    with pytest.raises(TypeError):
        gate.authorize(_target(), "release", review_evidence=_evidence())
    result = gate.authorize(_target(), "release")
    assert result.status is AuthorizationStatus.REVIEW_REQUIRED
    assert not result.permitted


def test_unrecognized_selection_policy_vocabulary_is_indeterminate():
    target = _target()
    gate = ActionAuthorizationGate(
        decisions=_DecisionProvider(DecisionState(target)),
        policy=_Policy(PolicyAssessment(
            target, "release", ReviewRequirement.REQUIRED,
            permitted_selections=("approve",))),
        reviews=_ReviewProvider(_evidence()), verifier=_Verifier(),
    )
    result = gate.authorize(target, "release")
    assert result.status is AuthorizationStatus.INDETERMINATE
    assert result.reason == "invalid_policy_selection_vocabulary"


@pytest.mark.parametrize("policy_target,policy_action", [
    (AuthorizationTarget(TargetType.GOVERNANCE_DECISION, "other", HASH_A),
     "release"),
    (_target(), "different-action"),
])
def test_policy_assessment_must_bind_exact_target_and_action(policy_target,
                                                              policy_action):
    target = _target()
    gate = ActionAuthorizationGate(
        decisions=_DecisionProvider(DecisionState(target)),
        policy=_Policy(PolicyAssessment(
            policy_target, policy_action, ReviewRequirement.NOT_REQUIRED)),
        reviews=_ReviewProvider(), verifier=_Verifier(),
    )
    result = gate.authorize(target, "release")
    assert result.status is AuthorizationStatus.INDETERMINATE
    assert result.reason == "policy_target_or_action_mismatch"


def test_ambiguous_action_block_state_is_indeterminate():
    target = _target()
    state = DecisionState(target=target, action_block=1)
    result = _gate(state=state, evidence=_evidence()).authorize(target, "release")
    assert result.status is AuthorizationStatus.INDETERMINATE
    assert result.reason == "invalid_decision_state"
