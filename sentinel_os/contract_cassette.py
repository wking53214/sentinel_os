"""Contract cassettes -- a signed data-use contract as an insertable lens.

WHAT THIS IS
------------
A regulatory cassette answers "does this decision read as compliant
with a rule an agency published?". A contract cassette answers a
narrower and more adversarial question: "is the operator honoring the
contract THIS counterparty signed, and can the counterparty confirm
that without trusting the operator's word?".

Mechanically it is the same object. ContractCassette subclasses
regulatory_cassette_interface.RegulatoryCassette, is validated by the
same validate_regulatory_cassette(), is inserted and removed through
the same RegulatoryDeck, and rides the same
regulatory_cassette_inserted / regulatory_cassette_removed record
kinds. The one difference is the reserved identity slot:

    contract:<counterparty_id>:<version>

so a ledger query can tell a contract lens from a regulatory lens from
operational policy by identity alone, exactly as the regulatory slot
already does. Nothing here re-implements registry, validation,
insertion events, content binding, or the screening disclaimer -- all
four already existed and all four are reused.

counterparty_id, NOT customer_id
--------------------------------
The word "subject" is already taken in this codebase and means the
person a decision is ABOUT (obligation_sweep.subject_of(); a loan
applicant). The party who signed the contract is a different identity
entirely, and conflating the two would silently mix a data subject
into a cohort keyed by contract counterparty. "Counterparty" is also
the word the contract itself uses. "Tenant" was rejected: it implies
an infrastructure isolation guarantee this repo does not make, and
these words end up in a document an auditor reads.

TERMS ARE STRUCTURED, NOT PROSE
-------------------------------
A term is a typed obligation with typed parameters, never free text.
A checker can only be mechanical if the thing it checks is mechanical.
Free-text contract clauses would have forced either a natural-language
model in the governance path (unauditable) or a human in every check
(not a check). The four types below are the minimum that make Parts
2-4 possible; new types are additive.

WHAT A CONTRACT LENS DOES NOT DO
--------------------------------
It never blocks and never attaches live. MODES is (MODE_OBSERVER,) on
every contract lens by construction, enforced in validate(). The
egress chokepoint (contract_egress.py) is the one place a contract
term can cause a refusal, and it refuses to issue an AUTHORIZATION --
it does not stop the operator's data movement. Sentinel stays an
observer. See contract_egress's module docstring for the full posture
argument.
"""
from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from regulatory_cassette_interface import (
    MODE_OBSERVER,
    RegulatoryCassette,
    RegulatoryCassetteConfig,
    RegulatoryCassetteRegistry,
    RegulatoryValidationError,
    regulatory_cassette_version_of,
)

# ---------------------------------------------------------------------------
# Stable vocabulary -- these strings ride in ledger rows and reports.
# ---------------------------------------------------------------------------

# The reserved identity slot. Sibling of REGULATORY_DOMAIN, same purpose.
CONTRACT_DOMAIN = "contract"

# Term types. The parameter each one carries is named in TERM_PARAMETERS
# below and validated on construction -- a term whose parameters do not
# type-check is refused at authoring time, not discovered at check time.
TERM_RETENTION_MAX_DAYS = "RETENTION_MAX_DAYS"
TERM_EGRESS_PROHIBITED = "EGRESS_PROHIBITED"
TERM_EGRESS_REQUIRES_APPROVAL = "EGRESS_REQUIRES_APPROVAL"
TERM_PURPOSE_RESTRICTION = "PURPOSE_RESTRICTION"

CONTRACT_TERM_TYPES: Tuple[str, ...] = (
    TERM_RETENTION_MAX_DAYS,
    TERM_EGRESS_PROHIBITED,
    TERM_EGRESS_REQUIRES_APPROVAL,
    TERM_PURPOSE_RESTRICTION,
)

# Recipient classes an egress can name. subcontractor is the class
# Part 4's approval check keys off; the others exist so a contract can
# say something precise about them rather than lumping everything an
# approval rule does not cover into one bucket.
RECIPIENT_CLASS_SUBCONTRACTOR = "subcontractor"
RECIPIENT_CLASS_AFFILIATE = "affiliate"
RECIPIENT_CLASS_COUNTERPARTY = "counterparty"
RECIPIENT_CLASS_REGULATOR = "regulator"
RECIPIENT_CLASS_OTHER = "other"

