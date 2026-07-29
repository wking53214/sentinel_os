"""The live path, wired.

Before this work every production caller of make_episode/judge_episode
was a test: the harness scored calls down a parallel route and the
governance kernel judged nothing that had actually happened. These tests
assert that is no longer true, and that the numbers the kernel now judges
carry an honest account of where they came from.

Nothing here claims the ingest heuristics were fixed. The route is still
inferred from the last digit of the caller's number and the per-node
waits are still a fixed ratio split. What is asserted is that both now
say so.
"""

import pytest

from event_v1 import PROVENANCE_ESTIMATED, PROVENANCE_VERIFIED

CONFIG = {"postgres_host": None, "claude_api_key": None, "twilio_account_sid": None}
CALL = {"sid": "CAKERNEL001", "status": "completed", "duration": 150,
        "from": "+16125555551", "to": "+billing"}


@pytest.fixture
def harness():
    from production_harness import IcebergProductionHarness
    return IcebergProductionHarness(dict(CONFIG), require_cassette_binding=False)


def _assembly(harness, call=None):
    call = dict(call or CALL)
    journey = harness.twilio_parser.parse_call_log(call)
    emotion = harness.observer.get_emotional_state(journey.caller_id, [],
                                                   journey.total_duration)
    first_queue = next((n for n in journey.journey if "queue" in n), "general_queue")
    return harness._assemble_live_episode(
        journey, first_queue, friction_count=1, friction_events=[],
        emotion=emotion, measured_waits=getattr(journey, "wait_times", {}) or {},
        call_sid=call["sid"], twilio_record=call)


# ---------------------------------------------------------------------------
# The kernel is live.
# ---------------------------------------------------------------------------

def test_the_kernel_judges_a_real_call(harness):
    from episode import judge_episode
    assembly = _assembly(harness)
    result = judge_episode(harness.cassette, assembly.episode)
    assert result.tier in ("excellent", "good", "poor", "failed")
    assert 0.0 <= result.score <= 1.0


def test_process_call_records_the_kernel_verdict(harness):
    result = harness.process_call(dict(CALL))
    assert result.get("quality") is not None


def test_the_kernel_agrees_with_the_legacy_scoring_path(harness):
    """The IVR cassette documents judge() and score_outcome_quality() as
    arithmetically identical. Nothing proved it on live data until now."""
    from episode import judge_episode
    call = dict(CALL)
    journey = harness.twilio_parser.parse_call_log(call)
    emotion = harness.observer.get_emotional_state(journey.caller_id, [],
                                                   journey.total_duration)
    legacy = harness.sentinel.score_outcome_quality(
        journey.resolved, journey.total_duration, 1, emotion)
    assembly = _assembly(harness, call)
    kernel = judge_episode(harness.cassette, assembly.episode)
    assert str(kernel.tier).lower() == str(legacy.quality_tier.value).lower()


# ---------------------------------------------------------------------------
# The estimates now say they are estimates.
# ---------------------------------------------------------------------------

def test_the_route_is_stamped_estimated_and_names_the_phone_digit_rule(harness):
    assembly = _assembly(harness)
    route = [e for e in assembly.episode.timeline if e.kind == "route_selected"][0]
    assert route.detail["provenance"] == PROVENANCE_ESTIMATED
    assert "last digit" in route.detail["method"]


def test_the_wait_times_are_stamped_estimated_and_name_the_ratio_split(harness):
    assembly = _assembly(harness)
    waits = [e for e in assembly.episode.timeline if e.kind == "wait_observed"]
    assert waits, "the ratio split produces per-node waits; they should be events"
    for event in waits:
        assert event.detail["provenance"] == PROVENANCE_ESTIMATED
        assert "0.1/0.5/0.4" in event.detail["method"]


def test_the_two_facts_twilio_actually_reports_are_stamped_verified(harness):
    assembly = _assembly(harness)
    ended = [e for e in assembly.episode.timeline if e.kind == "call_ended"][0]
    assert ended.detail["provenance"] == PROVENANCE_VERIFIED
    assert assembly.provenance["resolved"] == PROVENANCE_VERIFIED
    assert assembly.provenance["duration"] == PROVENANCE_VERIFIED


def test_an_auditor_can_list_exactly_which_fields_were_estimated(harness):
    from event_v1 import estimated_fields
    assembly = _assembly(harness)
    estimated = set(estimated_fields(assembly.episode))
    assert "route" in estimated
    assert "resolved" not in estimated
    assert "duration" not in estimated


def test_the_provenance_map_rides_into_the_episode_the_cassette_judges(harness):
    from event_v1 import episode_provenance
    assembly = _assembly(harness)
    assert episode_provenance(assembly.episode) == assembly.provenance


# ---------------------------------------------------------------------------
# The kernel's own invariants still apply on the live path.
# ---------------------------------------------------------------------------

def test_an_unresolved_call_carries_a_real_reason_not_boilerplate(harness):
    call = dict(CALL, sid="CAKERNEL002", status="no-answer", duration=8)
    assembly = _assembly(harness, call)
    assert assembly.episode.outcome_reasons
    reason = assembly.episode.outcome_reasons[0]
    assert "no-answer" in reason and "friction_count" in reason


def test_an_unresolved_call_still_validates_and_judges(harness):
    from episode import judge_episode
    call = dict(CALL, sid="CAKERNEL003", status="no-answer", duration=8)
    assembly = _assembly(harness, call)
    assert judge_episode(harness.cassette, assembly.episode).tier


def test_the_cassette_reads_observed_facts_not_the_actor_story(harness):
    """episode.py's rule, still holding once real events flow through."""
    assembly = _assembly(harness)
    assert "resolved" in assembly.episode.actual
    assert assembly.episode.actor_report == {}


# ---------------------------------------------------------------------------
# IVR owes no outcome obligation, and says so rather than inventing one.
# ---------------------------------------------------------------------------

def test_ivr_declares_no_outcome_obligation(harness):
    from cassette_capabilities import CAPABILITY_OUTCOME_OBLIGATION
    assert CAPABILITY_OUTCOME_OBLIGATION not in harness.cassette.capabilities()
    assert harness._outcome_obligation_declaration() is None


def test_a_domain_that_enables_the_capability_declares_a_horizon(harness):
    """The other half: a cassette that does have maturing outcomes gets a
    declaration string that hashes into its decision rows."""
    from outcome_v1 import MaturationRule

    class _Lending:
        def capabilities(self):
            return ("outcome_obligation",)

        def get_maturation_rule(self):
            return MaturationRule.parse("loan_performance@24mo")

    harness.cassette = _Lending()
    assert harness._outcome_obligation_declaration() == "loan_performance@24mo"


def test_an_unreadable_maturation_rule_declares_nothing_rather_than_guessing(harness):
    class _Broken:
        def capabilities(self):
            return ("outcome_obligation",)

        def get_maturation_rule(self):
            raise ValueError("misconfigured")

    harness.cassette = _Broken()
    assert harness._outcome_obligation_declaration() is None
