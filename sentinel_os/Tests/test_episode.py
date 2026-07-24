"""
test_episode -- kernel ground-truth record proof suite.

Proves the two kernel invariants and the judgment entry points:

1. REASON ON ANY MISMATCH: an episode whose actual outcome differs from
   what was requested does not validate without an outcome reason --
   including the paid-but-reduced shape (no denial anywhere) that
   denial-triggered reason rules structurally miss.
2. NEVER TRUST THE ACTOR: the acting system's self-report is always
   cross-checked against the observed record; divergence is surfaced
   as a first-class finding, and judgment reads only the observed
   record.
3. No judgment path admits an unvalidated episode (judge_episode /
   explain_episode validate first).
4. Behavior preservation: IVR's kernel judge is arithmetically
   identical to its telephony score_outcome_quality, and banking's
   kernel judge reproduces its old scoring exactly.
"""

import itertools
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from cassettes.banking_cassette import BankingCassette
from cassettes.ivr_cassette import IvrCassette
from episode import (
    EpisodeIntegrityError,
    UNOBSERVED,
    actor_discrepancies,
    explain_episode,
    judge_episode,
    make_episode,
    outcome_mismatches,
    validate_episode,
)


def _call_episode(resolved=True, duration=100.0, friction=0, frustration=0.0,
                  reasons=(), **overrides):
    kwargs = dict(
        episode_id="EP-1", domain="test",
        requested={"resolved": True},
        actual={"resolved": resolved},
        outcome_reasons=reasons if reasons else (
            () if resolved else ("caller abandoned before resolution",)),
        attributes={"duration": duration, "friction_count": friction,
                    "emotion": {"frustration": frustration}},
    )
    kwargs.update(overrides)
    return make_episode(**kwargs)


# ---------------------------------------------------------------------------
# Invariant 1: reason owed on ANY outcome mismatch, not just denials.
# ---------------------------------------------------------------------------

def test_reduced_payment_without_reason_refused():
    """The insurance downcoding shape: claim PAID, amount reduced, no
    denial anywhere -- and still refused without a reason on file.
    This is the case denial-triggered reason rules structurally miss."""
    ep = make_episode(
        "CLM-77", "insurance",
        requested={"outcome": "paid", "amount": 1200.0},
        actual={"outcome": "paid", "amount": 900.0},
    )
    with pytest.raises(EpisodeIntegrityError) as exc:
        validate_episode(ep)
    joined = "\n".join(exc.value.violations)
    assert "amount" in joined
    assert "not only on formal denials" in joined


def test_reduced_payment_with_reason_validates():
    ep = make_episode(
        "CLM-78", "insurance",
        requested={"outcome": "paid", "amount": 1200.0},
        actual={"outcome": "paid", "amount": 900.0},
        outcome_reasons=("adjusted to fee schedule 12.4 for code 99214",),
    )
    report = validate_episode(ep)
    assert [m.name for m in report.mismatches] == ["amount"]


def test_requested_field_absent_from_actual_is_a_mismatch():
    """An outcome that never materialized is not a match."""
    ep = make_episode("EP-2", "test",
                      requested={"delivered": True}, actual={})
    with pytest.raises(EpisodeIntegrityError):
        validate_episode(ep)
    assert [m.name for m in outcome_mismatches(ep)] == ["delivered"]


def test_matching_outcome_needs_no_reason():
    ep = make_episode("EP-3", "test",
                      requested={"resolved": True}, actual={"resolved": True})
    report = validate_episode(ep)
    assert report.mismatches == ()


def test_blank_reason_is_no_reason():
    ep = make_episode("EP-4", "test",
                      requested={"resolved": True}, actual={"resolved": False},
                      outcome_reasons=("   ",))
    with pytest.raises(EpisodeIntegrityError) as exc:
        validate_episode(ep)
    assert any("blank reason" in v for v in exc.value.violations)


# ---------------------------------------------------------------------------
# Invariant 2: the actor's self-report is cross-checked, never trusted.
# ---------------------------------------------------------------------------

def test_actor_divergence_is_surfaced():
    ep = _call_episode(resolved=False,
                       actor_report={"resolved": True})
    report = validate_episode(ep)
    assert len(report.discrepancies) == 1
    d = report.discrepancies[0]
    assert (d.name, d.kind) == ("resolved", "DIVERGE")
    assert d.actor_claimed is True and d.observed is False


def test_actor_claim_with_no_observation_is_extra():
    ep = _call_episode(actor_report={"customer_satisfied": True})
    (d,) = actor_discrepancies(ep)
    assert (d.name, d.kind, d.observed) == ("customer_satisfied", "EXTRA", UNOBSERVED)