RECIPIENT_CLASSES: Tuple[str, ...] = (
    RECIPIENT_CLASS_SUBCONTRACTOR,
    RECIPIENT_CLASS_AFFILIATE,
    RECIPIENT_CLASS_COUNTERPARTY,
    RECIPIENT_CLASS_REGULATOR,
    RECIPIENT_CLASS_OTHER,
)

# Required parameter names per term type, and the Python type each must be.
TERM_PARAMETERS: Dict[str, Dict[str, type]] = {
    TERM_RETENTION_MAX_DAYS: {"max_days": int},
    TERM_EGRESS_PROHIBITED: {"purpose": str},
    TERM_EGRESS_REQUIRES_APPROVAL: {"recipient_class": str},
    TERM_PURPOSE_RESTRICTION: {"permitted_purposes": list},
}

# Optional parameters, same validation, absent by default.
#
# backup_max_days exists because of a real drafting reality, not a
# design preference: purging one record from immutable backup media is
# genuinely hard, so data-processing agreements that address the
# question at all almost always give backups their own, longer clock.
# A contract type that could not express that would have forced every
# real agreement to be authored as a lie. A contract that says nothing
# about backups gets the strict reading -- archived data is retained
# data, and max_days governs it. Possession is what a retention clause
# turns on, not convenience of access. See contract_retention.py.
TERM_OPTIONAL_PARAMETERS: Dict[str, Dict[str, type]] = {
    TERM_RETENTION_MAX_DAYS: {"backup_max_days": int},
}


class ContractValidationError(RegulatoryValidationError):
    """A contract lens or one of its terms failed validation. Subclasses
    the regulatory error so a caller catching either family catches
    both -- these are the same class of failure."""


@dataclass(frozen=True)
class ContractTerm:
    """One typed, structured obligation from a signed contract.

    term_type is from CONTRACT_TERM_TYPES; params carries exactly the
    parameters that type declares. Frozen and JSON-safe because terms
    are part of the lens profile, which is content-hashed: a term that
    could mutate after binding would make the hash a statement about
    nothing.
    """

    term_type: str
    params: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        violations = validate_term(self)
        if violations:
            raise ContractValidationError(f"term:{self.term_type}", violations)

    def as_dict(self) -> Dict[str, Any]:
        """JSON-safe form -- what the profile (and therefore the content
        hash, and therefore the customer-facing report) records."""
        return {"term_type": self.term_type, "params": dict(self.params)}


