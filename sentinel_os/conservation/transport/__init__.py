"""Enforced conservation gateway around ``conservation_kernel``.

Vendored, near-verbatim, from `GEMS/transport/gems_transport/` (see PROVENANCE.md).
`conservation_kernel` verifies typed transformations but cannot stop a caller
from ignoring a rejection; `ConservationGateway` is that choke point:

- a transformer's output is an untrusted *proposal*, never an accepted artifact;
- the gateway resolves the input artifact from its own accepted-artifact map;
- only ``submit()`` can promote a candidate, and only if the kernel accepts.

Sentinel uses this as a **stateless verifier + choke point** per governed
decision (a fresh gateway per `_write_decision` call). The kernel ledger inside
it is transient; Sentinel's durable ledger stays Postgres, and reconstruction
stays Sentinel's own event-sourced path.
"""

from .artifact import AuthorityReference, LineageReference, ProvenanceReference
from .builder import BaseGem
from .contracts import (
    ConservationDecision,
    DecisionStatus,
    GemIdentity,
    TransformationProposal,
    TransformationRequest,
    TransformationResult,
    TransportState,
)
from .errors import BoundaryViolation, GemsError, InvalidContract, UnknownArtifact
from .registry import GemRegistry, TransformationLedger
from .transport import ConservationGateway, GatewayReconstruction

__all__ = [
    "AuthorityReference",
    "BaseGem",
    "BoundaryViolation",
    "ConservationDecision",
    "ConservationGateway",
    "DecisionStatus",
    "GatewayReconstruction",
    "GemRegistry",
    "GemsError",
    "GemIdentity",
    "InvalidContract",
    "LineageReference",
    "ProvenanceReference",
    "TransformationLedger",
    "TransformationProposal",
    "TransformationRequest",
    "TransformationResult",
    "TransportState",
    "UnknownArtifact",
]
