"""The conservation boundary: episode -> judgment as a verified transformation.

This is the whole test surface for the governed conservation boundary. The
pre-transport gateway and its two test files (test_conservation_integration.py,
test_conservation_gateway_security.py) were removed 2026-09-03; the coverage
map is in conservation/CONFORMANCE.md.
"""
import inspect

import pytest

from conservation_kernel import (
    EvidenceRegistry, EpistemicStatus, AuthorityStatus, OriginStatus, Proposition,
)

from episode import make_episode, EpisodeEvent
from conservation.boundary import verify_governed_decision, ConservationBoundaryRejected
from conservation.episode_source import build_episode_source
from conservation.judgment import GovernanceJudgmentTransformer
from conservation.transport import ConservationGateway


class _Record:
    node = "mortgage"
    cassette_version = "mortgage-v2.1.0"
    cassette_code_hash = "codehash-abc"
    model_identity = "claude-sonnet-5"
    reasoning = "DTI 0.47 exceeds the 0.43 cap; denial is consistent with the observed record."
    output = {"approved": False}
    authorized_by = "governor_claude_api"


def _episode(**over):
    base = dict(
        episode_id="ep-boundary-1", domain="mortgage",
        requested={"approve": True, "rate": 6.5},
        actual={"approved": False, "dti": 0.47, "reason_code": "DTI_EXCEEDED"},
        actor_report={"approved": False, "confidence": 0.9},
        outcome_reasons=("DTI 0.47 exceeds 0.43 policy cap",),
        timeline=(EpisodeEvent(at=0.0, kind="application_received", detail={}),),
        attributes={"duration_s": 3.4, "friction_count": 1},
    )
    base.update(over)
    return make_episode(**base)


def test_honest_denial_is_accepted():
    result = verify_governed_decision(_episode(), _Record())
    assert result.accepted
    assert result.decision.kernel_status == "PASS_WITH_DECLARED_TRANSFORMATION"


def test_honest_approval_is_accepted():
    rec = _Record()
    rec.output = {"approved": True}
    rec.reasoning = "All policy checks pass."
    ep = _episode(episode_id="ep-boundary-2", actual={"approved": True},
                  actor_report={"approved": True}, outcome_reasons=())
    assert verify_governed_decision(ep, rec).accepted


def test_minimal_episode_is_accepted():
    ep = _episode(episode_id="ep-boundary-3", domain="banking",
                  requested={"x": 1}, actual={"x": 1}, actor_report={},
                  outcome_reasons=(), timeline=(), attributes={})
    assert verify_governed_decision(ep, _Record()).accepted


def test_actor_report_is_not_an_observation():
    """The adapter must map actor_report to a non-FACT/non-OBSERVATION status."""
    src = build_episode_source(_episode(), EvidenceRegistry())
    actor_props = [p for p in src.propositions if p.proposition_id.startswith("p-actor-")]
    assert actor_props
    for p in actor_props:
        assert p.epistemic_status not in (EpistemicStatus.FACT, EpistemicStatus.OBSERVATION)
        assert p.origin == OriginStatus.MACHINE_ORIGINATED


def test_keys_that_slug_to_the_same_id_stay_distinct():
    """`reason_code` and `reason.code` both slug to `p-actual-reason-code`;
    the adapter must not emit two propositions with the same id."""
    ep = _episode(
        episode_id="ep-boundary-collide",
        actual={"reason_code": "A", "reason.code": "B", "reason-code": "C"},
    )
    src = build_episode_source(ep, EvidenceRegistry())
    ids = [p.proposition_id for p in src.propositions]
    assert len(ids) == len(set(ids))
    assert verify_governed_decision(ep, _Record()).accepted


def test_authorized_by_string_cannot_raise_kernel_authority():
    """The judgment stays MACHINE_ORIGINATED / PROPOSED no matter what
    `authorized_by` says -- the transport path does no string->authority
    mapping, so the substring attacks the old gateway defended against (A1/A2:
    'inhumane' contains 'human', 'x_canonical_fraud' contains 'canonical')
    have no surface here. This is the invariant CONFORMANCE.md's "Not bridged,
    by design" section commits to: the keyed `authorized_by` attestation is
    never mapped onto a kernel authority status. Tripwire for the day any
    authorization_refs work reintroduces string handling on this path."""
    for spoof in ("human", "inhumane_canonical_fraud", "CANONICAL", "governor_claude_api x"):
        rec = _Record()
        rec.authorized_by = spoof
        src = build_episode_source(_episode(), (reg := EvidenceRegistry()))
        gw = ConservationGateway(registry=reg)
        gw.ingest_source(src)
        tx = GovernanceJudgmentTransformer.for_record(rec, _episode())
        gw.register_gem(tx.identity)
        req = tx.make_request(src)
        proposal = tx.build_proposal(req, rec)
        judgment = proposal.output_artifact.proposition_map()["p-governance-judgment"]
        assert judgment.authority == AuthorityStatus.PROPOSED
        assert judgment.origin == OriginStatus.MACHINE_ORIGINATED
        assert gw.submit(req, proposal).accepted


def test_unrooted_judgment_is_rejected():
    """A judgment proposition with no lineage must not pass the boundary."""
    registry = EvidenceRegistry()
    gateway = ConservationGateway(registry=registry)
    ep = _episode()
    source = build_episode_source(ep, registry)
    gateway.ingest_source(source)
    tx = GovernanceJudgmentTransformer.for_record(_Record(), ep)
    gateway.register_gem(tx.identity)
    request = tx.make_request(source)

    bad = Proposition(
        proposition_id="p-governance-judgment",
        text="Blocked, with no lineage to any observed fact.",
        epistemic_status=EpistemicStatus.DECISION,
        origin=OriginStatus.MACHINE_ORIGINATED,
        authority=AuthorityStatus.PROPOSED,
    )
    output = tx._artifact(request, suffix="judgment", content="bad judgment",
                          propositions=source.propositions + (bad,))
    result = gateway.submit(request, tx._proposal(request, output))
    assert not result.accepted
    assert any(r.code == "UNROOTED_NEW_PROPOSITION" for r in result.decision.rejections)


def test_verify_propagates_the_real_rejection(monkeypatch):
    """A genuine kernel rejection surfaces out of verify_governed_decision,
    carrying the real rejection code -- the whole gateway + verifier + kernel
    path runs, only the transformer is coerced into emitting a bad judgment."""
    from conservation.judgment import GovernanceJudgmentTransformer as T

    def _unrooted_proposal(self, request, record):
        source = request.input_artifact
        bad = Proposition(
            proposition_id="p-governance-judgment",
            text="Blocked, with no lineage to any observed fact.",
            epistemic_status=EpistemicStatus.DECISION,
            origin=OriginStatus.MACHINE_ORIGINATED,
            authority=AuthorityStatus.PROPOSED,
        )
        output = self._artifact(
            request, suffix="judgment", content="unrooted judgment",
            propositions=source.propositions + (bad,),
        )
        return self._proposal(request, output)

    monkeypatch.setattr(T, "build_proposal", _unrooted_proposal)

    with pytest.raises(ConservationBoundaryRejected) as exc:
        verify_governed_decision(_episode(), _Record())
    assert any(
        r.code == "UNROOTED_NEW_PROPOSITION" for r in exc.value.decision.rejections
    )


def test_boundary_is_wired_into_write_decision():
    from governance_harness import GovernanceHarness
    source = inspect.getsource(GovernanceHarness._write_decision)
    assert "verify_governed_decision" in source
    assert "append_decision" in source
    assert source.index("verify_governed_decision") < source.index("append_decision")