def validate_term(term: ContractTerm) -> List[str]:
    """Full violation list for one term. Returns [] when the term is
    well-formed. Separate from __post_init__ so callers that want to
    report every problem at once (an authoring tool, a test) can."""
    violations: List[str] = []
    if term.term_type not in CONTRACT_TERM_TYPES:
        violations.append(
            f"unknown term_type '{term.term_type}'; known: "
            f"{list(CONTRACT_TERM_TYPES)}"
        )
        return violations
    if not isinstance(term.params, dict):
        violations.append("params must be a dict")
        return violations

    required = TERM_PARAMETERS[term.term_type]
    optional = TERM_OPTIONAL_PARAMETERS.get(term.term_type, {})
    for name, expected in required.items():
        if name not in term.params:
            violations.append(f"{term.term_type} requires parameter '{name}'")
            continue
        value = term.params[name]
        # bool is an int subclass; an int parameter given True is a
        # drafting mistake, not a valid value.
        if expected is int and isinstance(value, bool):
            violations.append(f"{term.term_type}.{name} must be an int, got bool")
        elif not isinstance(value, expected):
            violations.append(
                f"{term.term_type}.{name} must be {expected.__name__}, "
                f"got {type(value).__name__}"
            )
        elif expected is int and value < 0:
            violations.append(f"{term.term_type}.{name} must not be negative")
        elif expected is str and not str(value).strip():
            violations.append(f"{term.term_type}.{name} must be a non-empty string")

    for name, value in term.params.items():
        if name in required or name in optional:
            continue
        violations.append(
            f"{term.term_type} does not declare parameter '{name}'; declared: "
            f"{sorted(list(required) + list(optional))}"
        )

    for name, expected in optional.items():
        if name not in term.params:
            continue
        value = term.params[name]
        if expected is int and isinstance(value, bool):
            violations.append(f"{term.term_type}.{name} must be an int, got bool")
        elif not isinstance(value, expected):
            violations.append(
                f"{term.term_type}.{name} must be {expected.__name__}, "
                f"got {type(value).__name__}"
            )
        elif expected is int and value < 0:
            violations.append(f"{term.term_type}.{name} must not be negative")

    if term.term_type == TERM_EGRESS_REQUIRES_APPROVAL:
        rc = term.params.get("recipient_class")
        if isinstance(rc, str) and rc not in RECIPIENT_CLASSES:
            violations.append(
                f"unknown recipient_class '{rc}'; known: {list(RECIPIENT_CLASSES)}"
            )
    if term.term_type == TERM_PURPOSE_RESTRICTION:
        purposes = term.params.get("permitted_purposes")
        if isinstance(purposes, list):
            if not purposes:
                violations.append(
                    "PURPOSE_RESTRICTION.permitted_purposes must not be empty; an "
                    "empty permitted list is a prohibition, which is a different "
                    "term type"
                )
            for i, p in enumerate(purposes):
                if not isinstance(p, str) or not p.strip():
                    violations.append(
                        f"PURPOSE_RESTRICTION.permitted_purposes[{i}] must be a "
                        f"non-empty string"
                    )
    if term.term_type == TERM_RETENTION_MAX_DAYS:
        backup = term.params.get("backup_max_days")
        max_days = term.params.get("max_days")
        if (isinstance(backup, int) and not isinstance(backup, bool)
                and isinstance(max_days, int) and not isinstance(max_days, bool)
                and backup < max_days):
            violations.append(
                "RETENTION_MAX_DAYS.backup_max_days is shorter than max_days; a "
                "backup carve-out that expires first is not a carve-out. Drop it "
                "and the strict reading applies to backups too"
            )
    return violations


