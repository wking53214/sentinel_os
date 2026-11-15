"""The evidence an episode source registers is about the propositions that cite it.

The Conservation_Kernel counts evidence only when it is about the proposition that cites it, or one of that
proposition's parents, and a "*" subject counts for none: a root OBSERVATION must cite evidence about itself, and a
derived proposition may cite only evidence about itself or a parent. These tests hold that for the episode source
without depending on which kernel version is installed, so the source can move to a newer kernel without a rewrite.
"""
from conservation_kernel import EpistemicStatus, EvidenceRegistry

from episode import make_episode, EpisodeEvent
from conservation.episode_source import (
    OBSERVED_EVIDENCE_ID,
    build_episode_source,
    observed_proposition_ids,
)


def _episode(**over):
    base = dict(
        episode_id="ep-evidence-1", domain="mortgage",
        requested={"approve": True, "rate": 6.5},
        actual={"approved": False, "dti": 0.47, "reason_code": "DTI_EXCEEDED"},
        actor_report={"approved": False, "confidence": 0.9},
        outcome_reasons=("DTI 0.47 exceeds 0.43 policy cap", "second recorded reason"),
        timeline=(EpisodeEvent(at=0.0, kind="application_received", detail={}),),
        attributes={"duration_s": 3.4, "friction_count": 1},
    )
    base.update(over)
    return make_episode(**base)


def _source(episode):
    registry = EvidenceRegistry()
    return build_episode_source(episode, registry), registry


def test_no_evidence_record_is_about_everything():
    _, registry = _source(_episode())
    subjects = {registry.evidence(ref).subject_id for ref in (OBSERVED_EVIDENCE_ID,)}
    assert "*" not in subjects


def test_every_root_observation_cites_evidence_about_itself():
    source, registry = _source(_episode())
    observations = [p for p in source.propositions if p.epistemic_status is EpistemicStatus.OBSERVATION]
    assert observations
    for prop in observations:
        cited = [registry.evidence(ref) for ref in prop.evidence_refs]
        assert all(item is not None for item in cited), prop.proposition_id
        assert any(item.subject_id == prop.proposition_id for item in cited), prop.proposition_id


def test_the_shared_record_is_about_p_episode_so_a_judgment_can_cite_it():
    source, registry = _source(_episode())
    shared = registry.evidence(OBSERVED_EVIDENCE_ID)
    assert shared.subject_id == "p-episode"
    # The judgment is rooted to every observed proposition, p-episode first, so the record is about a parent.
    assert observed_proposition_ids(_episode())[0] == "p-episode"
    assert source.propositions[0].proposition_id == "p-episode"


def test_every_cited_record_is_registered_and_belongs_to_this_episode():
    source, registry = _source(_episode())
    for prop in source.propositions:
        for ref in prop.evidence_refs:
            record = registry.evidence(ref)
            assert record is not None, (prop.proposition_id, ref)
            assert record.detail["episode_id"] == "ep-evidence-1"


def test_the_source_is_deterministic_so_a_guard_that_rebuilds_it_sees_the_same_one():
    first, _ = _source(_episode())
    second, _ = _source(_episode())
    # EpisodeRootGuard compares (artifact_id, content_digest); artifact_digest includes a creation time.
    assert (first.artifact_id, first.content_digest) == (second.artifact_id, second.content_digest)
    assert [p.evidence_refs for p in first.propositions] == [p.evidence_refs for p in second.propositions]


def test_colliding_field_names_still_get_their_own_evidence_each():
    episode = _episode(actual={"reason_code": "A", "reason.code": "B"})
    source, registry = _source(episode)
    by_id = {p.proposition_id: p for p in source.propositions}
    assert "p-actual-reason-code" in by_id and "p-actual-reason-code-2" in by_id
    for pid in ("p-actual-reason-code", "p-actual-reason-code-2"):
        assert any(registry.evidence(ref).subject_id == pid for ref in by_id[pid].evidence_refs)
