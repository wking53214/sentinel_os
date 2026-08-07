"""
GovernanceHarness proof suite -- verifies the new kernel governance
harness against Wm's acceptance spec (2026-08-05), built alongside
production_harness.py, not replacing it.

Mirrors test_cassette_governs_every_decision.py's real-Postgres
convention (skip cleanly if unreachable) for the ledger-backed tests,
and uses MortgageCassette throughout rather than IVR -- this harness's
whole point is not being telephony-shaped.
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

from cassette_capabilities import CapabilityError
from cassette_schema import CassetteValidationError, validate_cassette
from cassettes.ivr_cassette import IvrCassette
from cassettes.mortgage_cassette import MortgageCassette
from episode import make_episode
from governance_harness import GovernanceHarness


def _pg_available() -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(
            host="localhost", port=5432, dbname="iceberg",
            user="iceberg", password="iceberg", connect_timeout=2,
        )
        conn.close()
        return True
    except Exception:
        return False


PG_AVAILABLE = _pg_available()
requires_pg = pytest.mark.skipif(
    not PG_AVAILABLE,
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
    no network."""

    def __init__(self, safe: bool = True, reasoning: str = "stub: within bounds"):
        self.safe = safe
        self.reasoning = reasoning
        self.calls = []

    def safety_check(self, action, details):
        self.calls.append((action, dict(details)))
        return {"safe": self.safe, "reasoning": self.reasoning,
                "model_identity": "stub-model" if self.safe else None,
                "cost": None}


def _clean_episode(eid="E-1", mismatch=False):
    if mismatch:
        return make_episode(
            eid, "mortgage",
            requested={"granted": True, "amount": 500.0},
            actual={"granted": True, "amount": 350.0},
            outcome_reasons=("amount capped by program ceiling",))
    return make_episode(eid, "mortgage",
                        requested={"granted": True}, actual={"granted": True})


# --------------------------------------------------------------------------
# Construction: accepts kernel-only (mortgage), refuses telephony (IVR).
# This is the acceptance-spec bullet that is the INVERSE of
# production_harness.py's own posture -- see test_cassette_capabilities.py's
# test_production_harness_swap_refuses_non_telephony_cassette for the
# mirror-image old behavior.
# --------------------------------------------------------------------------

def test_accepts_kernel_only_mortgage_cassette():
    harness = GovernanceHarness(OFFLINE_CONFIG, MortgageCassette(),
                                require_cassette_binding=False)
    assert harness.cassette.get_config().domain == "mortgage"


def test_refuses_telephony_cassette_at_construction():
    with pytest.raises(CapabilityError, match="telephony_ingest"):
        GovernanceHarness(OFFLINE_CONFIG, IvrCassette(),
                          require_cassette_binding=False)


def test_refuses_invalid_cassette_at_construction():
    class Broken(MortgageCassette):
        def get_governance_parameters(self):
            params = super().get_governance_parameters()
            del params["governance_trigger"]
            return params

    with pytest.raises(CassetteValidationError, match="governance_trigger"):
        GovernanceHarness(OFFLINE_CONFIG, Broken(), require_cassette_binding=False)


# --------------------------------------------------------------------------
# swap_cassette: same door, decision-time read.
# --------------------------------------------------------------------------

def test_swap_refuses_telephony_and_keeps_current_cassette():
    harness = GovernanceHarness(OFFLINE_CONFIG, MortgageCassette(),
                                require_cassette_binding=False)
    with pytest.raises(CapabilityError, match="telephony_ingest"):
        harness.swap_cassette(IvrCassette())
    assert harness.cassette.get_config().domain == "mortgage", \
        "a refused swap must not replace the governing cassette"


def test_swap_changes_the_very_next_decision():
    harness = GovernanceHarness(OFFLINE_CONFIG, MortgageCassette(),
                                require_cassette_binding=False)
    stub = StubDecider()
    harness.decider = stub

    r1 = harness.process(_clean_episode("E-swap-1"), issue_count=1)  # == trigger(1)
    assert r1["governed"] is True and len(stub.calls) == 1

    class LaxMortgage(MortgageCassette):
        _GOVERNANCE_PARAMETERS = {
            **MortgageCassette._GOVERNANCE_PARAMETERS,
            "governance_trigger": {
                **MortgageCassette._GOVERNANCE_PARAMETERS["governance_trigger"],
                "value": 5,
            },
        }
    harness.swap_cassette(LaxMortgage())
    r2 = harness.process(_clean_episode("E-swap-2"), issue_count=1)  # 1 < 5
    assert r2["governed"] is False and len(stub.calls) == 1


