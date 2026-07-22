"""
test_cassette_governs_every_decision -- Items #4-#6 proof suite.

Item #3 proved the cassette DECLARES the policy. This suite proves the
RUNNING SYSTEM OBEYS it, at decision time, on real calls, with the
evidence in the ledger:

- the cassette's governance_trigger decides which calls reach the
  governor, and swapping the cassette changes the very next decision
  (nothing is cached);
- every ledger decision row carries the cassette version and the full
  policy snapshot that governed it -- and a row without a cassette
  version is REFUSED (tripwire);
- friction is one rule (friction_core.compute_friction), one threshold
  (the cassette's), on every path -- production harness, cassette
  harness, simulator;
- auto-discovery is locked down: an invalid cassette halts a default
  load_all, is skipped only in explicit debug mode, and cannot touch an
  explicit production_mode load of a different domain.

PostgreSQL-backed tests run against a live iceberg/iceberg@localhost
database and skip cleanly (with a stated reason) when it is absent.
"""

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

from cassette_schema import CassetteValidationError, validate_cassette
from cassette_interface import CassetteConfig
from cassette_loader import CassetteLoader
from cassettes.ivr_cassette import IvrCassette
from governance.friction_core import compute_friction
from production_harness import IcebergProductionHarness

from test_cassette_source_of_truth import (
    ConfigurableCassette,
    _good_params,
    scan_for_hardcodes,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------
# Infrastructure: live-PostgreSQL detection, harness configs, stub governor,
# tunable cassettes with unique versions for ledger isolation.
# --------------------------------------------------------------------------

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
    "postgres_password": "iceberg",
    "claude_api_key": None, "twilio_account_sid": None,
}
OFFLINE_CONFIG = {
    "postgres_host": None, "claude_api_key": None, "twilio_account_sid": None,
}


class StubDecider:
    """Recording governor stand-in: same contract as the Claude decider,
    fully deterministic, no network."""

    def __init__(self, safe: bool = True, reasoning: str = "stub: within declared bounds"):
        self.safe = safe
        self.reasoning = reasoning
        self.calls = []

    def safety_check(self, action, details):
        self.calls.append((action, dict(details)))
        return {
            "safe": self.safe, "risk_level": "low",
            "reasoning": self.reasoning,
            "recommendations": [], "confidence": 0.99,
        }


class TunedCassette(ConfigurableCassette):
    """A valid IVR-shaped cassette with tunable governance values and a
    UNIQUE version string, so ledger rows written by one test are
    queryable in isolation from every other test's rows."""

    def __init__(self, trigger=None, long_wait=None, bounds=None, tag=""):
        params = _good_params()
        if trigger is not None:
            params["governance_trigger"]["value"] = trigger
        if long_wait is not None:
            params["long_wait_threshold"]["value"] = float(long_wait)
        if bounds is not None:
            params["expected_wait_bounds"]["value"] = [float(bounds[0]), float(bounds[1])]
        super().__init__(params)
        self._version = f"{tag or 'tuned'}-{uuid.uuid4().hex[:10]}"

    def get_config(self) -> CassetteConfig:
        return CassetteConfig(
            name="tuned-test", version=self._version,
            description="behavioral conformance test cassette", domain="ivr",
        )


def _call(duration: int, sid: str = None):
    """A completed billing call. With the parser's 0.1/0.5/0.4 split,
    per-node waits are (0.1d, 0.5d, 0.4d) -- so against a 30s threshold:
    duration 55 -> friction 0, 70 -> 1, 250 -> 2, 400 -> 3."""
    return {
        "sid": sid or f"CA{duration}{uuid.uuid4().hex[:6]}",
        "status": "completed", "duration": duration,
        "from": "+16125551111", "to": "+billing",
    }


# --------------------------------------------------------------------------
# friction_core: the rule itself.
# --------------------------------------------------------------------------

def test_compute_friction_basic():
    """One breaching wait is one friction event; a non-breaching wait
    is none; the threshold is whatever the caller passes."""
    assert compute_friction(150, 30) == 1
    assert compute_friction(25, 30) == 0
    assert compute_friction(500, 120) == 1
    assert compute_friction(45, 120) == 0


