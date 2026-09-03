"""Versioned, machine-readable contracts for Gem transformations."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from conservation_kernel import Actor, ActorKind, Artifact, DeclaredChange, TransformationRecord as KernelTransformationRecord

from .artifact import AuthorityReference, LineageReference, ProvenanceReference
from .errors import InvalidContract

PROTOCOL_VERSION = "GEMS/0.1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidContract(f"{field_name} must be a non-empty string")
    return value


def _strings(values: tuple[str, ...] | list[str], field_name: str) -> tuple[str, ...]:
    result = tuple(values)
    if any(not isinstance(value, str) or not value.strip() for value in result):
        raise InvalidContract(f"{field_name} must contain non-empty strings")
    if len(result) != len(set(result)):
        raise InvalidContract(f"{field_name} may not contain duplicates")
    return result


class TransportState(str, Enum):
    PROPOSED = "PROPOSED"
    VALIDATING = "VALIDATING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class DecisionStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    REQUIRES_AUTHORIZATION = "REQUIRES_AUTHORIZATION"
    REQUIRES_VERIFICATION = "REQUIRES_VERIFICATION"


@dataclass(frozen=True)
class GemIdentity:
    """An identity registered by the gateway, not asserted by a Gem output."""

    gem_id: str
    gem_version: str
    implementation_id: str
    role: str
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    actor_kind: ActorKind = ActorKind.MODEL

    def __post_init__(self) -> None:
        for name in ("gem_id", "gem_version", "implementation_id", "role"):
            _text(getattr(self, name), name)
        object.__setattr__(self, "capabilities", _strings(self.capabilities, "capabilities"))
        if not isinstance(self.actor_kind, ActorKind):
            try:
                object.__setattr__(self, "actor_kind", ActorKind(self.actor_kind))
            except (TypeError, ValueError) as exc:
                raise InvalidContract("invalid Gem actor_kind") from exc

    @property
    def key(self) -> str:
        return f"{self.gem_id}@{self.gem_version}:{self.implementation_id}"

    def actor(self) -> Actor:
        return Actor(self.implementation_id, self.actor_kind, self.gem_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gem_id": self.gem_id,
            "gem_version": self.gem_version,
            "implementation_id": self.implementation_id,
            "role": self.role,
            "capabilities": list(self.capabilities),
            "actor_kind": self.actor_kind.value,
            "key": self.key,
        }


@dataclass(frozen=True)
class TransformationRequest:
    """The input gate message given to an untrusted Gem."""

    request_id: str
    source: LineageReference
    input_artifact: Artifact
    gem: GemIdentity
    transformation_type: str
    intent: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _text(self.request_id, "request_id")
        if not isinstance(self.source, LineageReference):
            raise InvalidContract("source must be a LineageReference")
        if not isinstance(self.input_artifact, Artifact):
            raise InvalidContract("input_artifact must be a conservation_kernel Artifact")
        if self.source.artifact_id != self.input_artifact.artifact_id or self.source.artifact_digest != self.input_artifact.artifact_digest:
            raise InvalidContract("source reference must match input_artifact identity")
        if not isinstance(self.gem, GemIdentity):
            raise InvalidContract("gem must be a GemIdentity")
        _text(self.transformation_type, "transformation_type")
        _text(self.intent, "intent")
        values = dict(self.metadata or {})
        try:
            json.dumps(values, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise InvalidContract("request metadata must be JSON serializable") from exc
        object.__setattr__(self, "metadata", values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": self.request_id,
            "source": self.source.to_dict(),
            "gem": self.gem.to_dict(),
            "transformation_type": self.transformation_type,
            "intent": self.intent,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class TransformationRecord:
    """GEMS transport record wrapping the kernel's authoritative record."""

    request_id: str
    transformation_id: str
    source: LineageReference
    output: LineageReference
    gem: GemIdentity
    transformation_type: str
    intent: str
    kernel_record: KernelTransformationRecord
    provenance_refs: tuple[ProvenanceReference, ...] = field(default_factory=tuple)
    authority_refs: tuple[AuthorityReference, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _text(self.request_id, "request_id")
        _text(self.transformation_id, "transformation_id")
        if not isinstance(self.source, LineageReference) or not isinstance(self.output, LineageReference):
            raise InvalidContract("source and output must be LineageReference objects")
        if not isinstance(self.gem, GemIdentity):
            raise InvalidContract("gem must be a GemIdentity")
        if not isinstance(self.kernel_record, KernelTransformationRecord):
            raise InvalidContract("kernel_record must be a conservation_kernel TransformationRecord")
        if self.kernel_record.transformation_id != self.transformation_id:
            raise InvalidContract("GEMS and kernel transformation IDs must match")
        if tuple(self.kernel_record.input_artifact_ids) != (self.source.artifact_id,):
            raise InvalidContract("kernel record must name the GEMS source artifact")
        if self.kernel_record.transformer != self.gem.actor():
            raise InvalidContract("kernel transformer actor must match Gem identity")
        _text(self.transformation_type, "transformation_type")
        _text(self.intent, "intent")
        object.__setattr__(self, "provenance_refs", tuple(self.provenance_refs))
        object.__setattr__(self, "authority_refs", tuple(self.authority_refs))
        values = dict(self.metadata or {})
        try:
            json.dumps(values, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise InvalidContract("transformation metadata must be JSON serializable") from exc
        object.__setattr__(self, "metadata", values)

    @property
    def declared_changes(self) -> tuple[DeclaredChange, ...]:
        return self.kernel_record.declared_changes

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        return self.kernel_record.evidence_refs

    @property
    def authorization_refs(self) -> tuple[str, ...]:
        return self.kernel_record.authorization_refs

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": self.request_id,
            "transformation_id": self.transformation_id,
            "source": self.source.to_dict(),
            "output": self.output.to_dict(),
            "gem": self.gem.to_dict(),
            "transformation_type": self.transformation_type,
            "intent": self.intent,
            "kernel_record": self.kernel_record.to_dict(),
            "provenance_refs": [item.to_dict() for item in self.provenance_refs],
            "authority_refs": [item.to_dict() for item in self.authority_refs],
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class TransformationProposal:
    """Untrusted Gem output.  It is not an accepted artifact."""

    request_id: str
    output_artifact: Artifact
    record: TransformationRecord
    claimed_validation_results: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _text(self.request_id, "request_id")
        if not isinstance(self.output_artifact, Artifact):
            raise InvalidContract("output_artifact must be a conservation_kernel Artifact")
        if not isinstance(self.record, TransformationRecord):
            raise InvalidContract("record must be a GEMS TransformationRecord")
        if self.record.request_id != self.request_id:
            raise InvalidContract("proposal request_id must match record request_id")
        object.__setattr__(self, "claimed_validation_results", _strings(self.claimed_validation_results, "claimed_validation_results"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "output_artifact": self.output_artifact.to_dict(),
            "record": self.record.to_dict(),
            "claimed_validation_results": list(self.claimed_validation_results),
        }


@dataclass(frozen=True)
class Rejection:
    code: str
    detail: str
    dimension: str | None = None
    subject_id: str | None = None

    def __post_init__(self) -> None:
        _text(self.code, "rejection code")
        _text(self.detail, "rejection detail")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "dimension": self.dimension,
            "subject_id": self.subject_id,
        }


@dataclass(frozen=True)
class ConservationDecision:
    status: DecisionStatus
    kernel_status: str | None
    rejections: tuple[Rejection, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.status, DecisionStatus):
            try:
                object.__setattr__(self, "status", DecisionStatus(self.status))
            except (TypeError, ValueError) as exc:
                raise InvalidContract("invalid conservation decision status") from exc
        object.__setattr__(self, "rejections", tuple(self.rejections))

    @property
    def accepted(self) -> bool:
        return self.status is DecisionStatus.ACCEPTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "kernel_status": self.kernel_status,
            "accepted": self.accepted,
            "rejections": [item.to_dict() for item in self.rejections],
        }


@dataclass(frozen=True)
class TransformationResult:
    """The only result a downstream Gem is allowed to consume."""

    request_id: str
    transformation_id: str
    state: TransportState
    decision: ConservationDecision
    record: TransformationRecord | None
    candidate_artifact: Artifact | None
    accepted_artifact: Artifact | None
    kernel_result: Any = None

    def __post_init__(self) -> None:
        if self.state is TransportState.ACCEPTED and not self.decision.accepted:
            raise InvalidContract("accepted transport state requires an accepted decision")
        if self.decision.accepted and self.accepted_artifact is None:
            raise InvalidContract("accepted decision requires an accepted artifact")
        if not self.decision.accepted and self.accepted_artifact is not None:
            raise InvalidContract("rejected decision cannot expose an accepted artifact")

    @property
    def accepted(self) -> bool:
        return self.decision.accepted

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": self.request_id,
            "transformation_id": self.transformation_id,
            "state": self.state.value,
            "decision": self.decision.to_dict(),
            "record": self.record.to_dict() if self.record else None,
            "candidate_artifact": self.candidate_artifact.to_dict() if self.candidate_artifact else None,
            "accepted_artifact": self.accepted_artifact.to_dict() if self.accepted_artifact else None,
            "kernel_result": self.kernel_result.to_dict() if self.kernel_result is not None else None,
        }


class GemTransformer(Protocol):
    """Future real Gems implement this protocol; reference Gems are deterministic."""

    identity: GemIdentity

    def make_request(self, input_artifact: Artifact, **kwargs: Any) -> TransformationRequest:
        ...

    def transform(self, request: TransformationRequest) -> TransformationProposal:
        ...


__all__ = [
    "AuthorityReference",
    "ConservationDecision",
    "DecisionStatus",
    "GemIdentity",
    "GemTransformer",
    "LineageReference",
    "PROTOCOL_VERSION",
    "ProvenanceReference",
    "Rejection",
    "TransformationProposal",
    "TransformationRecord",
    "TransformationRequest",
    "TransformationResult",
    "TransportState",
]
