"""The conservation boundary: episode -> judgment as a verified transformation.

Replaces the pre-transport gateway's tests for the governed hot path. The
old-path modules still have their own tests (test_conservation_integration.py,
test_conservation_gateway_security.py) until they are removed.
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


def test_boundary_raises_are_fail_closed(monkeypatch):
    """Any boundary rejection propagates out of verify_governed_decision."""
    import conservation.boundary as B

    class _Rejected:
        accepted = False
        class decision:
            status = "REJECTED"
            rejections = ()

    monkeypatch.setattr(
        B.ConservationGateway, "submit", lambda *a, **k: _Rejected(),
    )
    with pytest.raises(ConservationBoundaryRejected):
        verify_governed_decision(_episode(), _Record())


def test_boundary_is_wired_into_write_decision():
    from governance_harness import GovernanceHarness
    source = inspect.getsource(GovernanceHarness._write_decision)
    assert "verify_governed_decision" in source
    assert "append_decision" in source
    assert source.index("verify_governed_decision") < source.index("append_decision")
