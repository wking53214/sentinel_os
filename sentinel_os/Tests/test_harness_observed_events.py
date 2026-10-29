"""GovernanceHarness.process(observed_events=...): the events reach the boundary and the ledger.

Real Postgres, no mocks. The Claude client is left unconfigured, so a governed episode takes the
decider's documented fail-closed path and still writes the ledger.
"""
import os

import psycopg2
import pytest

os.environ.setdefault("ICEBERG_LEDGER_RUNTIME_USER", "")

from cassettes.mortgage_cassette import MortgageCassette  # noqa: E402
from conservation.boundary import ConservationBoundaryRejected  # noqa: E402
from episode import make_episode, outcome_mismatches  # noqa: E402
from event_v1 import assemble_episode, make_event  # noqa: E402
from governance_harness import GovernanceHarness  # noqa: E402

PG = dict(host="localhost", port=5432, dbname="iceberg", user="iceberg", password="iceberg")
REASON = "reduced based on updated appraisal value on file"


def _rows(where):
    conn = psycopg2.connect(connect_timeout=2, **PG)
    cur = conn.cursor()
    cur.execute(f"SELECT count(*) FROM ledger_entries WHERE {where}")  # nosec B608 -- fixed test strings
    n = cur.fetchone()[0]
    conn.close()
    return n


@pytest.fixture()
def harness():
    conn = psycopg2.connect(**PG)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("ALTER TABLE ledger_entries DISABLE TRIGGER USER;")
    cur.execute("TRUNCATE ledger_entries RESTART IDENTITY;")
    cur.execute("ALTER TABLE ledger_entries ENABLE TRIGGER USER;")
    conn.close()
    gh = GovernanceHarness({"postgres_host": PG["host"], "postgres_port": PG["port"], "postgres_db": PG["dbname"],
                            "postgres_user": PG["user"], "postgres_password": PG["password"]}, MortgageCassette())
    yield gh
    gh.shutdown()


def _events(episode_id):
    return [make_event(event_id=f"{episode_id}:decision", episode_id=episode_id, domain="mortgage",
                       kind="decision_observed", occurred_at=1000.0, observed_at=1001.0,
                       source="loan_system", provenance="verified", fields={"amount": 250000.0})]


def _assembled(episode_id, events):
    return assemble_episode(episode_id, "mortgage", {"amount": 300000.0}, events, outcome_reasons=(REASON,)).episode


def test_events_that_reproduce_the_episode_are_recorded_with_the_decision(harness):
    events = _events("HE1")
    episode = _assembled("HE1", events)
    result = harness.process(episode, len(outcome_mismatches(episode)), observed_events=events)
    assert result["decision_recorded"] is True
    assert harness.ledger.episode_decision_exists("HE1")
    assert _rows("record_kind = 'observed_event'") == 1


def test_an_episode_the_events_do_not_support_is_refused_and_nothing_is_written(harness):
    events = _events("HE2")
    unsupported = make_episode(episode_id="HE2", domain="mortgage", requested={"amount": 300000.0},
                               actual={"amount": 100000.0}, outcome_reasons=(REASON,))
    with pytest.raises(ConservationBoundaryRejected) as refused:
        harness.process(unsupported, len(outcome_mismatches(unsupported)), observed_events=events)
    assert "ROOT_NOT_ATTESTED" in str(refused.value)
    assert harness.ledger.episode_decision_exists("HE2") is False
    assert _rows("record_kind = 'observed_event'") == 0


def test_without_events_nothing_is_recorded_beyond_the_decision(harness):
    episode = make_episode(episode_id="HE3", domain="mortgage", requested={"amount": 300000.0},
                           actual={"amount": 250000.0}, outcome_reasons=(REASON,))
    result = harness.process(episode, len(outcome_mismatches(episode)))
    assert result["decision_recorded"] is True
    assert _rows("record_kind = 'observed_event'") == 0
