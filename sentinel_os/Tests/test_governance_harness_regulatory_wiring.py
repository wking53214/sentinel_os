"""Bias/fair-lending screening wired into the live mortgage decision
path (2026-08-07) -- GovernanceHarness now accepts an optional
regulatory_deck; sentinel_worker.py's main() inserts the CFPB Reg B
reference lens, LIVE mode, flag-only (block_on_placeholder stays at
its default False), covering dimension 1 (declared proxy /
prohibited-basis input screening) and adverse-action reason
specificity -- the two dimensions that need no extra infrastructure.

Tested against a real ledger, same convention test_governance_harness.py
already established (StubDecider, OFFLINE_CONFIG/PG_CONFIG,
requires_pg). Proves real divergence, not a happy-path no-op: a
proxy-named input variable and a textbook-boilerplate adverse-action
reason actually produce findings, actually get disclosed to the
ledger, and actually do NOT block the decision -- while a clean
episode produces none of that noise.
"""
import pytest

from cassettes.mortgage_cassette import MortgageCassette
from episode import make_episode
from governance_harness import GovernanceHarness
from regulatory_cassette_interface import MODE_LIVE
from regulatory_cassettes.cfpb_reg_b import CFPBRegBLens
from regulatory_deck import RegulatoryDeck
from Tests.conftest import PG_CONFIG as RAW_PG_CONFIG


def _pg_available() -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(connect_timeout=2, **RAW_PG_CONFIG)
        conn.close()
        return True
    except Exception:
        return False


requires_pg = pytest.mark.skipif(
    not _pg_available(),
    reason="live PostgreSQL (iceberg/iceberg@localhost:5432) not available",
)

PG_CONFIG = {
    "postgres_host": "localhost", "postgres_port": 5432,
    "postgres_db": "iceberg", "postgres_user": "iceberg",
    "postgres_password": "iceberg", "claude_api_key": None,
}
OFFLINE_CONFIG = {"postgres_host": None, "claude_api_key": None}


class StubDecider:
    """Same contract as GovernanceDecider.safety_check, deterministic,
    no network -- mirrors test_governance_harness.py's own StubDecider."""

    def __init__(self, safe: bool = True, reasoning: str = "stub: within bounds"):
        self.safe = safe
        self.reasoning = reasoning
        self.calls = []

    def safety_check(self, action, details):
        self.calls.append((action, dict(details)))
        return {"safe": self.safe, "reasoning": self.reasoning,
                "model_identity": "stub-model" if self.safe else None,
                "cost": None}


def _proxy_and_boilerplate_episode(eid="E-bias-1"):
    """A real mortgage-shaped episode carrying a declared proxy
    variable (borrower_last_name -- BISG-style name proxy, per
    CFPB_REG_B_PROFILE.proxy_variables) AND a textbook-boilerplate
    adverse-action reason (generic under Reg B). Two independent,
    real divergences from a clean episode, not a synthetic edge case."""
    return make_episode(
        eid, "mortgage",
        requested={"granted": True, "amount": 500.0},
        actual={"granted": True, "amount": 350.0},
        outcome_reasons=("does not meet our minimum credit standards",),
        attributes={"borrower_last_name": "Alvarez"},
    )


def _clean_episode(eid="E-clean-1"):
    return make_episode(
        eid, "mortgage",
        requested={"granted": True, "amount": 500.0},
        actual={"granted": True, "amount": 350.0},
        outcome_reasons=("credit score 574 is below the 620 minimum "
                         "required for the requested loan amount",),
    )


def _live_deck(ledger):
    deck = RegulatoryDeck(ledger, default_authorized_by="test:cfpb-live")
    deck.insert(CFPBRegBLens(), MODE_LIVE, inserted_by="test:cfpb-live")
    return deck