class ContractCassette(RegulatoryCassette):
    """A signed data-use contract, expressed as an observer lens.

    Concrete contract lenses declare a counterparty_id, a contract
    reference (the document the counterparty actually signed), and a
    tuple of ContractTerms. Everything else -- validation, registry,
    insertion events, content binding, snapshot hashing -- comes from
    the regulatory lens machinery unchanged.
    """

    IDENTITY_DOMAIN = CONTRACT_DOMAIN

    # Observer only, always. Not a default a subclass may override:
    # validate_contract_cassette refuses a contract lens declaring live
    # mode. A contract term causing a live block would put Sentinel in
    # the operator's data path, which is the posture decision this
    # whole feature was built around avoiding.
    MODES: Tuple[str, ...] = (MODE_OBSERVER,)

    @abstractmethod
    def get_counterparty_id(self) -> str:
        """The party who signed this contract. Scopes every report."""

    @abstractmethod
    def get_contract_reference(self) -> str:
        """Human-readable identifier of the signed document (e.g.
        'DPA-2026-0031 rev C, executed 2026-03-14'). Rides in the
        insertion event and every report so a counterparty can match
        the loaded lens against their own copy."""

    @abstractmethod
    def get_terms(self) -> Tuple[ContractTerm, ...]:
        """The structured obligations this contract declares."""

    # -- convenience lookups over the declared terms -----------------

    def terms_of_type(self, term_type: str) -> Tuple[ContractTerm, ...]:
        return tuple(t for t in self.get_terms() if t.term_type == term_type)

    def retention_max_days(self) -> Optional[int]:
        """The active-systems retention ceiling, or None when the
        contract declares no retention term. None is not 'unlimited';
        it means the check has nothing to check against and reports
        INDETERMINATE."""
        terms = self.terms_of_type(TERM_RETENTION_MAX_DAYS)
        if not terms:
            return None
        return min(int(t.params["max_days"]) for t in terms)

    def backup_max_days(self) -> Optional[int]:
        """The declared backup carve-out, or None when the contract is
        silent -- in which case backups fall under retention_max_days
        like any other copy."""
        for term in self.terms_of_type(TERM_RETENTION_MAX_DAYS):
            if "backup_max_days" in term.params:
                return int(term.params["backup_max_days"])
        return None

    def prohibited_purposes(self) -> Tuple[str, ...]:
        return tuple(str(t.params["purpose"])
                     for t in self.terms_of_type(TERM_EGRESS_PROHIBITED))

    def approval_required_classes(self) -> Tuple[str, ...]:
        return tuple(str(t.params["recipient_class"])
                     for t in self.terms_of_type(TERM_EGRESS_REQUIRES_APPROVAL))

    def permitted_purposes(self) -> Optional[Tuple[str, ...]]:
        """The allow-list of purposes, or None when the contract
        declares no PURPOSE_RESTRICTION. None means unrestricted by
        this term type -- an EGRESS_PROHIBITED term can still refuse a
        specific purpose."""
        terms = self.terms_of_type(TERM_PURPOSE_RESTRICTION)
        if not terms:
            return None
        # Several restriction terms intersect: every one must permit it.
        permitted = set(str(p) for p in terms[0].params["permitted_purposes"])
        for term in terms[1:]:
            permitted &= set(str(p) for p in term.params["permitted_purposes"])
        return tuple(sorted(permitted))

    # -- lens contract ----------------------------------------------

    def get_config(self) -> RegulatoryCassetteConfig:
        """Contract lenses map onto the shared config shape rather than
        inventing a second one: `regulation` carries the contract
        reference (the thing this lens claims to check against) and
        `authority` carries the counterparty (the party the obligation
        runs to). Both already ride in insertion and disclosure ledger
        events, so a contract lens becomes queryable in the chain with
        no new columns and no new record kinds."""
        return RegulatoryCassetteConfig(
            name=self.get_counterparty_id(),
            version=self.get_contract_version(),
            description=self.get_contract_description(),
            regulation=self.get_contract_reference(),
            authority=self.get_counterparty_id(),
        )

    @abstractmethod
    def get_contract_version(self) -> str:
        """Version of this contract expression. Changing terms without
        changing this is what the binding tripwire catches."""

    def get_contract_description(self) -> str:
        return (f"Data-use contract terms for counterparty "
                f"{self.get_counterparty_id()}")

    def get_profile(self) -> Dict[str, Any]:
        """The content-hashed configuration: the terms themselves, plus
        the contract reference. This is what makes 'the hash in my
        report matches the contract I signed' a checkable statement."""
        return {
            "counterparty_id": self.get_counterparty_id(),
            "contract_reference": self.get_contract_reference(),
            "terms": [t.as_dict() for t in self.get_terms()],
        }

    def get_checks(self) -> Tuple[str, ...]:
        """Contract lenses do not screen decisions the way a regulatory
        lens does; their checks run over egress, deletion and approval
        events (contract_egress / contract_retention /
        contract_approval). The names are still declared here because
        they are part of the lens's tamper-evident public surface and
        ride in the insertion event."""
        return ("contract_egress_permission",
                "contract_retention_status",
                "contract_subcontractor_approval")

    def review(self, material) -> List[Any]:
        """Read-only by contract, and deliberately empty: a contract
        lens has nothing to say about an individual governance decision
        in the live path. Its findings come from the event checks named
        in get_checks(), which run over a counterparty's egress,
        deletion and approval history rather than over one episode.
        Returning [] is the honest answer, not a stub -- see
        contract_attestation.build_attestation for where contract
        findings are actually produced."""
        return []

    def validate(self) -> bool:
        violations = validate_contract_cassette_terms(self)
        if violations:
            raise ContractValidationError(
                regulatory_cassette_version_of(self), violations)
        return True


