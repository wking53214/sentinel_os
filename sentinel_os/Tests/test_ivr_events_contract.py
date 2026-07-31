"""The ivr_events ingestion contract.

twilio_log_ingestion has always had to guess a call's route and per-node
wait times from a bare Twilio Call record, because that record carries no
per-node data. These tests cover the generic escape hatch: when a caller
supplies real per-node events under the "ivr_events" key, parse_call_log
uses them directly and stamps the result VERIFIED, naming the real source.
Absent that key, nothing changes -- the existing heuristic still runs,
still stamped ESTIMATED. Present but malformed, parsing fails loud rather
than silently degrading to a guess (see twilio_log_ingestion's module
docstring for why).
"""

import pytest

from event_v1 import PROVENANCE_ESTIMATED, PROVENANCE_VERIFIED
from twilio_log_ingestion import (
    FALLBACK_ROUTE_METHOD,
    FALLBACK_WAIT_METHOD,
    NODE_ROLE_AGENT,
    NODE_ROLE_ESCALATION,
    NODE_ROLE_QUEUE,
    TwilioLogParser,
    _validate_ivr_events,
)
from cassettes.ivr_cassette import IvrCassette


@pytest.fixture
def parser():
    return TwilioLogParser(cassette=IvrCassette())


BASE_CALL = {"sid": "CAEVENTS001", "status": "completed", "duration": 150,
             "from": "+16125555559", "to": "+billing"}


# ---------------------------------------------------------------------------
# Absent ivr_events: unchanged fallback behavior.
# ---------------------------------------------------------------------------

def test_absent_ivr_events_uses_the_unchanged_heuristic(parser):
    journey = parser.parse_call_log(dict(BASE_CALL))
    assert journey.route_provenance == PROVENANCE_ESTIMATED
    assert journey.route_method == FALLBACK_ROUTE_METHOD
    assert journey.wait_provenance == PROVENANCE_ESTIMATED
    assert journey.wait_method == FALLBACK_WAIT_METHOD
    # last-digit rule: "+16125555559" ends in 9 -> general_queue
    assert "general_queue" in journey.journey


# ---------------------------------------------------------------------------
# Present and valid: real events drive the journey, stamped VERIFIED.
# ---------------------------------------------------------------------------

def test_real_events_produce_a_verified_journey(parser):
    call = dict(BASE_CALL, ivr_events=[
        {"node": "sales_queue", "wait_seconds": 15.0, "source": "taskrouter"},
        {"node": "agent_b", "wait_seconds": 90.0, "source": "taskrouter"},
    ])
    journey = parser.parse_call_log(call)
    assert journey.route_provenance == PROVENANCE_VERIFIED
    assert journey.wait_provenance == PROVENANCE_VERIFIED
    assert "taskrouter" in journey.route_method
    assert "taskrouter" in journey.wait_method


def test_real_events_journey_uses_supplied_node_names_in_order(parser):
    call = dict(BASE_CALL, ivr_events=[
        {"node": "sales_queue", "wait_seconds": 15.0, "source": "taskrouter"},
        {"node": "agent_b", "wait_seconds": 90.0, "source": "taskrouter"},
    ])
    journey = parser.parse_call_log(call)
    assert journey.journey == ["root", "sales_queue", "agent_b", "exit"]


def test_real_events_wait_times_match_supplied_values(parser):
    call = dict(BASE_CALL, ivr_events=[
        {"node": "sales_queue", "wait_seconds": 15.0, "source": "taskrouter"},
        {"node": "agent_b", "wait_seconds": 90.0, "source": "taskrouter"},
    ])
    journey = parser.parse_call_log(call)
    assert journey.wait_times == {"sales_queue": 15.0, "agent_b": 90.0}


def test_repeated_node_visits_sum_their_wait_times(parser):
    call = dict(BASE_CALL, ivr_events=[
        {"node": "billing_queue", "wait_seconds": 10.0, "source": "studio_flow"},
        {"node": "agent_a", "wait_seconds": 5.0, "source": "studio_flow"},
        {"node": "billing_queue", "wait_seconds": 8.0, "source": "studio_flow"},
    ])
    journey = parser.parse_call_log(call)
    assert journey.wait_times["billing_queue"] == 18.0


def test_multiple_sources_are_all_named_in_the_method_label(parser):
    call = dict(BASE_CALL, ivr_events=[
        {"node": "billing_queue", "wait_seconds": 10.0, "source": "studio_flow"},
        {"node": "agent_a", "wait_seconds": 5.0, "source": "custom_webhook"},
    ])
    journey = parser.parse_call_log(call)
    assert "studio_flow" in journey.route_method
    assert "custom_webhook" in journey.route_method


# ---------------------------------------------------------------------------
# Present but malformed: fail loud, never silently fall back.
# ---------------------------------------------------------------------------

def test_empty_ivr_events_list_fails_loud(parser):
    call = dict(BASE_CALL, ivr_events=[])
    with pytest.raises(ValueError, match="empty"):
        parser.parse_call_log(call)


def test_missing_node_fails_loud():
    with pytest.raises(ValueError, match="node"):
        _validate_ivr_events([{"wait_seconds": 5.0, "source": "taskrouter"}])


