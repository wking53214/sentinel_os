"""The evidence guard for the conservation boundary: only the records this episode's source produced.

The Conservation_Kernel refuses to take evidence from just anyone: a registry with no `evidence_guard` would let any
caller add a record that declares itself independent verification, and the verifier would count it. Sentinel builds
the evidence for an episode itself (`episode_source.build_episode_source`), so the guard can be exact: it permits a
record only if it is one of the records that function produces for this episode, with the subject, kind, observer
and episode it records. A record with any other id, subject, kind, observer, episode or domain is refused.

The guard decides nothing about whether the episode is true or the judgment right; that is the kernel's verifier and
the root guard. It only says which evidence may enter the registry.
"""
from __future__ import annotations

from typing import Any

from conservation_kernel.enums import EvidenceKind

from conservation.episode_source import _observer, expected_evidence
from episode import Episode


class EpisodeEvidenceGuard:
    """Callable for `EvidenceRegistry(evidence_guard=...)`: True only for this episode's own evidence."""

    def __init__(self, episode: Episode) -> None:
        self._expected = dict(expected_evidence(episode))
        self._detail = {"episode_id": episode.episode_id, "domain": episode.domain}
        self._observer = _observer()

    def __call__(self, record: Any) -> bool:
        try:
            subject = self._expected.get(record.evidence_id)
            return bool(
                subject is not None
                and record.subject_id == subject
                and record.kind is EvidenceKind.SOURCE_OBSERVATION
                and record.provided_by == self._observer
                and record.independent is True
                and record.active is True
                and dict(record.detail) == self._detail
            )
        except Exception:
            # A record that cannot be read as an evidence record is not this episode's: refuse, never raise.
            return False
