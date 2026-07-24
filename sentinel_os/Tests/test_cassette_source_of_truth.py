"""
test_cassette_source_of_truth -- Item #3 proof suite.

Proves the cassette is the single, typed, versioned source of truth for
governance parameters: the schema is declared and enforced fail-loud;
valid cassettes validate and invalid ones are rejected with the FULL
violation list; typed accessors refuse type confusion; every load path
(loader, registry, core injection) validates; the harness and simulator
read the cassette rather than any literal; and -- the backstop -- a
scanner walks the governance-path files and fails if any banned literal
has crept back in.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from cassette_schema import (
    SCHEMA_VERSION,
    CassetteValidationError,
    validate_cassette,
)
from cassette_interface import CassetteConfig, CassetteRegistry
from cassettes.ivr_cassette import IvrCassette
from cassettes.banking_cassette import BankingCassette
from sentinel_core import SentinelCore


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------
# A deliberately broken cassette: valid Cassette subclass, invalid schema.
# Each instance can omit or corrupt exactly one governance parameter so
# individual tests can target one violation.
# --------------------------------------------------------------------------
class ConfigurableCassette(IvrCassette):
    """IVR cassette whose governance declaration can be mutated per-test."""

    def __init__(self, params):
        self._params = params

    def get_config(self) -> CassetteConfig:
        return CassetteConfig(
            name="configurable-test", version="9.9.9",
            description="test cassette", domain="ivr",
        )

    def get_governance_parameters(self):
        import copy
        return copy.deepcopy(self._params)


def _good_params():
    import copy
    return copy.deepcopy(IvrCassette._GOVERNANCE_PARAMETERS)


def test_schema_version_declared():
    """The schema announces its own version, and it rides in snapshots."""
    assert SCHEMA_VERSION
    params = validate_cassette(IvrCassette())
    assert params.snapshot()["schema_version"] == SCHEMA_VERSION


def test_ivr_cassette_passes_validation():
    """The reference IVR cassette validates and reads back its declared
    values (30.0 / 2 / (4, 120))."""
    params = validate_cassette(IvrCassette())
    assert params.cassette_version == "ivr:standard-ivr:2.0.1"
    assert params.float_value("long_wait_threshold") == 30.0
    assert params.int_value("governance_trigger") == 2
    assert params.range_value("expected_wait_bounds") == (4.0, 120.0)


def test_banking_cassette_passes_validation():
    """Banking validates with its OWN different values -- two domains,
    two policies, one schema. Its parameter contract is now exactly
    what its manifest obligates: kernel + self_healing. The three
    flagged placeholder twilio_* thresholds (and long_wait_threshold)
    are GONE, because telephony_ingest is no longer enabled -- the
    whole point of capability-scoped requirements."""
    params = validate_cassette(BankingCassette())
    assert params.cassette_version == "banking:banking-v1:2.0.2"
    assert params.range_value("expected_wait_bounds") == (15.0, 300.0)
    assert params.int_value("governance_trigger") == 2
    assert sorted(params.capabilities) == [
        "rl", "routing_topology", "self_healing"]
    for placeholder in ("twilio_long_duration_threshold",
                        "twilio_medium_duration_threshold",
                        "twilio_short_duration_threshold",
                        "long_wait_threshold"):
        assert placeholder not in params.names(), \
            f"banking must no longer declare '{placeholder}'"


def test_missing_required_parameter_is_named():
    """Dropping a required parameter fails, and the error names it."""
    params = _good_params()
    del params["governance_trigger"]
    with pytest.raises(CassetteValidationError) as exc:
        validate_cassette(ConfigurableCassette(params))
    assert any("governance_trigger" in v for v in exc.value.violations)


def test_out_of_range_value_fails():
    """A declared value outside its own [min, max] is a violation."""
    params = _good_params()
    params["long_wait_threshold"]["value"] = 9999.0  # max is 600
    with pytest.raises(CassetteValidationError) as exc:
        validate_cassette(ConfigurableCassette(params))
    assert any("outside declared range" in v for v in exc.value.violations)


def test_contradictory_range_fails():
    """A range whose lo >= hi is a contradiction and is rejected."""
    params = _good_params()
    params["expected_wait_bounds"]["value"] = [120.0, 4.0]  # inverted
    with pytest.raises(CassetteValidationError) as exc:
        validate_cassette(ConfigurableCassette(params))
    assert any("range lo" in v and "hi" in v for v in exc.value.violations)


def test_missing_metadata_slot_fails():
    """A parameter missing a forensic metadata slot fails validation."""
    params = _good_params()
    del params["long_wait_threshold"]["metadata"]["justification"]
    with pytest.raises(CassetteValidationError) as exc:
        validate_cassette(ConfigurableCassette(params))
    assert any("metadata slot" in v and "justification" in v for v in exc.value.violations)


def test_all_violations_reported_together():
    """Multiple faults in one cassette are reported in ONE error, not
    one-at-a-time -- an auditor sees the whole picture at once."""
    params = _good_params()
    del params["governance_trigger"]                      # violation 1
    params["long_wait_threshold"]["value"] = 9999.0        # violation 2
    params["expected_wait_bounds"]["value"] = [120.0, 4.0] # violation 3
    with pytest.raises(CassetteValidationError) as exc:
        validate_cassette(ConfigurableCassette(params))
    assert len(exc.value.violations) >= 3


def test_typed_accessors_enforce_types():
    """Asking for the wrong type is a contract violation, not a silent
    coercion."""
    params = validate_cassette(IvrCassette())
    with pytest.raises(TypeError):
        params.int_value("long_wait_threshold")   # it's a float
    with pytest.raises(TypeError):
        params.float_value("governance_trigger")  # it's an int
    with pytest.raises(TypeError):
        params.float_value("expected_wait_bounds")  # it's a range


def test_registry_validates_fail_loud():
    """Registering an invalid cassette raises CassetteValidationError --
    registration is a load path and admits nothing unvalidated."""
    params = _good_params()
    del params["expected_wait_bounds"]
    registry = CassetteRegistry()
    with pytest.raises(CassetteValidationError):
        registry.register(ConfigurableCassette(params))


def test_sentinel_core_rejects_invalid_cassette():
    """Injecting an invalid cassette straight into the core still gets
    validated -- injection is a load path too."""
    params = _good_params()
    params["long_wait_threshold"]["value"] = -5.0  # must be > 0
    with pytest.raises(CassetteValidationError):
        SentinelCore(ConfigurableCassette(params))


def test_harness_friction_threshold_from_cassette():
    """The production harness reads its friction threshold from the
    cassette AT DECISION TIME: swap the cassette, and the very next
    call's friction observation changes.

    Observed through emotion: long-wait friction events feed the
    emotional model, so a threshold no wait can breach must leave
    frustration at exactly 0.0."""
    from production_harness import IcebergProductionHarness

    harness = IcebergProductionHarness(
        {"postgres_host": None, "claude_api_key": None, "twilio_account_sid": None},
        require_cassette_binding=False,
    )
    call = {"sid": "CAX", "status": "completed", "duration": 400,
            "from": "+16125551111", "to": "+billing"}
    r_default = harness.process_call(call)
    assert r_default["emotion_frustration"] > 0.0, \
        "with the declared 30s threshold, a 400s call must show wait friction"

    # Swap to a cassette declaring a 500s threshold: no wait in a 400s
    # call can breach it, so friction-driven frustration must be zero.
    params = _good_params()
    params["long_wait_threshold"]["value"] = 500.0
    harness.swap_cassette(ConfigurableCassette(params))
    r_swapped = harness.process_call(
        {"sid": "CAX2", "status": "completed", "duration": 400,
         "from": "+16125552222", "to": "+billing"})
    assert r_swapped["emotion_frustration"] == 0.0
    assert r_swapped["cassette_version"] == "ivr:configurable-test:9.9.9"


def test_governance_trigger_inclusive_semantics():
    """friction_count == trigger IS governed (inclusive >=). Proven at
    the harness with a recording stub decider so no network is needed."""
    from production_harness import IcebergProductionHarness

    class StubDecider:
        def __init__(self):
            self.calls = 0
        def safety_check(self, action, details):
            self.calls += 1
            return {"safe": True, "risk_level": "low", "reasoning": "stub",
                    "recommendations": [], "confidence": 0.99}

    harness = IcebergProductionHarness(
        {"postgres_host": None, "claude_api_key": None, "twilio_account_sid": None},
        require_cassette_binding=False,
    )
    stub = StubDecider()
    harness.claude_decider = stub

    # A quiet call stays under the trigger: not governed.
    r_quiet = harness.process_call(
        {"sid": "CAY0", "status": "completed", "duration": 55,
         "from": "+16125551111", "to": "+billing"})
    assert r_quiet["governed"] is False
    assert stub.calls == 0

    # A 400s call reaches friction_count == trigger territory:
    # inclusive >= means it IS governed and the governor runs.
    r_hot = harness.process_call(
        {"sid": "CAY1", "status": "completed", "duration": 400,
         "from": "+16125551111", "to": "+billing"})
    assert r_hot["governed"] is True
    assert r_hot["friction_count"] >= r_hot["governance_trigger"]
    assert stub.calls == 1, "friction_count >= trigger must be governed (inclusive)"


def test_healing_bounds_from_cassette_in_simulator():
    """The simulator's heal band is the cassette's expected_wait_bounds,
    read through validation -- not a literal in the simulator."""
    from iceberg_complete_simulator import IcebergCompleteSimulator
    from Engines.simple_rl_trainer import SimpleRLTrainer
    from observe_perceive_core import ObserveCore
    from governance.log_rotation_v1 import LogRotationManager, LocalDiskAdapter
    import tempfile

    sim = IcebergCompleteSimulator(
        LogRotationManager(LocalDiskAdapter(tempfile.mkdtemp()), seed="815"),
        SimpleRLTrainer(state_dim=10, action_dim=2, lr=0.001),
        ObserveCore(),
        IvrCassette(),
    )
    band = sim._heal_band()
    assert (band.lo, band.hi) == (4.0, 120.0)


# --------------------------------------------------------------------------
# The scanner: a backstop against literal reintroduction. Importable so
# the Item #4-6 suite can reuse it to prove a fresh hardcode is caught.
# --------------------------------------------------------------------------

# Files on the governance path where a bare threshold/bound literal would
# be a second source of truth competing with the cassette.
_GOVERNANCE_PATH_FILES = {
    "production_harness.py",
    "cassette_harness.py",
    "sentinel_core.py",
    "iceberg_complete_simulator.py",
    "load_test.py",
    "claude_governance_api.py",
    os.path.join("governance", "self_heal_v1.py"),
    os.path.join("governance", "recommend_v1.py"),
}

# Whole files exempt from the scan, each for a stated reason.
_EXEMPT_FILES = {
    # Cassettes ARE the declaration site -- literals belong here.
    os.path.join("cassettes", "ivr_cassette.py"),
    os.path.join("cassettes", "banking_cassette.py"),
    os.path.join("cassettes", "__init__.py"),
    # The proof suites legitimately name numbers to assert on.
    os.path.join("Tests", "test_cassette_source_of_truth.py"),
    os.path.join("Tests", "test_cassette_governs_every_decision.py"),
    # friction_core holds the RULE but no threshold of its own.
    os.path.join("governance", "friction_core.py"),
    # drift_core_v1 is sealed v1 (statistical policy), flagged for v2.
    os.path.join("governance", "drift_core_v1.py"),
    # Item #7 scope: ingest heuristics unify with friction_core when the
    # ingest path joins the production flow.
    "twilio_log_ingestion.py",
    # Item #7 scope: observe_perceive_core long_wait threshold.
    "observe_perceive_core.py",
}

# (compiled pattern, human description). Applied to every governance-path
# file except exemptions.
_BANNED_PATTERNS = [
    (re.compile(r"HealBand\(\s*(?:lo\s*=\s*)?\d"), "HealBand(...) built from a numeric literal"),
    (re.compile(r"friction_count\s*>\s*[1-9]"), "friction_count gated on a numeric literal"),
    (re.compile(r"friction_count\s*>=\s*[1-9]"), "friction_count gated on a numeric literal (inclusive)"),
    (re.compile(r'["\']band_(?:lo|hi)["\']\s*:\s*\d'), "band_lo/band_hi literal in a dict"),
    (re.compile(r'\.get\(\s*["\']long_wait_threshold["\']\s*,\s*\d'), "long_wait_threshold .get() with a literal fallback"),
    (re.compile(r"\blo_bound\s*=\s*\d|\bhi_bound\s*=\s*\d"), "lo_bound/hi_bound literal assignment"),
    (re.compile(r"\bgovernance_trigger\s*=\s*\d"), "governance_trigger literal assignment"),
    (re.compile(r">\s*30(?:\.0)?\b"), "bare > 30 wait-threshold literal"),
]


def scan_for_hardcodes(root: str = REPO_ROOT, extra_files=None):
    """Walk the governance-path files and return a list of
    (file, line_number, snippet, reason) for every banned literal found.

    Only the governance-path file set is scanned, and exempt files are
    skipped -- the goal is "no second source of truth on the paths that
    make decisions", not "no number anywhere". A module that joins the
    governance path joins _GOVERNANCE_PATH_FILES in the same change.
    extra_files lets a caller put additional paths under the same scan
    (used by the reintroduction test to prove detection works).
    """
    violations = []
    for rel in sorted(set(_GOVERNANCE_PATH_FILES) | set(extra_files or [])):
        if rel in _EXEMPT_FILES:
            continue
        path = os.path.join(root, rel)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                code = line.split("#", 1)[0]  # ignore comments
                if not code.strip():
                    continue
                for pattern, reason in _BANNED_PATTERNS:
                    if pattern.search(code):
                        violations.append((rel, lineno, line.rstrip(), reason))
    return violations


def test_no_governance_hardcodes_in_codebase():
    """The backstop: no banned literal on any governance-path file.

    If this fails, a threshold or bound has been written as a literal
    somewhere it would compete with the cassette -- move it into the
    cassette declaration instead.
    """
    violations = scan_for_hardcodes(REPO_ROOT)
    assert not violations, "Governance hardcodes reintroduced:\n" + "\n".join(
        f"  {f}:{ln}  [{reason}]  {snippet.strip()}" for f, ln, snippet, reason in violations
    )
