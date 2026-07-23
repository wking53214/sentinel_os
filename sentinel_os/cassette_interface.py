"""
Cassette Interface -- the domain-blind KERNEL contract.

Every cassette, in every industry, implements exactly this:

  - identity          (get_config -- names the policy a ledger row cites)
  - typed declaration (get_governance_parameters -- the schema-validated
                       parameter contract; see cassette_schema)
  - judgment          (judge(episode) -> QualityResult -- score AND tier,
                       cassette-owned cutoffs)
  - explanation       (explain(episode) -> factor-level reasons)
  - self-check        (validate)
  - manifest          (CAPABILITIES -- which opt-in capability modules
                       this domain enables; see cassette_capabilities)

Nothing call-center-shaped remains here. Queue definitions, intent
labeling, the fixed (resolved, duration, friction_count, emotion_data)
scoring signature, abandonment diagnosis, reward signals, and healing
bounds all moved into opt-in capability contracts
(cassette_capabilities.py) that a cassette enables only when the
domain genuinely has that surface. The IVR cassette enables all of
them and is the REFERENCE IMPLEMENTATION of kernel + capabilities --
not the template other domains contort into.

Sentinel's role at this layer is JUDGE, not ACTOR: a cassette judges
an Episode (episode.py) -- the ground-truth record of what was
requested, what observably happened, and what the acting system
claims -- after the fact. It does not drive the decision.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from episode import Episode

@dataclass
class CassetteConfig:
    """Configuration for a cassette"""
    name: str
    version: str
    description: str
    domain: str  # "ivr", "banking", "healthcare", etc.

@dataclass(frozen=True)
class QualityResult:
    """A cassette's domain judgment of one outcome: score AND tier.

    The cassette owns both halves -- the score arithmetic and the
    cutoffs that turn a score into a tier label. Two domains may judge
    the same call differently by design (banking's "excellent" bar is
    not IVR's bar). Consumers that need the core's OutcomeQuality enum
    translate the tier label; they never re-derive a tier from the
    score with their own cutoffs, because then two places would own
    the same judgment and could quietly disagree.
    """
    score: float  # 0.0-1.0, cassette-computed
    tier: str  # "excellent" | "good" | "poor" | "failed"

class Cassette(ABC):
    """Abstract base: the KERNEL every cassette implements.

    Domain-shaped surfaces (telephony scoring, queues, rewards,
    healing) are NOT here -- they are capability contracts a concrete
    cassette additionally subclasses and lists in CAPABILITIES.
    """

    # The capability manifest. REQUIRED on every concrete cassette:
    # a tuple of names from cassette_capabilities.CAPABILITIES. An
    # empty tuple is a legitimate declaration ("kernel-only domain")
    # and must be made explicitly -- there is no default, because a
    # cassette that never said what it is would validate by accident.
    CAPABILITIES: Tuple[str, ...]

    @abstractmethod
    def get_config(self) -> CassetteConfig:
        """Return cassette metadata"""
        pass

    @abstractmethod
    def get_governance_parameters(self) -> Dict[str, Dict]:
        """The typed governance declaration this domain runs under.

        Shape is defined by cassette_schema (SCHEMA_VERSION). The
        REQUIRED set is the kernel's parameters plus the union of the
        enabled capabilities' parameters -- a cassette declares what
        its manifest obligates it to, no more (parameters owned by a
        capability it did not enable are rejected) and no less. Every
        value the engine reads on the governance path comes from
        here -- validated on load, read at decision time. See
        cassette_schema.validate_cassette.
        """
        pass

    @abstractmethod
    def judge(self, episode: Episode) -> QualityResult:
        """Judge one validated Episode with this domain's own rules.

        Returns QualityResult(score, tier); the cassette owns its tier
        cutoffs. Judgment reads episode.actual (the observed record)
        and episode.attributes -- NEVER episode.actor_report, which is
        the acting system's unverified story about itself. Callers go
        through episode.judge_episode so no judgment path admits an
        unvalidated episode.
        """
        pass

    @abstractmethod
    def explain(self, episode: Episode) -> List[Dict[str, Any]]:
        """Factor-level reasons behind this domain's judgment of the
        episode: a list of dicts, each naming one factor and its
        contribution, in the domain's own vocabulary. The kernel entry
        point (episode.explain_episode) prepends its own verification
        findings (actor-report divergences, outcome mismatches), so a
        cassette explains its judgment and the kernel guarantees the
        integrity findings ride along.
        """
        pass

    @abstractmethod
    def validate(self) -> bool:
        """Verify cassette is valid and complete"""
        pass

    def capabilities(self) -> Tuple[str, ...]:
        """The declared manifest, normalized. See
        cassette_capabilities.enabled_capabilities for the guard
        engines use."""
        from cassette_capabilities import enabled_capabilities
        return enabled_capabilities(self)

class CassetteRegistry:
    """Load and manage multiple cassettes"""

    def __init__(self):
        self.cassettes = {}

    def register(self, cassette: Cassette):
        """Register a cassette (fail-loud).

        Full schema validation runs here, not just the cassette's own
        self-check: an invalid cassette raises CassetteValidationError
        carrying the complete violation list. Registration is a load
        path, and no load path admits an unvalidated cassette.
        """
        from cassette_schema import validate_cassette

        config = cassette.get_config()
        key = f"{config.domain}:{config.name}"

        validate_cassette(cassette)

        self.cassettes[key] = cassette

    def get(self, domain: str) -> Cassette:
        """Get cassette by domain"""
        for key, cassette in self.cassettes.items():
            if key.startswith(domain):
                return cassette
        raise KeyError(f"No cassette found for domain: {domain}")

    def list_all(self) -> Dict:
        """List all registered cassettes"""
        return {
            key: cassette.get_config() for key, cassette in self.cassettes.items()
        }