# --------------------------------------------------------------------------
# The governance-trigger comparison: inclusive >=, caller-supplied count.
# --------------------------------------------------------------------------

def test_issue_count_below_trigger_not_governed():
    harness = GovernanceHarness(OFFLINE_CONFIG, MortgageCassette(),
                                require_cassette_binding=False)
    stub = StubDecider()
    harness.decider = stub
    result = harness.process(_clean_episode(), issue_count=0)  # trigger is 1
    assert result["governance_required"] is False
    assert result["governed"] is False
    assert stub.calls == []
    assert result["quality"].tier in ("excellent", "good", "poor", "failed"), \
        "judgment must still run even when governance isn't required"


def test_issue_count_equal_to_trigger_is_governed_inclusive():
    harness = GovernanceHarness(OFFLINE_CONFIG, MortgageCassette(),
                                require_cassette_binding=False)
    stub = StubDecider()
    harness.decider = stub
    result = harness.process(_clean_episode(mismatch=True), issue_count=1)  # == trigger
    assert result["governance_required"] is True
    assert result["governed"] is True
    assert len(stub.calls) == 1


# --------------------------------------------------------------------------
# Fail-closed: withholds the action, does not kill the pipeline.
# --------------------------------------------------------------------------

def test_unusable_governor_answer_blocks_action_not_pipeline():
    harness = GovernanceHarness(OFFLINE_CONFIG, MortgageCassette(),
                                require_cassette_binding=False)
    harness.decider = StubDecider(safe=False, reasoning="declined")
    result = harness.process(_clean_episode(mismatch=True), issue_count=1)
    assert result["governance_blocked"] is True
    assert result["governance_approved"] is False
    # the pipeline was NOT aborted: judgment ran and is in the result.
    assert result["quality"] is not None
    assert result["episode_id"] == "E-1"


def test_real_governance_decider_fails_closed_with_no_api_key():
    """No stub -- the real kernel GovernanceDecider, no client configured.
    Proves the harness's default decider genuinely fails closed, not
    just a test double standing in for it."""
    harness = GovernanceHarness(OFFLINE_CONFIG, MortgageCassette(),
                                require_cassette_binding=False)
    result = harness.process(_clean_episode(mismatch=True), issue_count=1)
    assert result["governed"] is True
    assert result["governance_approved"] is False
    assert result["governance_blocked"] is True
    assert result["model_identity"] is None


# --------------------------------------------------------------------------
# Startup / binding: refuse to start unbound-but-required; opt-out works;
# reachable-but-wrong DB refuses exactly like absent.
# --------------------------------------------------------------------------

def test_refuses_to_start_without_ledger_when_binding_required():
    with pytest.raises(RuntimeError, match="require_cassette_binding"):
        GovernanceHarness({"postgres_host": None, "claude_api_key": None},
                          MortgageCassette())


def test_refuses_to_start_on_ledger_connection_failure_when_binding_required():
    bad_config = {
        "postgres_host": "127.0.0.1", "postgres_port": 5432,
        "postgres_db": "iceberg", "postgres_user": "iceberg",
        "postgres_password": "definitely-the-wrong-password",
        "claude_api_key": None,
    }
    with pytest.raises(RuntimeError, match="require_cassette_binding"):
        GovernanceHarness(bad_config, MortgageCassette())


def test_opt_out_still_starts_unbound_without_ledger():
    harness = GovernanceHarness({"postgres_host": None, "claude_api_key": None},
                                MortgageCassette(), require_cassette_binding=False)
    assert harness.ledger is None
    assert harness.require_cassette_binding is False


def test_shutdown_is_a_clean_noop_when_never_bound():
    harness = GovernanceHarness(OFFLINE_CONFIG, MortgageCassette(),
                                require_cassette_binding=False)
    harness.shutdown()  # must not raise


# --------------------------------------------------------------------------
# Ledger-backed: policy snapshot, cassette version, ai_cost disclosure.
# --------------------------------------------------------------------------

