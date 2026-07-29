"""EventV1: provenance stamps, the method rule, and episode assembly.

The property under test throughout is the one the live path violated: a
derived number must never be indistinguishable from a measured one.
"""


import pytest

from episode import explain_episode
from event_v1 import (PROVENANCE_ATTESTED, PROVENANCE_ESTIMATED,
                      PROVENANCE_STAMPS, PROVENANCE_VERIFIED,
                      EventIntegrityError, assemble_episode, episode_provenance,
                      estimated_fields, make_event, validate_event)

NOW = 1_700_000_000.0


def _event(**kw):
    base = dict(event_id="e1", episode_id="ep1", domain="lending", kind="decision",
                occurred_at=NOW, observed_at=NOW, source="underwriting:engine",
                provenance=PROVENANCE_VERIFIED, fields={"outcome": "approved"})
    base.update(kw)
    return make_event(**base)


# ---------------------------------------------------------------------------
# The method rule. This is the whole point of the schema.
# ---------------------------------------------------------------------------

def test_estimated_without_a_method_is_refused():
    with pytest.raises(EventIntegrityError) as exc:
        validate_event(_event(provenance=PROVENANCE_ESTIMATED, method=None))
    assert any("no method is named" in v for v in exc.value.violations)


def test_estimated_with_a_method_validates():
    validate_event(_event(provenance=PROVENANCE_ESTIMATED,
                          method="total_duration*0.5"))


def test_a_blank_method_is_not_a_method():
    with pytest.raises(EventIntegrityError):
        validate_event(_event(provenance=PROVENANCE_ESTIMATED, method="   "))


@pytest.mark.parametrize("stamp", [PROVENANCE_VERIFIED, PROVENANCE_ATTESTED])
def test_an_observed_or_claimed_fact_cannot_carry_a_derivation(stamp):
    with pytest.raises(EventIntegrityError) as exc:
        validate_event(_event(provenance=stamp, method="duration*0.5"))
    assert any("was not derived" in v for v in exc.value.violations)


def test_unknown_stamp_refused():
    with pytest.raises(EventIntegrityError) as exc:
        validate_event(_event(provenance="probably"))
    assert any("bounded on" in v for v in exc.value.violations)


def test_the_stamp_vocabulary_is_exactly_three():
    assert PROVENANCE_STAMPS == (PROVENANCE_VERIFIED, PROVENANCE_ATTESTED,
                                 PROVENANCE_ESTIMATED)


def test_offsets_are_refused_as_timestamps():
    """0.0 is what an offset-from-start looks like. Absolute epoch only."""
    with pytest.raises(EventIntegrityError) as exc:
        validate_event(_event(occurred_at=0.0))
    assert any("absolute epoch" in v for v in exc.value.violations)


def test_every_violation_is_reported_at_once():
    with pytest.raises(EventIntegrityError) as exc:
        validate_event(_event(event_id="", provenance="nope", occurred_at=-1))
    assert len(exc.value.violations) >= 3


# ---------------------------------------------------------------------------
# Assembly: routing by stamp is what makes the kernel's existing
# actor-distrust machinery start working on ingested data.
# ---------------------------------------------------------------------------

def _assembly():
    return assemble_episode(
        episode_id="ep1", domain="lending",
        requested={"outcome": "approved"},
        events=[
            _event(event_id="a", fields={"amount": 25000}, occurred_at=NOW),
            _event(event_id="b", fields={"outcome": "denied"}, occurred_at=NOW + 5),
            _event(event_id="c", provenance=PROVENANCE_ESTIMATED,
                   method="total_duration*0.5", fields={"queue_wait": 30.0},
                   occurred_at=NOW + 2),
            _event(event_id="d", provenance=PROVENANCE_ATTESTED,
                   fields={"outcome": "approved"}, occurred_at=NOW + 6),
        ],
        outcome_reasons=("insufficient verified income",))


def test_verified_and_estimated_land_in_actual_attested_lands_in_actor_report():
    a = _assembly()
    assert a.episode.actual["outcome"] == "denied"
    assert a.episode.actual["queue_wait"] == 30.0
    assert a.episode.actor_report == {"outcome": "approved"}


