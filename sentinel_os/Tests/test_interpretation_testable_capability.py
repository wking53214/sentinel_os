"""
test_interpretation_testable_capability -- proves the resolve_scenario
capability seam (cassette_capabilities.CAPABILITY_INTERPRETATION_TESTABLE):
opt-in by default, but mandatory -- refused at load, same
anti-placeholder-refusal mechanism as an owned-but-unenabled parameter
-- for any cassette that declares outcome_obligation or a non-empty
REGULATORY_BINDINGS (cassette_interface.Cassette).
"""

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from cassette_capabilities import (
    CAPABILITY_INTERPRETATION_TESTABLE,
    CAPABILITY_OUTCOME_OBLIGATION,
)
from cassette_interface import Cassette, CassetteConfig, QualityResult
from cassette_schema import CassetteValidationError, validate_cassette
from cassettes.banking_cassette import BankingCassette
from cassettes.mortgage_cassette import MortgageCassette


# ---------------------------------------------------------------------------
# Opt-in default -- most domains have neither trigger and never have to
# declare the capability.
# ---------------------------------------------------------------------------

def test_capability_not_required_by_default():
    """Banking enables neither outcome_obligation nor a regulatory
    binding, and does not declare interpretation_testable -- validates
    clean, because the capability is opt-in."""
    assert CAPABILITY_INTERPRETATION_TESTABLE not in BankingCassette.CAPABILITIES
    params = validate_cassette(BankingCassette())
    assert CAPABILITY_INTERPRETATION_TESTABLE not in params.capabilities


def test_regulatory_bindings_defaults_to_empty_tuple():
    """The optional marker itself defaults to empty on the kernel
    class and every cassette that doesn't override it."""
    assert Cassette.REGULATORY_BINDINGS == ()
    assert BankingCassette.REGULATORY_BINDINGS == ()


# ---------------------------------------------------------------------------
# Declared -> testable: mortgage is the one cassette this rule binds
# today, and it declares the capability.
# ---------------------------------------------------------------------------

def test_mortgage_declares_the_capability():
    assert CAPABILITY_INTERPRETATION_TESTABLE in MortgageCassette.CAPABILITIES
    params = validate_cassette(MortgageCassette())
    assert CAPABILITY_INTERPRETATION_TESTABLE in params.capabilities


# ---------------------------------------------------------------------------
# The exception, in both directions -- outcome_obligation and
# regulatory-cassette binding are independent triggers.
# ---------------------------------------------------------------------------

def test_outcome_obligation_without_capability_refused_at_load():
    class UnderdeclaredMortgage(MortgageCassette):
        CAPABILITIES = (CAPABILITY_OUTCOME_OBLIGATION,)  # drops interpretation_testable

    with pytest.raises(CassetteValidationError) as exc:
        validate_cassette(UnderdeclaredMortgage())
    joined = "\n".join(exc.value.violations)
    assert "outcome_obligation" in joined
    assert CAPABILITY_INTERPRETATION_TESTABLE in joined


class _MinimalKernelCassette(Cassette):
    """A bare kernel-only cassette used to isolate the regulatory-
    binding trigger from outcome_obligation entirely."""

    CAPABILITIES = ()

    _GOVERNANCE_PARAMETERS = {
        "governance_trigger": {
            "value": 1, "type": "int", "min": 0, "max": 100,
            "unit": "adverse events",
            "description": "test parameter.",
            "metadata": {"approval_date": None, "justification": "test",
                         "last_reviewed": None},
        },
    }

    def get_config(self):
        return CassetteConfig(name="bound-untestable", version="0.1.0",
                              description="test", domain="test_bound")

    def get_governance_parameters(self):
        return copy.deepcopy(self._GOVERNANCE_PARAMETERS)

    def judge(self, episode):
        return QualityResult(score=1.0, tier="excellent")

    def explain(self, episode):
        return []

    def validate(self):
        return True


def test_regulatory_binding_without_capability_refused_at_load():
    """A kernel-only cassette that declares a regulatory binding but no
    outcome_obligation is refused the same way -- proves the two
    triggers are independent, not outcome_obligation in disguise."""
    class BoundButUntestable(_MinimalKernelCassette):
        REGULATORY_BINDINGS = ("cfpb-ecoa-reg-b",)

    with pytest.raises(CassetteValidationError) as exc:
        validate_cassette(BoundButUntestable())
    joined = "\n".join(exc.value.violations)
    assert "regulatory-cassette binding" in joined
    assert "cfpb-ecoa-reg-b" in joined
    assert CAPABILITY_INTERPRETATION_TESTABLE in joined


def test_neither_trigger_present_regulatory_binding_alone_is_fine():
    """Sanity check on the negative: a cassette with a REGULATORY_
    BINDINGS attribute that is empty is not held to the rule -- only a
    non-empty binding triggers it."""
    class UnboundKernelCassette(_MinimalKernelCassette):
        REGULATORY_BINDINGS = ()

    params = validate_cassette(UnboundKernelCassette())
    assert CAPABILITY_INTERPRETATION_TESTABLE not in params.capabilities


def test_both_triggers_together_produce_one_combined_violation():
    class DoublyObligated(MortgageCassette):
        CAPABILITIES = (CAPABILITY_OUTCOME_OBLIGATION,)
        REGULATORY_BINDINGS = ("cfpb-ecoa-reg-b",)

    with pytest.raises(CassetteValidationError) as exc:
        validate_cassette(DoublyObligated())
    joined = "\n".join(exc.value.violations)
    assert "outcome_obligation" in joined
    assert "regulatory-cassette binding" in joined


def test_enabling_the_capability_without_resolve_scenario_is_refused():
    """Same method-contract enforcement every other capability gets:
    declaring interpretation_testable without implementing
    resolve_scenario is a load-time violation, not a runtime
    surprise."""
    class OverpromisingBanking(BankingCassette):
        CAPABILITIES = BankingCassette.CAPABILITIES + (CAPABILITY_INTERPRETATION_TESTABLE,)

    with pytest.raises(CassetteValidationError) as exc:
        validate_cassette(OverpromisingBanking())
    joined = "\n".join(exc.value.violations)
    assert "interpretation_testable" in joined
    assert "resolve_scenario" in joined
