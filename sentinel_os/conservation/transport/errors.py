"""Errors raised at the GEMS transport boundary."""

from __future__ import annotations


class GemsError(Exception):
    """Base class for GEMS errors."""


class InvalidContract(GemsError):
    """A typed request, proposal, or identity is malformed."""


class BoundaryViolation(GemsError):
    """A caller attempted to use an artifact outside the governed boundary."""


class UnknownArtifact(BoundaryViolation):
    """An artifact is not an accepted artifact in this gateway."""


class PipelineRejected(GemsError):
    """A pipeline stopped because the gateway rejected a proposal."""

    def __init__(self, result) -> None:
        self.result = result
        super().__init__(
            f"pipeline rejected transformation {result.transformation_id}: "
            f"{result.decision.status.value}"
        )

