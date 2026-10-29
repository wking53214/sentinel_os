"""A queued mortgage job may carry its observed events. With them the episode is assembled from the events,
the ledger records them, and the conservation boundary registers the root because they reproduce it.
Without them the job behaves exactly as before. Real Postgres, no mocks.
"""
import os

import psycopg2
import pytest

os.environ.setdefault("ICEBERG_LEDGER_RUNTIME_USER", "")

from cassettes.mortgage_cassette import MortgageCassette  # noqa: E402
from governance_harness import GovernanceHarness  # noqa: E402
from sentinel_worker import GovernanceHarnessJobAdapter, _payload_to_mortgage_episode  # noqa: E402

PG = dict(host="localhost", port=5432, dbname="iceberg", user="iceberg", password="iceberg")
REASON = "reduced based on updated appraisal value on file"


def _count(where):
    conn = psycopg2.connect(connect_timeout=2, **PG)
    cur = conn.cursor()
    cur.execute(f"SELECT count(*) FROM ledger_entries WHERE {where}")  # nosec B608 -- fixed test strings
    n = cur.fetchone()[0]
    conn.close()
    return n


@pytest.fixture()
def adapter():
    conn = psycopg2.connect(**PG)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("ALTER TABLE ledger_entries DISABLE TRIGGER USER;")
    cur.execute("TRUNCATE ledger_entries RESTART IDENTITY;")
    cur.execute("ALTER TABLE ledger_entries ENABLE TRIGGER USER;")
    conn.close()
    gh = GovernanceHarness({"postgres_host": PG["host"], "postgres_port": PG["port"], "postgres_db": PG["dbname"],
                            "postgres_user": PG["user"], "postgres_password": PG["password"]}, MortgageCassette())
    yield GovernanceHarnessJobAdapter(gh)
    gh.shutdown()


def _event(episode_id, event_id="decision", **over):
    body = dict(event_id=f"{episode_id}:{event_id}", episode_id=episode_id, domain="mortgage",
                kind="decision_observed", occurred_at=1000.0, observed_at=1001.0, source="loan_system",
                provenance="verified", fields={"amount": 250000.0})
    body.update(over)
    return body


def with_events(episode_id, **over):
    return {"episode_id": episode_id, "requested": {"amount": 300000.0}, "outcome_reasons": [REASON],
            "events": [_event(episode_id)], **over}


def test_the_episode_is_assembled_from_the_events_not_from_a_stated_actual():
    episode, issue_count = _payload_to_mortgage_episode(MortgageCassette(), with_events("PE1"))
    assert dict(episode.actual) == {"amount": 250000.0}
    assert issue_count == 1  # one requested-vs-actual mismatch, a substantive reason


@pytest.mark.parametrize("name", ["actual", "actor_report"])
def test_a_payload_stating_both_events_and_actual_or_actor_report_is_refused(name):
    with pytest.raises(ValueError, match=name):
        _payload_to_mortgage_episode(MortgageCassette(), with_events("PE2", **{name: {"amount": 1.0}}))


@pytest.mark.parametrize("events", [[], "not a list", ["not a dict"]])
def test_events_must_be_a_non_empty_list_of_dicts(events):
    with pytest.raises(ValueError):
        _payload_to_mortgage_episode(MortgageCassette(), with_events("PE3", events=events))


def test_an_event_from_another_domain_is_refused():
    payload = with_events("PE4", events=[_event("PE4", domain="telephony")])
    with pytest.raises(ValueError, match="telephony"):
        _payload_to_mortgage_episode(MortgageCassette(), payload)


def test_a_job_with_events_is_recorded_with_its_events(adapter):
    result = adapter.process_call(with_events("PE5"))
    assert result.get("error") is None
    assert adapter.harness.ledger.episode_decision_exists("PE5")
    assert _count("record_kind = 'observed_event'") == 1


def test_an_event_belonging_to_another_episode_is_a_parse_failure_and_writes_nothing(adapter):
    result = adapter.process_call(with_events("PE6", events=[_event("SOMEONE-ELSE")]))
    assert str(result["error"]).startswith("Failed to parse job")
    assert adapter.harness.ledger.episode_decision_exists("PE6") is False
    assert _count("record_kind = 'observed_event'") == 0


def test_a_job_without_events_is_unchanged(adapter):
    payload = {"episode_id": "PE7", "requested": {"amount": 300000.0}, "actual": {"amount": 250000.0},
               "outcome_reasons": [REASON]}
    result = adapter.process_call(payload)
    assert result.get("error") is None
    assert adapter.harness.ledger.episode_decision_exists("PE7")
    assert _count("record_kind = 'observed_event'") == 0
