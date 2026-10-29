"""The root guard for Sentinel's conservation boundary: a root registers only when stamped events reproduce the Episode.

WHY THIS EXISTS

`verify_governed_decision` builds a root artifact from an Episode and registers it with the kernel in the same
function. Nothing outside that function said the Episode was a fair account of anything, so the kernel's root
admission was a ceremony: the code that wants the root is the code that approves it. The observed events are an
independent account. `event_v1.assemble_episode` folds stamped events into an Episode, so an Episode that really came
from its events can be rebuilt from them, field for field.

`EpisodeRootGuard` is the callable the kernel holds as its `root_guard`. It is closed. `attest(episode, events)` rebuilds
the Episode from the events (with the Episode's own requested, outcome reasons and attributes, which are not
observations) and opens the guard only if the rebuilt Episode equals the one given. It then says yes once, to the one
root artifact that Episode produces, and no to everything else. A refusal does not spend it.

WHAT IT DOES NOT DO

The events are supplied by the same caller as the Episode, and their stamps (source, provenance) are claims. This
catches an Episode that says more, or something else, than its events support. It does not prove the events are
true. Where no events are supplied there is nothing to compare, and the boundary keeps its explicit
`allow_unguarded_roots=True` path: that gap is named, not closed. One guard serves one Episode.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Tuple

from conservation_kernel import EvidenceRegistry

from conservation.episode_source import build_episode_source
from episode import Episode

__all__ = ["EpisodeNotAttested", "EpisodeRootGuard"]


class EpisodeNotAttested(Exception):
    """The observed events do not reproduce the Episode, so its root must not be registered."""


def _identity(artifact: Any) -> Tuple[str, str]:
    return artifact.artifact_id, artifact.content_digest


class EpisodeRootGuard:
    """Closed until `attest` succeeds; then opens once, for the one root that Episode produces."""

    def __init__(self) -> None:
        self._approved: Optional[Tuple[str, str]] = None
        self._spent = False

    @property
    def attested(self) -> bool:
        return self._approved is not None

    def attest(self, episode: Episode, events: Iterable[Any]) -> None:
        """Open the guard for `episode`, or raise EpisodeNotAttested and leave it closed."""
        if self._approved is not None:
            raise EpisodeNotAttested("this guard was already attested; one guard serves one episode")
        from event_v1 import assemble_episode  # lazy, like the ledger: keeps the cold path clean

        events = list(events)
        if not events:
            raise EpisodeNotAttested("no observed events were supplied, so there is nothing to attest the episode against")
        try:
            rebuilt = assemble_episode(
                episode.episode_id, episode.domain, episode.requested, events,
                outcome_reasons=episode.outcome_reasons, attributes=episode.attributes,
            ).episode
        except Exception as exc:  # an event that does not validate, or from another episode
            raise EpisodeNotAttested(f"the observed events could not be assembled into an episode: {exc}") from exc
        if rebuilt != episode:
            differs = sorted(name for name in ("requested", "actual", "actor_report", "timeline", "outcome_reasons", "attributes")
                             if getattr(rebuilt, name) != getattr(episode, name))
            raise EpisodeNotAttested(
                "the observed events do not reproduce this episode; it differs from what they support in: "
                + ", ".join(differs or ["episode_id or domain"]))
        self._approved = _identity(build_episode_source(episode, EvidenceRegistry()))

    def __call__(self, artifact: Any) -> bool:
        if self._approved is None or self._spent:
            return False
        try:
            same = _identity(artifact) == self._approved
        except Exception:  # a root that cannot be read never permits
            return False
        if not same:
            return False
        self._spent = True
        return True
