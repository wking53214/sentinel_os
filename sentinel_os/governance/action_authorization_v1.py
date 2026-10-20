"""Fail-closed contract for authorizing a future governed action.

This module is an authorization foundation, not an execution boundary. A
future action consumer must call a gate built with trusted implementations of
the provider protocols below. No production business-action consumer is
currently wired to this contract.

The gate obtains decision state, policy assessment, review evidence, and
evidence verification from those providers. It accepts no caller-supplied
approval flag, review object, or principal argument. In particular, the current
API-key name is not an individual reviewer identity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Protocol, Tuple

from .human_selection_v1 import HUMAN_SELECTIONS


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class TargetType(str, Enum):
    GOVERNANCE_DECISION = "governance_decision"
    REGULATORY_DISCLOSURE_FINDING = "regulatory_disclosure_finding"


class AuthorizationStatus(str, Enum):
    PERMITTED = "permitted"
    DENIED = "denied"
    REVIEW_REQUIRED = "review_required"
    INDETERMINATE = "indeterminate"


class ReviewRequirement(str, Enum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class AuthorizationTarget:
    """Exact immutable record/finding reference requested for authorization.

    For ``REGULATORY_DISCLOSURE_FINDING``, ``stable_id`` must identify the
    disclosure record and ``evidence_hash`` must identify the finding evidence
    hash bound to that record. It is deliberately not represented as a
    governance decision.
    """

    target_type: TargetType
    stable_id: str
    evidence_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.target_type, TargetType):
            raise ValueError("target_type must be a TargetType")
        if not isinstance(self.stable_id, str) or not self.stable_id.strip():
            raise ValueError("stable_id is required")
        if not isinstance(self.evidence_hash, str) or not _SHA256_RE.fullmatch(
                self.evidence_hash):
            raise ValueError("evidence_hash must be a lowercase SHA-256 hex digest")


@dataclass(frozen=True)
class AuthorizationResult:
    status: AuthorizationStatus
    reason: str
    target: Optional[AuthorizationTarget]
    action: str
    evidence_id: Optional[str] = None

    @property
    def permitted(self) -> bool:
        return self.status is AuthorizationStatus.PERMITTED


@dataclass(frozen=True)
class DecisionState:
    """State loaded from the authoritative decision/disclosure store."""

    target: AuthorizationTarget
    action_block: bool = False
    payload: Any = None


@dataclass(frozen=True)
class PolicyAssessment:
    """Trusted policy result; empty permitted selections means deny release."""

    requirement: ReviewRequirement
    permitted_selections: Tuple[str, ...] = ()
    action_block_release_permitted: bool = False
    policy_id: Optional[str] = None
    policy_version: Optional[str] = None


@dataclass(frozen=True)
class ReviewEvidence:
    """Review row loaded from trusted storage, never accepted from a caller."""

    evidence_id: str
    target: AuthorizationTarget
    selection: str
    reviewer_subject: str


@dataclass(frozen=True)
class VerificationResult:
    """Integrity, authenticated-human identity, and role verification result.

    A successful result requires all three assertions. Ledger attestation of a
    string such as an API-key label is not reviewer authentication or authority.
    """

    integrity_verified: bool
    reviewer_authenticated: bool
    reviewer_authorized: bool
    reason: str


class DecisionStateProvider(Protocol):
    def load(self, target: AuthorizationTarget) -> Optional[DecisionState]: ...


class ActionPolicy(Protocol):
    def assess(self, state: DecisionState, action: str) -> PolicyAssessment: ...


class ReviewEvidenceProvider(Protocol):
    def load(self, target: AuthorizationTarget) -> Optional[ReviewEvidence]: ...


class ReviewEvidenceVerifier(Protocol):
    def verify(self, evidence: ReviewEvidence, target: AuthorizationTarget,
               action: str) -> VerificationResult: ...


class ActionAuthorizationGate:
    """Evaluate authorization using only configured trusted providers.

    Provider construction and wiring are part of the trust boundary. The
    default outcome for missing providers, malformed returns, and exceptions
    is ``INDETERMINATE`` (therefore not permitted).
    """

    def __init__(self, *, decisions: DecisionStateProvider,
                 policy: ActionPolicy, reviews: ReviewEvidenceProvider,
                 verifier: ReviewEvidenceVerifier):
        self._decisions = decisions
        self._policy = policy
        self._reviews = reviews
        self._verifier = verifier

    def authorize(self, target: AuthorizationTarget,
                  action: str) -> AuthorizationResult:
        if not isinstance(target, AuthorizationTarget):
            return AuthorizationResult(
                AuthorizationStatus.INDETERMINATE, "invalid_target",
                None, str(action))
        if not isinstance(action, str) or not action.strip():
            return self._result(AuthorizationStatus.DENIED, "invalid_action",
                                target, str(action))

        try:
            state = self._decisions.load(target)
        except Exception:
            return self._result(AuthorizationStatus.INDETERMINATE,
                                "decision_lookup_failed", target, action)
        if state is None:
            return self._result(AuthorizationStatus.DENIED,
                                "target_not_found", target, action)
        if not isinstance(state, DecisionState) or state.target != target:
            return self._result(AuthorizationStatus.INDETERMINATE,
                                "decision_target_mismatch", target, action)
        if not isinstance(state.action_block, bool):
            return self._result(AuthorizationStatus.INDETERMINATE,
                                "invalid_decision_state", target, action)

        try:
            assessment = self._policy.assess(state, action)
        except Exception:
            return self._result(AuthorizationStatus.INDETERMINATE,
                                "policy_evaluation_failed", target, action)
        if not isinstance(assessment, PolicyAssessment):
            return self._result(AuthorizationStatus.INDETERMINATE,
                                "invalid_policy_result", target, action)
        if (not isinstance(assessment.permitted_selections, tuple)
                or any(selection not in HUMAN_SELECTIONS
                       for selection in assessment.permitted_selections)
                or not isinstance(assessment.action_block_release_permitted,
                                  bool)):
            return self._result(AuthorizationStatus.INDETERMINATE,
                                "invalid_policy_selection_vocabulary",
                                target, action)
        if assessment.requirement is ReviewRequirement.INDETERMINATE:
            return self._result(AuthorizationStatus.INDETERMINATE,
                                "review_policy_indeterminate", target, action)
        if assessment.requirement is ReviewRequirement.NOT_REQUIRED:
            if state.action_block:
                return self._result(AuthorizationStatus.DENIED,
                                    "action_block_release_not_authorized",
                                    target, action)
            return self._result(AuthorizationStatus.PERMITTED,
                                "policy_does_not_require_review", target, action)
        if assessment.requirement is not ReviewRequirement.REQUIRED:
            return self._result(AuthorizationStatus.INDETERMINATE,
                                "invalid_review_requirement", target, action)

        if state.action_block and not assessment.action_block_release_permitted:
            return self._result(AuthorizationStatus.DENIED,
                                "action_block_release_not_authorized",
                                target, action)

        try:
            evidence = self._reviews.load(target)
        except Exception:
            return self._result(AuthorizationStatus.INDETERMINATE,
                                "review_lookup_failed", target, action)
        if evidence is None:
            return self._result(AuthorizationStatus.REVIEW_REQUIRED,
                                "review_evidence_missing", target, action)
        if (not isinstance(evidence, ReviewEvidence)
                or evidence.target != target
                or not isinstance(evidence.selection, str)
                or not isinstance(evidence.evidence_id, str)
                or not evidence.evidence_id.strip()
                or not isinstance(evidence.reviewer_subject, str)
                or not evidence.reviewer_subject.strip()):
            return self._result(AuthorizationStatus.DENIED,
                                "review_evidence_malformed_or_mismatched",
                                target, action)
        if evidence.selection not in assessment.permitted_selections:
            return self._result(AuthorizationStatus.DENIED,
                                "selection_not_permitted_by_policy",
                                target, action, evidence.evidence_id)

        try:
            verification = self._verifier.verify(evidence, target, action)
        except Exception:
            return self._result(AuthorizationStatus.INDETERMINATE,
                                "review_verification_failed",
                                target, action, evidence.evidence_id)
        if not isinstance(verification, VerificationResult):
            return self._result(AuthorizationStatus.INDETERMINATE,
                                "review_verification_ambiguous",
                                target, action, evidence.evidence_id)
        if not (verification.integrity_verified is True
                and verification.reviewer_authenticated is True
                and verification.reviewer_authorized is True):
            return self._result(AuthorizationStatus.DENIED,
                                "review_integrity_or_reviewer_authority_not_verified",
                                target, action, evidence.evidence_id)

        return self._result(AuthorizationStatus.PERMITTED,
                            "policy_valid_review_verified",
                            target, action, evidence.evidence_id)

    @staticmethod
    def _result(status: AuthorizationStatus, reason: str,
                target: AuthorizationTarget, action: str,
                evidence_id: Optional[str] = None) -> AuthorizationResult:
        return AuthorizationResult(status, reason, target, action, evidence_id)