def test_compute_friction_boundary():
    """Strictly greater-than: a wait EQUAL to the threshold is not
    friction. The cassette declares the line; the rule stands exactly
    on it."""
    assert compute_friction(30, 30) == 0
    assert compute_friction(30.0001, 30) == 1
    assert compute_friction(0, 0) == 0


# --------------------------------------------------------------------------
# Item #5: the running system obeys the cassette at decision time.
# --------------------------------------------------------------------------

def test_swap_cassette_changes_next_decision():
    """Decision-time read, not cached: swap the governing cassette and
    the VERY NEXT call is judged under the new trigger."""
    harness = IcebergProductionHarness(OFFLINE_CONFIG, cassette=TunedCassette(trigger=2), require_cassette_binding=False)
    stub = StubDecider()
    harness.claude_decider = stub

    r1 = harness.process_call(_call(250))     # friction 2 >= trigger 2
    assert r1["governed"] is True and len(stub.calls) == 1

    harness.swap_cassette(TunedCassette(trigger=3))
    r2 = harness.process_call(_call(250))     # friction 2 < trigger 3
    assert r2["governed"] is False and len(stub.calls) == 1

    harness.swap_cassette(TunedCassette(trigger=1))
    r3 = harness.process_call(_call(70))      # friction 1 >= trigger 1
    assert r3["governed"] is True and len(stub.calls) == 2


def test_friction_from_cassette_threshold():
    """The measured friction count follows the cassette's threshold:
    same call, swapped threshold, different count."""
    harness = IcebergProductionHarness(OFFLINE_CONFIG, cassette=TunedCassette(long_wait=30), require_cassette_binding=False)
    r30 = harness.process_call(_call(250))    # waits 25/125/100 vs 30 -> 2
    assert r30["friction_count"] == 2

    harness.swap_cassette(TunedCassette(long_wait=110))
    r110 = harness.process_call(_call(250))   # 125 > 110 only -> 1
    assert r110["friction_count"] == 1

    harness.swap_cassette(TunedCassette(long_wait=500))
    r500 = harness.process_call(_call(250))   # nothing breaches -> 0
    assert r500["friction_count"] == 0


def test_bad_cassette_halts_harness():
    """A cassette that cannot state its contract does not run: harness
    construction raises with the missing parameter NAMED, and no
    partially-initialized harness exists."""
    params = _good_params()
    del params["governance_trigger"]
    with pytest.raises(CassetteValidationError) as exc:
        IcebergProductionHarness(OFFLINE_CONFIG, cassette=ConfigurableCassette(params), require_cassette_binding=False)
    assert "governance_trigger" in str(exc.value)


def test_friction_unified_across_paths():
    """One rule, one threshold, every path: the raw rule, the production
    harness, the cassette harness, and the simulator all agree.
    (The Twilio ingest heuristic is Item #7 scope and deliberately not
    on the governance path.)"""
    # The rule itself.
    assert compute_friction(150, 30) == 1
    assert compute_friction(25, 30) == 0

    # Production harness: measured count equals the rule applied to the
    # parsed per-node waits.
    harness = IcebergProductionHarness(OFFLINE_CONFIG, cassette=IvrCassette(), require_cassette_binding=False)
    duration = 250
    result = harness.process_call(_call(duration))
    expected = sum(
        compute_friction(w, 30.0)
        for w in (duration * 0.1, duration * 0.5, duration * 0.4)
    )
    assert result["friction_count"] == expected == 2

    # Cassette harness: same rule, cassette threshold, no fallback.
    from cassette_harness import CassetteHarness
    boombox = CassetteHarness("ivr", OFFLINE_CONFIG, require_cassette_binding=False)
    assert boombox._count_friction({"duration": 150}, []) == 1
    assert boombox._count_friction({"duration": 25}, []) == 0

    # Simulator: same rule, cassette threshold.
    from iceberg_complete_simulator import IcebergCompleteSimulator
    from Engines.simple_rl_trainer import SimpleRLTrainer
    from observe_perceive_core import ObserveCore
    from governance.log_rotation_v1 import LogRotationManager, LocalDiskAdapter
    import tempfile
    sim = IcebergCompleteSimulator(
        LogRotationManager(LocalDiskAdapter(tempfile.mkdtemp()), seed="815"),
        SimpleRLTrainer(state_dim=10, action_dim=2, lr=0.001),
        ObserveCore(), IvrCassette(),
    )
    assert sim._wait_friction(150) == 1
    assert sim._wait_friction(25) == 0


