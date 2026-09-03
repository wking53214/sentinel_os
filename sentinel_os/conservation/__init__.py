"""Conservation boundary for Sentinel OS governed decisions.

`governance_harness._write_decision` calls `verify_governed_decision(episode,
record)` before persisting to the ledger. It models the decision as a
conservation transformation -- `episode (observed record) -> judgment` -- and
submits it through an enforced gateway around `conservation_kernel`
(`conservation/transport/`, vendored from GEMS). Fail-closed: no durable state
without conservation verification.

The pre-transport modules (`gateway.py`, `artifact_factory.py`,
`transformation_factory.py`, `artifact_store.py`, `types.py`, `receipt.py`) are
kept for now but are OFF the governed hot path -- see CONFORMANCE.md; they are
slated for removal.
"""

from .boundary import ConservationBoundaryRejected, verify_governed_decision

__all__ = [
    "ConservationBoundaryRejected",
    "verify_governed_decision",
]