def test_no_deck_is_byte_identical_to_before():
    """regulatory_deck defaults to None -- the new key exists but is
    empty, and nothing about judgment changes for a caller who never
    opts in (every other GovernanceHarness user: tests, any future
    non-mortgage cassette on this harness)."""
    harness = GovernanceHarness(OFFLINE_CONFIG, MortgageCassette(),
                                require_cassette_binding=False)
    result = harness.process(_proxy_and_boilerplate_episode(), issue_count=0)
    assert result["regulatory_findings"] == []


@requires_pg
def test_proxy_variable_and_boilerplate_reason_produce_real_findings():
    harness = GovernanceHarness(PG_CONFIG, MortgageCassette())
    harness.decider = StubDecider(safe=True)
    harness.regulatory_deck = _live_deck(harness.ledger)

    result = harness.process(_proxy_and_boilerplate_episode(), issue_count=0)

    classifications = {f["classification"] for f in result["regulatory_findings"]}
    assert "proxy_variable" in classifications, result["regulatory_findings"]
    assert "generic" in classifications, result["regulatory_findings"]
    harness.shutdown()


@requires_pg
def test_findings_are_flag_only_decision_proceeds_untouched():
    """The whole point of block_on_placeholder=False: real findings on
    a real episode, and the AI decider's own answer still governs
    approval -- regulatory screening rides NEXT TO the judgment, never
    inside it."""
    harness = GovernanceHarness(PG_CONFIG, MortgageCassette())
    harness.decider = StubDecider(safe=True, reasoning="stub: approved")
    harness.regulatory_deck = _live_deck(harness.ledger)

    result = harness.process(_proxy_and_boilerplate_episode(), issue_count=1)

    assert result["regulatory_findings"], "expected real findings on this episode"
    assert all(f["action"] == "flag" for f in result["regulatory_findings"]), \
        result["regulatory_findings"]
    assert result["governance_approved"] is True
    assert result["governance_blocked"] is False
    harness.shutdown()


@requires_pg
def test_findings_are_disclosed_to_the_ledger():
    """Episode ID must be unique per run, not hardcoded: the primary
    ledger is a real, persistent, append-only Postgres database, never
    wiped between test runs by design -- a fixed subject ID means every
    repeat run of this file accumulates another regulatory_disclosure
    row under the same subject, and the exact-count assertion below
    breaks the moment the suite has run more than once against this
    database (caught 2026-08-08 after several same-day runs)."""
    import uuid
    harness = GovernanceHarness(PG_CONFIG, MortgageCassette())
    harness.decider = StubDecider(safe=True)
    harness.regulatory_deck = _live_deck(harness.ledger)

    episode = _proxy_and_boilerplate_episode(f"E-bias-disclosed-{uuid.uuid4().hex[:8]}")
    result = harness.process(episode, issue_count=0)
    assert result["regulatory_findings"]

    import psycopg2
    conn = psycopg2.connect(**RAW_PG_CONFIG)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT data FROM ledger_entries
            WHERE record_kind = 'regulatory_disclosure'
              AND data->>'subject' = %s
        """, (episode.episode_id,))
        rows = cur.fetchall()
    finally:
        conn.close()
    assert len(rows) == len(result["regulatory_findings"]), \
        "every regulatory finding on a real divergence must be disclosed"
    harness.shutdown()


@requires_pg
def test_clean_episode_produces_no_regulatory_findings():
    """Not indiscriminate flagging: a normal, well-specified mortgage
    episode with no proxy-named inputs produces nothing."""
    harness = GovernanceHarness(PG_CONFIG, MortgageCassette())
    harness.decider = StubDecider(safe=True)
    harness.regulatory_deck = _live_deck(harness.ledger)

    result = harness.process(_clean_episode(), issue_count=0)
    assert result["regulatory_findings"] == []
    harness.shutdown()


@requires_pg
def test_lens_insertion_is_itself_on_the_record():
    harness = GovernanceHarness(PG_CONFIG, MortgageCassette())
    deck = _live_deck(harness.ledger)
    active = deck.active()
    assert len(active) == 1
    assert active[0]["mode"] == MODE_LIVE
    assert "reg-b" in active[0]["identity"] or "cfpb" in active[0]["identity"]
    harness.shutdown()
