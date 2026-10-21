"""The human-review loop, end to end, over HTTP, on a real ledger.

test_api_server_v2.py tests the two review routes against a stand-in ledger,
and test_human_selection_ledger.py tests record_human_selection against a real
ledger without HTTP. This file closes the gap between them: a governed
decision is written to a real PostgreSQL ledger, a reviewer reads it through
GET /governance/decisions/{id} and answers it through
POST /governance/decisions/{id}/human-selection, and the answer is read back
out of the ledger and checked against the real hash chain.

What is real here: the ledger, the cassette integrity check, the API key
check, and the route code. What is not: the Redis queue (the review routes do
not use it, so the app is run without its startup hook) and the per-IP rate
limit, which is replaced with a roomy one so this file does not share a bucket
with other suites in the same process.

Needs PostgreSQL at iceberg/iceberg@localhost:5432, like the other ledger
tests, and skips when it is not reachable.
"""
import secrets
import sys

import pytest

from governance.human_selection_v1 import (
    HUMAN_SELECTION_CONCUR,
    HUMAN_SELECTION_ESCALATE,
    HUMAN_SELECTION_OVERRIDE,
)
from governance.ledger_postgres import GovernanceDecisionRecord

CASSETTE_VERSION = "ivr:standard-ivr:2.0.2"
REVIEWER_NAME = "reviewer-alice"
# Generated per run so no key is ever written into the source tree.
REVIEWER_KEY = "e2e-" + secrets.token_hex(16)
GOVERNOR_REASONING = "AI safety check: risk elevated on repeat contact"
GOVERNOR_OUTPUT = {"safe": False, "risk_level": "high", "confidence": 0.87}
UNKNOWN_DECISION_ID = 999999
# The API speaks uppercase (CONCUR / OVERRIDE / ESCALATE). The ledger stores
# the lowercase constants. A request must arrive as the first and be recorded
# as the second.
API_CONCUR, API_OVERRIDE, API_ESCALATE = "CONCUR", "OVERRIDE", "ESCALATE"


def _governance_params():
    from cassette_schema import validate_cassette
    from cassettes.ivr_cassette import IvrCassette
    return validate_cassette(IvrCassette())


def _write_real_decision(ledger):
    """Write one governance_decision row and return (id, current_hash)."""
    record = GovernanceDecisionRecord(
        action_type="governance_decision", node="billing_queue",
        cassette_version=CASSETTE_VERSION,
        input_data={"call_sid": "REVIEWE2E001"},
        policy_parameters={"governance_trigger": 2},
        reasoning=GOVERNOR_REASONING,
        output=dict(GOVERNOR_OUTPUT))
    assert ledger.append_decision(record, governance_params=_governance_params())
    latest = ledger.get_decisions(limit=1)[0]
    return latest["id"], latest["current_hash"]


@pytest.fixture
def review(test_ledger, monkeypatch):
    """A client wired to the real ledger and the real API key check."""
    # api_server_v2 and api_key_auth read their environment (Redis address,
    # queue namespace, API keys) once, at first import. The other API suites
    # set that environment and then import them for the first time. If this
    # file imported them first, every later suite in the same run would be
    # bound to this file's environment, so whatever this fixture is first to
    # import it removes again afterwards.
    first_imports = [name for name in ("api_server_v2", "api_key_auth")
                     if name not in sys.modules]
    import api_key_auth
    import api_server_v2
    from fastapi.testclient import TestClient

    monkeypatch.setitem(
        api_key_auth.api_key_manager.keys, REVIEWER_KEY,
        {"name": REVIEWER_NAME, "created": "2026-10-21T00:00:00+00:00",
         "enabled": True})
    monkeypatch.setattr(
        api_key_auth, "rate_limiter",
        api_key_auth.RateLimiter(max_requests=100000, window_seconds=60.0))
    monkeypatch.setitem(
        api_server_v2.app.dependency_overrides,
        api_server_v2.get_human_review_ledger_dependency,
        lambda: test_ledger)
    # No `with`: the startup hook connects to Redis, which these routes never use.
    client = TestClient(api_server_v2.app)
    client.headers.update({"X-API-Key": REVIEWER_KEY})
    yield client
    for name in first_imports:
        sys.modules.pop(name, None)


def _selection_count(ledger):
    return len(ledger.get_human_selections())


