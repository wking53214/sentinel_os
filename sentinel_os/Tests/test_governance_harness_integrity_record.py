"""A governed decision that carries an epistemic verdict must seal the
verdict's summary (status, failing gate, gate names) into the ledger row,
and must never seal the DIT failure text. A decision with no verdict must
land exactly as before, with no reasoning_integrity key.

Live PostgreSQL (iceberg/iceberg@localhost:5432), same convention as
test_governance_harness_outcome_obligation.py.
"""
import json

import pytest

from cassettes.mortgage_cassette import MortgageCassette
from episode import make_episode
from governance_harness import GovernanceHarness

PG_CONFIG = {
    "postgres_host": "localhost", "postgres_port": 5432,
    "postgres_db": "iceberg", "postgres_user": "iceberg",
    "postgres_password": "iceberg", "claude_api_key": None,
}

SECRET = "QUOTED-REJECTED-PROSE-DO-NOT-SEAL"


def _pg_available() -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(connect_timeout=2, host="localhost", port=5432,
                                dbname="iceberg", user="iceberg", password="iceberg")
        conn.close()
        return True
    except Exception:
        return False


requires_pg = pytest.mark.skipif(
    not _pg_available(),
    reason="live PostgreSQL (iceberg/iceberg@localhost:5432) not available",
)


class VerdictDecider:
    """Stands in for the model decider: returns a verdict with failure text
    that must NOT reach the ledger."""

    def __init__(self, with_verdict: bool):
        self.with_verdict = with_verdict

    def safety_check(self, action, details):
        out = {"safe": True, "reasoning": "stub: within bounds",
               "model_identity": "stub-model", "cost": None}
        if self.with_verdict:
            out["reasoning_integrity"] = {
                "status": "flagged",
                "parity": False,
                "failures": [f"hedging: {SECRET}"],
                "terminal_gate": None,
                "gates": ["hedging", "affective"],
                "detail": None,
            }
        return out


def _episode(eid):
    return make_episode(eid, "mortgage",
                        requested={"granted": True}, actual={"granted": True})


def _fetch_output(episode_id: str):
    import psycopg2

    conn = psycopg2.connect(host="localhost", port=5432, dbname="iceberg",
                            user="iceberg", password="iceberg")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT decision_output FROM ledger_entries "
                "WHERE record_kind='governance_decision' "
                "AND input_data->>'episode_id' = %s "
                "ORDER BY id DESC LIMIT 1", (episode_id,))
            row = cur.fetchone()
            assert row is not None, f"no ledger row for {episode_id}"
            raw = row[0]
            return json.loads(raw) if isinstance(raw, str) else raw
    finally:
        conn.close()


@requires_pg
def test_verdict_summary_lands_on_the_row_without_failure_text():
    harness = GovernanceHarness(PG_CONFIG, MortgageCassette())
    harness.decider = VerdictDecider(with_verdict=True)
    result = harness.process(_episode("E-INTEG-1"), issue_count=1)
    assert result["governed"] is True
    output = _fetch_output("E-INTEG-1")
    assert output["reasoning_integrity"] == {
        "status": "flagged",
        "terminal_gate": None,
        "gates": ["hedging", "affective"],
    }
    assert SECRET not in json.dumps(output)
    harness.shutdown()


@requires_pg
def test_decision_without_verdict_has_no_integrity_key():
    harness = GovernanceHarness(PG_CONFIG, MortgageCassette())
    harness.decider = VerdictDecider(with_verdict=False)
    result = harness.process(_episode("E-INTEG-2"), issue_count=1)
    assert result["governed"] is True
    output = _fetch_output("E-INTEG-2")
    assert output["approved"] is True
    assert "reasoning_integrity" not in output
    harness.shutdown()


@requires_pg
def test_chain_still_verifies_after_writing_a_verdict_summary():
    harness = GovernanceHarness(PG_CONFIG, MortgageCassette())
    harness.decider = VerdictDecider(with_verdict=True)
    harness.process(_episode("E-INTEG-3"), issue_count=1)
    report = harness.ledger.verify_chain(mode="strict")
    assert report["ok"] is True, report.get("violations")
    harness.shutdown()
