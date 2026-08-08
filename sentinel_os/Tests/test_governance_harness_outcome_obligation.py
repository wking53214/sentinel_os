"""GovernanceHarness._outcome_obligation_declaration (2026-08-08): the
upstream gap this module's own docstring already flagged as "real and
worth having... outside the nine-bullet spec... flagged as a follow-on,
not built silently." Confirmed missing while scoping the twin/cohort-
equity pipeline: GovernanceHarness._write_decision never set
outcome_obligation, so a mortgage decision never actually declared the
maturation rule the twin's /obligations/derive endpoint (and
everything downstream of it -- the whole C2 cohort-equity sweep) reads
from `outcome_obligation IS NOT NULL`. Without this, that entire
pipeline is unreachable from the real production path no matter how
much twin/sweep infrastructure exists.

Deliberately fail-loud, not fail-soft: unlike production_harness.py's
older _outcome_obligation_declaration (try/except + warn, return
None), this version lets a broken maturation rule raise -- matching
GovernanceHarness's own established posture everywhere else in this
file (construction, swap_cassette, cassette binding all refuse a
broken cassette rather than silently degrading).

Tested against real MortgageCassette (the only cassette that declares
CAPABILITY_OUTCOME_OBLIGATION today), same convention
test_governance_harness.py already established.
"""
import pytest

from cassettes.banking_cassette import BankingCassette
from cassettes.mortgage_cassette import MortgageCassette
from episode import make_episode
from governance_harness import GovernanceHarness

OFFLINE_CONFIG = {"postgres_host": None, "claude_api_key": None}
PG_CONFIG = {
    "postgres_host": "localhost", "postgres_port": 5432,
    "postgres_db": "iceberg", "postgres_user": "iceberg",
    "postgres_password": "iceberg", "claude_api_key": None,
}


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


class StubDecider:
    def __init__(self, safe: bool = True, reasoning: str = "stub: within bounds"):
        self.safe = safe
        self.reasoning = reasoning

    def safety_check(self, action, details):
        return {"safe": self.safe, "reasoning": self.reasoning,
                "model_identity": "stub-model" if self.safe else None,
                "cost": None}


def _mortgage_episode(eid="E-OBL-1"):
    return make_episode(eid, "mortgage",
                        requested={"granted": True}, actual={"granted": True})


def _banking_episode(eid="E-OBL-BANK-1"):
    return make_episode(eid, "banking",
                        requested={"granted": True},
                        actual={"granted": True, "resolved": True},
                        attributes={"duration": 120.0, "friction_count": 0})


def test_capability_declaring_cassette_returns_the_real_declaration():
    harness = GovernanceHarness(OFFLINE_CONFIG, MortgageCassette(),
                                require_cassette_binding=False)
    assert harness._outcome_obligation_declaration() == "loan_performance@3y"


def test_cassette_without_the_capability_returns_none():
    """BankingCassette: a real, existing, non-telephony cassette that
    genuinely does not declare CAPABILITY_OUTCOME_OBLIGATION (see
    cassettes/banking_cassette.py's own CAPABILITIES tuple) -- not a
    hand-stripped MortgageCassette, which cannot represent this case
    at all (its own validate() unconditionally requires
    outcome_horizon_days to agree with get_maturation_rule(), so a
    capability-stripped-but-otherwise-unchanged MortgageCassette is
    simply an invalid cassette, not a "no capability" one)."""
    harness = GovernanceHarness(OFFLINE_CONFIG, BankingCassette(),
                                require_cassette_binding=False)
    assert harness._outcome_obligation_declaration() is None


def test_a_broken_maturation_rule_fails_loud_at_construction():
    """The deliberate divergence from production_harness.py's older,
    warn-and-return-None pattern: a cassette that declares the
    capability but cannot honor it is a broken cassette. Turns out
    this repo's own cassette_schema.validate_cassette (called at
    GovernanceHarness construction) already calls get_maturation_rule
    as part of its own validation -- so a broken rule never reaches
    _outcome_obligation_declaration at all; the cassette is refused
    before the harness exists. Stronger proof than the originally
    imagined "raises when the harness calls it" -- there is no window
    where a broken cassette could ever silently record a missing
    obligation, because it can never be constructed into a harness in
    the first place."""
    class _BrokenMaturationMortgage(MortgageCassette):
        def get_maturation_rule(self):
            raise RuntimeError("maturation rule intentionally broken for this test")

    from cassette_schema import CassetteValidationError
    with pytest.raises(CassetteValidationError, match="intentionally broken"):
        GovernanceHarness(OFFLINE_CONFIG, _BrokenMaturationMortgage(),
                          require_cassette_binding=False)


def _fetch_outcome_obligation(episode_id: str) -> str:
    """get_decisions() doesn't project outcome_obligation -- same raw-SQL
    convention Tests/test_outcome_chain_records.py already established
    for reading this specific column back."""
    import psycopg2

    conn = psycopg2.connect(host="localhost", port=5432, dbname="iceberg",
                            user="iceberg", password="iceberg")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT outcome_obligation FROM ledger_entries "
                "WHERE record_kind='governance_decision' "
                "AND input_data->>'episode_id' = %s "
                "ORDER BY id DESC LIMIT 1", (episode_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None, f"no governance_decision row found for {episode_id!r}"
    return row[0]


@requires_pg
def test_the_declaration_actually_lands_on_the_ledger_row():
    """Not just the helper in isolation -- a real governed decision,
    through process(), must actually carry the real declaration on the
    ledger row the twin's derivation endpoint will one day read."""
    harness = GovernanceHarness(PG_CONFIG, MortgageCassette())
    harness.decider = StubDecider(safe=True)
    result = harness.process(_mortgage_episode("E-OBL-PG-1"), issue_count=1)
    assert result["governed"] is True
    assert _fetch_outcome_obligation("E-OBL-PG-1") == "loan_performance@3y"
    harness.shutdown()


@requires_pg
def test_no_capability_cassette_lands_null_on_the_ledger_row():
    harness = GovernanceHarness(PG_CONFIG, BankingCassette())
    harness.decider = StubDecider(safe=True)
    # BankingCassette's own governance_trigger is 2 (friction events);
    # issue_count is caller-supplied regardless of the episode's own
    # content (see governance_harness.py's module docstring), so 2 is
    # what actually trips governance_required here.
    result = harness.process(_banking_episode("E-OBL-PG-2"), issue_count=2)
    assert result["governed"] is True
    assert _fetch_outcome_obligation("E-OBL-PG-2") is None
    harness.shutdown()
