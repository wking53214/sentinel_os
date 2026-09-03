"""Gem registration and append-only transport ledger."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from conservation_kernel import Artifact

from .contracts import GemIdentity, TransformationResult, utc_now
from .errors import InvalidContract


class GemRegistry:
    """Explicit allow-list of identities recognized by a GEMS deployment."""

    def __init__(self) -> None:
        self._identities: dict[str, GemIdentity] = {}

    def register(self, identity: GemIdentity) -> None:
        if not isinstance(identity, GemIdentity):
            raise InvalidContract("only GemIdentity objects can be registered")
        existing = self._identities.get(identity.key)
        if existing is not None and existing != identity:
            raise InvalidContract(f"Gem identity key collision: {identity.key}")
        self._identities[identity.key] = identity

    def contains(self, identity: GemIdentity) -> bool:
        return self._identities.get(identity.key) == identity

    def identities(self) -> tuple[GemIdentity, ...]:
        return tuple(self._identities.values())

    def snapshot(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._identities.values()]


@dataclass(frozen=True)
class LedgerEntry:
    """One durable root, accepted transformation, or rejected attempt."""

    sequence: int
    event_type: str
    created_at: str
    artifact_id: str
    artifact: dict[str, Any]
    request_id: str | None = None
    transformation_id: str | None = None
    source_artifact_id: str | None = None
    decision_status: str | None = None
    transport_state: str | None = None
    gem: dict[str, Any] | None = None
    result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "event_type": self.event_type,
            "created_at": self.created_at,
            "artifact_id": self.artifact_id,
            "artifact": self.artifact,
            "request_id": self.request_id,
            "transformation_id": self.transformation_id,
            "source_artifact_id": self.source_artifact_id,
            "decision_status": self.decision_status,
            "transport_state": self.transport_state,
            "gem": self.gem,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LedgerEntry":
        required = ("sequence", "event_type", "created_at", "artifact_id", "artifact")
        missing = [name for name in required if name not in data]
        if missing:
            raise InvalidContract(f"ledger entry missing fields: {missing}")
        return cls(
            sequence=data["sequence"],
            event_type=data["event_type"],
            created_at=data["created_at"],
            artifact_id=data["artifact_id"],
            artifact=data["artifact"],
            request_id=data.get("request_id"),
            transformation_id=data.get("transformation_id"),
            source_artifact_id=data.get("source_artifact_id"),
            decision_status=data.get("decision_status"),
            transport_state=data.get("transport_state"),
            gem=data.get("gem"),
            result=data.get("result"),
        )


class TransformationLedger:
    """Append-only JSONL ledger for transport events.

    The conservation kernel remains authoritative for accepted artifact
    reconstruction.  This ledger adds the GEMS transport identity and rejected
    attempts.  JSONL is durable enough for the v0.1 experiment but is not a
    cryptographically authenticated or tamper-proof store.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._entries: list[LedgerEntry] = []
        if self.path is not None and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._entries.append(LedgerEntry.from_dict(json.loads(line)))

    def _append(self, entry: LedgerEntry) -> LedgerEntry:
        self._entries.append(entry)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry.to_dict(), sort_keys=True) + "\n")
        return entry

    def record_root(self, artifact: Artifact) -> LedgerEntry:
        if any(item.event_type == "ROOT" and item.artifact_id == artifact.artifact_id for item in self._entries):
            return next(item for item in self._entries if item.event_type == "ROOT" and item.artifact_id == artifact.artifact_id)
        return self._append(
            LedgerEntry(
                sequence=len(self._entries) + 1,
                event_type="ROOT",
                created_at=artifact.created_at,
                artifact_id=artifact.artifact_id,
                artifact=artifact.to_dict(),
            )
        )

    def record_result(self, result: TransformationResult) -> LedgerEntry:
        record = result.record
        candidate = result.candidate_artifact
        return self._append(
            LedgerEntry(
                sequence=len(self._entries) + 1,
                event_type="TRANSFORMATION",
                created_at=record.created_at if record is not None else utc_now(),
                artifact_id=candidate.artifact_id if candidate is not None else result.transformation_id,
                artifact=candidate.to_dict() if candidate is not None else {},
                request_id=result.request_id,
                transformation_id=result.transformation_id,
                source_artifact_id=record.source.artifact_id if record is not None else None,
                decision_status=result.decision.status.value,
                transport_state=result.state.value,
                gem=record.gem.to_dict() if record is not None else None,
                result=result.to_dict(),
            )
        )

    def entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(self._entries)

    def accepted_entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(item for item in self._entries if item.decision_status == "ACCEPTED")

    def rejected_entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(item for item in self._entries if item.event_type == "TRANSFORMATION" and item.decision_status != "ACCEPTED")

    def snapshot(self) -> dict[str, Any]:
        return {"entries": [item.to_dict() for item in self._entries]}