def test_judgment_reads_observed_record_not_actor_report():
    """Actor says resolved; observation says not. Judgment must score
    the OBSERVED unresolved outcome (banking base 0.2, not 0.7)."""
    ep = make_episode(
        "EP-5", "banking",
        requested={"resolved": True},
        actual={"resolved": False},
        actor_report={"resolved": True},
        outcome_reasons=("escalated to human fraud review",),
        attributes={"duration": 60.0, "friction_count": 0, "emotion": {}},
    )
    result = judge_episode(BankingCassette(), ep)
    assert result.score == pytest.approx(0.2 + 0.2)  # unresolved base + fast
    assert result.tier == "poor"


def test_explain_prepends_kernel_findings():
    """Verification findings ride ahead of the cassette's own factors,
    guaranteed by the kernel, not by cassette courtesy."""
    ep = make_episode(
        "EP-6", "ivr",
        requested={"resolved": True},
        actual={"resolved": False},
        actor_report={"resolved": True},
        outcome_reasons=("dropped at verification step",),
        attributes={"duration": 200.0, "friction_count": 3,
                    "emotion": {"frustration": 0.8},
                    "journey": ["entry", "billing_queue", "billing_queue"]},
    )
    factors = explain_episode(IvrCassette(), ep)
    kinds = [f["factor"] for f in factors]
    assert kinds[0] == "actor_report_divergence"
    assert "outcome_mismatch" in kinds
    assert kinds.index("outcome_mismatch") < kinds.index("resolved")
    assert "abandonment_diagnosis" in kinds  # IVR's own vocabulary rides along


# ---------------------------------------------------------------------------
# Invariant 3: no judgment path admits an unvalidated episode.
# ---------------------------------------------------------------------------

def test_judge_episode_validates_first():
    class SpyCassette:
        def __init__(self):
            self.judged = 0
        def judge(self, episode):
            self.judged += 1

    spy = SpyCassette()
    bad = make_episode("EP-7", "test",
                       requested={"resolved": True}, actual={"resolved": False})
    with pytest.raises(EpisodeIntegrityError):
        judge_episode(spy, bad)
    assert spy.judged == 0, "an invalid episode must never reach the cassette"


# ---------------------------------------------------------------------------
# Behavior preservation: the kernel surface changes WHERE judgment is
# invoked, never WHAT the domains conclude.
# ---------------------------------------------------------------------------

def test_ivr_judge_identical_to_score_outcome_quality():
    """Zero behavior change for IVR, pinned: judge(episode) and the
    telephony score_outcome_quality produce identical (score, tier)
    across a full sweep of inputs -- one arithmetic, two entrances."""
    ivr = IvrCassette()
    sweep = itertools.product([True, False], [50.0, 119.9, 150.0, 299.9, 400.0],
                              [0, 1, 2, 5], [0.0, 0.3, 0.7, 1.0])
    for resolved, duration, friction, frustration in sweep:
        ep = _call_episode(resolved=resolved, duration=duration,
                           friction=friction, frustration=frustration)
        via_kernel = judge_episode(ivr, ep)
        via_telephony = ivr.score_outcome_quality(
            resolved, duration, friction, {"frustration": frustration})
        assert (via_kernel.score, via_kernel.tier) == \
               (via_telephony.score, via_telephony.tier), \
            (resolved, duration, friction, frustration)


def test_banking_judge_preserves_old_arithmetic():
    """Banking's judgment moved to the kernel surface with its
    arithmetic intact: same weights, same 0.80 excellent cutoff.
    Pinned against hand-computed values of the pre-split formula."""
    bank = BankingCassette()

    # resolved, 150s, 0 friction, 0.1 frustration:
    # 0.7 + 0.2 - 0 - 0.01 = 0.89 -> excellent (banking's 0.80 bar)
    r = judge_episode(bank, _call_episode(True, 150.0, 0, 0.1))
    assert r.score == pytest.approx(0.89) and r.tier == "excellent"

    # unresolved, 400s, 2 friction, 0.6 frustration:
    # 0.2 + 0.05 - min(2*0.25, 0.4) - 0.06 = -0.21 -> clamp 0.0 -> failed
    r = judge_episode(bank, _call_episode(False, 400.0, 2, 0.6))
    assert r.score == 0.0 and r.tier == "failed"

    # unresolved fast clean call, NEITHER fraud-escalation path fired
    # (no customer_stated_fraud attribute, no fraud_detection_queue in
    # journey -- both absent here): 0.2 + 0.2 = 0.4 -> poor. An ordinary
    # non-fraud escalation (agent gives up, unresolved dispute, IVR
    # loop-out) still lands here -- see the fraud-escalation tests below
    # for the two paths that now carve out "excellent" instead.
    r = judge_episode(bank, _call_episode(False, 60.0, 0, 0.0))
    assert r.score == pytest.approx(0.4) and r.tier == "poor"


