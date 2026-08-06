"""Contract cassette layer -- pure logic, no Postgres needed.

Covers term typing/validation, the observer-only posture rule, the
reserved identity slot, and the registry's refusal to guess.
"""
import pytest

import cassette_forensics
from contract_cassette import (
    CONTRACT_DOMAIN,
    RECIPIENT_CLASS_AFFILIATE,
    RECIPIENT_CLASS_SUBCONTRACTOR,
    TERM_EGRESS_PROHIBITED,
    TERM_EGRESS_REQUIRES_APPROVAL,
    TERM_PURPOSE_RESTRICTION,
    TERM_RETENTION_MAX_DAYS,
    ContractCassette,
    ContractCassetteRegistry,
    ContractTerm,
    ContractValidationError,
    contract_cassette_version_of,
    validate_term,
)
from contract_cassettes.reference_dpa import ReferenceDPAContract
from regulatory_cassette_interface import (
    MODE_LIVE,
    MODE_OBSERVER,
    RegulatoryCassetteRegistry,
    regulatory_cassette_version_of,
    validate_regulatory_cassette,
)
from regulatory_cassettes.cfpb_reg_b import CFPBRegBLens


def _contract(counterparty="acme-bank", version="1.0.0", terms=None,
              modes=(MODE_OBSERVER,)):
    """Build a throwaway contract lens. Defined as a factory rather than
    a module-level fixture class so a test can vary one thing without
    the others drifting."""

    class _Lens(ContractCassette):
        MODES = tuple(modes)

        def get_counterparty_id(self):
            return counterparty

        def get_contract_reference(self):
            return f"DPA-{counterparty}-0001"

        def get_contract_version(self):
            return version

        def get_terms(self):
            if terms is not None:
                return tuple(terms)
            return (ContractTerm(TERM_RETENTION_MAX_DAYS, {"max_days": 90}),)

    return _Lens()


# -- term typing -------------------------------------------------------

def test_every_term_type_accepts_its_declared_parameters():
    assert validate_term(ContractTerm(TERM_RETENTION_MAX_DAYS,
                                      {"max_days": 30})) == []
    assert validate_term(ContractTerm(TERM_EGRESS_PROHIBITED,
                                      {"purpose": "model_training"})) == []
    assert validate_term(ContractTerm(
        TERM_EGRESS_REQUIRES_APPROVAL,
        {"recipient_class": RECIPIENT_CLASS_SUBCONTRACTOR})) == []
    assert validate_term(ContractTerm(
        TERM_PURPOSE_RESTRICTION,
        {"permitted_purposes": ["fraud_screening"]})) == []


def test_unknown_term_type_is_refused_at_construction():
    with pytest.raises(ContractValidationError):
        ContractTerm("RETAIN_FOREVER", {"max_days": 1})


def test_missing_required_parameter_is_refused():
    with pytest.raises(ContractValidationError) as exc:
        ContractTerm(TERM_RETENTION_MAX_DAYS, {})
    assert "max_days" in str(exc.value)


def test_undeclared_parameter_is_refused_rather_than_ignored():
    """A typo'd parameter that silently does nothing is worse than a
    crash: the contract would read as enforced and enforce nothing."""
    with pytest.raises(ContractValidationError) as exc:
        ContractTerm(TERM_RETENTION_MAX_DAYS, {"max_days": 90, "max_dayz": 30})
    assert "max_dayz" in str(exc.value)


def test_bool_is_not_accepted_where_an_int_is_declared():
    with pytest.raises(ContractValidationError):
        ContractTerm(TERM_RETENTION_MAX_DAYS, {"max_days": True})


def test_unknown_recipient_class_is_refused():
    with pytest.raises(ContractValidationError):
        ContractTerm(TERM_EGRESS_REQUIRES_APPROVAL,
                     {"recipient_class": "some-vendor-we-like"})


def test_empty_permitted_purposes_is_refused_as_a_miswritten_prohibition():
    with pytest.raises(ContractValidationError) as exc:
        ContractTerm(TERM_PURPOSE_RESTRICTION, {"permitted_purposes": []})
    assert "prohibition" in str(exc.value)


def test_backup_carve_out_shorter_than_the_main_clock_is_refused():
    with pytest.raises(ContractValidationError) as exc:
        ContractTerm(TERM_RETENTION_MAX_DAYS,
                     {"max_days": 90, "backup_max_days": 30})
    assert "carve-out" in str(exc.value)


def test_backup_carve_out_longer_than_the_main_clock_is_accepted():
    term = ContractTerm(TERM_RETENTION_MAX_DAYS,
                        {"max_days": 90, "backup_max_days": 365})
    assert term.params["backup_max_days"] == 365


# -- identity and posture ---------------------------------------------

def test_identity_uses_the_reserved_contract_slot():
    lens = _contract("acme-bank", "2.1.0")
    assert contract_cassette_version_of(lens) == f"{CONTRACT_DOMAIN}:acme-bank:2.1.0"


def test_regulatory_lens_identity_is_unchanged_by_the_shared_slot():
    """The identity function was generalized, not repointed. Existing
    lens identities are already written into shipped ledger rows."""
    assert regulatory_cassette_version_of(CFPBRegBLens()).startswith("regulatory:")


def test_a_contract_lens_declaring_live_mode_is_refused():
    lens = _contract(modes=(MODE_OBSERVER, MODE_LIVE))
    with pytest.raises(ContractValidationError) as exc:
        lens.validate()
    assert "MODES" in str(exc.value)


