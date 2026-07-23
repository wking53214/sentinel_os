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
from typing import Dict, List, Tuple

# Manifest names -- stable strings, these ride in ledger snapshots.
CAPABILITY_TELEPHONY_INGEST = "telephony_ingest"
CAPABILITY_ROUTING_TOPOLOGY = "routing_topology"
CAPABILITY_RL = "rl"
CAPABILITY_SELF_HEALING = "self_healing"


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


# The registry load-time validation walks. An unknown name in a
# cassette's manifest is a violation, not a shrug.
CAPABILITIES: Dict[str, type] = {
    cap.NAME: cap
    for cap in (TelephonyIngest, RoutingTopology, ReinforcementLearning, SelfHealing)
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
