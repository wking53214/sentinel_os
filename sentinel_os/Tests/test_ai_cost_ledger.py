"""ai_cost as an optional hashed field on governance_decision rows
(2026-07-31) -- Item 8, same contract every prior optional field
(cassette_hash, model_identity, authorized_by, ...) already uses.

Tested against a real ledger, same convention test_outcome_chain_records.py
already established for outcome_obligation.
"""

from canonical_fields import OPTIONAL_HASHED_FIELDS
from governance.ledger_postgres import GovernanceDecisionRecord


def _record(**kw):
    base = dict(
        action_type="governance_decision", node="billing_queue",
        cassette_version="ivr:standard-ivr:2.0.2",
        input_data={"call_sid": "CATEST001"},
        policy_parameters={"governance_trigger": 2},
        reasoning="AI safety check",
        output={"safe": True, "risk_level": "low", "confidence": 0.9})
    base.update(kw)
    return GovernanceDecisionRecord(**base)


_COST = {"model": "claude-opus-4-6", "input_tokens": 512, "output_tokens": 128,
        "cost_usd": 0.005760, "unpriced_reason": None,
        "pricing_source": "https://platform.claude.com/docs/en/about-claude/pricing"}


def test_ai_cost_is_an_optional_hashed_field():
    assert "ai_cost" in OPTIONAL_HASHED_FIELDS


def test_a_decision_with_no_ai_cost_hashes_as_it_always_did(test_ledger):
    """The migration guarantee every optional hashed field carries: a row
    that omits ai_cost (no API call happened) hashes byte-identically to
    one written before this field existed."""
    from cassette_schema import validate_cassette
    from cassettes.ivr_cassette import IvrCassette
    params = validate_cassette(IvrCassette())

    assert test_ledger.append_decision(_record(), governance_params=params)
    assert test_ledger.get_entries(limit=5)
    assert test_ledger.verify_chain()["ok"]


def test_a_decision_with_ai_cost_lands_in_the_row_and_the_chain(test_ledger):
    from cassette_schema import validate_cassette
    from cassettes.ivr_cassette import IvrCassette
    from Tests.conftest import PG_CONFIG
    import psycopg2
    params = validate_cassette(IvrCassette())

    assert test_ledger.append_decision(
        _record(ai_cost=_COST), governance_params=params)
    assert test_ledger.verify_chain()["ok"]

    conn = psycopg2.connect(**PG_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT ai_cost FROM ledger_entries "
                       "WHERE record_kind='governance_decision' "
                       "ORDER BY id DESC LIMIT 1")
            (stored,) = cur.fetchone()
    finally:
        conn.close()
    assert stored["cost_usd"] == _COST["cost_usd"]
    assert stored["input_tokens"] == _COST["input_tokens"]


def test_changing_the_ai_cost_changes_the_hash(test_ledger):
    """If it didn't, a decision's recorded cost could be quietly edited
    after the fact without breaking the chain."""
    from cassette_schema import validate_cassette
    from cassettes.ivr_cassette import IvrCassette
    params = validate_cassette(IvrCassette())

    cheaper = dict(_COST, cost_usd=0.001, input_tokens=50)
    test_ledger.append_decision(
        _record(ai_cost=_COST, input_data={"call_sid": "CATEST002"}),
        governance_params=params)
    test_ledger.append_decision(
        _record(ai_cost=cheaper, input_data={"call_sid": "CATEST003"}),
        governance_params=params)
    rows = test_ledger.get_entries(limit=2)
    assert len({r["current_hash"] for r in rows}) == 2
    assert test_ledger.verify_chain()["ok"]


def test_an_unpriced_model_cost_dict_still_hashes_and_verifies(test_ledger):
    """cost_usd=None inside the dict (unpriced model) is still a real,
    non-empty dict at the top level -- must hash and verify the same as
    any other ai_cost value, not be silently dropped."""
    from cassette_schema import validate_cassette
    from cassettes.ivr_cassette import IvrCassette
    params = validate_cassette(IvrCassette())

    unpriced = {"model": "claude-hypothetical-99", "input_tokens": 10,
               "output_tokens": 5, "cost_usd": None,
               "unpriced_reason": "no pricing data for model...",
               "pricing_source": None}
    assert test_ledger.append_decision(
        _record(ai_cost=unpriced), governance_params=params)
    assert test_ledger.verify_chain()["ok"]


def test_get_decisions_surfaces_ai_cost(test_ledger):
    from cassette_schema import validate_cassette
    from cassettes.ivr_cassette import IvrCassette
    params = validate_cassette(IvrCassette())

    test_ledger.append_decision(_record(ai_cost=_COST), governance_params=params)
    decisions = test_ledger.get_decisions(limit=1)
    assert decisions[0]["ai_cost"]["cost_usd"] == _COST["cost_usd"]


def test_get_decisions_ai_cost_is_none_when_absent(test_ledger):
    from cassette_schema import validate_cassette
    from cassettes.ivr_cassette import IvrCassette
    params = validate_cassette(IvrCassette())

    test_ledger.append_decision(_record(), governance_params=params)
    decisions = test_ledger.get_decisions(limit=1)
    assert decisions[0]["ai_cost"] is None
