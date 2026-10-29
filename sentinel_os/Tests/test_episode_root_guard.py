"""The boundary's root guard: a root registers only when stamped events reproduce the Episode.

The kernel in these tests is built with `root_guard=` and without `allow_unguarded_roots`.
"""
import pytest

from conservation.boundary import ConservationBoundaryRejected, verify_governed_decision
from conservation.episode_guard import EpisodeNotAttested, EpisodeRootGuard
from conservation.episode_source import build_episode_source
from conservation_kernel import ConservationKernel, EvidenceRegistry
from conservation_kernel.errors import RootAdmissionError
from episode import make_episode
from event_v1 import assemble_episode, make_event


class _Record:
    node = "mortgage"
    cassette_version = "mortgage-v2.1.0"
    cassette_code_hash = "codehash-abc"
    model_identity = "claude-sonnet-5"
    reasoning = "DTI 0.47 exceeds the 0.43 cap; denial is consistent with the observed record."
    output = {"approved": False}
    authorized_by = "governor_claude_api"


def _events(episode_id="ep-guard-1", dti=0.47):
    return [
        make_event(event_id=f"{episode_id}:decision", episode_id=episode_id, domain="mortgage",
                   kind="decision_observed", occurred_at=1000.0, observed_at=1001.0,
                   source="loan_system", provenance="verified",
                   fields={"approved": False, "dti": dti, "reason_code": "DTI_EXCEEDED"}),
        make_event(event_id=f"{episode_id}:claim", episode_id=episode_id, domain="mortgage",
                   kind="actor_claim", occurred_at=1002.0, observed_at=1003.0,
                   source="underwriting_agent", provenance="attested",
                   fields={"approved": False, "confidence": 0.9}),
    ]


def _assembled(episode_id="ep-guard-1", events=None):
    return assemble_episode(
        episode_id, "mortgage", {"approve": True, "rate": 6.5}, events or _events(episode_id),
        outcome_reasons=("DTI 0.47 exceeds 0.43 policy cap",), attributes={"duration_s": 3.4},
    ).episode


def _root(episode):
    return build_episode_source(episode, EvidenceRegistry())


def test_the_guard_is_closed_until_attested():
    guard = EpisodeRootGuard()
    assert guard.attested is False
    assert guard(_root(_assembled())) is False


def test_events_that_reproduce_the_episode_open_the_guard_once():
    episode = _assembled()
    guard = EpisodeRootGuard()
    guard.attest(episode, _events())
    assert guard.attested
    assert guard(_root(episode)) is True
    assert guard(_root(episode)) is False  # one use


def test_it_says_no_to_a_root_built_from_another_episode():
    guard = EpisodeRootGuard()
    guard.attest(_assembled(), _events())
    other = _assembled("ep-guard-2", _events("ep-guard-2"))
    assert guard(_root(other)) is False
    assert guard.attested  # a refusal does not spend it
    assert guard(_root(_assembled())) is True


def test_an_episode_that_says_more_than_its_events_is_refused():
    episode = _assembled()
    inflated = make_episode(episode.episode_id, episode.domain, episode.requested,
                            {**episode.actual, "income": 250000}, episode.actor_report,
                            episode.timeline, episode.outcome_reasons, episode.attributes)
    guard = EpisodeRootGuard()
    with pytest.raises(EpisodeNotAttested, match="actual"):
        guard.attest(inflated, _events())
    assert guard.attested is False
    assert guard(_root(inflated)) is False


def test_events_that_disagree_with_the_episode_are_refused():
    guard = EpisodeRootGuard()
    with pytest.raises(EpisodeNotAttested, match="actual"):
        guard.attest(_assembled(), _events(dti=0.30))


def test_events_from_another_episode_are_refused():
    guard = EpisodeRootGuard()
    with pytest.raises(EpisodeNotAttested, match="could not be assembled"):
        guard.attest(_assembled(), _events("ep-other"))


def test_no_events_means_nothing_to_attest_against():
    with pytest.raises(EpisodeNotAttested, match="no observed events"):
        EpisodeRootGuard().attest(_assembled(), [])


def test_one_guard_serves_one_episode():
    guard = EpisodeRootGuard()
    guard.attest(_assembled(), _events())
    with pytest.raises(EpisodeNotAttested, match="already attested"):
        guard.attest(_assembled(), _events())


def test_a_kernel_with_the_guard_refuses_a_root_that_was_not_attested():
    guard = EpisodeRootGuard()
    kernel = ConservationKernel(registry=EvidenceRegistry(), root_guard=guard)
    with pytest.raises(RootAdmissionError):
        kernel.register_root(_root(_assembled()))


def test_the_boundary_accepts_an_episode_its_events_reproduce():
    result = verify_governed_decision(_assembled(), _Record(), observed_events=_events())
    assert result.accepted


def test_the_boundary_refuses_an_episode_its_events_do_not_support():
    episode = _assembled()
    inflated = make_episode(episode.episode_id, episode.domain, episode.requested,
                            {**episode.actual, "income": 250000}, episode.actor_report,
                            episode.timeline, episode.outcome_reasons, episode.attributes)
    with pytest.raises(ConservationBoundaryRejected) as caught:
        verify_governed_decision(inflated, _Record(), observed_events=_events())
    assert [r.code for r in caught.value.decision.rejections] == ["ROOT_NOT_ATTESTED"]


def test_without_events_the_boundary_behaves_as_before():
    assert verify_governed_decision(_assembled(), _Record()).accepted


def test_the_events_path_builds_the_kernel_with_a_root_guard(monkeypatch):
    """The accepted-episode test would also pass if the guard were never used; this one cannot."""
    import conservation.boundary as boundary
    seen = []

    class _Spy(boundary.ConservationGateway):
        def __init__(self, **kw):
            super().__init__(**kw)
            seen.append(self.kernel.has_root_guard)

    monkeypatch.setattr(boundary, "ConservationGateway", _Spy)
    verify_governed_decision(_assembled(), _Record(), observed_events=_events())
    verify_governed_decision(_assembled(), _Record())
    assert seen == [True, False]


def test_the_ledger_hands_its_observed_events_to_the_boundary(test_ledger, monkeypatch):
    """Needs the test database, like the other ledger boundary tests. Stops at the boundary: nothing is written."""
    import conservation.boundary as boundary
    from governance.ledger_postgres import ENV_CONSERVATION_DEV_OVERRIDE, GovernanceDecisionRecord
    monkeypatch.delenv(ENV_CONSERVATION_DEV_OVERRIDE, raising=False)
    calls = []

    def _spy(episode, record, observed_events=None):
        calls.append(observed_events)
        raise ValueError("stop here")

    monkeypatch.setattr(boundary, "verify_governed_decision", _spy)
    record = GovernanceDecisionRecord(
        action_type="governance_decision", node="mortgage", cassette_version="mortgage-v2.1.0",
        input_data={"episode_id": "ep-guard-1", "issue_count": 1}, policy_parameters={"dti_cap": 0.43},
        reasoning="DTI 0.47 exceeds the 0.43 cap.", output={"approved": False})
    events = _events()
    with pytest.raises(RuntimeError, match="decision not persisted"):
        test_ledger.append_decision(record, episode=_assembled(), observed_events=events)
    assert [e.event_id for e in calls[0]] == [e.event_id for e in events]
