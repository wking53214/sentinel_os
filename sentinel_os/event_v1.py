"""
EventV1 -- the provenance-stamped observed fact, and the unit an Episode
is ASSEMBLED from rather than guessed at.

WHY THIS FILE EXISTS
--------------------
episode.py gave the kernel a domain-blind unit of JUDGMENT. It did not
give the kernel a domain-blind unit of OBSERVATION, so nothing in the
system could tell the difference between a number that was measured and
a number that was derived. The live path exploited that gap by
accident: twilio_log_ingestion._reconstruct_journey routes a call on the
last digit of the caller's phone number, and _extract_wait_times splits
total duration 0.1/0.5/0.4 across nodes. Both are labelled heuristics in
their own source, and both then hand back plain floats that are
indistinguishable, downstream, from real IVR event timestamps.

An estimate that cannot be told apart from a measurement is the exact
record an auditor cannot accept. EventV1 closes that by making the
stamp structural: every observed fact carries WHERE it came from and
HOW MUCH to trust it, and an estimate that will not name its method
does not validate.

THE THREE STAMPS
----------------
The stamps are not a confidence score. They are three different KINDS
of claim, and episode.py already draws the line between two of them:

  VERIFIED  -- independently observed. A real event timestamp from a
               system that is not the actor. Lands in Episode.actual.
  ATTESTED  -- the acting system's own claim about itself. Recorded,
               cross-checked, never trusted. Lands in
               Episode.actor_report, where actor_discrepancies() already
               cross-checks it against the observed record.
  ESTIMATED -- derived by a NAMED method from something else. Lands in
               Episode.actual (judgment has to run on something) but is
               recorded as estimated so explain_episode can say so.

That mapping is the whole point of the assembler below: route by stamp
and the kernel's existing actor-distrust machinery starts working on
real ingested events with no change to episode.py at all.

THE METHOD RULE
---------------
ESTIMATED without a method name does not validate. Not a warning -- a
refusal, in the same posture as episode.py's "a mismatch with no reason
on file does not validate". "duration*0.5" is a legitimate method; it
is honest, reviewable, and an auditor can argue with it. A bare float
with no derivation is none of those things, and it is what the system
produces today.

Absolute time, deliberately. EpisodeEvent.at is seconds from episode
start, which silently assumes the start time is known. EventV1 carries
occurred_at (when the fact happened) and observed_at (when an observer
recorded it) as separate absolute epoch seconds, so ordering survives a
wrong or missing start time and so ingest lag is measurable instead of
invisible. The assembler derives EpisodeEvent.at from the earliest
occurred_at it was given, which makes the start time a computed fact
with events behind it rather than an assumption.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from episode import Episode, EpisodeEvent, make_episode

# --- The three stamps. Stable strings: these ride in ledger rows. ---
PROVENANCE_VERIFIED = "verified"
PROVENANCE_ATTESTED = "attested"
PROVENANCE_ESTIMATED = "estimated"

# --- Versioning, for events that get persisted and replayed later ------------
# EVENT_SCHEMA_VERSION is the shape of an EventV1 payload as it lands in a
# ledger row (governance/ledger_postgres.py observed_event rows). Bump it when
# a field is added, removed, or changes meaning, so a replay can tell whether
# it is reading a stream it still understands.
#
# REDUCER_VERSION names the assemble_episode fold below. A persisted event
# stamps the reducer version that was current when it was recorded, so a later
# replay through a CHANGED assemble_episode can say "verdict recomputed under
# reducer vN, the stream was recorded under vM" instead of silently claiming a
# match. Bump it whenever assemble_episode's folding behaviour changes in a way
# that could change the assembled episode for the same input events.
EVENT_SCHEMA_VERSION = 1
REDUCER_VERSION = "assemble_episode.v1"

PROVENANCE_STAMPS: Tuple[str, ...] = (
    PROVENANCE_VERIFIED,
    PROVENANCE_ATTESTED,
    PROVENANCE_ESTIMATED,
)

# Reserved Episode.attributes key carrying the per-field stamp map the
# assembler computed. Namespaced so a domain attribute called
# "provenance" can never collide with it. One owner: the episode.
PROVENANCE_ATTRIBUTE = "sentinel_provenance"

# Where each stamp's payload lands when an episode is assembled. This
# table IS the actor-distrust rule from episode.py, restated at ingest.
_STAMP_DESTINATION: Dict[str, str] = {
    PROVENANCE_VERIFIED: "actual",
    PROVENANCE_ESTIMATED: "actual",
    PROVENANCE_ATTESTED: "actor_report",
}


class EventIntegrityError(Exception):
    """An event failed validation. Carries every violation found so one
    attempt reports the whole picture (same reporting posture as
    EpisodeIntegrityError and CassetteValidationError)."""

    def __init__(self, event_id: str, violations: List[str]):
        self.event_id = event_id
        self.violations = list(violations)
        lines = "\n".join(f"  - {v}" for v in self.violations)
        super().__init__(
            f"Event '{event_id}' failed integrity validation "
            f"({len(self.violations)} violation(s)):\n{lines}"
        )


@dataclass(frozen=True)
class EventV1:
    """One observed fact about one episode, stamped with its provenance.

    event_id    -- stable identity. Also the dedupe key: at-least-once
                   transports are normal, and re-delivering the same
                   fact must not double-count it.
    episode_id  -- which episode this fact belongs to.
    domain      -- the domain whose vocabulary `kind` is written in.
    kind        -- a domain word ("node_entered", "wait", "decision",
                   "payment_adjusted", ...). The kernel never
                   interprets it; the cassette does.
    occurred_at -- absolute epoch seconds: when the fact HAPPENED.
    observed_at -- absolute epoch seconds: when an observer recorded it.
                   Separate from occurred_at so ingest lag is a
                   measurable fact rather than an invisible one.
    source      -- which system observed it ("twilio:webhook",
                   "core_banking:eod", ...). An estimate's source is
                   whatever derived it, not whatever it was derived from.
    provenance  -- one of PROVENANCE_STAMPS.
    method      -- REQUIRED when provenance is estimated: how the value
                   was derived, in reviewable terms ("duration*0.5").
                   Refused on verified/attested, where a derivation
                   would be a contradiction in terms.
    fields      -- the payload: field name -> value. These are the
                   values that land in the episode, keyed by name so
                   the stamp map can be built per field.
    detail      -- anything else the domain records about the event.
                   Never enters the episode's judged fields.
    schema_version  -- the shape of this event, for replay. Defaults to
                   EVENT_SCHEMA_VERSION.
    reducer_version -- the assemble_episode version this event was
                   recorded under, for replay. Defaults to REDUCER_VERSION.

    schema_version / reducer_version are additive and only matter to a
    consumer that PERSISTS events and replays them later. They do not enter
    any hash today: assemble_episode drops them (they are not fields and not
    domain detail), and the live governance path records only the assembled
    summary, never the raw events. They become load-bearing when
    ledger_postgres persists observed_event rows.
    """

    event_id: str
    episode_id: str
    domain: str
    kind: str
    occurred_at: float
    observed_at: float
    source: str
    provenance: str
    fields: Dict[str, Any] = field(default_factory=dict)
    method: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)
    schema_version: int = EVENT_SCHEMA_VERSION
    reducer_version: str = REDUCER_VERSION


def make_event(event_id: str, episode_id: str, domain: str, kind: str,
               occurred_at: float, observed_at: float, source: str,
               provenance: str,
               fields: Mapping[str, Any] | None = None,
               method: Optional[str] = None,
               detail: Mapping[str, Any] | None = None,
               schema_version: int = EVENT_SCHEMA_VERSION,
               reducer_version: Optional[str] = None) -> EventV1:
    """Normalizing constructor: copies mappings, coerces scalars.

    reducer_version defaults to REDUCER_VERSION (the fold this build ships).
    Pass it explicitly only when re-materializing a persisted event whose
    stream was recorded under an older fold.
    """
    return EventV1(
        event_id=str(event_id),
        episode_id=str(episode_id),
        domain=str(domain),
        kind=str(kind),
        occurred_at=float(occurred_at),
        observed_at=float(observed_at),
        source=str(source),
        provenance=str(provenance),
        fields=dict(fields or {}),
        method=None if method is None else str(method),
        detail=dict(detail or {}),
        schema_version=int(schema_version),
        reducer_version=(REDUCER_VERSION if reducer_version is None
                         else str(reducer_version)),
    )


def validate_event(event: EventV1) -> None:
    """Fail-loud validation for one event. Raises EventIntegrityError
    with the complete violation list, or returns.

    The hard invariant: an ESTIMATED event with no method named does
    not validate. There is deliberately no way to waive it. An
    unlabelled estimate is the failure mode this whole module exists to
    make impossible, and a system that lets one through has quietly
    gone back to shipping guesses that look like measurements."""
    violations: List[str] = []

    for label, value in (("event_id", event.event_id),
                         ("episode_id", event.episode_id),
                         ("domain", event.domain),
                         ("kind", event.kind),
                         ("source", event.source)):
        if not str(value).strip():
            violations.append(f"{label} must be a non-empty string")

    if event.provenance not in PROVENANCE_STAMPS:
        violations.append(
            f"provenance must be one of {list(PROVENANCE_STAMPS)}, got "
            f"{event.provenance!r} -- the stamp vocabulary is bounded on "
            f"purpose; an unrecognized stamp is not a new kind of claim, "
            f"it is an unreadable one"
        )

    if event.provenance == PROVENANCE_ESTIMATED and not str(event.method or "").strip():
        violations.append(
            "provenance is 'estimated' but no method is named -- an estimate "
            "that will not say how it was derived is indistinguishable from a "
            "measurement, which is precisely the record this schema refuses. "
            "Name the derivation (e.g. 'total_duration*0.5')"
        )
    if event.provenance in (PROVENANCE_VERIFIED, PROVENANCE_ATTESTED) and event.method:
        violations.append(
            f"provenance is {event.provenance!r} but a derivation method "
            f"({event.method!r}) is set -- a fact that was observed or claimed "
            f"was not derived; if it was derived, the stamp is 'estimated'"
        )

    for label, value in (("fields", event.fields), ("detail", event.detail)):
        if not isinstance(value, dict):
            violations.append(f"{label} must be a dict, got {type(value).__name__}")

    if not isinstance(event.schema_version, int) or isinstance(event.schema_version, bool) \
            or event.schema_version < 1:
        violations.append(
            f"schema_version must be a positive int, got {event.schema_version!r}"
        )
    if not str(event.reducer_version or "").strip():
        violations.append(
            "reducer_version must be a non-empty string -- a persisted event that "
            "does not name the fold it was recorded under cannot be safely replayed"
        )

    for label, value in (("occurred_at", event.occurred_at),
                         ("observed_at", event.observed_at)):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            violations.append(f"{label} must be a number, got {type(value).__name__}")
        elif value <= 0:
            violations.append(
                f"{label} must be a positive absolute epoch timestamp, got {value!r} "
                f"-- offsets from an assumed start time are what this field replaces"
            )

    if violations:
        raise EventIntegrityError(event.event_id, violations)


@dataclass(frozen=True)
class EpisodeAssembly:
    """What assemble_episode established: the episode itself, plus the
    per-field provenance map that justifies every value in it.

    The same map rides inside episode.attributes under
    PROVENANCE_ATTRIBUTE, so it survives into judgment, into
    explain_episode's output, and into the ledger's policy snapshot
    without the caller having to remember to carry it. This dataclass
    is the caller's convenient handle on it, not a second copy of the
    truth."""

    episode: Episode
    provenance: Dict[str, str]
    estimated_fields: Tuple[str, ...]
    source_events: Tuple[str, ...]
    #: the assemble_episode fold that produced this. A replay compares this
    #: against the reducer_version stamped on the persisted events: equal ->
    #: an exact recomputation; different -> "recomputed under a newer fold".
    reducer_version: str = REDUCER_VERSION


def assemble_episode(episode_id: str, domain: str,
                     requested: Mapping[str, Any],
                     events: Iterable[EventV1],
                     outcome_reasons: Iterable[str] = (),
                     attributes: Mapping[str, Any] | None = None) -> EpisodeAssembly:
    """Build an Episode from stamped events instead of from guesses.

    Routing is by stamp, per _STAMP_DESTINATION: verified and estimated
    facts land in `actual` (judgment has to run on something), attested
    facts land in `actor_report` where the kernel already cross-checks
    them. That single table is what makes episode.py's actor-distrust
    invariant start working on real ingested data without episode.py
    changing at all.

    Every event is validated first. One bad event fails the whole
    assembly rather than being skipped: a silently dropped event is a
    hole in the record that nothing downstream can see.

    Later events win on a repeated field, ordered by occurred_at then
    event_id -- a stable order that does not consult arrival time or
    wall clocks, the same reason twin_detector reconstructs chain order
    from primary_id rather than from when things showed up.

    `requested` stays a caller argument: what was ASKED FOR is not an
    observation, it is the promise the observations get judged against.
    """
    ordered = sorted(events, key=lambda e: (e.occurred_at, e.event_id))
    for event in ordered:
        validate_event(event)

    mismatched_episode = [e for e in ordered if e.episode_id != str(episode_id)]
    if mismatched_episode:
        raise EventIntegrityError(
            mismatched_episode[0].event_id,
            [f"event belongs to episode {e.episode_id!r}, not {episode_id!r} -- "
             f"assembling an episode from another episode's events would "
             f"fabricate a record neither one supports"
             for e in mismatched_episode],
        )

    actual: Dict[str, Any] = {}
    actor_report: Dict[str, Any] = {}
    provenance: Dict[str, str] = {}
    timeline: List[EpisodeEvent] = []

    origin = ordered[0].occurred_at if ordered else 0.0

    for event in ordered:
        target = actual if _STAMP_DESTINATION[event.provenance] == "actual" else actor_report
        for name, value in event.fields.items():
            target[name] = value
            if _STAMP_DESTINATION[event.provenance] == "actual":
                provenance[name] = event.provenance
        detail = dict(event.detail)
        detail.update({
            "event_id": event.event_id,
            "source": event.source,
            "provenance": event.provenance,
        })
        if event.method:
            detail["method"] = event.method
        timeline.append(EpisodeEvent(at=event.occurred_at - origin,
                                     kind=event.kind, detail=detail))

    merged_attributes = dict(attributes or {})
    merged_attributes[PROVENANCE_ATTRIBUTE] = dict(provenance)

    episode = make_episode(
        episode_id=episode_id,
        domain=domain,
        requested=requested,
        actual=actual,
        actor_report=actor_report,
        timeline=timeline,
        outcome_reasons=outcome_reasons,
        attributes=merged_attributes,
    )
    return EpisodeAssembly(
        episode=episode,
        provenance=dict(provenance),
        estimated_fields=tuple(sorted(
            n for n, p in provenance.items() if p == PROVENANCE_ESTIMATED)),
        source_events=tuple(e.event_id for e in ordered),
        reducer_version=REDUCER_VERSION,
    )


def episode_provenance(episode: Episode) -> Dict[str, str]:
    """The per-field stamp map an assembled episode carries, or {} for
    an episode built by hand. Read-only accessor so callers never have
    to know the reserved attribute key."""
    found = episode.attributes.get(PROVENANCE_ATTRIBUTE)
    return dict(found) if isinstance(found, dict) else {}


def estimated_fields(episode: Episode) -> Tuple[str, ...]:
    """Which of this episode's judged fields are estimates rather than
    observations. This is what an auditor asks for when they want to
    know how much of a record was measured."""
    return tuple(sorted(n for n, p in episode_provenance(episode).items()
                        if p == PROVENANCE_ESTIMATED))
