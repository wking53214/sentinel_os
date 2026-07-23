"""
Episode -- the kernel's domain-blind ground-truth record.

An Episode is what a cassette judges: a record of what was REQUESTED,
what ACTUALLY happened per independent observation, what the acting
system CLAIMS happened, and the timeline in between. It replaces the
call-shaped fixed signature (resolved, duration, friction_count,
emotion_data) as the kernel's unit of judgment; telephony keeps that
signature inside its own capability, but the kernel speaks episodes.

Two invariants live here, both fail-closed:

1. REASON ON ANY MISMATCH (not just denials). If any requested field's
   actual value differs from what was requested, the episode MUST carry
   at least one outcome reason or it does not validate. Proven
   necessary by a real insurance pattern: a claim can be PAID at a
   reduced amount with no formal denial anywhere, which silently
   bypasses reason-requirements built only around denials
   ("downcoding"). Mismatch means mismatch -- reduced, substituted,
   delayed, or denied alike.

2. NEVER TRUST THE ACTOR'S SELF-REPORT. The acting system's own claim
   of what happened (actor_report) is recorded but never treated as
   truth: validation always cross-checks it against the observed
   record (actual) and surfaces every divergence, in the same posture
   as the twin replica cross-check (twin_custody / twin_detector):
   the primary's story is compared against an independent record, and
   disagreement is a first-class finding, not noise. Judgment
   (Cassette.judge) reads `actual`, never `actor_report`.

Like cassettes, episodes have exactly one validation entry point for
every judgment path: judge_episode / explain_episode below validate
first, so no judgment path admits an unvalidated episode -- the same
rule cassette loading already enforces ("no load path admits an
unvalidated cassette").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence, Tuple

# Sentinel used in discrepancies when the actor claims a field the
# observed record never captured at all. String (not object()) so it
# survives JSON round-trips into ledger rows.
UNOBSERVED = "<<UNOBSERVED>>"


@dataclass(frozen=True)
class EpisodeEvent:
    """One timeline entry, in the domain's own vocabulary.

    `at` is seconds from episode start; `kind` is a domain word
    ("node_entered", "wait", "decision", "payment_adjusted", ...);
    `detail` carries whatever the domain records about the event.
    """

    at: float
    kind: str
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Mismatch:
    """A requested field whose actual value differs from the request."""

    name: str
    requested: Any
    actual: Any


@dataclass(frozen=True)
class Discrepancy:
    """The actor's self-report disagrees with the observed record.

    kind follows the twin verdict vocabulary: "DIVERGE" when both
    sides carry the field with different values; "EXTRA" when the
    actor claims a field the observed record never captured.
    """

    name: str
    actor_claimed: Any
    observed: Any
    kind: str  # "DIVERGE" | "EXTRA"


@dataclass(frozen=True)
class Episode:
    """The ground-truth record of one governed episode.

    requested     -- what was asked for / expected (the promise).
    actual        -- what happened per the OBSERVED record (the truth
                     judgment runs on).
    actor_report  -- what the acting system itself claims happened.
                     Recorded, cross-checked, never trusted.
    timeline      -- ordered EpisodeEvents in domain vocabulary.
    outcome_reasons -- REQUIRED (non-empty) whenever any requested
                     field mismatches its actual value.
    attributes    -- domain measurements the cassette's judgment may
                     read (duration, friction_count, emotion, ...).
    """

    episode_id: str
    domain: str
    requested: Dict[str, Any]
    actual: Dict[str, Any]
    actor_report: Dict[str, Any] = field(default_factory=dict)
    timeline: Tuple[EpisodeEvent, ...] = ()
    outcome_reasons: Tuple[str, ...] = ()
    attributes: Dict[str, Any] = field(default_factory=dict)


class EpisodeIntegrityError(Exception):
    """An episode failed kernel validation. Carries every violation
    found so one attempt reports the whole picture (same reporting
    posture as CassetteValidationError)."""

    def __init__(self, episode_id: str, violations: List[str]):
        self.episode_id = episode_id
        self.violations = list(violations)
        lines = "\n".join(f"  - {v}" for v in self.violations)
        super().__init__(
            f"Episode '{episode_id}' failed integrity validation "
            f"({len(self.violations)} violation(s)):\n{lines}"
        )


@dataclass(frozen=True)
class EpisodeReport:
    """What validation established about an episode: which requested
    fields mismatched, and where the actor's story diverges from the
    observed record. Both lists are always computed -- an episode with
    reasons on file still reports its mismatches, and a divergent
    actor report is surfaced even when judgment would proceed."""

    mismatches: Tuple[Mismatch, ...]
    discrepancies: Tuple[Discrepancy, ...]


def make_episode(episode_id: str, domain: str, requested: Mapping[str, Any],
                 actual: Mapping[str, Any],
                 actor_report: Mapping[str, Any] | None = None,
                 timeline: Sequence[EpisodeEvent] = (),
                 outcome_reasons: Sequence[str] = (),
                 attributes: Mapping[str, Any] | None = None) -> Episode:
    """Normalizing constructor: copies mappings, freezes sequences."""
    return Episode(
        episode_id=str(episode_id),
        domain=str(domain),
        requested=dict(requested),
        actual=dict(actual),
        actor_report=dict(actor_report or {}),
        timeline=tuple(timeline),
        outcome_reasons=tuple(str(r) for r in outcome_reasons),
        attributes=dict(attributes or {}),
    )


def outcome_mismatches(episode: Episode) -> List[Mismatch]:
    """Every requested field whose actual value differs -- ANY
    difference, not just denial-shaped ones. A request for
    {"outcome": "paid", "amount": 1200.0} answered by
    {"outcome": "paid", "amount": 900.0} mismatches on amount even
    though nothing was denied; that is the case this rule exists for.
    A requested field absent from `actual` is also a mismatch: an
    outcome that never materialized is not a match."""
    found: List[Mismatch] = []
    for name in sorted(episode.requested):
        want = episode.requested[name]
        if name not in episode.actual:
            found.append(Mismatch(name=name, requested=want, actual=None))
        elif episode.actual[name] != want:
            found.append(Mismatch(name=name, requested=want,
                                  actual=episode.actual[name]))
    return found


def actor_discrepancies(episode: Episode) -> List[Discrepancy]:
    """Cross-check the actor's self-report against the observed record.

    Same posture as the twin: the actor's story is evidence about the
    actor, not evidence about the world. Divergence on a shared field
    is DIVERGE; a field the actor claims but observation never
    captured is EXTRA (a claim with nothing behind it)."""
    found: List[Discrepancy] = []
    for name in sorted(episode.actor_report):
        claimed = episode.actor_report[name]
        if name not in episode.actual:
            found.append(Discrepancy(name=name, actor_claimed=claimed,
                                     observed=UNOBSERVED, kind="EXTRA"))
        elif episode.actual[name] != claimed:
            found.append(Discrepancy(name=name, actor_claimed=claimed,
                                     observed=episode.actual[name],
                                     kind="DIVERGE"))
    return found


def validate_episode(episode: Episode) -> EpisodeReport:
    """The single fail-loud validation entry point for every judgment
    path. Raises EpisodeIntegrityError with the complete violation
    list, or returns the EpisodeReport (mismatches + actor
    discrepancies) judgment can proceed with.

    The hard invariant: a mismatch between requested and actual with
    no outcome reason on file does not validate. There is deliberately
    no way to waive this -- an outcome that differs from what was
    asked, with no stated reason, is exactly the record an auditor
    cannot accept."""
    violations: List[str] = []

    if not str(episode.episode_id).strip():
        violations.append("episode_id must be a non-empty string")
    if not str(episode.domain).strip():
        violations.append("domain must be a non-empty string")
    for label, value in (("requested", episode.requested),
                         ("actual", episode.actual),
                         ("actor_report", episode.actor_report),
                         ("attributes", episode.attributes)):
        if not isinstance(value, dict):
            violations.append(f"{label} must be a dict, got {type(value).__name__}")
    for i, reason in enumerate(episode.outcome_reasons):
        if not str(reason).strip():
            violations.append(f"outcome_reasons[{i}] is empty; a blank reason is no reason")
    for i, event in enumerate(episode.timeline):
        if not isinstance(event, EpisodeEvent):
            violations.append(
                f"timeline[{i}] must be an EpisodeEvent, got {type(event).__name__}"
            )

    if violations:
        raise EpisodeIntegrityError(episode.episode_id, violations)

    mismatches = outcome_mismatches(episode)
    if mismatches and not episode.outcome_reasons:
        for m in mismatches:
            violations.append(
                f"outcome mismatch on '{m.name}' (requested {m.requested!r}, "
                f"actual {m.actual!r}) with NO outcome reason recorded -- a "
                f"reason is owed any time the outcome differs from what was "
                f"requested, not only on formal denials"
            )
        raise EpisodeIntegrityError(episode.episode_id, violations)

    return EpisodeReport(
        mismatches=tuple(mismatches),
        discrepancies=tuple(actor_discrepancies(episode)),
    )


def judge_episode(cassette, episode: Episode):
    """Kernel judgment entry point: validate, then let the cassette
    judge. Returns the cassette's QualityResult. No judgment path
    admits an unvalidated episode."""
    validate_episode(episode)
    return cassette.judge(episode)


def explain_episode(cassette, episode: Episode) -> List[Dict[str, Any]]:
    """Kernel explanation entry point: validate, then combine the
    kernel's own findings with the cassette's factor-level reasons.

    The kernel PREPENDS verification findings -- actor-report
    divergences and requested/actual mismatches -- so no cassette can
    forget to surface them. Verification posture is kernel policy, not
    a per-domain courtesy."""
    report = validate_episode(episode)
    factors: List[Dict[str, Any]] = []
    for d in report.discrepancies:
        factors.append({
            "factor": "actor_report_divergence",
            "field": d.name,
            "actor_claimed": d.actor_claimed,
            "observed": d.observed,
            "kind": d.kind,
            "detail": "actor self-report disagrees with the observed record; "
                      "judgment used the observed record",
        })
    for m in report.mismatches:
        factors.append({
            "factor": "outcome_mismatch",
            "field": m.name,
            "requested": m.requested,
            "actual": m.actual,
            "reasons_on_file": list(episode.outcome_reasons),
        })
    factors.extend(cassette.explain(episode))
    return factors