@requires_pg
def test_harness_binds_cassette_on_construction():
    from twin_custody import recompute_current_hash
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(host="localhost", dbname="iceberg",
                            user="iceberg", password="iceberg")
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    harness = GovernanceHarness(PG_CONFIG, MortgageCassette())
    assert harness.ledger is not None

    cur.execute("SELECT record_kind, cassette_version, current_hash, previous_hash, "
               "cassette_hash, cassette_code_hash "
               "FROM ledger_entries WHERE record_kind='cassette_binding' "
               "AND cassette_version=%s ORDER BY id DESC LIMIT 1",
               ("mortgage:mortgage-v1:1.0.1",))
    row = cur.fetchone()
    assert row is not None, "expected a cassette_binding row for mortgage"
    # The stored current_hash must actually be what the canonical entry
    # hashes to -- proves the binding row is genuinely content-bound, not
    # merely present (see twin_custody.recompute_current_hash, the same
    # recomputation a witness twin runs independently).
    assert recompute_current_hash(dict(row)) == row["current_hash"]
    harness.shutdown()
    conn.close()


@requires_pg
def test_every_governed_decision_has_cassette_version_and_snapshot():
    tag = uuid.uuid4().hex[:8]

    class TaggedMortgage(MortgageCassette):
        def get_config(self):
            cfg = super().get_config()
            from cassette_interface import CassetteConfig
            return CassetteConfig(name=f"tagged-{tag}", version="1.0.0",
                                  description=cfg.description, domain="mortgage")

    cassette = TaggedMortgage()
    version = validate_cassette(cassette).cassette_version
    harness = GovernanceHarness(PG_CONFIG, cassette)
    harness.decider = StubDecider()

    result = harness.process(_clean_episode(f"E-{tag}", mismatch=True), issue_count=1)
    assert result["governed"] is True

    rows = harness.ledger.get_decisions(cassette_version=version)
    assert len(rows) == 1
    row = rows[0]
    assert row["cassette_version"] == version
    assert row["policy_parameters"].get("parameters"), "missing policy snapshot"
    assert row["policy_parameters"]["parameters"]["governance_trigger"]["value"] == 1
    harness.shutdown()


@requires_pg
def test_ai_cost_disclosed_when_decider_reports_it():
    tag = uuid.uuid4().hex[:8]

    class TaggedMortgage(MortgageCassette):
        def get_config(self):
            cfg = super().get_config()
            from cassette_interface import CassetteConfig
            return CassetteConfig(name=f"cost-{tag}", version="1.0.0",
                                  description=cfg.description, domain="mortgage")

    class CostReportingDecider:
        def safety_check(self, action, details):
            return {"safe": True, "reasoning": "ok",
                    "model_identity": "claude-opus-4-6",
                    "cost": {"cost_usd": 0.0033, "input_tokens": 300,
                             "output_tokens": 80}}

    cassette = TaggedMortgage()
    version = validate_cassette(cassette).cassette_version
    harness = GovernanceHarness(PG_CONFIG, cassette)
    harness.decider = CostReportingDecider()

    harness.process(_clean_episode(f"E-cost-{tag}", mismatch=True), issue_count=1)
    rows = harness.ledger.get_decisions(cassette_version=version)
    assert rows[0]["ai_cost"]["cost_usd"] == 0.0033
    harness.shutdown()


@requires_pg
def test_content_reused_version_string_is_refused_at_load():
    """Same version string, materially different declared content ->
    binding conflict at construction, not detectable-later-on-demand."""
    tag = uuid.uuid4().hex[:8]

    class V1(MortgageCassette):
        def get_config(self):
            from cassette_interface import CassetteConfig
            return CassetteConfig(name=f"conflict-{tag}", version="1.0.0",
                                  description="d", domain="mortgage")

    class V2Different(V1):
        def get_governance_parameters(self):
            params = super().get_governance_parameters()
            params["governance_trigger"]["value"] = 2
            return params

    harness_a = GovernanceHarness(PG_CONFIG, V1())
    assert harness_a.ledger is not None
    with pytest.raises(ValueError, match="binding conflict"):
        GovernanceHarness(PG_CONFIG, V2Different())
    harness_a.shutdown()
