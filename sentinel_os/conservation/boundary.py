"""The mandatory conservation boundary for governed decisions.

`governance_harness._write_decision` calls `verify_governed_decision(episode,
record)` before persisting to the ledger. It raises on anything other than a
clean acceptance -- no durable state without conservation verification.

A fresh gateway per call: the kernel is used here as a stateless verifier plus
choke point, not as an accumulating ledger. Sentinel's durable ledger is
Postgres; reconstruction is Sentinel's own event-sourced path.
"""
from __future__ import annotations

from typing import Any

from conservation_kernel import EvidenceRegistry

from conservation.episode_source import build_episode_source
from conservation.judgment import GovernanceJudgmentTransformer
from conservation.transport import ConservationGateway
from episode import Episode


class ConservationBoundaryRejected(RuntimeError):
    """The conservation boundary refused a governed decision. Fail-closed."""

    def __init__(self, node: str, decision: Any) -> None:
        self.node = node
        self.decision = decision
        codes = ", ".join(
            f"{r.code}({r.detail})" for r in getattr(decision, "rejections", ())
        ) or getattr(decision, "status", "REJECTED")
        super().__init__(
            f"conservation boundary rejected the governed decision for '{node}': {codes}"
        )


def verify_governed_decision(episode: Episode, record: Any) -> Any:
    """Verify episode -> judgment as a conservation transformation.

    Returns the accepted TransformationResult. Raises ConservationBoundaryRejected
    on any non-acceptance.
    """
    registry = EvidenceRegistry()
    gateway = ConservationGateway(registry=registry)

    source = build_episode_source(episode, registry)
    gateway.ingest_source(source)

    transformer = GovernanceJudgmentTransformer.for_record(record, episode)
    gateway.register_gem(transformer.identity)

    request = transformer.make_request(source)
    proposal = transformer.build_proposal(request, record)
    result = gateway.submit(request, proposal)

    if not result.accepted:
        raise ConservationBoundaryRejected(getattr(record, "node", "?"), result.decision)
    return result