def test_contract_lens_passes_the_shared_regulatory_validation():
    """Same validator, no contract-specific load path."""
    snapshot = validate_regulatory_cassette(_contract())
    assert snapshot["cassette_version"].startswith(f"{CONTRACT_DOMAIN}:")


def test_empty_term_list_is_refused():
    lens = _contract(terms=[])
    with pytest.raises(ContractValidationError) as exc:
        lens.validate()
    assert "no terms" in str(exc.value)


def test_colon_in_a_component_is_refused_as_ambiguous_identity():
    lens = _contract(counterparty="acme:bank")
    with pytest.raises(ContractValidationError) as exc:
        lens.validate()
    assert "':'" in str(exc.value)


# -- term lookups ------------------------------------------------------

def test_retention_lookup_takes_the_strictest_declared_clock():
    lens = _contract(terms=[
        ContractTerm(TERM_RETENTION_MAX_DAYS, {"max_days": 90}),
        ContractTerm(TERM_RETENTION_MAX_DAYS, {"max_days": 30}),
    ])
    assert lens.retention_max_days() == 30


def test_no_retention_term_returns_none_not_unlimited():
    lens = _contract(terms=[ContractTerm(TERM_EGRESS_PROHIBITED,
                                         {"purpose": "resale"})])
    assert lens.retention_max_days() is None


def test_permitted_purposes_intersect_across_multiple_restriction_terms():
    lens = _contract(terms=[
        ContractTerm(TERM_PURPOSE_RESTRICTION,
                     {"permitted_purposes": ["a", "b", "c"]}),
        ContractTerm(TERM_PURPOSE_RESTRICTION,
                     {"permitted_purposes": ["b", "c", "d"]}),
    ])
    assert lens.permitted_purposes() == ("b", "c")


def test_no_purpose_restriction_returns_none_meaning_unrestricted_by_this_type():
    assert _contract().permitted_purposes() is None


# -- registry ----------------------------------------------------------

def test_registry_looks_up_by_counterparty():
    registry = ContractCassetteRegistry()
    registry.register(_contract("acme-bank"))
    registry.register(_contract("beta-corp"))
    assert registry.for_counterparty("beta-corp").get_counterparty_id() == "beta-corp"
    assert registry.counterparties() == ("acme-bank", "beta-corp")


def test_registry_refuses_a_regulatory_lens():
    registry = ContractCassetteRegistry()
    with pytest.raises(ContractValidationError):
        registry.register(CFPBRegBLens())


def test_registry_refuses_to_pick_between_two_contracts_for_one_counterparty():
    """Which terms apply is the question a report exists to answer. It is
    not resolvable by choosing one."""
    registry = ContractCassetteRegistry()
    registry.register(_contract("acme-bank", "1.0.0"))
    registry.register(_contract("acme-bank", "2.0.0"))
    with pytest.raises(ContractValidationError) as exc:
        registry.for_counterparty("acme-bank")
    assert "not resolvable" in str(exc.value)


def test_unknown_counterparty_raises_rather_than_returning_none():
    registry = ContractCassetteRegistry()
    with pytest.raises(KeyError):
        registry.for_counterparty("nobody")


def test_contract_registry_is_still_a_regulatory_registry():
    """Subclassed, not duplicated -- the separation that matters (never
    returned by a domain lookup) is inherited, not re-implemented."""
    assert issubclass(ContractCassetteRegistry, RegulatoryCassetteRegistry)


# -- content binding ---------------------------------------------------

def test_changing_a_term_changes_the_content_hash():
    """The whole basis of 'the hash in my report matches what I signed'."""
    before = cassette_forensics.compute_cassette_hash(
        _contract(terms=[ContractTerm(TERM_RETENTION_MAX_DAYS,
                                      {"max_days": 90})]).snapshot())
    after = cassette_forensics.compute_cassette_hash(
        _contract(terms=[ContractTerm(TERM_RETENTION_MAX_DAYS,
                                      {"max_days": 91})]).snapshot())
    assert before != after


def test_identical_terms_hash_identically():
    a = cassette_forensics.compute_cassette_hash(_contract().snapshot())
    b = cassette_forensics.compute_cassette_hash(_contract().snapshot())
    assert a == b


def test_profile_carries_the_contract_reference_and_terms():
    profile = _contract().get_profile()
    assert profile["contract_reference"] == "DPA-acme-bank-0001"
    assert profile["terms"][0]["term_type"] == TERM_RETENTION_MAX_DAYS


# -- reference lens ----------------------------------------------------

def test_reference_dpa_validates_and_declares_all_four_term_types():
    lens = ReferenceDPAContract()
    validate_regulatory_cassette(lens)
    assert lens.validate() is True
    declared = {t.term_type for t in lens.get_terms()}
    assert declared == {TERM_RETENTION_MAX_DAYS, TERM_EGRESS_PROHIBITED,
                        TERM_EGRESS_REQUIRES_APPROVAL, TERM_PURPOSE_RESTRICTION}


def test_reference_dpa_backup_clock_is_longer_than_its_active_clock():
    lens = ReferenceDPAContract()
    assert lens.retention_max_days() == 90
    assert lens.backup_max_days() == 365


def test_reference_dpa_requires_approval_for_subcontractors_only():
    lens = ReferenceDPAContract()
    assert lens.approval_required_classes() == (RECIPIENT_CLASS_SUBCONTRACTOR,)
    assert RECIPIENT_CLASS_AFFILIATE not in lens.approval_required_classes()
