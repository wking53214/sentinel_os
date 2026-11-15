"""The boundary's registry takes only the evidence this episode's source produces.

The Conservation_Kernel refuses to take evidence from just anyone, because a record that declares itself
independent verification would be counted by the verifier. `EpisodeEvidenceGuard` is Sentinel's answer: the same
function that builds an episode's evidence also says exactly which records are permitted, and anything else is
refused. These tests hold the guard, that the builder passes through it, and that the boundary's registry carries it.
"""
import dataclasses

import pytest

from conservation_kernel import Actor, EvidenceRegistry
from conservation_kernel.enums import EvidenceKind

from episode import make_episode, EpisodeEvent
from conservation import boundary as boundary_module
from conservation.boundary import verify_governed_decision
from conservation.episode_source import OBSERVED_EVIDENCE_ID, build_episode_source, expected_evidence
from conservation.evidence_guard import EpisodeEvidenceGuard


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
        episode_id="ep-guard-1", domain="mortgage",
        requested={"approve": True, "rate": 6.5},
        actual={"approved": False, "dti": 0.47, "reason_code": "DTI_EXCEEDED"},
        actor_report={"approved": False, "confidence": 0.9},
        outcome_reasons=("DTI 0.47 exceeds 0.43 policy cap",),
        timeline=(EpisodeEvent(at=0.0, kind="application_received", detail={}),),
        attributes={"duration_s": 3.4, "friction_count": 1},
    )
    base.update(over)
    return make_episode(**base)


def _genuine(episode):
    """The records the source produces, taken from a registry with no guard."""
    registry = EvidenceRegistry(allow_untrusted_humans=True)
    build_episode_source(episode, registry)
    return [registry.evidence(evidence_id) for evidence_id in expected_evidence(episode)]


def test_the_guard_permits_every_record_the_source_produces():
    episode = _episode()
    guard = EpisodeEvidenceGuard(episode)
    records = _genuine(episode)
    assert len(records) == 1 + len(episode.actual) + len(episode.outcome_reasons)
    assert all(guard(record) is True for record in records)


def test_the_source_builds_into_a_registry_that_carries_the_guard():
    episode = _episode()
    registry = EvidenceRegistry(evidence_guard=EpisodeEvidenceGuard(episode))
    source = build_episode_source(episode, registry)
    assert source.propositions
    assert registry.has_evidence_guard


@pytest.mark.parametrize("change", [
    {"evidence_id": "ev-invented"},
    {"subject_id": "p-some-other-proposition"},
    {"subject_id": "*"},
    {"kind": EvidenceKind.INDEPENDENT_VERIFICATION},
    {"independent": False},
    {"active": False},
    {"provided_by": Actor.external("someone-else", "not the observed record")},
    {"detail": {"episode_id": "ep-other", "domain": "mortgage"}},
    {"detail": {"episode_id": "ep-guard-1", "domain": "other-domain"}},
    {"detail": {"episode_id": "ep-guard-1", "domain": "mortgage", "extra": "x"}},
])
def test_the_guard_refuses_a_record_that_differs_from_what_the_source_produces(change):
    episode = _episode()
    guard = EpisodeEvidenceGuard(episode)
    genuine = _genuine(episode)[0]
    assert guard(dataclasses.replace(genuine, **change)) is False


def test_the_guard_refuses_a_genuine_record_of_another_episode():
    other = _episode(episode_id="ep-guard-2")
    records = _genuine(other)
    guard = EpisodeEvidenceGuard(_episode())
    assert all(guard(record) is False for record in records)


def test_the_guard_refuses_something_that_is_not_an_evidence_record():
    guard = EpisodeEvidenceGuard(_episode())
    assert guard(object()) is False
    assert guard(None) is False


def test_the_guard_never_raises_and_answers_exactly_true_or_false():
    episode = _episode()
    guard = EpisodeEvidenceGuard(episode)
    answers = {guard(record) for record in _genuine(episode)} | {guard("x"), guard(42)}
    assert answers <= {True, False}


def test_a_registry_with_the_guard_refuses_a_forged_record_of_the_shared_id():
    episode = _episode()
    registry = EvidenceRegistry(evidence_guard=EpisodeEvidenceGuard(episode))
    build_episode_source(episode, registry)
    forged = dataclasses.replace(_genuine(episode)[0], evidence_id="ev-forged-independent-check")
    with pytest.raises(Exception):  # noqa: B017 - the registry's own refusal; its type is the kernel's to name
        registry.add_evidence(forged)
    assert registry.evidence("ev-forged-independent-check") is None


def test_the_boundary_builds_its_registry_with_the_guard():
    seen = []
    original = boundary_module.EvidenceRegistry

    def spy(*args, **kwargs):
        registry = original(*args, **kwargs)
        seen.append(registry)
        return registry

    boundary_module.EvidenceRegistry = spy
    try:
        result = verify_governed_decision(_episode(), _Record())
    finally:
        boundary_module.EvidenceRegistry = original
    assert result.accepted
    assert seen and all(registry.has_evidence_guard for registry in seen)


def test_the_shared_record_id_is_the_one_the_judgment_cites():
    assert OBSERVED_EVIDENCE_ID in expected_evidence(_episode())
