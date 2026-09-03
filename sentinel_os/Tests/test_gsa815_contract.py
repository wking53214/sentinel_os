"""Provider side of the sentinel_os <-> GSA-815 contract.

GSA-815 (the extracted IVR/Iceberg application) consumes this kernel by name off
PYTHONPATH -- there is no package or submodule, so nothing else catches a kernel
change that breaks GSA-815 until someone runs GSA-815 by hand. This file is that
check: the module/symbol list below is exactly what GSA-815's code imports today
(see GSA-815/DEPENDENCIES.md and its actual `from <kernel_module> import ...`
lines), and the signature checks pin the handful of call sites where a parameter
change would break GSA-815 silently rather than at import.

A red here means: a kernel change is about to break GSA-815. Land the GSA-815
side in the same change, then update this file to match.

Not exhaustive and not bilateral -- it does not run GSA-815's own suite, and it
won't notice GSA-815 starting to import a new symbol. It catches renames,
deletions, and signature drift on the known surface. `governance_contracts` is
deliberately absent: GSA-815 imports it but it is GSA-815's own missing module,
not a kernel obligation.
"""
import importlib
import inspect

import pytest

# module -> symbols GSA-815 imports from it (2026-09-03)
CONTRACT = {
    "episode": ["Episode", "EpisodeIntegrityError", "judge_episode",
                "explain_episode", "make_episode"],
    "event_v1": ["EventIntegrityError", "PROVENANCE_ESTIMATED", "PROVENANCE_VERIFIED",
                 "assemble_episode", "estimated_fields", "make_event", "episode_provenance"],
    "outcome_v1": [],
    "canonical_fields": ["NODE_ROLE_QUEUE"],
    "queue_schema": ["ClaimedJob", "EnqueueResult", "Outcome", "Reason", "TransmissionQueue"],
    "cassette_schema": ["validate_cassette"],
    "cassette_interface": ["Cassette", "CassetteConfig", "QualityResult"],
    "cassette_loader": ["CassetteLoader"],
    "cassette_capabilities": ["CAPABILITY_OUTCOME_OBLIGATION", "CAPABILITY_RL",
                              "CAPABILITY_ROUTING_TOPOLOGY", "CAPABILITY_SELF_HEALING",
                              "CAPABILITY_TELEPHONY_INGEST", "ReinforcementLearning",
                              "RoutingTopology", "SelfHealing", "TelephonyIngest"],
    "cassette_forensics": ["compute_cassette_code_hash", "compute_cassette_hash",
                           "serialize_cassette_for_ledger"],
    "governance_decider": ["GovernanceDecider"],
    "governor_injection_defense": ["build_governance_call"],
    "circuit_breaker": ["CircuitBreaker", "CircuitState"],
    "operational_resilience": ["setup_logging", "CircuitBreaker", "retry_with_backoff",
                               "HealthChecker", "export_alert_rules"],
    "array_ops": [],
    "ai_cost_tracking": ["cost_of_call"],
    "tracing": ["tracer", "mark_error"],
    "api_key_auth": ["api_key_manager", "require_api_key"],
    "governance_loop_guard": ["PipelineStateEngine"],
    "governance.friction_core": ["compute_friction"],
    "governance.ledger_postgres": ["GovernanceDecisionRecord", "PostgreSQLLedger"],
}


@pytest.mark.parametrize("module_name", sorted(CONTRACT))
def test_declared_module_imports(module_name):
    importlib.import_module(module_name)


@pytest.mark.parametrize(
    "module_name,symbol",
    [(m, s) for m, syms in sorted(CONTRACT.items()) for s in syms],
)
def test_declared_symbol_present(module_name, symbol):
    mod = importlib.import_module(module_name)
    assert hasattr(mod, symbol), f"GSA-815 imports {module_name}.{symbol}; it is gone"


def _params(func):
    return list(inspect.signature(func).parameters)


def test_judge_episode_signature():
    from episode import judge_episode
    assert _params(judge_episode) == ["cassette", "episode"], (
        "GSA-815 calls judge_episode(cassette, episode) positionally")


def test_governance_decider_safety_check_signature():
    from governance_decider import GovernanceDecider
    # ClaudeGovernanceDecider in GSA-815 subclasses this and overrides safety_check
    assert _params(GovernanceDecider.safety_check) == ["self", "action", "details"]


def test_build_governance_call_signature():
    from governor_injection_defense import build_governance_call
    assert _params(build_governance_call) == [
        "system_instruction", "caller_fields", "task_and_format"]


def test_postgresql_ledger_init_params():
    from governance.ledger_postgres import PostgreSQLLedger
    params = set(_params(PostgreSQLLedger.__init__))
    # the fail-closed runtime identity + the base connection args GSA-815 passes
    assert {"host", "port", "dbname", "user", "password",
            "runtime_user", "runtime_password"} <= params


def test_transmission_queue_is_a_class():
    from queue_schema import TransmissionQueue
    assert inspect.isclass(TransmissionQueue)
