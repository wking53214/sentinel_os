"""The production worker records only jobs that carry observed events. Real Postgres, no mocks."""
import ast
import os
from pathlib import Path

import psycopg2
import pytest

os.environ.setdefault("ICEBERG_LEDGER_RUNTIME_USER", "")

from cassettes.mortgage_cassette import MortgageCassette  # noqa: E402
from governance_harness import GovernanceHarness  # noqa: E402
from sentinel_worker import GovernanceHarnessJobAdapter  # noqa: E402

PG = dict(host="localhost", port=5432, dbname="iceberg", user="iceberg", password="iceberg")
REASON = "reduced based on updated appraisal value on file"
WORKER = Path(__file__).resolve().parents[1] / "sentinel_worker.py"


@pytest.fixture()
def gh():
    conn = psycopg2.connect(**PG)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("ALTER TABLE ledger_entries DISABLE TRIGGER USER;")
    cur.execute("TRUNCATE ledger_entries RESTART IDENTITY;")
    cur.execute("ALTER TABLE ledger_entries ENABLE TRIGGER USER;")
    conn.close()
    harness = GovernanceHarness({"postgres_host": PG["host"], "postgres_port": PG["port"], "postgres_db": PG["dbname"],
                                 "postgres_user": PG["user"], "postgres_password": PG["password"]}, MortgageCassette())
    yield harness
    harness.shutdown()


def _plain(episode_id):
    return {"episode_id": episode_id, "requested": {"amount": 300000.0}, "actual": {"amount": 250000.0},
            "outcome_reasons": [REASON]}


def _with_events(episode_id):
    return {"episode_id": episode_id, "requested": {"amount": 300000.0}, "outcome_reasons": [REASON],
            "events": [dict(event_id=f"{episode_id}:d", episode_id=episode_id, domain="mortgage",
                            kind="decision_observed", occurred_at=1000.0, observed_at=1001.0,
                            source="loan_system", provenance="verified", fields={"amount": 250000.0})]}


def test_a_strict_worker_refuses_a_job_without_events_and_records_nothing(gh):
    result = GovernanceHarnessJobAdapter(gh, require_events=True).process_call(_plain("RE1"))
    assert str(result["error"]).startswith("Failed to parse job") and "events" in result["error"]
    assert gh.ledger.episode_decision_exists("RE1") is False


def test_a_strict_worker_still_records_a_job_that_carries_events(gh):
    result = GovernanceHarnessJobAdapter(gh, require_events=True).process_call(_with_events("RE2"))
    assert result.get("error") is None
    assert gh.ledger.episode_decision_exists("RE2")


def test_the_default_adapter_still_takes_a_job_without_events(gh):
    result = GovernanceHarnessJobAdapter(gh).process_call(_plain("RE3"))
    assert result.get("error") is None
    assert gh.ledger.episode_decision_exists("RE3")


def test_the_production_entrypoint_builds_a_strict_adapter():
    tree = ast.parse(WORKER.read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = [n for n in ast.walk(main) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "GovernanceHarnessJobAdapter"]
    assert len(calls) == 1
    flag = [kw.value for kw in calls[0].keywords if kw.arg == "require_events"]
    assert len(flag) == 1 and isinstance(flag[0], ast.Constant) and flag[0].value is True
