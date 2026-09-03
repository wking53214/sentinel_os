"""Base contract and deterministic helpers for reference Gems."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from hashlib import sha256
from typing import Any

from conservation_kernel import (
    Actor,
    Artifact,
    DeclaredChange,
    Dimension,
    Proposition,
    TransformationRecord as KernelTransformationRecord,
)
from conservation_kernel.model import canonical_json

from .artifact import AuthorityReference, LineageReference, ProvenanceReference
from .contracts import (
    GemIdentity,
    TransformationProposal,
    TransformationRecord,
    TransformationRequest,
    utc_now,
)


class BaseGem:
    """Reference implementation base class.

    A Gem can construct a proposal, including false declarations.  It cannot
    commit an artifact: only ``ConservationGateway.submit`` can do that.
    """

    def __init__(self, identity: GemIdentity, *, clock: Callable[[], str] = utc_now) -> None:
        self.identity = identity
        self._clock = clock

    def make_request(
        self,
        input_artifact: Artifact,
        *,
        transformation_type: str | None = None,
        intent: str | None = None,
        request_suffix: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> TransformationRequest:
        suffix = f"-{request_suffix}" if request_suffix else ""
        return TransformationRequest(
            request_id=f"req-{self.identity.gem_id}-{input_artifact.artifact_id}{suffix}",
            source=LineageReference.from_artifact(input_artifact),
            input_artifact=input_artifact,
            gem=self.identity,
            transformation_type=transformation_type or self.identity.role.upper(),
            intent=intent or f"deterministic {self.identity.role.lower()} transformation",
            metadata=dict(metadata or {}),
            created_at=self._clock(),
        )

    def transform(self, request: TransformationRequest) -> TransformationProposal:
        raise NotImplementedError

    def _artifact(
        self,
        request: TransformationRequest,
        *,
        suffix: str,
        content: str,
        propositions: Iterable[Proposition] | None = None,
        producer: Actor | None = None,
    ) -> Artifact:
        source = request.input_artifact
        return Artifact(
            artifact_id=f"{source.artifact_id}:{self.identity.gem_id}:{suffix}",
            content=content,
            propositions=tuple(propositions if propositions is not None else source.propositions),
            producer=producer or self.identity.actor(),
            parent_artifact_ids=(source.artifact_id,),
            version=source.version + 1,
            functional_contract=source.functional_contract,
            created_at=request.created_at,
        )

    @staticmethod
    def _text_digest(value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def declared_changes_for(
        cls,
        source: Artifact,
        output: Artifact,
        *,
        reason: str,
        transition_kind=None,
    ) -> tuple[DeclaredChange, ...]:
        """Declare independently observable changes for a reference Gem.

        This helper is deliberately available to hostile fixtures as well.
        The declarations remain untrusted claims and are rechecked by the
        conservation kernel.
        """

        changes: list[DeclaredChange] = []

        def add(subject: str, dimension: Dimension, before: Any, after: Any) -> None:
            if canonical_json(before) != canonical_json(after):
                changes.append(DeclaredChange(subject, dimension, before, after, reason, transition_kind))

        add(output.artifact_id, Dimension.CONTENT, source.content_digest, output.content_digest)
        old = source.proposition_map()
        new = output.proposition_map()
        for proposition_id, old_prop in old.items():
            if proposition_id not in new:
                add(proposition_id, Dimension.LINEAGE, "present", "absent")
                continue
            new_prop = new[proposition_id]
            add(proposition_id, Dimension.CONTENT, cls._text_digest(old_prop.text), cls._text_digest(new_prop.text))
            fields = (
                (Dimension.EPISTEMIC_STATUS, old_prop.epistemic_status.value, new_prop.epistemic_status.value),
                (Dimension.HUMAN_ORIGIN, old_prop.origin.value, new_prop.origin.value),
                (Dimension.AUTHORITY, old_prop.authority.value, new_prop.authority.value),
                (Dimension.UNCERTAINTY, old_prop.uncertainty.to_dict(), new_prop.uncertainty.to_dict()),
                (Dimension.TEMPORAL_STATE, old_prop.temporal.to_dict(), new_prop.temporal.to_dict()),
                (Dimension.EVIDENCE, list(old_prop.evidence_refs), list(new_prop.evidence_refs)),
                (Dimension.CANONICALITY, old_prop.canonical_state.value, new_prop.canonical_state.value),
                (Dimension.LINEAGE, list(old_prop.parent_proposition_ids), list(new_prop.parent_proposition_ids)),
                (Dimension.PROVENANCE, list(old_prop.source_refs), list(new_prop.source_refs)),
                (Dimension.PROVENANCE, dict(old_prop.metadata), dict(new_prop.metadata)),
                (Dimension.EPISTEMIC_STATUS, old_prop.derivation_method, new_prop.derivation_method),
            )
            for dimension, before, after in fields:
                add(proposition_id, dimension, before, after)
        for proposition_id in new:
            if proposition_id not in old:
                add(proposition_id, Dimension.LINEAGE, "absent", "present")
        return tuple(changes)

    def _proposal(
        self,
        request: TransformationRequest,
        output: Artifact,
        *,
        declared_changes: Iterable[DeclaredChange] | None = None,
        evidence_refs: Iterable[str] = (),
        authorization_refs: Iterable[str] = (),
        claimed_validation_results: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
        provenance_refs: Iterable[ProvenanceReference] | None = None,
        authority_refs: Iterable[AuthorityReference] = (),
    ) -> TransformationProposal:
        evidence = tuple(dict.fromkeys(evidence_refs))
        authorizations = tuple(dict.fromkeys(authorization_refs))
        claims = tuple(dict.fromkeys(claimed_validation_results))
        declared = tuple(declared_changes) if declared_changes is not None else self.declared_changes_for(
            request.input_artifact,
            output,
            reason=request.intent,
        )
        kernel_record = KernelTransformationRecord(
            transformation_id=f"tx-{request.request_id}",
            input_artifact_ids=(request.input_artifact.artifact_id,),
            output_artifact_id=output.artifact_id,
            transformer=self.identity.actor(),
            transformation_type=request.transformation_type,
            declared_changes=declared,
            input_hashes=(request.input_artifact.artifact_digest,),
            output_hash=output.artifact_digest,
            authorization_refs=authorizations,
            evidence_refs=evidence,
            reason=request.intent,
            claimed_validation_results=claims,
            created_at=request.created_at,
        )
        source_refs = tuple(
            ProvenanceReference(reference_id=ref, subject_id=proposition.proposition_id)
            for proposition in output.propositions
            for ref in proposition.source_refs
        )
        record = TransformationRecord(
            request_id=request.request_id,
            transformation_id=kernel_record.transformation_id,
            source=LineageReference.from_artifact(request.input_artifact),
            output=LineageReference.from_artifact(output),
            gem=self.identity,
            transformation_type=request.transformation_type,
            intent=request.intent,
            kernel_record=kernel_record,
            provenance_refs=tuple(provenance_refs) if provenance_refs is not None else source_refs,
            authority_refs=tuple(authority_refs),
            metadata=dict(metadata or {}),
            created_at=request.created_at,
        )
        return TransformationProposal(request.request_id, output, record, claims)

    @staticmethod
    def clone_proposition(proposition: Proposition, **changes: Any) -> Proposition:
        return replace(proposition, **changes)
