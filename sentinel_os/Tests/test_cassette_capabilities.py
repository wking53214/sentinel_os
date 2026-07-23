"""
test_cassette_capabilities -- kernel/capability split proof suite.

Proves the manifest is the contract: required parameters are kernel +
the union of enabled capabilities; enabling a capability obligates its
methods; declaring a parameter owned by a DISABLED capability is
refused (the anti-placeholder rule); every telephony-shaped engine
refuses a non-telephony cassette at the door; and -- the point of the
whole split -- a kernel-only cassette with zero call-center surface
loads, validates, and judges episodes.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from cassette_capabilities import (
    CAPABILITIES,
    CAPABILITY_RL,
    CAPABILITY_ROUTING_TOPOLOGY,
    CAPABILITY_SELF_HEALING,
    CAPABILITY_TELEPHONY_INGEST,
    CapabilityError,
    PARAMETER_OWNERS,
    require_capabilities,
)
from cassette_interface import Cassette, CassetteConfig, QualityResult
from cassette_schema import (
    CassetteValidationError,
    KERNEL_REQUIRED_PARAMETERS,
    required_parameters_for,
    validate_cassette,
)
from cassettes.banking_cassette import BankingCassette
from cassettes.ivr_cassette import IvrCassette
from episode import judge_episode, make_episode


# ---------------------------------------------------------------------------
# The manifests as shipped.
# ---------------------------------------------------------------------------

def test_ivr_is_the_full_reference_implementation():
    assert sorted(IvrCassette.CAPABILITIES) == [
        CAPABILITY_RL, CAPABILITY_ROUTING_TOPOLOGY,
        CAPABILITY_SELF_HEALING, CAPABILITY_TELEPHONY_INGEST]


def test_banking_does_not_enable_telephony():
    assert CAPABILITY_TELEPHONY_INGEST not in BankingCassette.CAPABILITIES


def test_parameter_ownership_map():
    """Every capability-owned parameter names exactly one owner; the
    kernel set and the owned sets are disjoint."""
    assert PARAMETER_OWNERS["twilio_long_duration_threshold"] == CAPABILITY_TELEPHONY_INGEST
    assert PARAMETER_OWNERS["long_wait_threshold"] == CAPABILITY_TELEPHONY_INGEST
    assert PARAMETER_OWNERS["expected_wait_bounds"] == CAPABILITY_SELF_HEALING
    assert not set(KERNEL_REQUIRED_PARAMETERS) & set(PARAMETER_OWNERS)


def test_required_set_is_kernel_plus_enabled_union():
    assert required_parameters_for(()) == KERNEL_REQUIRED_PARAMETERS
    with_tel = required_parameters_for((CAPABILITY_TELEPHONY_INGEST,))
    assert set(with_tel) == {"governance_trigger", "long_wait_threshold",
                             "twilio_long_duration_threshold",
                             "twilio_medium_duration_threshold",
                             "twilio_short_duration_threshold"}
    everything = required_parameters_for(tuple(CAPABILITIES))
    assert set(everything) == set(with_tel) | {"expected_wait_bounds"}


# ---------------------------------------------------------------------------
# Manifest validation is fail-closed in every direction.
# ---------------------------------------------------------------------------

def test_unknown_capability_refused():
    class MysteryCassette(IvrCassette):
        CAPABILITIES = ("telepathy_ingest",)

    with pytest.raises(CassetteValidationError) as exc:
        validate_cassette(MysteryCassette())
    assert any("telepathy_ingest" in v for v in exc.value.violations)


def test_missing_manifest_refused():
    class UndeclaredCassette(IvrCassette):
        CAPABILITIES = None

    with pytest.raises(CassetteValidationError) as exc:
        validate_cassette(UndeclaredCassette())
    assert any("no CAPABILITIES manifest" in v for v in exc.value.violations)


def test_enabled_capability_requires_its_methods():
    """A manifest that promises telephony without implementing its
    judgment surface is a load-time violation, not a runtime surprise."""
    class OverpromisingCassette(BankingCassette):
        CAPABILITIES = BankingCassette.CAPABILITIES + (CAPABILITY_TELEPHONY_INGEST,)

    with pytest.raises(CassetteValidationError) as exc:
        validate_cassette(OverpromisingCassette())
    joined = "\n".join(exc.value.violations)
    assert "telephony_ingest" in joined
    assert "score_outcome_quality" in joined  # names the missing method
    assert "long_wait_threshold" in joined    # and the missing parameters


def test_owned_parameter_without_capability_refused():
    """The anti-placeholder rule: declaring a twilio_* threshold while
    telephony_ingest is disabled is refused outright -- flagged fake
    values to satisfy an unused surface cannot quietly return."""
    class PlaceholderRelapse(BankingCassette):
        def get_governance_parameters(self):
            params = super().get_governance_parameters()
            params["twilio_long_duration_threshold"] = {
                "value": 450, "type": "int", "min": 1, "max": 3600,
                "unit": "seconds", "description": "placeholder again",
                "metadata": {"approval_date": None,
                             "justification": "PLACEHOLDER",
                             "last_reviewed": None},
            }
            return params

    with pytest.raises(CassetteValidationError) as exc:
        validate_cassette(PlaceholderRelapse())
    assert any("owned by capability 'telephony_ingest'" in v
               and "placeholder" in v.lower()
               for v in exc.value.violations)


# ---------------------------------------------------------------------------
# Engine doors: every telephony-shaped pipeline refuses banking with a
# legible capability error at construction, never a KeyError mid-call.
# ---------------------------------------------------------------------------

_OFFLINE = {"postgres_host": None, "claude_api_key": None,
            "twilio_account_sid": None}


def test_sentinel_core_refuses_non_telephony_cassette():
    from sentinel_core import SentinelCore
    with pytest.raises(CapabilityError) as exc:
        SentinelCore(BankingCassette())
    msg = str(exc.value)
    assert "SentinelCore" in msg and "telephony_ingest" in msg and "banking" in msg


def test_cassette_harness_refuses_non_telephony_cassette():
    from cassette_harness import CassetteHarness
    with pytest.raises(CapabilityError) as exc:
        CassetteHarness("banking", _OFFLINE, require_cassette_binding=False)
    assert "CassetteHarness" in str(exc.value)


def test_production_harness_swap_refuses_non_telephony_cassette():
    from production_harness import IcebergProductionHarness
    harness = IcebergProductionHarness(dict(_OFFLINE),
                                       require_cassette_binding=False)
    with pytest.raises(CapabilityError) as exc:
        harness.swap_cassette(BankingCassette())
    assert "swap_cassette" in str(exc.value)
    # The refused swap must not have replaced the governing cassette.
    assert harness.cassette.get_config().domain == "ivr"


def test_twilio_ingest_refuses_non_telephony_cassette():
    from twilio_log_ingestion import TwilioLogParser
    with pytest.raises(ValueError) as exc:
        TwilioLogParser()._count_friction(
            {"duration": 250, "status": "completed"}, [],
            cassette=BankingCassette())
    assert "telephony_ingest" in str(exc.value)


def test_require_capabilities_passes_the_full_manifest():
    require_capabilities(IvrCassette(), tuple(CAPABILITIES), consumer="test")


# ---------------------------------------------------------------------------
# The unlock: a kernel-only cassette. No queues, no calls, no rewards,
# no healing -- and it loads, validates, and judges. This is the shape
# a hiring (or any non-telephony) domain starts from.
# ---------------------------------------------------------------------------

class KernelOnlyCassette(Cassette):
    """Minimal legitimate cassette: kernel contract only."""

    CAPABILITIES = ()

    _GOVERNANCE_PARAMETERS = {
        "governance_trigger": {
            "value": 1, "type": "int", "min": 0, "max": 100,
            "unit": "adverse events",
            "description": "Episodes with this many adverse events go to the governor.",
            "metadata": {"approval_date": None,
                         "justification": "strictest sensible default for a new domain",
                         "last_reviewed": None},
        },
    }

    def get_config(self):
        return CassetteConfig(name="kernel-only", version="0.1.0",
                              description="minimal non-telephony domain",
                              domain="minimal")

    def get_governance_parameters(self):
        import copy
        return copy.deepcopy(self._GOVERNANCE_PARAMETERS)

    def judge(self, episode):
        # Toy rule: full marks when the outcome matched the request;
        # otherwise judged by whether a reason is on file (validation
        # guarantees it is) and how many fields drifted.
        drifted = sum(1 for k, v in episode.requested.items()
                      if episode.actual.get(k) != v)
        score = max(0.0, 1.0 - 0.3 * drifted)
        tier = ("excellent" if score > 0.85 else
                "good" if score > 0.65 else
                "poor" if score > 0.35 else "failed")
        return QualityResult(score=score, tier=tier)

    def explain(self, episode):
        return [{"factor": "request_fulfillment",
                 "detail": "score reflects requested fields honored as asked"}]

    def validate(self):
        return True


def test_kernel_only_cassette_validates_and_judges():
    cassette = KernelOnlyCassette()
    params = validate_cassette(cassette)
    assert params.names() == ["governance_trigger"]
    assert params.capabilities == ()

    matched = make_episode("K-1", "minimal",
                           requested={"granted": True}, actual={"granted": True})
    assert judge_episode(cassette, matched).tier == "excellent"

    adjusted = make_episode(
        "K-2", "minimal",
        requested={"granted": True, "amount": 500.0},
        actual={"granted": True, "amount": 350.0},
        outcome_reasons=("amount capped by program ceiling",),
    )
    assert judge_episode(cassette, adjusted).tier == "good"


def test_kernel_only_cassette_registers_in_the_registry():
    from cassette_interface import CassetteRegistry
    registry = CassetteRegistry()
    registry.register(KernelOnlyCassette())
    assert registry.get("minimal").get_config().name == "kernel-only"
