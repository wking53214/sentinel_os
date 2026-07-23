"""
Regulatory Cassette Interface -- the auditor-insertable LENS contract.

A regulatory cassette is NOT a domain cassette. A domain cassette
(cassette_interface.Cassette -- IVR, banking, ...) is operational
policy: it drives judgment of episodes in its industry's own terms. A
regulatory cassette is a LENS an auditor inserts to check decisions
against ONE regulation's requirements. It never judges quality, never
owns governance parameters the engine reads, and never drives a
decision -- it reviews decisions (recorded or in flight) and produces
findings for human review. Keeping the two contracts, and their
registries, separate is deliberate: a ledger row citing
"regulatory:cfpb-ecoa-reg-b:1.0.0" must never look like the policy
that produced the decision.

Two modes, declared per lens in its MODES manifest (explicitly -- no
default, same posture as the kernel's CAPABILITIES manifest):

  observer (the expected default use) -- read-only against the
      existing immutable ledger. Produces a review report scoped to
      the lens's regulation. Touches nothing in any live decision
      path; zero production risk.
  live -- opt-in. The lens attaches to the kernel judgment path
      (regulatory_deck.RegulatoryDeck) and reviews episodes as they
      are judged. It can flag, or block, in real time -- and every
      such action is itself logged to the ledger as a first-class
      regulatory_disclosure event BEFORE the action takes effect.
      There is no silent path: a live lens that cannot disclose does
      not act (see regulatory_deck).

WHAT A FINDING IS -- AND IS NOT. Every check in this framework is a
screening signal for human review: "this recorded reason reads as
generic," "this input variable is a known proxy for a protected
characteristic." A finding is NOT a determination that a decision
violated (or an absence of findings that it satisfied) ECOA, Reg B,
or any other law. Legal compliance is a legal determination made by
people; this code scores and flags. SCREENING_DISCLAIMER below rides
in every report so that boundary cannot quietly disappear from the
output.

Tamper evidence reuses the machinery domain cassettes already proved:
a lens's full configuration (its snapshot(), including the check
profile that parameterizes its behavior) is content-hashed and bound
into the ledger chain via bind_cassette_version at insertion. Same
guarantee, same tripwire: an altered "CFPB regs" lens presenting the
same version string is refused loud. Insertion itself is a
first-class ledger event (regulatory_cassette_inserted: who, when,
which lens, which mode, which content hash) so an examiner can query
"when was the CFPB lens active" directly -- see
governance/ledger_postgres.record_regulatory_cassette_event.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Tuple

from episode import Episode, outcome_mismatches

# ---------------------------------------------------------------------------
# Stable vocabulary -- these strings ride in ledger rows.
# ---------------------------------------------------------------------------

MODE_OBSERVER = "observer"
MODE_LIVE = "live"
REGULATORY_MODES = (MODE_OBSERVER, MODE_LIVE)

# The domain slot every regulatory lens identity uses
# ("regulatory:<name>:<version>"). One reserved word, so a ledger query
# can always tell a lens from operational policy by its identity alone.
REGULATORY_DOMAIN = "regulatory"

# Versioning of the lens snapshot shape itself (self-describing records,
# same idea as cassette_schema.SCHEMA_VERSION for domain cassettes).
REGULATORY_SCHEMA_VERSION = "1.0.0"

# Disclosure action vocabulary. "adjust" is reserved so the disclosure
# event type can name it if a future lens ever adjusts an output -- but
# no lens in this repo emits it, and the deck's rule stands regardless:
# an undisclosed adjustment is not a smaller adjustment, it is a
# forbidden one.
ACTION_FLAG = "flag"
ACTION_BLOCK = "block"
ACTION_ADJUST = "adjust"
REGULATORY_ACTIONS = (ACTION_FLAG, ACTION_BLOCK, ACTION_ADJUST)

# Rides in every report and every observer review. Deliberately blunt.
SCREENING_DISCLAIMER = (
    "Screening output for human review. Findings score how a recorded "
    "decision reads against this lens's configured checks; they are not "
    "a legal determination of compliance or non-compliance with any "
    "regulation, and the absence of findings is not a certification."
)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass
class RegulatoryCassetteConfig:
    """Identity of one regulatory lens.

    regulation names the specific rule the lens checks against (e.g.
    "ECOA / Regulation B, 12 CFR Part 1002 ..."); authority names the
    agency that owns that rule. Both ride in insertion/disclosure
    ledger events so an examiner reading the chain sees WHICH
    regulation a lens claimed to check, not just a lens name.
    """

    name: str
    version: str
    description: str
    regulation: str
    authority: str


@dataclass(frozen=True)
class RegulatoryFinding:
    """One screening finding from one check on one decision.

    action  -- what the lens asks for: "flag" (surface for human
               review; judgment proceeds) or "block" (in live mode,
               the deck refuses to return judgment for this episode
               until a human reviews). See regulatory_deck for how
               each is disclosed and enforced.
    classification -- the check's own vocabulary for what it saw
               ("generic", "placeholder", "missing", "proxy_variable",
               "direct_protected_characteristic", ...).
    score   -- 0.0-1.0, meaning defined per check and stated in the
               evidence (e.g. the specificity checker's score is
               "how case-specific this reason reads"; the proxy screen
               reports 1.0 for a name-pattern match). Never a
               compliance probability.
    evidence -- JSON-safe dict showing exactly WHY the finding fired
               (phrase hits, matched variables, referenced fields), so
               the human reviewing it sees the mechanism, not a verdict.
    """

    check: str
    subject_id: str
    regulation: str
    action: str
    classification: str
    score: float
    evidence: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        """JSON-safe form -- what a regulatory_disclosure ledger event
        stores and hashes as the finding body."""
        return {
            "check": self.check,
            "subject_id": self.subject_id,
            "regulation": self.regulation,
            "action": self.action,
            "classification": self.classification,
            "score": float(self.score),
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class DecisionMaterial:
    """The normalized view of one decision that checks run on.

    Regulatory checks must work on BOTH shapes a decision exists in:
    a kernel Episode (live mode / episode review) and a recorded
    governance_decision ledger row (observer mode). This is the one
    shape both are adapted into, so a check is written once and runs
    on either -- that adaptation, not any single lens, is what makes
    the checker reusable across regulations and data sources.

    reasons           -- the recorded outcome reason(s) for review.
    input_fields      -- the decision's input variables (name -> value):
                         what the deciding system saw.
    mismatched_fields -- requested-vs-actual mismatch field names, when
                         known (episodes). Ledger decision rows do not
                         record a requested/actual split, so this is
                         empty there -- checks treat it as one signal
                         among several, never a required one.
    outcome           -- the recorded outcome/output mapping.
    source            -- "episode" | "ledger" (evidence context only).
    """

    subject_id: str
    domain: str
    reasons: Tuple[str, ...]
    input_fields: Dict[str, Any]
    mismatched_fields: Tuple[str, ...]
    outcome: Dict[str, Any]
    source: str


def material_from_episode(episode: Episode) -> DecisionMaterial:
    """Adapt a kernel Episode into check material.

    input_fields is what the deciding system had in front of it: the
    request plus the recorded attributes. actor_report is deliberately
    NOT included -- same kernel rule as judgment: the actor's story
    about itself is never treated as decision material.
    """
    inputs: Dict[str, Any] = dict(episode.requested)
    inputs.update(episode.attributes)
    return DecisionMaterial(
        subject_id=episode.episode_id,
        domain=episode.domain,
        reasons=tuple(episode.outcome_reasons),
        input_fields=inputs,
        mismatched_fields=tuple(m.name for m in outcome_mismatches(episode)),
        outcome=dict(episode.actual),
        source="episode",
    )


def material_from_ledger_row(row: Mapping[str, Any]) -> DecisionMaterial:
    """Adapt a governance_decision ledger row into check material.

    Reasons come from the row's recorded reasoning plus any "reasons"
    list the decision output carried. A row that recorded neither has
    an empty tuple -- which is itself reviewable material (see the
    specificity check's "missing" classification).
    """
    reasons: List[str] = []
    reasoning = row.get("reasoning", row.get("reason"))
    if isinstance(reasoning, str) and reasoning.strip():
        reasons.append(reasoning.strip())
    output = row.get("output", row.get("decision_output")) or {}
    if isinstance(output, dict):
        extra = output.get("reasons")
        if isinstance(extra, (list, tuple)):
            reasons.extend(str(r).strip() for r in extra if str(r).strip())
    input_data = row.get("input_data") or {}
    return DecisionMaterial(
        subject_id=str(row.get("id", row.get("decision_id", "unknown"))),
        domain=str(row.get("cassette_version", "unknown")),
        reasons=tuple(reasons),
        input_fields=dict(input_data) if isinstance(input_data, dict) else {},
        mismatched_fields=(),
        outcome=dict(output) if isinstance(output, dict) else {},
        source="ledger",
    )


# ---------------------------------------------------------------------------
# The lens contract
# ---------------------------------------------------------------------------

class RegulatoryValidationError(Exception):
    """A regulatory lens failed validation. Carries the full violation
    list so one insertion attempt reports every problem at once (same
    reporting posture as CassetteValidationError)."""

    def __init__(self, lens_label: str, violations: List[str]):
        self.lens_label = lens_label
        self.violations = list(violations)
        lines = "\n".join(f"  - {v}" for v in self.violations)
        super().__init__(
            f"Regulatory cassette '{lens_label}' failed validation "
            f"({len(self.violations)} violation(s)):\n{lines}"
        )


class RegulatoryBlock(Exception):
    """A LIVE regulatory lens blocked an episode's judgment.

    Raised by regulatory_deck.RegulatoryDeck.judge AFTER every finding
    (blocking and otherwise) has been disclosed to the ledger -- the
    block is always on the record before it takes effect. In judge
    mode "block" means: the deck refuses to return a judgment for this
    episode until a human reviews the findings. Sentinel remains the
    judge, not the actor -- it does not reach into the deciding
    system; it declines to certify.
    """

    def __init__(self, lens_identity: str, regulation: str,
                 findings: Tuple[RegulatoryFinding, ...]):
        self.lens_identity = lens_identity
        self.regulation = regulation
        self.findings = tuple(findings)
        blocking = [f for f in self.findings if f.action == ACTION_BLOCK]
        super().__init__(
            f"Regulatory lens '{lens_identity}' ({regulation}) blocked this "
            f"episode: {len(blocking)} blocking finding(s) of "
            f"{len(self.findings)} total. All findings were disclosed to the "
            f"ledger before this block took effect. Human review required."
        )


class RegulatoryCassette(ABC):
    """Abstract base: the contract every regulatory lens implements.

    Deliberately kernel-ADJACENT, not kernel: this class does not
    subclass cassette_interface.Cassette, has no judge()/explain(),
    owns no governance parameters, and never appears in the domain
    CassetteRegistry. It matches the domain-cassette AUTHORING pattern
    (a Python class with identity, a declaration, a self-check,
    fail-loud validation on every load path) without ever being
    loadable as operational policy.
    """

    # The mode manifest. REQUIRED on every concrete lens: a tuple of
    # names from REGULATORY_MODES. There is no default -- a lens that
    # never said whether it may attach live would attach by accident.
    MODES: Tuple[str, ...]

    @abstractmethod
    def get_config(self) -> RegulatoryCassetteConfig:
        """Return lens identity metadata."""

    @abstractmethod
    def get_checks(self) -> Tuple[str, ...]:
        """The names of the checks this lens runs. These names ride in
        regulatory_disclosure ledger events, so they are part of the
        lens's public, tamper-evident surface."""

    @abstractmethod
    def get_profile(self) -> Dict[str, Any]:
        """The JSON-safe configuration that parameterizes this lens's
        checks (generic-phrase lists, thresholds, proxy-variable maps,
        block behavior, ...). This is the regulation-specific CONFIG
        the reusable checkers run under -- a CMS or NAIC lens differs
        from a CFPB lens here, not in checker code. It is included in
        snapshot() and therefore in the content hash: changing the
        profile changes the hash, and the binding tripwire then
        requires a new version string. Configuration is policy."""

    @abstractmethod
    def review(self, material: DecisionMaterial) -> List[RegulatoryFinding]:
        """Run this lens's checks against one decision's material and
        return findings (empty list when nothing warrants review).
        Read-only by contract: review() must not write anywhere --
        disclosure logging is the deck's job, precisely so no lens can
        forget it."""

    @abstractmethod
    def validate(self) -> bool:
        """Lens self-check."""

    def modes(self) -> Tuple[str, ...]:
        """The declared mode manifest, normalized (full validation with
        the complete violation list happens in
        validate_regulatory_cassette)."""
        declared = getattr(self, "MODES", None)
        if declared is None or isinstance(declared, str) \
                or not isinstance(declared, (tuple, list)):
            raise RegulatoryValidationError(
                type(self).__name__,
                ["MODES must be a tuple/list of mode names; every regulatory "
                 "cassette must declare which modes it supports"],
            )
        return tuple(str(m) for m in declared)

    def snapshot(self) -> Dict[str, Any]:
        """The full, JSON-safe record of what this lens IS: identity,
        regulation, modes, checks, and the complete check profile.
        This is what gets content-hashed
        (cassette_forensics.compute_cassette_hash) and bound into the
        ledger at insertion -- the auditor's guarantee that the lens
        reviewed today is byte-identical in configuration to the one
        the insertion event recorded."""
        config = self.get_config()
        return {
            "regulatory_schema_version": REGULATORY_SCHEMA_VERSION,
            "cassette_version": regulatory_cassette_version_of(self),
            "kind": "regulatory_lens",
            "name": config.name,
            "version": config.version,
            "description": config.description,
            "regulation": config.regulation,
            "authority": config.authority,
            "modes": sorted(self.modes()),
            "checks": sorted(self.get_checks()),
            "profile": self.get_profile(),
        }


def regulatory_cassette_version_of(lens) -> str:
    """Canonical identity string a ledger row uses to name a lens:
    regulatory:<name>:<version>. The reserved "regulatory" domain slot
    keeps lens identities visibly distinct from operational cassette
    identities in every ledger query."""
    config = lens.get_config()
    return f"{REGULATORY_DOMAIN}:{config.name}:{config.version}"


def validate_regulatory_cassette(lens) -> Dict[str, Any]:
    """The single fail-loud validation entry point for every lens load
    path (registry registration AND deck insertion alike). Raises
    RegulatoryValidationError with the complete violation list, or
    returns the validated snapshot() so callers validate and hash in
    one step. Nothing is defaulted, nothing repaired."""
    violations: List[str] = []

    config = None
    try:
        config = lens.get_config()
    except Exception as exc:  # a config that crashes is a missing config
        violations.append(f"get_config() raised: {exc}")
    if config is not None:
        for attr in ("name", "version", "description", "regulation", "authority"):
            value = getattr(config, attr, None)
            if not isinstance(value, str) or not value.strip():
                violations.append(f"config.{attr} must be a non-empty string")
    elif not violations:
        violations.append("get_config() returned None")
    label = (f"{REGULATORY_DOMAIN}:{getattr(config, 'name', '?')}:"
             f"{getattr(config, 'version', '?')}")

    # Mode manifest: explicit, known names only, at least one.
    declared = getattr(lens, "MODES", None)
    if declared is None:
        violations.append(
            "lens declares no MODES manifest; every regulatory cassette must "
            "state which modes it supports (observer and/or live)"
        )
    elif isinstance(declared, str) or not isinstance(declared, (tuple, list)):
        violations.append(
            f"MODES must be a tuple/list of mode names, got {type(declared).__name__}"
        )
    else:
        modes = tuple(str(m) for m in declared)
        if not modes:
            violations.append(
                "MODES is empty; a lens that supports no mode cannot be inserted"
            )
        for mode in modes:
            if mode not in REGULATORY_MODES:
                violations.append(
                    f"unknown mode '{mode}' in MODES; known: {list(REGULATORY_MODES)}"
                )

    # Checks: the lens's public surface must be named and non-empty.
    try:
        checks = lens.get_checks()
        if isinstance(checks, str) or not isinstance(checks, (tuple, list)) or not checks:
            violations.append("get_checks() must return a non-empty tuple of check names")
        else:
            for i, name in enumerate(checks):
                if not str(name).strip():
                    violations.append(f"get_checks()[{i}] is empty; a nameless check "
                                      f"cannot be disclosed")
    except Exception as exc:
        violations.append(f"get_checks() raised: {exc}")

    # Profile: must exist and be strictly JSON-safe -- the content hash
    # is only a commitment if the same configuration always serializes
    # to the same bytes. default=str fallbacks are deliberately NOT
    # allowed here: a profile carrying a non-JSON object would hash by
    # its repr, which can change without the configuration changing.
    try:
        profile = lens.get_profile()
        if not isinstance(profile, dict):
            violations.append(
                f"get_profile() must return a dict, got {type(profile).__name__}"
            )
        else:
            try:
                json.dumps(profile, sort_keys=True)
            except (TypeError, ValueError) as exc:
                violations.append(
                    f"get_profile() must be strictly JSON-serializable "
                    f"(it is content-hashed as the lens's configuration): {exc}"
                )
    except Exception as exc:
        violations.append(f"get_profile() raised: {exc}")

    if not callable(getattr(lens, "review", None)):
        violations.append("review(material) is missing or not callable")

    try:
        if lens.validate() is not True:
            violations.append("lens.validate() self-check did not return True")
    except Exception as exc:  # a self-check that crashes is a failed self-check
        violations.append(f"lens.validate() raised: {exc}")

    if violations:
        raise RegulatoryValidationError(label, violations)

    return lens.snapshot()


# ---------------------------------------------------------------------------
# Registry -- separate from the domain CassetteRegistry, on purpose.
# ---------------------------------------------------------------------------

class RegulatoryCassetteRegistry:
    """Registry of regulatory lenses. DELIBERATELY separate from
    cassette_interface.CassetteRegistry: a lens is not operational
    policy, must never be returned by a domain lookup, and must never
    look -- in code or in a ledger row -- like it drove a decision.
    The domain registry answers "what policy governs banking?"; this
    one answers "what lenses exist for review?". Two questions, two
    registries."""

    def __init__(self):
        self.lenses: Dict[str, RegulatoryCassette] = {}

    def register(self, lens: RegulatoryCassette) -> str:
        """Register a lens (fail-loud). Full validation runs here --
        registration is a load path, and no load path admits an
        unvalidated lens. Returns the lens identity."""
        validate_regulatory_cassette(lens)
        identity = regulatory_cassette_version_of(lens)
        self.lenses[identity] = lens
        return identity

    def get(self, identity: str) -> RegulatoryCassette:
        if identity in self.lenses:
            return self.lenses[identity]
        raise KeyError(
            f"No regulatory cassette registered under '{identity}'; "
            f"registered: {sorted(self.lenses)}"
        )

    def list_all(self) -> Dict[str, RegulatoryCassetteConfig]:
        return {identity: lens.get_config()
                for identity, lens in self.lenses.items()}