# --------------------------------------------------------------------------
# Item #5: the ledger is the proof surface.
# --------------------------------------------------------------------------

@requires_pg
def test_governance_trigger_from_cassette_governs_decision():
    """The cassette's trigger decides who reaches the governor, and the
    ledger shows it: trigger 2 records a decision for a friction-2
    call; trigger 3 records nothing for the same call."""
    governed_cassette = TunedCassette(trigger=2, tag="gov")
    harness = IcebergProductionHarness(PG_CONFIG, cassette=governed_cassette)
    harness.claude_decider = StubDecider()
    r = harness.process_call(_call(250))
    assert r["governed"] is True
    rows = harness.ledger.get_decisions(cassette_version=r["cassette_version"])
    assert len(rows) == 1

    strict_cassette = TunedCassette(trigger=3, tag="strict")
    harness.swap_cassette(strict_cassette)
    r2 = harness.process_call(_call(250))
    assert r2["governed"] is False
    assert harness.ledger.get_decisions(cassette_version=r2["cassette_version"]) == []
    harness.shutdown()


@requires_pg
def test_every_ledger_decision_has_cassette_version():
    """No decision without its policy: every row carries a non-empty
    cassette_version AND a non-empty policy snapshot."""
    harness = IcebergProductionHarness(PG_CONFIG, cassette=TunedCassette(trigger=1, tag="ver"))
    harness.claude_decider = StubDecider()
    r = harness.process_call(_call(70))
    rows = harness.ledger.get_decisions(cassette_version=r["cassette_version"])
    assert rows, "governed call must produce a decision row"
    for row in rows:
        assert row["cassette_version"], "decision row missing cassette_version"
        assert row["policy_parameters"].get("parameters"), "decision row missing policy snapshot"
        assert row["policy_parameters"].get("schema_version"), "snapshot missing schema_version"
    harness.shutdown()


@requires_pg
def test_decision_without_cassette_version_rejected():
    """The tripwire: the ledger REFUSES a decision that cannot name the
    policy that governed it. ValueError, nothing written."""
    from governance.ledger_postgres import PostgreSQLLedger, GovernanceDecisionRecord
    ledger = PostgreSQLLedger(host="localhost", dbname="iceberg",
                              user="iceberg", password="iceberg")
    with pytest.raises(ValueError):
        ledger.append_decision(GovernanceDecisionRecord(
            action_type="governance_decision", node="billing_queue",
            cassette_version="",  # the missing policy identity
            input_data={"friction_count": 2},
            policy_parameters={"schema_version": "1.0.0", "parameters": {"x": 1}},
            reasoning="tripwire probe", output={"approved": True},
        ))
    with pytest.raises(ValueError):
        ledger.append_decision(GovernanceDecisionRecord(
            action_type="governance_decision", node="billing_queue",
            cassette_version="ivr:probe:0.0.1",
            input_data={"friction_count": 2},
            policy_parameters={},  # the missing policy snapshot
            reasoning="tripwire probe", output={"approved": True},
        ))
    ledger.close()


