"""Conservation boundary for Sentinel OS governed decisions.

`governance_harness._write_decision` calls `verify_governed_decision(episode,
record)` before persisting to the ledger. It models the decision as a
conservation transformation -- `episode (observed record) -> judgment` -- and
submits it through an enforced gateway around `conservation_kernel`
(`conservation/transport/`, vendored from GEMS). Fail-closed: no durable state
without conservation verification. See CONFORMANCE.md.
"""

from .boundary import ConservationBoundaryRejected, verify_governed_decision

__all__ = [
    "ConservationBoundaryRejected",
    "verify_governed_decision",
]