# ---------------------------------------------------------------------------
# Fraud-escalation top-tier carve-out (banking_cassette._score_components):
# a fraud escalation now scores as banking's best possible outcome under
# exactly two legitimate, non-discretionary paths -- customer-stated, or
# system-identified via the cassette's own already-declared
# fraud_detection_queue. Neither is an AI judgment call, so neither needs
# verification. Non-fraud escalations are untouched (proven above).
# ---------------------------------------------------------------------------

def test_customer_stated_fraud_path_scores_top_tier():
    ep = _call_episode(False, 400.0, 2, 0.6,
                       attributes={"duration": 400.0, "friction_count": 2,
                                   "emotion": {"frustration": 0.6},
                                   "customer_stated_fraud": True})
    r = judge_episode(BankingCassette(), ep)
    assert (r.score, r.tier) == (1.0, "excellent")

    factors = explain_episode(BankingCassette(), ep)
    top_tier = next(f for f in factors if f["factor"] == "fraud_escalation_top_tier")
    assert top_tier["escalation_path"] == "customer_stated"
    assert top_tier["matched_parameter"] is None


def test_system_identified_fraud_path_via_fraud_detection_queue_scores_top_tier():
    ep = _call_episode(False, 400.0, 2, 0.6,
                       attributes={"duration": 400.0, "friction_count": 2,
                                   "emotion": {"frustration": 0.6},
                                   "journey": ["entry", "fraud_detection_queue"]})
    r = judge_episode(BankingCassette(), ep)
    assert (r.score, r.tier) == (1.0, "excellent")

    factors = explain_episode(BankingCassette(), ep)
    top_tier = next(f for f in factors if f["factor"] == "fraud_escalation_top_tier")
    assert top_tier["escalation_path"] == "system_identified:fraud_detection_queue"
    assert top_tier["matched_parameter"] == "fraud_detection_queue"


def test_no_escalation_path_present_does_not_qualify_for_top_tier():
    """Neither path present (no customer_stated_fraud, no
    fraud_detection_queue in journey) -- no audit trail to point to, so
    no top-tier classification; ordinary unresolved scoring applies."""
    ep = _call_episode(False, 400.0, 2, 0.6,
                       attributes={"duration": 400.0, "friction_count": 2,
                                   "emotion": {"frustration": 0.6},
                                   "journey": ["entry", "billing_queue"]})
    r = judge_episode(BankingCassette(), ep)
    assert r.tier != "excellent"
    assert r.score == 0.0 and r.tier == "failed"  # same arithmetic as before

    factors = explain_episode(BankingCassette(), ep)
    assert not any(f["factor"] == "fraud_escalation_top_tier" for f in factors)


def test_resolved_call_does_not_trigger_fraud_top_tier_override():
    """An escalation is by definition not resolved in-system -- a
    resolved call already competes for excellent on the ordinary
    arithmetic and the carve-out must not fire for it even if
    customer_stated_fraud happens to be set (e.g. a false alarm that
    was cleared and resolved anyway)."""
    ep = _call_episode(True, 150.0, 0, 0.1,
                       attributes={"duration": 150.0, "friction_count": 0,
                                   "emotion": {"frustration": 0.1},
                                   "customer_stated_fraud": True})
    r = judge_episode(BankingCassette(), ep)
    assert (r.score, r.tier) == (pytest.approx(0.89), "excellent")  # ordinary arithmetic
    factors = explain_episode(BankingCassette(), ep)
    top_tier = [f for f in factors if f["factor"] == "fraud_escalation_top_tier"]
    assert top_tier == []


def test_non_fraud_escalation_is_unaffected_by_the_carve_out():
    """Agent gives up / unresolved billing dispute / IVR loop-out --
    unresolved, but neither fraud path applies. Byte-identical to the
    pre-carve-out arithmetic (test_banking_judge_preserves_old_arithmetic
    pins the same numbers)."""
    ep = _call_episode(False, 400.0, 2, 0.6)
    r = judge_episode(BankingCassette(), ep)
    assert r.score == 0.0 and r.tier == "failed"
    factors = explain_episode(BankingCassette(), ep)
    assert not any(f["factor"] == "fraud_escalation_top_tier" for f in factors)


def test_domains_may_disagree_on_the_same_episode():
    """The point of the cassette layer, restated at the kernel surface:
    one episode, two domains, two legitimate verdicts."""
    ep = _call_episode(True, 150.0, 0, 0.1)
    ivr_r = judge_episode(IvrCassette(), ep)
    bank_r = judge_episode(BankingCassette(), ep)
    assert ivr_r.score != bank_r.score
    assert (ivr_r.tier, bank_r.tier) == ("good", "excellent")