def test_a_reviewer_reads_overrules_and_the_overrule_is_tamper_evident(
        review, test_ledger):
    decision_id, decision_hash = _write_real_decision(test_ledger)

    shown = review.get(f"/governance/decisions/{decision_id}")
    assert shown.status_code == 200
    body = shown.json()
    assert body["decision"]["current_hash"] == decision_hash
    assert body["decision"]["reasoning"] == GOVERNOR_REASONING
    assert body["decision"]["output"]["safe"] is False
    assert body["decision"]["output"]["risk_level"] == "high"
    assert body["cassette_proof"]["integrity_verified"] is True

    answered = review.post(
        f"/governance/decisions/{decision_id}/human-selection",
        json={"human_selection": API_OVERRIDE,
              "rationale": "reviewed the call recording, risk was miscalibrated"})
    assert answered.status_code == 201
    receipt = answered.json()
    assert receipt["status"] == "created"
    assert receipt["decision_hash"] == decision_hash

    stored = test_ledger.get_human_selections(decision_hash=decision_hash)
    assert len(stored) == 1
    row = stored[0]
    assert row["human_selection"] == HUMAN_SELECTION_OVERRIDE
    assert row["selected_by"] == REVIEWER_NAME
    assert row["rationale"] == "reviewed the call recording, risk was miscalibrated"
    # What the reviewer was shown is the governor's real verdict, taken from
    # the stored row, never from anything the request carried.
    assert row["recommendation_shown"]["reasoning"] == GOVERNOR_REASONING
    assert row["recommendation_shown"]["output"]["safe"] is False

    # The overrule is part of the hash chain, and the decision it answers is
    # untouched and still verifies against its cassette.
    assert test_ledger.verify_chain()["ok"]
    again = review.get(f"/governance/decisions/{decision_id}").json()
    assert again["decision"]["current_hash"] == decision_hash
    assert again["cassette_proof"]["integrity_verified"] is True


@pytest.mark.parametrize("sent, recorded", [
    (API_CONCUR, HUMAN_SELECTION_CONCUR),
    (API_OVERRIDE, HUMAN_SELECTION_OVERRIDE),
    (API_ESCALATE, HUMAN_SELECTION_ESCALATE)])
def test_each_selection_is_recorded_and_the_chain_still_verifies(
        review, test_ledger, sent, recorded):
    decision_id, decision_hash = _write_real_decision(test_ledger)
    answered = review.post(
        f"/governance/decisions/{decision_id}/human-selection",
        json={"human_selection": sent})
    assert answered.status_code == 201
    stored = test_ledger.get_human_selections(decision_hash=decision_hash)
    assert [r["human_selection"] for r in stored] == [recorded]
    assert test_ledger.verify_chain()["ok"]


def test_the_request_cannot_choose_who_the_reviewer_is(review, test_ledger):
    decision_id, _ = _write_real_decision(test_ledger)
    spoofed = review.post(
        f"/governance/decisions/{decision_id}/human-selection",
        json={"human_selection": API_CONCUR,
              "selected_by": "someone-else"})
    assert spoofed.status_code == 422
    assert _selection_count(test_ledger) == 0

    honest = review.post(
        f"/governance/decisions/{decision_id}/human-selection",
        json={"human_selection": API_CONCUR})
    assert honest.status_code == 201
    assert test_ledger.get_human_selections()[0]["selected_by"] == REVIEWER_NAME


def test_requests_without_a_valid_key_are_refused_and_write_nothing(
        review, test_ledger):
    decision_id, _ = _write_real_decision(test_ledger)
    read_path = f"/governance/decisions/{decision_id}"
    write_path = f"{read_path}/human-selection"
    payload = {"human_selection": API_CONCUR}

    assert review.get(read_path, headers={"X-API-Key": ""}).status_code == 401
    assert review.post(
        write_path, json=payload, headers={"X-API-Key": ""}).status_code == 401
    wrong = {"X-API-Key": "not-a-real-key"}
    assert review.get(read_path, headers=wrong).status_code == 403
    assert review.post(write_path, json=payload, headers=wrong).status_code == 403
    assert _selection_count(test_ledger) == 0


def test_an_unknown_decision_is_404_and_nothing_is_written(review, test_ledger):
    missing = f"/governance/decisions/{UNKNOWN_DECISION_ID}"
    assert review.get(missing).status_code == 404
    answered = review.post(
        f"{missing}/human-selection",
        json={"human_selection": API_CONCUR})
    assert answered.status_code == 404
    assert _selection_count(test_ledger) == 0


def test_an_invalid_selection_is_refused_and_nothing_is_written(
        review, test_ledger):
    decision_id, _ = _write_real_decision(test_ledger)
    answered = review.post(
        f"/governance/decisions/{decision_id}/human-selection",
        json={"human_selection": "APPROVE"})
    assert answered.status_code == 422
    assert _selection_count(test_ledger) == 0
