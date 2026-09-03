"""The governance judgment as a conservation transformation.

The judgment is one new proposition added to the episode source artifact:
a DECISION-status, MACHINE-originated claim at PROPOSED authority, rooted to
the observed-fact propositions it was drawn from. It does not restate facts
as its own; it does not claim human authorization. The judgment is fixed at
MACHINE_ORIGINATED / PROPOSED regardless of `record.authorized_by`: the keyed
`authorized_by` attestation proves writer integrity, not that anyone
authorized anything, so it is deliberately not mapped onto a kernel authority
status -- see conservation/CONFORMANCE.md ("Not bridged, by design").
"""
from __future__ import annotations

from typing import Any

from conservation_kernel import (
    ActorKind,
    Artifact,
    EpistemicStatus,
    AuthorityStatus,
    OriginStatus,
    Proposition,
    Uncertainty,
    UncertaintyState,
)

from conservation.episode_source import observed_proposition_ids, OBSERVED_EVIDENCE_ID
from conservation.transport import BaseGem, GemIdentity
from episode import Episode

_JUDGMENT_ID = "p-governance-judgment"


class GovernanceJudgmentTransformer(BaseGem):
    """A deterministic transformer: episode source -> source + judgment."""

    def __init__(self, identity: GemIdentity, *, roots: tuple[str, ...]) -> None:
        super().__init__(identity)
        self._roots = roots

    @classmethod
    def for_record(cls, record: Any, episode: Episode) -> "GovernanceJudgmentTransformer":
        model = getattr(record, "model_identity", None)
        identity = GemIdentity(
            gem_id="sentinel-governance-judgment",
            gem_version=str(getattr(record, "cassette_version", None) or "unversioned"),
            implementation_id=(
                "sentinel.conservation.judgment:"
                f"{getattr(record, 'cassette_code_hash', None) or 'nohash'}"
            ),
            role=f"governance-judgment:{getattr(record, 'node', None) or 'node'}",
            capabilities=("governance-decision",),
            actor_kind=ActorKind.MODEL if model else ActorKind.SYSTEM,
        )
        return cls(identity, roots=observed_proposition_ids(episode))

    def build_proposal(self, request, record: Any):
        source: Artifact = request.input_artifact
        present_roots = tuple(
            pid for pid in self._roots if pid in source.proposition_map()
        )
        approved = bool((getattr(record, "output", None) or {}).get("approved"))
        reasoning = (getattr(record, "reasoning", "") or "").strip() or "(no reasoning recorded)"
        verdict = "APPROVED" if approved else "BLOCKED"

        judgment = Proposition(
            proposition_id=_JUDGMENT_ID,
            text=(
                f"Governance decision for node '{getattr(record, 'node', '?')}' "
                f"(cassette {getattr(record, 'cassette_version', '?')}): {verdict}. "
                f"Reasoning: {reasoning}"
            ),
            epistemic_status=EpistemicStatus.DECISION,
            origin=OriginStatus.MACHINE_ORIGINATED,
            authority=AuthorityStatus.PROPOSED,
            uncertainty=Uncertainty(
                UncertaintyState.UNCERTAIN,
                "machine judgment, proposed for the durable ledger, not a human sign-off",
            ),
            evidence_refs=(OBSERVED_EVIDENCE_ID,),
            source_refs=(f"sentinel:decision:{getattr(record, 'node', 'node')}",),
            parent_proposition_ids=present_roots or (source.propositions[0].proposition_id,),
        )
        output = self._artifact(
            request,
            suffix="judgment",
            content=(
                f"Governance judgment: {verdict}. "
                f"node={getattr(record, 'node', '?')} "
                f"cassette={getattr(record, 'cassette_version', '?')}"
            ),
            propositions=source.propositions + (judgment,),
        )
        return self._proposal(request, output, evidence_refs=(OBSERVED_EVIDENCE_ID,))

    # BaseGem.transform is abstract; the boundary calls build_proposal directly.
    def transform(self, request):  # pragma: no cover - not used
        raise NotImplementedError("use build_proposal(request, record)")