def test_missing_wait_seconds_fails_loud():
    with pytest.raises(ValueError, match="wait_seconds"):
        _validate_ivr_events([{"node": "billing_queue", "source": "taskrouter"}])


def test_negative_wait_seconds_fails_loud():
    with pytest.raises(ValueError, match="negative"):
        _validate_ivr_events(
            [{"node": "billing_queue", "wait_seconds": -1.0, "source": "taskrouter"}])


def test_missing_source_fails_loud():
    with pytest.raises(ValueError, match="source"):
        _validate_ivr_events([{"node": "billing_queue", "wait_seconds": 5.0}])


def test_non_string_node_fails_loud():
    with pytest.raises(ValueError, match="node"):
        _validate_ivr_events([{"node": 123, "wait_seconds": 5.0, "source": "taskrouter"}])


def test_non_numeric_wait_seconds_fails_loud():
    with pytest.raises(ValueError, match="wait_seconds"):
        _validate_ivr_events(
            [{"node": "billing_queue", "wait_seconds": "soon", "source": "taskrouter"}])


# ---------------------------------------------------------------------------
# NODE ROLES: an event can declare what kind of stop it is, instead of
# requiring the node's NAME to follow the legacy convention.
# ---------------------------------------------------------------------------

def test_role_tag_produces_a_verified_journey_with_an_unconventional_name(parser):
    """A node name that would never match the old "*queue*"/agent_a
    convention still gets recognized, because the role tag says what it
    is directly."""
    call = dict(BASE_CALL, ivr_events=[
        {"node": "BillingTransfer", "wait_seconds": 15.0, "source": "studio_flow",
         "role": NODE_ROLE_QUEUE},
        {"node": "SupportRepC", "wait_seconds": 90.0, "source": "studio_flow",
         "role": NODE_ROLE_AGENT},
    ])
    journey = parser.parse_call_log(call)
    assert journey.node_roles == {"BillingTransfer": NODE_ROLE_QUEUE,
                                  "SupportRepC": NODE_ROLE_AGENT}


def test_role_tagging_is_per_stop_not_all_or_nothing(parser):
    """One event can be tagged while another in the same call is not --
    each stop is judged on its own."""
    call = dict(BASE_CALL, ivr_events=[
        {"node": "billing_queue", "wait_seconds": 15.0, "source": "studio_flow",
         "role": NODE_ROLE_QUEUE},
        {"node": "agent_a", "wait_seconds": 90.0, "source": "studio_flow"},
    ])
    journey = parser.parse_call_log(call)
    assert journey.node_roles == {"billing_queue": NODE_ROLE_QUEUE}
    assert "agent_a" not in journey.node_roles


def test_absent_ivr_events_has_no_node_roles(parser):
    """The fallback heuristic path knows no roles -- empty dict, not a
    missing attribute, so callers can always call .get() on it safely."""
    journey = parser.parse_call_log(dict(BASE_CALL))
    assert journey.node_roles == {}


def test_escalation_role_is_recognized(parser):
    call = dict(BASE_CALL, ivr_events=[
        {"node": "billing_queue", "wait_seconds": 15.0, "source": "studio_flow",
         "role": NODE_ROLE_QUEUE},
        {"node": "SupervisorEscalation", "wait_seconds": 30.0, "source": "studio_flow",
         "role": NODE_ROLE_ESCALATION},
    ])
    journey = parser.parse_call_log(call)
    assert journey.node_roles["SupervisorEscalation"] == NODE_ROLE_ESCALATION


def test_unrecognized_role_fails_loud():
    with pytest.raises(ValueError, match="role"):
        _validate_ivr_events([{"node": "billing_queue", "wait_seconds": 5.0,
                               "source": "taskrouter", "role": "manager"}])


def test_repeated_node_with_conflicting_roles_fails_loud():
    with pytest.raises(ValueError, match="conflicting"):
        _validate_ivr_events([
            {"node": "front_desk", "wait_seconds": 5.0, "source": "taskrouter",
             "role": NODE_ROLE_QUEUE},
            {"node": "front_desk", "wait_seconds": 3.0, "source": "taskrouter",
             "role": NODE_ROLE_AGENT},
        ])


def test_repeated_node_with_the_same_role_twice_is_fine():
    events = _validate_ivr_events([
        {"node": "front_desk", "wait_seconds": 5.0, "source": "taskrouter",
         "role": NODE_ROLE_QUEUE},
        {"node": "front_desk", "wait_seconds": 3.0, "source": "taskrouter",
         "role": NODE_ROLE_QUEUE},
    ])
    assert len(events) == 2  # no exception -- identical roles don't conflict


# ---------------------------------------------------------------------------
# Friction counting still runs on whichever journey it's handed -- the
# ivr_events fork happens upstream of it and it doesn't need to know about it.
# ---------------------------------------------------------------------------

def test_friction_counting_runs_the_same_on_a_real_journey(parser):
    call = dict(BASE_CALL, duration=500, ivr_events=[
        {"node": "billing_queue", "wait_seconds": 15.0, "source": "taskrouter"},
        {"node": "agent_a", "wait_seconds": 90.0, "source": "taskrouter"},
    ])
    journey = parser.parse_call_log(call)
    assert isinstance(journey.friction_count, int)