def validate_contract_cassette_terms(lens: ContractCassette) -> List[str]:
    """Contract-specific validation, on top of everything
    validate_regulatory_cassette already enforces. Returns the full
    violation list."""
    violations: List[str] = []

    for attr, label in (("get_counterparty_id", "counterparty_id"),
                        ("get_contract_reference", "contract_reference"),
                        ("get_contract_version", "contract_version")):
        try:
            value = getattr(lens, attr)()
        except Exception as exc:
            violations.append(f"{attr}() raised: {exc}")
            continue
        if not isinstance(value, str) or not value.strip():
            violations.append(f"{label} must be a non-empty string")
        elif ":" in value:
            # The identity string is colon-delimited; a colon inside a
            # component would make contract:a:b:c ambiguous to parse.
            violations.append(
                f"{label} must not contain ':' -- it is a component of the "
                f"colon-delimited lens identity")

    try:
        terms = lens.get_terms()
    except Exception as exc:
        violations.append(f"get_terms() raised: {exc}")
        return violations

    if isinstance(terms, (str, bytes)) or not isinstance(terms, (tuple, list)):
        violations.append("get_terms() must return a tuple/list of ContractTerm")
        return violations
    if not terms:
        violations.append(
            "get_terms() is empty; a contract lens declaring no terms would "
            "report every counterparty as having nothing to honor")
    for i, term in enumerate(terms):
        if not isinstance(term, ContractTerm):
            violations.append(f"get_terms()[{i}] is not a ContractTerm")
            continue
        for v in validate_term(term):
            violations.append(f"get_terms()[{i}]: {v}")

    declared_modes = tuple(getattr(lens, "MODES", ()) or ())
    if tuple(declared_modes) != (MODE_OBSERVER,):
        violations.append(
            f"a contract lens must declare MODES == ({MODE_OBSERVER!r},); got "
            f"{list(declared_modes)}. Contract terms never attach to the live "
            f"decision path -- the egress chokepoint is the only place a term "
            f"can refuse anything, and it refuses an authorization, not a "
            f"decision")
    return violations


class ContractCassetteRegistry(RegulatoryCassetteRegistry):
    """Contract lenses, indexed by counterparty as well as by identity.

    Subclasses the regulatory registry rather than duplicating it: the
    register/get/list_all semantics are identical and the separation
    that matters (never returned by a domain lookup, never loadable as
    operational policy) is already enforced there. What this adds is
    the one lookup contract work actually needs and regulatory work
    does not -- "which contract governs counterparty X" -- plus a
    refusal to register a lens that is not a contract lens, so a
    regulatory lens cannot end up answering a contract question.
    """

    def register(self, lens) -> str:
        if not isinstance(lens, ContractCassette):
            raise ContractValidationError(
                type(lens).__name__,
                ["ContractCassetteRegistry accepts ContractCassette instances "
                 "only; a regulatory lens registered here would be answering "
                 "for a contract it does not express"],
            )
        return super().register(lens)

    def for_counterparty(self, counterparty_id: str) -> ContractCassette:
        """The contract lens governing one counterparty.

        Fail-loud on both misses and ambiguity. Two live contracts for
        one counterparty is not something to resolve by picking one:
        which terms apply is the entire question the report answers,
        and guessing it would produce a confident wrong attestation.
        """
        matches = [lens for lens in self.lenses.values()
                   if lens.get_counterparty_id() == counterparty_id]
        if not matches:
            raise KeyError(
                f"No contract cassette registered for counterparty "
                f"'{counterparty_id}'; registered: "
                f"{sorted(self.counterparties())}")
        if len(matches) > 1:
            raise ContractValidationError(
                f"{CONTRACT_DOMAIN}:{counterparty_id}",
                [f"{len(matches)} contract lenses are registered for "
                 f"counterparty '{counterparty_id}': "
                 f"{sorted(regulatory_cassette_version_of(m) for m in matches)}. "
                 f"Which terms apply is the question a report exists to answer; "
                 f"it is not resolvable by picking one"],
            )
        return matches[0]

    def counterparties(self) -> Tuple[str, ...]:
        return tuple(sorted({lens.get_counterparty_id()
                             for lens in self.lenses.values()}))


def contract_cassette_version_of(lens: ContractCassette) -> str:
    """contract:<counterparty_id>:<version>. Thin alias over the shared
    identity function, kept so contract-side callers read naturally
    without a second implementation existing."""
    return regulatory_cassette_version_of(lens)
