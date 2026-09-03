"""Artifact and reference types at the GEMS boundary.

The semantic artifact implementation is imported from ``conservation_kernel``.
GEMS does not copy or fork that model.  The small reference objects here make
lineage, provenance, and authority references explicit in transport messages.
"""

from __future__ import annotations

from dataclasses import dataclass

from conservation_kernel import (
    Actor,
    Artifact,
    AuthorityStatus,
    CanonicalState,
    EpistemicStatus,
    FunctionalContract,
    OriginStatus,
    Proposition,
    TemporalMetadata,
    TemporalScope,
    Uncertainty,
    UncertaintyState,
)
from conservation_kernel.enums import TransitionKind

from .errors import InvalidContract


def _required(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidContract(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True)
class LineageReference:
    """A content-addressed reference to an artifact accepted by the gateway."""

    artifact_id: str
    artifact_digest: str
    relation: str = "PARENT"

    def __post_init__(self) -> None:
        _required(self.artifact_id, "artifact_id")
        _required(self.artifact_digest, "artifact_digest")
        _required(self.relation, "relation")

    @classmethod
    def from_artifact(cls, artifact: Artifact, *, relation: str = "PARENT") -> "LineageReference":
        return cls(artifact.artifact_id, artifact.artifact_digest, relation)

    def to_dict(self) -> dict[str, str]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_digest": self.artifact_digest,
            "relation": self.relation,
        }


@dataclass(frozen=True)
class ProvenanceReference:
    """A typed reference to source material or supporting provenance."""

    reference_id: str
    subject_id: str
    kind: str = "SOURCE"

    def __post_init__(self) -> None:
        _required(self.reference_id, "reference_id")
        _required(self.subject_id, "subject_id")
        _required(self.kind, "kind")

    def to_dict(self) -> dict[str, str]:
        return {
            "reference_id": self.reference_id,
            "subject_id": self.subject_id,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class AuthorityReference:
    """A reference to an externally registered authorization event."""

    authorization_id: str
    subject_id: str
    transition_kind: TransitionKind

    def __post_init__(self) -> None:
        _required(self.authorization_id, "authorization_id")
        _required(self.subject_id, "subject_id")
        if not isinstance(self.transition_kind, TransitionKind):
            try:
                object.__setattr__(self, "transition_kind", TransitionKind(self.transition_kind))
            except (TypeError, ValueError) as exc:
                raise InvalidContract("invalid authority transition_kind") from exc

    def to_dict(self) -> dict[str, str]:
        return {
            "authorization_id": self.authorization_id,
            "subject_id": self.subject_id,
            "transition_kind": self.transition_kind.value,
        }


__all__ = [
    "Actor",
    "Artifact",
    "AuthorityReference",
    "AuthorityStatus",
    "CanonicalState",
    "EpistemicStatus",
    "FunctionalContract",
    "LineageReference",
    "OriginStatus",
    "Proposition",
    "ProvenanceReference",
    "TemporalMetadata",
    "TemporalScope",
    "Uncertainty",
    "UncertaintyState",
]

