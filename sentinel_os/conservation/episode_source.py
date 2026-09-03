"""`Episode` -> a root source `Artifact` for the conservation boundary.

The Episode's own structure is already conservation-shaped:

- `actual`  -- the OBSERVED record -> OBSERVATION propositions, EXTERNAL origin.
- `requested` -- the promise -> ASSUMPTION propositions.
- `actor_report` -- the acting system's untrusted claims -> INFERENCE
  propositions, MACHINE origin, carrying an Uncertainty and a derivation_method.
  Never FACT/OBSERVATION -- the kernel's ACTOR_REPORT_NOT_OBSERVATION check is
  the same distinction Episode itself enforces via `discrepancies`.
- `outcome_reasons` -- stated reasons on the observed record -> OBSERVATION.

Timeline events and raw `attributes` are judgment inputs, not standalone
claims, so they are folded into the artifact content, not propositions.
"""
from __future__ import annotations

import re
from typing import Any

from conservation_kernel import (
    Actor,
    Artifact,
    EvidenceKind,
    EvidenceRecord,
    EvidenceRegistry,
    EpistemicStatus,
    AuthorityStatus,
    OriginStatus,
    Proposition,
    Uncertainty,
    UncertaintyState,
)

from episode import Episode

_OBSERVED_EVIDENCE_ID = "ev-observed-record"


def _slug(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return s or "field"


def _source_refs(episode: Episode) -> tuple[str, ...]:
    return (f"sentinel:episode:{episode.episode_id}",)


def observed_proposition_ids(episode: Episode) -> tuple[str, ...]:
    """The proposition IDs a judgment is allowed to root itself against."""
    ids = ["p-episode"]
    ids += [f"p-actual-{_slug(k)}" for k in sorted(episode.actual)]
    ids += [f"p-reason-{i}" for i in range(len(episode.outcome_reasons))]
    return tuple(ids)


def build_episode_source(episode: Episode, registry: EvidenceRegistry) -> Artifact:
    observer = Actor.external("sentinel-observed-record", "Sentinel governed-episode observed record")
    registry.add_evidence(
        EvidenceRecord(
            evidence_id=_OBSERVED_EVIDENCE_ID,
            subject_id="*",
            kind=EvidenceKind.SOURCE_OBSERVATION,
            provided_by=observer,
            independent=True,
            active=True,
            detail={"episode_id": episode.episode_id, "domain": episode.domain},
        )
    )
    src = _source_refs(episode)
    props: list[Proposition] = [
        Proposition(
            proposition_id="p-episode",
            text=(
                f"Governed episode {episode.episode_id} in domain '{episode.domain}'. "
                f"{len(episode.timeline)} timeline event(s); "
                f"attributes: {_short(episode.attributes)}."
            ),
            epistemic_status=EpistemicStatus.OBSERVATION,
            origin=OriginStatus.EXTERNAL_ORIGINATED,
            authority=AuthorityStatus.NONE,
            evidence_refs=(_OBSERVED_EVIDENCE_ID,),
            source_refs=src,
        )
    ]

    for key in sorted(episode.actual):
        props.append(
            Proposition(
                proposition_id=f"p-actual-{_slug(key)}",
                text=f"Observed record: {key} = {_short(episode.actual[key])}.",
                epistemic_status=EpistemicStatus.OBSERVATION,
                origin=OriginStatus.EXTERNAL_ORIGINATED,
                evidence_refs=(_OBSERVED_EVIDENCE_ID,),
                source_refs=src,
            )
        )

    for key in sorted(episode.requested):
        props.append(
            Proposition(
                proposition_id=f"p-requested-{_slug(key)}",
                text=f"The request asked for: {key} = {_short(episode.requested[key])}.",
                epistemic_status=EpistemicStatus.ASSUMPTION,
                origin=OriginStatus.EXTERNAL_ORIGINATED,
                source_refs=src,
            )
        )

    for key in sorted(episode.actor_report):
        props.append(
            Proposition(
                proposition_id=f"p-actor-{_slug(key)}",
                text=f"The acting system reports: {key} = {_short(episode.actor_report[key])}.",
                epistemic_status=EpistemicStatus.INFERENCE,
                origin=OriginStatus.MACHINE_ORIGINATED,
                uncertainty=Uncertainty(
                    UncertaintyState.UNCERTAIN,
                    "actor self-report, cross-checked against the observed record, never trusted",
                ),
                evidence_refs=(_OBSERVED_EVIDENCE_ID,),
                source_refs=src,
                derivation_method="actor-self-report",
            )
        )

    for i, reason in enumerate(episode.outcome_reasons):
        props.append(
            Proposition(
                proposition_id=f"p-reason-{i}",
                text=f"Recorded outcome reason: {_short(reason)}.",
                epistemic_status=EpistemicStatus.OBSERVATION,
                origin=OriginStatus.EXTERNAL_ORIGINATED,
                evidence_refs=(_OBSERVED_EVIDENCE_ID,),
                source_refs=src,
            )
        )

    return Artifact(
        artifact_id=f"episode:{episode.episode_id}",
        content=(
            f"Governed episode {episode.episode_id} (domain '{episode.domain}'): "
            f"requested={_short(episode.requested)} actual={_short(episode.actual)} "
            f"actor_report={_short(episode.actor_report)} "
            f"reasons={list(episode.outcome_reasons)}"
        ),
        propositions=tuple(props),
        producer=Actor.external("sentinel-episode-handoff", "Sentinel governed-episode source"),
    )


def _short(value: Any, limit: int = 160) -> str:
    text = repr(value) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[: limit - 1] + "…"