def test_the_kernel_catches_the_actor_divergence_with_no_change_to_episode_py():
    """The payoff. The acting system claimed approved; observation says
    denied; episode.py's existing cross-check fires on ingested events."""
    class _C:
        def explain(self, ep):
            return [{"factor": "domain"}]

    factors = explain_episode(_C(), _assembly().episode)
    divergences = [f for f in factors if f["factor"] == "actor_report_divergence"]
    assert len(divergences) == 1
    assert divergences[0]["field"] == "outcome"
    assert divergences[0]["actor_claimed"] == "approved"
    assert divergences[0]["observed"] == "denied"


def test_estimated_fields_are_identifiable_after_assembly():
    a = _assembly()
    assert a.estimated_fields == ("queue_wait",)
    assert estimated_fields(a.episode) == ("queue_wait",)
    assert episode_provenance(a.episode)["outcome"] == PROVENANCE_VERIFIED
    assert episode_provenance(a.episode)["queue_wait"] == PROVENANCE_ESTIMATED


def test_provenance_rides_inside_the_episode_not_just_the_return_value():
    """It has to survive into judgment and into the ledger snapshot
    without the caller remembering to carry it."""
    a = _assembly()
    assert episode_provenance(a.episode) == a.provenance


def test_a_hand_built_episode_reports_no_provenance_rather_than_lying():
    from episode import make_episode
    assert episode_provenance(make_episode("x", "d", {}, {})) == {}
    assert estimated_fields(make_episode("x", "d", {}, {})) == ()


def test_later_event_wins_on_a_repeated_field_by_occurrence_not_arrival():
    a = assemble_episode(
        episode_id="ep1", domain="lending", requested={},
        events=[_event(event_id="late", fields={"outcome": "final"}, occurred_at=NOW + 9),
                _event(event_id="early", fields={"outcome": "interim"}, occurred_at=NOW)])
    assert a.episode.actual["outcome"] == "final"
    assert a.source_events == ("early", "late")


def test_one_bad_event_fails_the_whole_assembly():
    """A silently skipped event is a hole nothing downstream can see."""
    with pytest.raises(EventIntegrityError):
        assemble_episode(
            episode_id="ep1", domain="lending", requested={},
            events=[_event(event_id="ok"),
                    _event(event_id="bad", provenance=PROVENANCE_ESTIMATED)])


def test_events_from_another_episode_are_refused():
    with pytest.raises(EventIntegrityError) as exc:
        assemble_episode(episode_id="ep1", domain="lending", requested={},
                         events=[_event(episode_id="ep2")])
    assert any("would" in v and "fabricate" in v for v in exc.value.violations)


def test_timeline_carries_the_stamp_and_the_method_for_each_event():
    a = _assembly()
    estimated = [e for e in a.episode.timeline
                 if e.detail.get("provenance") == PROVENANCE_ESTIMATED]
    assert len(estimated) == 1
    assert estimated[0].detail["method"] == "total_duration*0.5"
    assert estimated[0].detail["source"] == "underwriting:engine"


def test_timeline_offsets_are_relative_to_the_earliest_observed_event():
    a = _assembly()
    assert min(e.at for e in a.episode.timeline) == 0.0
    assert max(e.at for e in a.episode.timeline) == 6.0


def test_the_kernel_still_requires_a_reason_on_a_mismatch():
    """Assembly does not get to bypass episode.py's hard invariant."""
    from episode import EpisodeIntegrityError, validate_episode
    a = assemble_episode(episode_id="ep1", domain="lending",
                         requested={"outcome": "approved"},
                         events=[_event(fields={"outcome": "denied"})])
    with pytest.raises(EpisodeIntegrityError):
        validate_episode(a.episode)


def test_domain_attributes_survive_alongside_the_reserved_provenance_key():
    a = assemble_episode(episode_id="ep1", domain="lending", requested={},
                         events=[_event()],
                         attributes={"duration": 12.0, "friction_count": 1})
    assert a.episode.attributes["duration"] == 12.0
    assert a.episode.attributes["friction_count"] == 1
    assert episode_provenance(a.episode)["outcome"] == PROVENANCE_VERIFIED
