"""The enforced GEMS output gate around conservation_kernel."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from conservation_kernel import (
    Artifact,
    AuthorizationEvent,
    ConservationKernel,
    EvidenceRecord,
    EvidenceRegistry,
    VerificationResult,
)

from .contracts import (
    ConservationDecision,
    DecisionStatus,
    GemIdentity,
    Rejection,
    TransformationProposal,
    TransformationRecord,
    TransformationRequest,
    TransformationResult,
    TransportState,
)
from .errors import BoundaryViolation, InvalidContract, UnknownArtifact
from .registry import GemRegistry, LedgerEntry, TransformationLedger


ALLOWED_UNVERIFIABLE_PROPERTIES = frozenset({"semantic_content_equivalence"})


@dataclass(frozen=True)
class GatewayReconstruction:
    """Kernel reconstruction plus GEMS accepted/rejected transport history."""

    kernel_reconstruction: Any
    transport_entries: tuple[LedgerEntry, ...]

    @property
    def root_artifact_ids(self) -> tuple[str, ...]:
        return tuple(self.kernel_reconstruction.root_artifact_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kernel_reconstruction": self.kernel_reconstruction.to_dict(),
            "transport_entries": [item.to_dict() for item in self.transport_entries],
        }


class ConservationGateway:
    """The only accepted-artifact boundary in the governed GEMS workflow.

    A Gem can create any immutable candidate it wants.  The candidate becomes
    visible to downstream code only after this gateway resolves the source
    from its accepted-artifact map and obtains an accepted result from the
    conservation kernel.  The request's copy of the input artifact is not
    trusted as the authoritative input.
    """

    def __init__(
        self,
        *,
        kernel: ConservationKernel | None = None,
        registry: EvidenceRegistry | None = None,
        gem_registry: GemRegistry | None = None,
        ledger: TransformationLedger | None = None,
    ) -> None:
        if kernel is not None and registry is not None and kernel.registry is not registry:
            raise InvalidContract("provided kernel and registry must share the same registry")
        self.registry = registry or (kernel.registry if kernel is not None else EvidenceRegistry())
        self.kernel = kernel or ConservationKernel(registry=self.registry)
        self.gem_registry = gem_registry or GemRegistry()
        self.ledger = ledger or TransformationLedger()
        self._accepted: dict[str, Artifact] = {}
        self._transformations: dict[str, TransformationRecord] = {}

    def register_gem(self, identity: GemIdentity) -> None:
        self.gem_registry.register(identity)

    def register_evidence(self, evidence: EvidenceRecord) -> None:
        self.registry.add_evidence(evidence)

    def register_authorization(self, authorization: AuthorizationEvent) -> None:
        self.registry.add_authorization(authorization)

    def ingest_source(self, artifact: Artifact) -> Artifact:
        """Register a root source artifact at the explicit future-TIE handoff."""

        if artifact.parent_artifact_ids:
            raise BoundaryViolation("only parentless artifacts can enter as a source root")
        existing = self._accepted.get(artifact.artifact_id)
        if existing is not None:
            if existing.artifact_digest != artifact.artifact_digest:
                raise BoundaryViolation("source artifact ID is already bound to a different digest")
            return existing
        self.kernel.register_root(artifact)
        self._accepted[artifact.artifact_id] = artifact
        self.ledger.record_root(artifact)
        return artifact

    def is_accepted(self, artifact: Artifact | str) -> bool:
        artifact_id = artifact if isinstance(artifact, str) else artifact.artifact_id
        current = self._accepted.get(artifact_id)
        if current is None:
            return False
        if isinstance(artifact, str):
            return True
        return current.artifact_digest == artifact.artifact_digest

    def resolve_artifact(self, artifact_id: str) -> Artifact:
        try:
            return self._accepted[artifact_id]
        except KeyError as exc:
            raise UnknownArtifact(f"artifact {artifact_id} is not accepted by this GEMS gateway") from exc

    def accepted_artifacts(self) -> tuple[Artifact, ...]:
        return tuple(self._accepted.values())

    def accepted_transformations(self) -> tuple[TransformationRecord, ...]:
        return tuple(self._transformations.values())

    @staticmethod
    def _rejection_from_violation(violation) -> Rejection:
        return Rejection(
            code=violation.code,
            detail=violation.detail,
            dimension=violation.dimension.value if violation.dimension is not None else None,
            subject_id=violation.subject_id,
        )

    @staticmethod
    def _decision_for_kernel(verification: VerificationResult) -> ConservationDecision:
        if verification.accepted:
            return ConservationDecision(
                DecisionStatus.ACCEPTED,
                verification.status.value,
                (),
            )
        rejections = tuple(ConservationGateway._rejection_from_violation(item) for item in verification.violations)
        codes = {item.code for item in verification.violations}
        authorization_codes = {
            code
            for code in codes
            if "AUTHORIZATION" in code
            or code.startswith("UNAUTHORIZED_")
            or code in {
                "MISSING_HUMAN_ADOPTION",
                "DECISION_WITHOUT_AUTHORIZATION",
                "RECOMMENDATION_TO_DECISION_UNAUTHORIZED",
            }
        }
        verification_codes = {
            code
            for code in codes
            if "VERIFICATION" in code
            or code in {
                "INFERENCE_WITHOUT_SUPPORT",
                "ACTOR_REPORT_NOT_OBSERVATION",
                "CANONICAL_WITHOUT_SUPPORT",
                "NEW_STRONG_CLAIM_UNVERIFIED",
            }
        }
        if authorization_codes:
            status = DecisionStatus.REQUIRES_AUTHORIZATION
        elif verification_codes:
            status = DecisionStatus.REQUIRES_VERIFICATION
        else:
            status = DecisionStatus.REJECTED
        return ConservationDecision(status, verification.status.value, rejections)

    @staticmethod
    def _preflight_rejection(code: str, detail: str, *, dimension: str = "LINEAGE") -> Rejection:
        return Rejection(code=code, detail=detail, dimension=dimension)

    def _preflight(
        self,
        request: TransformationRequest,
        proposal: TransformationProposal,
    ) -> tuple[Rejection, ...]:
        rejections: list[Rejection] = []
        source = self._accepted.get(request.source.artifact_id)
        if source is None:
            rejections.append(self._preflight_rejection("SOURCE_NOT_ACCEPTED", "source artifact is not in the gateway accepted-artifact map"))
        elif source.artifact_digest != request.source.artifact_digest:
            rejections.append(self._preflight_rejection("SOURCE_DIGEST_MISMATCH", "source reference does not match the accepted artifact digest"))
        if not self.gem_registry.contains(request.gem):
            rejections.append(self._preflight_rejection("GEM_NOT_REGISTERED", "Gem identity is not registered for this governed workflow", dimension="IDENTITY"))
        record = proposal.record
        if proposal.request_id != request.request_id or record.request_id != request.request_id:
            rejections.append(self._preflight_rejection("REQUEST_ID_MISMATCH", "proposal and record do not identify the gateway request"))
        if record.source != request.source:
            rejections.append(self._preflight_rejection("RECORD_SOURCE_MISMATCH", "transformation record source differs from the gateway request"))
        if record.output.artifact_id != proposal.output_artifact.artifact_id or record.output.artifact_digest != proposal.output_artifact.artifact_digest:
            rejections.append(self._preflight_rejection("OUTPUT_REFERENCE_MISMATCH", "record output reference does not match the candidate artifact"))
        if record.gem != request.gem:
            rejections.append(self._preflight_rejection("GEM_IDENTITY_MISMATCH", "record Gem identity does not match the request Gem identity", dimension="IDENTITY"))
        if record.kernel_record.transformer != request.gem.actor():
            rejections.append(self._preflight_rejection("TRANSFORMER_IDENTITY_MISMATCH", "kernel transformer actor does not match the request Gem identity", dimension="IDENTITY"))
        if proposal.output_artifact.producer != request.gem.actor():
            rejections.append(self._preflight_rejection("OUTPUT_PRODUCER_MISMATCH", "candidate producer is not the registered Gem actor", dimension="ORIGIN"))
        if record.transformation_id in self._transformations:
            rejections.append(self._preflight_rejection("DUPLICATE_TRANSFORMATION_ID", "transformation ID has already been submitted", dimension="TRANSFORMATION_IDENTITY"))
        if proposal.output_artifact.artifact_id in self._accepted:
            rejections.append(self._preflight_rejection("DUPLICATE_ARTIFACT_ID", "candidate artifact ID has already been accepted", dimension="LINEAGE"))
        return tuple(rejections)

    def _result(
        self,
        request: TransformationRequest,
        proposal: TransformationProposal,
        *,
        decision: ConservationDecision,
        state: TransportState,
        kernel_result: VerificationResult | None = None,
        accepted_artifact: Artifact | None = None,
    ) -> TransformationResult:
        result = TransformationResult(
            request_id=request.request_id,
            transformation_id=proposal.record.transformation_id,
            state=state,
            decision=decision,
            record=proposal.record,
            candidate_artifact=proposal.output_artifact,
            accepted_artifact=accepted_artifact,
            kernel_result=kernel_result,
        )
        self.ledger.record_result(result)
        return result

    def submit(self, request: TransformationRequest, proposal: TransformationProposal) -> TransformationResult:
        """Validate a proposal and expose its output only if accepted."""

        preflight = self._preflight(request, proposal)
        if preflight:
            decision = ConservationDecision(DecisionStatus.REJECTED, None, preflight)
            return self._result(request, proposal, decision=decision, state=TransportState.REJECTED)

        source = self.resolve_artifact(request.source.artifact_id)
        verification = self.kernel.verifier.verify(
            source,
            proposal.output_artifact,
            proposal.record.kernel_record,
            self.registry,
        )
        disallowed_unknown = tuple(
            item for item in verification.unverifiable_properties
            if item not in ALLOWED_UNVERIFIABLE_PROPERTIES
        )
        if verification.accepted and disallowed_unknown:
            rejections = tuple(
                Rejection(
                    code="UNVERIFIABLE_REQUIRED_PROPERTY",
                    detail=f"kernel could not verify required property {item}",
                    dimension="PROVENANCE",
                )
                for item in disallowed_unknown
            )
            decision = ConservationDecision(
                DecisionStatus.REQUIRES_VERIFICATION,
                verification.status.value,
                rejections,
            )
            return self._result(
                request,
                proposal,
                decision=decision,
                state=TransportState.REJECTED,
                kernel_result=verification,
            )

        if not verification.accepted:
            # Submit rejected proposals through the kernel façade so its own
            # rejection report remains part of the authoritative kernel state.
            kernel_result = self.kernel.submit(
                source,
                proposal.output_artifact,
                proposal.record.kernel_record,
            )
            decision = self._decision_for_kernel(kernel_result)
            return self._result(
                request,
                proposal,
                decision=decision,
                state=TransportState.REJECTED,
                kernel_result=kernel_result,
            )

        kernel_result = self.kernel.submit(
            source,
            proposal.output_artifact,
            proposal.record.kernel_record,
        )
        if not kernel_result.accepted:
            decision = self._decision_for_kernel(kernel_result)
            return self._result(
                request,
                proposal,
                decision=decision,
                state=TransportState.REJECTED,
                kernel_result=kernel_result,
            )

        self._accepted[proposal.output_artifact.artifact_id] = proposal.output_artifact
        self._transformations[proposal.record.transformation_id] = proposal.record
        return self._result(
            request,
            proposal,
            decision=ConservationDecision(DecisionStatus.ACCEPTED, kernel_result.status.value, ()),
            state=TransportState.ACCEPTED,
            kernel_result=kernel_result,
            accepted_artifact=proposal.output_artifact,
        )

    def reconstruct(self, artifact_id: str) -> GatewayReconstruction:
        if not self.is_accepted(artifact_id):
            raise UnknownArtifact(f"cannot reconstruct artifact outside accepted workflow: {artifact_id}")
        kernel_reconstruction = self.kernel.reconstruct(artifact_id)
        chain_ids = set(kernel_reconstruction.artifact_ids_in_order)
        entries = tuple(
            item
            for item in self.ledger.entries()
            if item.event_type == "ROOT"
            or item.artifact_id in chain_ids
            or item.source_artifact_id in chain_ids
        )
        return GatewayReconstruction(kernel_reconstruction, entries)