@requires_pg
def test_cassette_version_links_decision_to_policy():
    """Two cassette versions, two decisions, and the ledger keeps them
    straight: filtering by version returns exactly that version's
    decision, carrying that version's trigger."""
    v1 = TunedCassette(trigger=2, tag="link1")
    v2 = TunedCassette(trigger=1, tag="link2")
    v1_version = validate_cassette(v1).cassette_version
    v2_version = validate_cassette(v2).cassette_version

    harness = IcebergProductionHarness(PG_CONFIG, cassette=v1)
    harness.claude_decider = StubDecider()
    harness.process_call(_call(250))          # friction 2 governed under v1
    harness.swap_cassette(v2)
    harness.process_call(_call(70))           # friction 1 governed under v2

    rows_v1 = harness.ledger.get_decisions(cassette_version=v1_version)
    rows_v2 = harness.ledger.get_decisions(cassette_version=v2_version)
    assert len(rows_v1) == 1 and len(rows_v2) == 1
    assert rows_v1[0]["input_data"]["governance_trigger"] == 2
    assert rows_v2[0]["input_data"]["governance_trigger"] == 1
    assert rows_v1[0]["policy_parameters"]["cassette_version"] == v1_version
    assert rows_v2[0]["policy_parameters"]["cassette_version"] == v2_version
    harness.shutdown()


@requires_pg
def test_policy_snapshot_matches_cassette():
    """The snapshot in the ledger IS the cassette that governed: the
    recorded long_wait_threshold equals the declared one, and changes
    when the declaration changes."""
    c45 = TunedCassette(trigger=1, long_wait=45, tag="snap45")
    harness = IcebergProductionHarness(PG_CONFIG, cassette=c45)
    harness.claude_decider = StubDecider()
    r = harness.process_call(_call(100))      # waits 10/50/40 vs 45 -> friction 1
    rows = harness.ledger.get_decisions(cassette_version=r["cassette_version"])
    snap = rows[0]["policy_parameters"]["parameters"]
    assert snap["long_wait_threshold"]["value"] == 45.0

    c60 = TunedCassette(trigger=1, long_wait=60, tag="snap60")
    harness.swap_cassette(c60)
    r2 = harness.process_call(_call(150))     # waits 15/75/60 vs 60 -> friction 1
    rows2 = harness.ledger.get_decisions(cassette_version=r2["cassette_version"])
    assert rows2[0]["policy_parameters"]["parameters"]["long_wait_threshold"]["value"] == 60.0
    harness.shutdown()


@requires_pg
def test_healing_bounds_in_policy_snapshot():
    """The cassette's healing band rides in every decision's snapshot,
    so an auditor can see which clamp bounds were in force."""
    cassette = TunedCassette(trigger=1, bounds=(7.5, 90.0), tag="bounds")
    harness = IcebergProductionHarness(PG_CONFIG, cassette=cassette)
    harness.claude_decider = StubDecider()
    r = harness.process_call(_call(70))
    rows = harness.ledger.get_decisions(cassette_version=r["cassette_version"])
    snap = rows[0]["policy_parameters"]["parameters"]["expected_wait_bounds"]
    assert snap["value"] == [7.5, 90.0]
    harness.shutdown()


@requires_pg
def test_real_call_end_to_end_proves_cassette_governs():
    """The forensic walkthrough as one test: a real call parses,
    measures friction under the cassette threshold, crosses the
    cassette trigger, gets a governor verdict, and lands in the ledger
    with version + snapshot + reasoning + output. Then the cassette is
    swapped and the SAME call no longer reaches the governor -- and the
    ledger shows nothing for the new version."""
    cassette = TunedCassette(trigger=2, tag="e2e")
    version = validate_cassette(cassette).cassette_version
    harness = IcebergProductionHarness(PG_CONFIG, cassette=cassette)
    stub = StubDecider(safe=True, reasoning="within declared bounds; reversible")
    harness.claude_decider = stub

    result = harness.process_call(_call(250))
    assert result["friction_count"] == 2
    assert result["governed"] is True
    assert result["cassette_version"] == version

    rows = harness.ledger.get_decisions(cassette_version=version)
    assert len(rows) == 1
    row = rows[0]
    assert row["output"]["approved"] is True
    assert row["reasoning"] == "within declared bounds; reversible"
    assert row["input_data"]["friction_count"] == 2
    assert row["input_data"]["governance_trigger"] == 2
    assert row["policy_parameters"]["parameters"]["governance_trigger"]["value"] == 2

    # Swap the policy: same behavior, different verdict, no new row.
    strict = TunedCassette(trigger=5, tag="e2e-strict")
    strict_version = validate_cassette(strict).cassette_version
    harness.swap_cassette(strict)
    result2 = harness.process_call(_call(250))
    assert result2["governed"] is False
    assert len(stub.calls) == 1, "governor must not run below the new trigger"
    assert harness.ledger.get_decisions(cassette_version=strict_version) == []
    harness.shutdown()


