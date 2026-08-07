"""
Cassette capabilities -- opt-in modules, each owning its own contract.

The kernel (cassette_interface.Cassette) is domain-blind: identity,
typed parameter declaration, judge(episode), explain(episode). Every
domain-SHAPED obligation lives here instead, as a capability a
cassette explicitly enables in its manifest (Cassette.CAPABILITIES):

  telephony_ingest -- this domain ingests phone calls. Owns the
      Twilio duration thresholds and long_wait_threshold, and the
      call-shaped judgment surface (score_outcome_quality,
      diagnose_abandonment, get_friction_thresholds) whose fixed
      (resolved, duration, friction_count, emotion_data) signature
      used to be forced on every domain.
  routing_topology -- this domain routes work through named queues.
      Owns queue definitions and intent labeling.
  rl -- this domain trains against a reward signal. Owns
      compute_reward.
  self_healing -- this domain lets the governor adjust its own
      parameters inside declared bounds. Owns get_healing_bounds and
      the expected_wait_bounds clamp band.
  outcome_obligation -- this domain's outcomes are NOT known at
      decision time and mature later on a declared schedule (a loan's
      performance, a claim's ultimate cost). Owns the maturation
      horizon and the domain call on whether a matured outcome was
      favorable. Opt-in for the same reason every capability is: an
      IVR call's quality is settled at hangup, so IVR owes nothing
      later, and forcing it to declare a horizon would produce exactly
      the fake declaration the anti-placeholder rule below exists to
      stop.
  interpretation_testable -- this domain's decisions can be probed by
      the monthly interpretation drift-check (interpretation/
      harness.py): given an approved Scenario, resolve_scenario
      answers with this domain's own reading (one of the scenario's
      options, or None to decline). Opt-in by default, same posture as
      every other capability -- most domains have no regulation-reading
      a drift-check needs to probe. NOT opt-in, however, for a cassette
      that also enables outcome_obligation, or that declares a
      REGULATORY_BINDINGS entry (cassette_interface.Cassette): a domain
      whose outcomes mature later, or that a regulatory lens is bound
      to review, is exactly the kind of decision this drift-check
      exists to probe, and cassette_schema.validate_governance_
      parameters refuses to load such a cassette unless it also enables
      this capability and implements resolve_scenario -- the same
      fail-closed, anti-placeholder posture as every other rule in this
      module, applied to "silently untestable" instead of "silently
      fake."

Load-time validation (cassette_schema.validate_cassette) checks the
kernel contract plus the UNION of the enabled capabilities' contracts:
methods present, parameters declared with the right types. It is
fail-closed in both directions -- enabling a capability without
implementing its contract is a violation, and declaring a parameter
OWNED by a capability the cassette did not enable is also a violation.
The second rule exists because of a real incident: the banking
cassette once declared three placeholder Twilio thresholds, explicitly
flagged as fake, purely to satisfy a universal required-parameter list.
A contract that forces fake declarations is worse than no contract;
capability-scoped requirements make the honest declaration ("I don't
ingest telephony") expressible.

Engines guard their entry points with require_capabilities() so a
cassette missing what a pipeline needs is refused at construction with
a clear error, not discovered mid-call as a KeyError.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

# Manifest names -- stable strings, these ride in ledger snapshots.
CAPABILITY_TELEPHONY_INGEST = "telephony_ingest"
CAPABILITY_ROUTING_TOPOLOGY = "routing_topology"
CAPABILITY_RL = "rl"
CAPABILITY_SELF_HEALING = "self_healing"
CAPABILITY_OUTCOME_OBLIGATION = "outcome_obligation"
CAPABILITY_INTERPRETATION_TESTABLE = "interpretation_testable"


class CapabilityError(Exception):
    """An engine needed capabilities the loaded cassette does not
    enable. Raised at construction/swap time -- fail-closed at the
    door, not mid-call."""


class TelephonyIngest(ABC):
    """Contract for domains that ingest phone calls.

    Owns the ingest thresholds AND the call-shaped judgment surface.
    A domain that enables this capability judges calls with the fixed
    (resolved, duration, friction_count, emotion_data) signature; a
    domain that doesn't is judged only through the kernel's
    judge(episode) and never has to fake call-center parameters.
    """

    NAME = CAPABILITY_TELEPHONY_INGEST
    REQUIRED_PARAMETERS: Dict[str, str] = {
        # A wait longer than this, at any node, is one friction event.
        "long_wait_threshold": "float",
        # Twilio ingest: calls longer than this contribute 2 friction points.
        "twilio_long_duration_threshold": "int",
        # Twilio ingest: calls longer than this contribute 1 friction point.
        "twilio_medium_duration_threshold": "int",
        # Twilio ingest: non-completed calls shorter than this are dropped-call friction.
        "twilio_short_duration_threshold": "int",
    }
    REQUIRED_METHODS: Tuple[str, ...] = (
        "score_outcome_quality",
        "diagnose_abandonment",
        "get_friction_thresholds",
    )

    @abstractmethod
    def score_outcome_quality(self, resolved: bool, duration: float,
                              friction_count: int, emotion_data: Dict):
        """Score one call with this domain's own rules; returns
        QualityResult (the cassette owns score arithmetic AND tier
        cutoffs -- see cassette_interface.QualityResult)."""

    @abstractmethod
    def diagnose_abandonment(self, journey: List[str], friction: List,
                             emotion: Dict, resolved: bool) -> Dict:
        """Name, in domain vocabulary, why a call abandoned."""

    @abstractmethod
    def get_friction_thresholds(self) -> Dict[str, float]:
        """Domain-specific friction detection thresholds."""


class RoutingTopology(ABC):
    """Contract for domains that route work through named queues."""

    NAME = CAPABILITY_ROUTING_TOPOLOGY
    REQUIRED_PARAMETERS: Dict[str, str] = {}
    REQUIRED_METHODS: Tuple[str, ...] = (
        "get_queue_definitions",
        "_infer_intent_to_label",
    )

    @abstractmethod
    def get_queue_definitions(self) -> Dict[str, Dict]:
        """Return queue names and properties (non-empty)."""

    @abstractmethod
    def _infer_intent_to_label(self, queue_name: str, caller_data: Dict) -> str:
        """Map a queue choice to an intent label."""


class ReinforcementLearning(ABC):
    """Contract for domains that train against a reward signal."""

    NAME = CAPABILITY_RL
    REQUIRED_PARAMETERS: Dict[str, str] = {}
    REQUIRED_METHODS: Tuple[str, ...] = ("compute_reward",)

    @abstractmethod
    def compute_reward(self, outcome: Dict) -> float:
        """RL reward signal for this domain."""


class SelfHealing(ABC):
    """Contract for domains that allow governed self-adjustment.

    Opt-in on purpose: judge-mode deployments (Sentinel as witness,
    not actor) have no healing surface at all, and a cassette that
    doesn't enable this capability gives the governor no bounds to
    move anything within."""

    NAME = CAPABILITY_SELF_HEALING
    REQUIRED_PARAMETERS: Dict[str, str] = {
        # Self-healing clamp band for the expected_wait parameter.
        "expected_wait_bounds": "range",
    }
    REQUIRED_METHODS: Tuple[str, ...] = ("get_healing_bounds",)

    @abstractmethod
    def get_healing_bounds(self) -> Dict[str, tuple]:
        """Domain-specific parameter bounds for self-healing."""


class OutcomeObligations(ABC):
    """Contract for domains whose outcomes mature AFTER the decision.

    The split this capability draws is the one that keeps the decision
    record closeable: a domain either knows its outcome at decision
    time or it does not, and a domain that does not owes a separate,
    durable obligation record instead of a decision row that reopens.
    See outcome_v1 for the record and the Provenance Rule governing it.

    The horizon is a governance PARAMETER, not a constant in code, so
    it lands in the hashed policy snapshot with the rest of the
    declaration -- "how long we said we would watch this" is exactly
    the kind of commitment an auditor checks against what actually
    happened.
    """

    NAME = CAPABILITY_OUTCOME_OBLIGATION
    REQUIRED_PARAMETERS: Dict[str, str] = {
        # Days from decision until an outcome obligation of this
        # domain's kind matures ("loan decisions carry a 24-month
        # performance obligation" -> 730).
        "outcome_horizon_days": "int",
    }
    REQUIRED_METHODS: Tuple[str, ...] = (
        "get_maturation_rule",
        "classify_outcome",
    )

    @abstractmethod
    def get_maturation_rule(self):
        """Return this domain's outcome_v1.MaturationRule: what kind of
        obligation a decision carries, and how long until it matures.
        The rule's declaration string is what hashes into the decision
        row and what the twin re-parses to derive the open set."""

    @abstractmethod
    def classify_outcome(self, evidence: Dict) -> "bool | None":
        """The domain's call on whether a matured outcome was
        favorable: True, False, or None for genuinely ambiguous.

        None is a first-class answer, not a failure. An outcome forced
        to a bool it does not support becomes a fabricated input to a
        fairness statistic, so the schema keeps the obligation OPEN on
        REASON_GENUINELY_AMBIGUOUS instead."""


class InterpretationTestable(ABC):
    """Contract for domains whose decisions can be probed by the
    monthly interpretation drift-check (interpretation/harness.py).

    resolve_scenario follows the kernel's judge()/explain() pattern:
    given a typed input (here, interpretation.scenarios.Scenario) it
    returns a typed answer the caller grades against a locked expected
    value. The answer shape matches interpretation.harness.Resolver
    exactly -- one of the scenario's own options, or None to decline --
    so a cassette's bound method is a drop-in resolver for
    TestHarness.run with no adapter needed.

    Opt-in, same posture as every other capability, EXCEPT: a cassette
    that also enables outcome_obligation, or that declares a non-empty
    REGULATORY_BINDINGS (cassette_interface.Cassette), MUST enable this
    capability too -- see the module docstring and
    cassette_schema.validate_governance_parameters, which refuses to
    load such a cassette otherwise.
    """

    NAME = CAPABILITY_INTERPRETATION_TESTABLE
    REQUIRED_PARAMETERS: Dict[str, str] = {}
    REQUIRED_METHODS: Tuple[str, ...] = ("resolve_scenario",)

    @abstractmethod
    def resolve_scenario(self, scenario: Any) -> Optional[str]:
        """Answer one interpretation scenario with this domain's own
        reading of it.

        scenario is an interpretation.scenarios.Scenario (typed as Any
        here so this kernel-adjacent module does not import the
        higher-level interpretation package). Returns the chosen
        option (a string that must appear in scenario.options) or None
        to decline -- declining is a first-class answer, not a
        failure, the same posture classify_outcome takes on a
        genuinely ambiguous outcome."""


# The registry load-time validation walks. An unknown name in a
# cassette's manifest is a violation, not a shrug.
CAPABILITIES: Dict[str, type] = {
    cap.NAME: cap
    for cap in (TelephonyIngest, RoutingTopology, ReinforcementLearning,
                SelfHealing, OutcomeObligations, InterpretationTestable)
}

# Reverse map: which capability OWNS a given governance parameter.
# Used to reject parameters declared without their owning capability
# enabled (the anti-placeholder rule described in the module docstring).
PARAMETER_OWNERS: Dict[str, str] = {
    param: cap.NAME
    for cap in CAPABILITIES.values()
    for param in cap.REQUIRED_PARAMETERS
}


def enabled_capabilities(cassette) -> Tuple[str, ...]:
    """The cassette's declared manifest, normalized. Raises
    CapabilityError if the declaration is missing or malformed --
    a cassette that cannot state what it is does not run. (Full
    manifest validation with the complete violation list happens in
    cassette_schema.validate_cassette; this accessor is for engines
    that need the manifest after validation has already passed.)"""
    declared = getattr(cassette, "CAPABILITIES", None)
    if declared is None:
        raise CapabilityError(
            f"{type(cassette).__name__} declares no CAPABILITIES manifest; "
            f"every cassette must declare one (an empty tuple means "
            f"kernel-only, and must be said explicitly)"
        )
    if isinstance(declared, str) or not isinstance(declared, (tuple, list)):
        raise CapabilityError(
            f"{type(cassette).__name__}.CAPABILITIES must be a tuple/list of "
            f"capability names, got {type(declared).__name__}"
        )
    return tuple(str(name) for name in declared)


def require_capabilities(cassette, required: Tuple[str, ...], consumer: str) -> None:
    """Engine-side guard: refuse, at the door, a cassette that does not
    enable everything this pipeline reads. The error names the
    consumer, what it needs, and what the cassette enables, so the fix
    is legible from the message alone."""
    enabled = set(enabled_capabilities(cassette))
    missing = [name for name in required if name not in enabled]
    if missing:
        label = getattr(getattr(cassette, "get_config", lambda: None)(), "domain",
                        type(cassette).__name__)
        raise CapabilityError(
            f"{consumer} requires capabilities {sorted(required)} but cassette "
            f"'{label}' enables {sorted(enabled) or '[] (kernel-only)'}; "
            f"missing: {missing}. This pipeline cannot run this cassette -- "
            f"use a judgment path built on the kernel (judge/explain over "
            f"episodes) or load a cassette that enables the capability."
        )