# --------------------------------------------------------------------------
# Item #4: auto-discovery lockdown.
# --------------------------------------------------------------------------

_BAD_CASSETTE_SOURCE = '''\
"""A cassette missing its governance_trigger -- must NOT load."""
import copy
from cassette_interface import CassetteConfig
from cassettes.ivr_cassette import IvrCassette

class BadCassette(IvrCassette):
    def get_config(self) -> CassetteConfig:
        return CassetteConfig(name="bad", version="0.0.1",
                              description="broken test cassette",
                              domain="brokenco")

    def get_governance_parameters(self):
        params = copy.deepcopy(IvrCassette._GOVERNANCE_PARAMETERS)
        del params["governance_trigger"]
        return params
'''


def _make_cassette_dir(tmp_path):
    """A cassette directory holding one valid cassette (the real IVR
    file, copied verbatim) and one invalid one."""
    src = os.path.join(REPO_ROOT, "cassettes", "ivr_cassette.py")
    with open(src, "r", encoding="utf-8") as fh:
        (tmp_path / "ivr_cassette.py").write_text(fh.read())
    (tmp_path / "bad_cassette.py").write_text(_BAD_CASSETTE_SOURCE)
    return str(tmp_path)


def test_auto_discovery_fails_on_bad_cassette(tmp_path):
    """Production posture (the default): ONE invalid cassette halts the
    whole load. No partial registry ever exists."""
    loader = CassetteLoader(_make_cassette_dir(tmp_path))
    with pytest.raises(CassetteValidationError) as exc:
        loader.load_all_cassettes()
    assert "governance_trigger" in str(exc.value)


def test_auto_discovery_skips_bad_in_debug_mode(tmp_path):
    """Debug posture (explicit opt-in only): the invalid cassette is
    skipped with a warning, the valid one loads."""
    loader = CassetteLoader(_make_cassette_dir(tmp_path))
    registry = loader.load_all_cassettes(fail_on_invalid=False)
    keys = sorted(registry.cassettes)
    assert keys == ["ivr:standard-ivr"], f"only the valid cassette may load, got {keys}"


def test_production_mode_ignores_bad_neighbors(tmp_path):
    """Explicit production load: production_mode('ivr') loads exactly
    the named domain and never opens the broken neighbor file."""
    cassette_dir = _make_cassette_dir(tmp_path)
    cassette = CassetteLoader.production_mode("ivr", cassette_dir=cassette_dir)
    assert cassette.get_config().domain == "ivr"
    assert validate_cassette(cassette).float_value("long_wait_threshold") == 30.0


# --------------------------------------------------------------------------
# The backstop still bites: reintroduce a hardcode, the scanner names it.
# --------------------------------------------------------------------------

def test_hardcode_reintroduction_fails_scanner():
    """Write a governance-path-shaped file containing `if duration > 30:`
    and prove the scanner reports it with file and line."""
    probe_rel = "_tripwire_probe.py"
    probe_abs = os.path.join(REPO_ROOT, probe_rel)
    try:
        with open(probe_abs, "w", encoding="utf-8") as fh:
            fh.write("def check(duration):\n")
            fh.write("    if duration > 30:\n")
            fh.write("        return 1\n")
            fh.write("    return 0\n")
        violations = scan_for_hardcodes(REPO_ROOT, extra_files=[probe_rel])
        probe_hits = [v for v in violations if v[0] == probe_rel]
        assert probe_hits, "scanner must catch the reintroduced literal"
        rel, lineno, snippet, reason = probe_hits[0]
        assert lineno == 2 and "> 30" in snippet and reason
        # And nothing ELSE in the real codebase tripped.
        assert [v for v in violations if v[0] != probe_rel] == []
    finally:
        if os.path.exists(probe_abs):
            os.remove(probe_abs)
