"""
OutcomeV1 -- the durable outcome obligation, and the governing rule for
everything Sentinel does not yet know.

THE PROVENANCE RULE
-------------------
    Every claim is stamped verified, attested, or estimated, and they
    are not interchangeable. If it's unknown, Sentinel will timestamp
    why and what would close it.

That is the whole specification for this module. An auditor will run
out of patience with "almost", "probably", "we made a guess" -- and a
governance system's own INDETERMINATE state earns exactly the same
reaction if it is a single mushy flag covering every different way a
thing can be unknown. So there is no flat "indeterminate" here. An
unresolved obligation carries a TYPED reason from a bounded vocabulary,
the timestamp it opened, and the horizon by which it is expected to
close. "Overdue" is deliberately not a state: it is arithmetic on those
two timestamps (see is_overdue), which means nobody can store a lie
about it.

The same discipline is already proven elsewhere in this codebase on a
different problem -- regulatory_checks' five-level tier confidence
scale (undeclared -> attested-unsupported ->
attested-accountable-unsupported -> attested-accountable-evidenced ->
verified) replaced one silent PASS with five labels that each say
something true. This module does that for outcomes.

TWO RECORDS, TWO LIFESPANS
--------------------------
The decision record -- what was asked, what was decided, why -- closes
PERMANENTLY at decision time and never reopens. An outcome that arrives
two years later does not get to edit it, because a record an auditor
can watch change is not a record.

An obligation is a SEPARATE, durable record with its own lifespan. It
can stay open indefinitely, maturing on a schedule the cassette
declares ("loan decisions carry a 24-month performance obligation").

LINKAGE, AND WHY IT ONLY POINTS ONE WAY
---------------------------------------
An obligation points AT a decision, by that decision's current_hash.
The decision never points at the obligation, because it cannot: it is
closed, and a pointer written into it later would be a mutation of a
hashed row.

What the decision DOES carry, hashed in at decision time, is the
maturation rule that was in force when it was made
(outcome_obligation, via canonical_fields.OPTIONAL_HASHED_FIELDS). That
is knowable at decision time, so it never changes, and it is what makes
independent derivation possible: the twin can compute the whole set of
open obligations from the decision feed alone, without the primary
signing an "obligation opened" event for every decision. An operator
who wants to make an obligation disappear has to make the DECISION
disappear, which trips MISSING on the twin. Independence without a
signing burden.

WHAT IS GOVERNANCE HERE AND WHAT IS NOT
---------------------------------------
Per-decision outcome -- was THIS one right -- is business reporting,
not governance, and stays OUT of the tamper-evident chain. A defaulted
loan on a calibrated model is expected loss, not evidence of a bad
decision, and a chain that treats it as evidence teaches its readers to
misread it.

Two things ARE governance. A harm event (a denial reversed on appeal:
the decision PROCESS failed, not the odds) is a chain event with its
own record kind. And cohort-level performance -- calibration parity,
the four-fifths rule -- is governance, requires real resolved outcomes
flowing back in, and cannot run at decision time by construction. That
is the feedback path C2 dimension 4 has been structurally waiting for;
see to_cohort_decision at the bottom of this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from event_v1 import PROVENANCE_ESTIMATED, PROVENANCE_STAMPS

# --- Lifecycle states. Stable strings: these ride in stored rows. ---
# Deliberately NOT the twin's verdict vocabulary. twin_detector's EXTRA
# means "wipe" and its PENDING means "inside the transport SLA"; outcome
# lag is unbounded and structurally different from both, and overloading
# either word would make two unrelated conditions read the same to a
# regulator.
OUTCOME_OPEN = "OPEN"
OUTCOME_RESOLVED = "RESOLVED"
OUTCOME_ABANDONED = "ABANDONED"

OUTCOME_STATES: Tuple[str, ...] = (OUTCOME_OPEN, OUTCOME_RESOLVED, OUTCOME_ABANDONED)

# --- Why an obligation is still open. Bounded, never free text. ---
# Free text here would rebuild the mushy flag this module exists to
# prevent: it cannot be counted, cannot be alerted on, and cannot be
# compared across two auditors reading the same ledger.
REASON_NOT_YET_DUE = "not_yet_due"
REASON_INSUFFICIENT_COHORT = "insufficient_cohort"
REASON_DATA_SOURCE_UNREACHABLE = "data_source_unreachable"
REASON_GENUINELY_AMBIGUOUS = "genuinely_ambiguous"

OPEN_REASONS: Tuple[str, ...] = (
    REASON_NOT_YET_DUE,             # the horizon has not arrived; the honest default
    REASON_INSUFFICIENT_COHORT,     # matured, but too few peers to say anything
    REASON_DATA_SOURCE_UNREACHABLE, # matured, but the system of record is unavailable
    REASON_GENUINELY_AMBIGUOUS,     # matured, data present, and it genuinely does not
                                    # resolve to favorable or unfavorable. Saying so is
                                    # the point; forcing a bool here would feed a
                                    # coin-flip into a four-fifths test.
)

# --- Why an obligation will never resolve. Also bounded. ---
REASON_SUBJECT_WITHDREW = "subject_withdrew"
REASON_DECISION_SUPERSEDED = "decision_superseded"
REASON_RETENTION_EXPIRED = "retention_expired"

ABANDONED_REASONS: Tuple[str, ...] = (
    REASON_SUBJECT_WITHDREW,
    REASON_DECISION_SUPERSEDED,
    REASON_RETENTION_EXPIRED,
)

# Duration suffixes for a maturation declaration. Months and years are
# FIXED-LENGTH approximations (30 and 365 days), stated here rather than
# hidden: "24mo" is 730 days, not 24 calendar months. A domain that
# needs calendar-exact maturation declares in days and computes it
# itself, because a horizon that quietly shifts with the calendar is a
# horizon an auditor cannot check the arithmetic on.
_UNIT_SECONDS: Dict[str, float] = {
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    "mo": 86400.0 * 30,
    "y": 86400.0 * 365,
}
_DECLARATION_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)@(\d+)(s|m|h|d|mo|y)$")


class OutcomeIntegrityError(Exception):
    """An obligation failed validation. Carries every violation found so
    one attempt reports the whole picture (same reporting posture as
    EpisodeIntegrityError and EventIntegrityError)."""

    def __init__(self, obligation_id: str, violations: List[str]):
        self.obligation_id = obligation_id
        self.violations = list(violations)
        lines = "\n".join(f"  - {v}" for v in self.violations)
        super().__init__(
            f"Outcome obligation '{obligation_id}' failed integrity validation "
            f"({len(self.violations)} violation(s)):\n{lines}"
        )


@dataclass(frozen=True)
class MaturationRule:
    """When an obligation of a given kind comes due, in a form that is
    stable enough to hash into a decision row at decision time.

    The declaration string ("loan_performance@24mo") is what lands in
    the ledger's outcome_obligation field and what the twin re-parses to
    derive the open set independently. Both sides parse the same string
    with the same function, for the same reason canonical_fields exists:
    two implementations of one rule is two rules."""

    kind: str
    horizon_seconds: float

    def declaration(self) -> str:
        """The canonical string form. Emits the largest exact unit so a
        rule round-trips to the same text it was parsed from."""
        for suffix in ("y", "mo", "d", "h", "m", "s"):
            size = _UNIT_SECONDS[suffix]
            if self.horizon_seconds >= size and self.horizon_seconds % size == 0:
                return f"{self.kind}@{int(self.horizon_seconds // size)}{suffix}"
        return f"{self.kind}@{int(self.horizon_seconds)}s"

    @classmethod
    def parse(cls, declaration: str) -> "MaturationRule":
        """Parse a declaration string. Fail-loud: an unparseable rule is
        refused rather than defaulted, because a defaulted horizon is a
        horizon nobody chose."""
        match = _DECLARATION_RE.match(str(declaration).strip())
        if not match:
            raise ValueError(
                f"unparseable maturation declaration {declaration!r}; expected "
                f"'<kind>@<count><unit>' with unit in "
                f"{sorted(_UNIT_SECONDS)} (e.g. 'loan_performance@24mo')"
            )
        kind, count, unit = match.group(1), int(match.group(2)), match.group(3)
        if count <= 0:
            raise ValueError(
                f"maturation horizon must be positive, got {count!r} in "
                f"{declaration!r} -- a zero-length obligation is not an "
                f"obligation, it is a decision that already closed"
            )
        return cls(kind=kind, horizon_seconds=count * _UNIT_SECONDS[unit])


@dataclass(frozen=True)
class OutcomeObligation:
    """One durable obligation attached to one closed decision.

    obligation_id -- stable identity; also the dedupe key.
    decision_hash -- the current_hash of the decision this is owed on.
                     Points one way, at a row that never changes.
    obligation_kind -- from the cassette's maturation rule
                     ("loan_performance").
    opened_at / expected_by -- absolute epoch seconds. The pair is what
                     makes overdue computable instead of storable.
    state         -- one of OUTCOME_STATES.
    reason_code   -- required on OPEN and ABANDONED, from the matching
                     bounded vocabulary. Never set on RESOLVED.
    resolved_at / resolved_value / resolution_provenance /
    resolution_method -- set together on RESOLVED, and only there.
    favorable     -- the cassette's domain call on whether the outcome
                     was favorable, or None when it genuinely does not
                     resolve to one. Feeds cohort testing; see
                     to_cohort_decision.
    """

    obligation_id: str
    decision_hash: str
    domain: str
    obligation_kind: str
    opened_at: float
    expected_by: float
    state: str = OUTCOME_OPEN
    reason_code: Optional[str] = REASON_NOT_YET_DUE
    resolved_at: Optional[float] = None
    resolved_value: Optional[Dict[str, Any]] = None
    resolution_provenance: Optional[str] = None
    resolution_method: Optional[str] = None
    favorable: Optional[bool] = None
    subject_id: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)


def open_obligation(obligation_id: str, decision_hash: str, domain: str,
                    rule: MaturationRule, opened_at: float,
                    subject_id: Optional[str] = None,
                    detail: Mapping[str, Any] | None = None) -> OutcomeObligation:
    """Open an obligation from a maturation rule. The horizon is
    computed, never passed in, so expected_by cannot drift from the rule
    that justifies it."""
    return OutcomeObligation(
        obligation_id=str(obligation_id),
        decision_hash=str(decision_hash),
        domain=str(domain),
        obligation_kind=rule.kind,
        opened_at=float(opened_at),
        expected_by=float(opened_at) + rule.horizon_seconds,
        state=OUTCOME_OPEN,
        reason_code=REASON_NOT_YET_DUE,
        subject_id=None if subject_id is None else str(subject_id),
        detail=dict(detail or {}),
    )


def resolve(obligation: OutcomeObligation, resolved_at: float,
            resolved_value: Mapping[str, Any], provenance: str,
            favorable: Optional[bool] = None,
            method: Optional[str] = None) -> OutcomeObligation:
    """Close an obligation with a real result. Returns a NEW obligation;
    the original is frozen, and an append-only store keeps both."""
    closed = OutcomeObligation(
        obligation_id=obligation.obligation_id,
        decision_hash=obligation.decision_hash,
        domain=obligation.domain,
        obligation_kind=obligation.obligation_kind,
        opened_at=obligation.opened_at,
        expected_by=obligation.expected_by,
        state=OUTCOME_RESOLVED,
        reason_code=None,
        resolved_at=float(resolved_at),
        resolved_value=dict(resolved_value),
        resolution_provenance=str(provenance),
        resolution_method=None if method is None else str(method),
        favorable=favorable,
        subject_id=obligation.subject_id,
        detail=dict(obligation.detail),
    )
    validate_obligation(closed)
    return closed


def stay_open(obligation: OutcomeObligation, reason_code: str) -> OutcomeObligation:
    """Restate an open obligation with a more specific reason. A matured
    obligation that could not be resolved moves off not_yet_due onto the
    reason that actually applies -- which is the difference between a
    system that is waiting and a system that is stuck, and an auditor is
    entitled to know which one they are looking at."""
    restated = OutcomeObligation(
        obligation_id=obligation.obligation_id,
        decision_hash=obligation.decision_hash,
        domain=obligation.domain,
        obligation_kind=obligation.obligation_kind,
        opened_at=obligation.opened_at,
        expected_by=obligation.expected_by,
        state=OUTCOME_OPEN,
        reason_code=str(reason_code),
        favorable=None,
        subject_id=obligation.subject_id,
        detail=dict(obligation.detail),
    )
    validate_obligation(restated)
    return restated


def abandon(obligation: OutcomeObligation, reason_code: str,
            at: float) -> OutcomeObligation:
    """Declare an obligation will never resolve, with a bounded reason.
    Recorded, not deleted: an obligation that vanishes is
    indistinguishable from one that was never opened."""
    dropped = OutcomeObligation(
        obligation_id=obligation.obligation_id,
        decision_hash=obligation.decision_hash,
        domain=obligation.domain,
        obligation_kind=obligation.obligation_kind,
        opened_at=obligation.opened_at,
        expected_by=obligation.expected_by,
        state=OUTCOME_ABANDONED,
        reason_code=str(reason_code),
        resolved_at=float(at),
        favorable=None,
        subject_id=obligation.subject_id,
        detail=dict(obligation.detail),
    )
    validate_obligation(dropped)
    return dropped


def validate_obligation(obligation: OutcomeObligation) -> None:
    """Fail-loud validation. Raises OutcomeIntegrityError with the
    complete violation list, or returns.

    The invariants are all one rule wearing different clothes: an
    unknown must say WHY it is unknown and WHAT WOULD CLOSE IT, and a
    known must say how it came to be known."""
    violations: List[str] = []

    for label, value in (("obligation_id", obligation.obligation_id),
                         ("decision_hash", obligation.decision_hash),
                         ("domain", obligation.domain),
                         ("obligation_kind", obligation.obligation_kind)):
        if not str(value).strip():
            violations.append(f"{label} must be a non-empty string")

    if obligation.state not in OUTCOME_STATES:
        violations.append(
            f"state must be one of {list(OUTCOME_STATES)}, got {obligation.state!r}"
        )

    for label, value in (("opened_at", obligation.opened_at),
                         ("expected_by", obligation.expected_by)):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            violations.append(f"{label} must be a number, got {type(value).__name__}")
    if (isinstance(obligation.opened_at, (int, float))
            and isinstance(obligation.expected_by, (int, float))
            and obligation.expected_by <= obligation.opened_at):
        violations.append(
            f"expected_by ({obligation.expected_by!r}) must be after opened_at "
            f"({obligation.opened_at!r}) -- an obligation that matures before it "
            f"opens has no horizon to be honored or missed"
        )

    if obligation.state == OUTCOME_OPEN:
        if obligation.reason_code not in OPEN_REASONS:
            violations.append(
                f"an OPEN obligation needs a typed reason from {list(OPEN_REASONS)}, "
                f"got {obligation.reason_code!r} -- 'unknown' with no reason is the "
                f"flat indeterminate flag this schema exists to refuse"
            )
        if obligation.resolved_at is not None or obligation.resolved_value is not None:
            violations.append(
                "an OPEN obligation carries no resolution; it is open"
            )
        if obligation.favorable is not None:
            violations.append(
                f"an OPEN obligation cannot already be favorable={obligation.favorable!r} "
                f"-- a verdict without a resolution behind it is exactly the "
                f"guess-dressed-as-a-measurement this schema refuses"
            )

    elif obligation.state == OUTCOME_RESOLVED:
        if obligation.reason_code is not None:
            violations.append(
                f"a RESOLVED obligation carries no open-reason, got "
                f"{obligation.reason_code!r} -- it cannot be both closed and pending"
            )
        if obligation.resolved_at is None:
            violations.append("a RESOLVED obligation must record resolved_at")
        if not isinstance(obligation.resolved_value, dict) or not obligation.resolved_value:
            violations.append(
                "a RESOLVED obligation must record a non-empty resolved_value -- "
                "closing with nothing recorded is closing with nothing established"
            )
        if obligation.resolution_provenance not in PROVENANCE_STAMPS:
            violations.append(
                f"a RESOLVED obligation must stamp its resolution with one of "
                f"{list(PROVENANCE_STAMPS)}, got {obligation.resolution_provenance!r} "
                f"-- verified, attested and estimated are not interchangeable"
            )
        if (obligation.resolution_provenance == PROVENANCE_ESTIMATED
                and not str(obligation.resolution_method or "").strip()):
            violations.append(
                "an ESTIMATED resolution must name its method -- an estimated "
                "outcome that will not say how it was estimated is the "
                "proxy-metric-as-measurement pattern this system refuses to ship"
            )

    elif obligation.state == OUTCOME_ABANDONED:
        if obligation.reason_code not in ABANDONED_REASONS:
            violations.append(
                f"an ABANDONED obligation needs a typed reason from "
                f"{list(ABANDONED_REASONS)}, got {obligation.reason_code!r}"
            )
        if obligation.favorable is not None:
            violations.append(
                "an ABANDONED obligation has no favorability; nothing resolved"
            )

    if violations:
        raise OutcomeIntegrityError(obligation.obligation_id, violations)


def is_overdue(obligation: OutcomeObligation, now: float) -> bool:
    """Whether an open obligation has blown past its declared horizon.

    Computed, never stored. Two timestamps and a comparison cannot be
    quietly set to False by whoever would prefer the report to be clean;
    a stored `overdue` column can."""
    return obligation.state == OUTCOME_OPEN and float(now) > obligation.expected_by


def horizon_honored(obligations: Iterable[OutcomeObligation],
                    now: float) -> Dict[str, int]:
    """Portfolio answer to 'is this system honoring its own horizons' --
    the question an auditor asks after they stop believing any single
    obligation. Counts by state, plus how many open ones are overdue and
    the reason breakdown for those still open."""
    counts: Dict[str, int] = {state: 0 for state in OUTCOME_STATES}
    counts["overdue"] = 0
    reasons: Dict[str, int] = {}
    for obligation in obligations:
        counts[obligation.state] = counts.get(obligation.state, 0) + 1
        if is_overdue(obligation, now):
            counts["overdue"] += 1
        if obligation.state == OUTCOME_OPEN and obligation.reason_code:
            reasons[obligation.reason_code] = reasons.get(obligation.reason_code, 0) + 1
    for reason, count in reasons.items():
        counts[f"open:{reason}"] = count
    return counts


def derive_open_obligations(decision_rows: Iterable[Mapping[str, Any]],
                            ) -> List[OutcomeObligation]:
    """Derive the obligation set from decision rows alone.

    This is what lets the twin be an independent witness of what is
    OWED, not just of what was recorded. Every input row carries its own
    maturation declaration, hashed in at decision time, so the twin
    re-parses that string and computes the same set the primary would --
    without the primary signing an obligation-open event for every
    decision, and without being able to quietly shorten the list.

    Rows with no outcome_obligation declared are skipped: a domain whose
    outcomes are known at decision time (an IVR call's quality is
    settled at hangup) genuinely owes nothing later, and inventing an
    obligation for it would be fabricating a debt.

    Each row needs: current_hash, timestamp, outcome_obligation, and a
    domain. A row that declares an obligation but cannot be parsed is
    raised, not skipped -- an unreadable declaration is a hole in the
    twin's derivation, and a silent skip is how a hole becomes invisible.
    """
    derived: List[OutcomeObligation] = []
    for row in decision_rows:
        declaration = row.get("outcome_obligation")
        if not declaration:
            continue
        rule = MaturationRule.parse(declaration)
        decision_hash = str(row.get("current_hash") or "")
        derived.append(open_obligation(
            obligation_id=f"{decision_hash}:{rule.kind}",
            decision_hash=decision_hash,
            domain=str(row.get("domain") or row.get("cassette_version") or "unknown"),
            rule=rule,
            opened_at=float(row.get("timestamp") or 0.0),
            subject_id=row.get("subject_id"),
        ))
    return derived


def cohort_favorable(obligation: OutcomeObligation) -> bool:
    """The two testability rules ANY cohort dimension needs before it can
    use a RESOLVED obligation -- split out of to_cohort_decision (which
    now delegates here) so a dimension that needs favorable/subject but
    NOT a demographic estimate (dimension 6's geography-only check) can
    reuse this gate directly, without going through to_cohort_decision's
    group_distribution parameter at all.

    Same refusal posture as to_cohort_decision: an obligation that isn't
    RESOLVED, or that resolved to genuinely ambiguous (favorable=None),
    raises rather than returning a placeholder -- coercing either case
    into a bool would put an unmeasured or genuinely-unknown decision
    into a statistical finding.
    """
    if obligation.state != OUTCOME_RESOLVED:
        raise OutcomeIntegrityError(obligation.obligation_id, [
            f"cannot enter a cohort test in state {obligation.state!r} with reason "
            f"{obligation.reason_code!r}; only RESOLVED outcomes are testable, and "
            f"substituting a default for an unresolved one would put an unmeasured "
            f"decision into a statistical finding"
        ])
    if obligation.favorable is None:
        raise OutcomeIntegrityError(obligation.obligation_id, [
            "resolved with favorable=None (genuinely ambiguous); a cohort test "
            "needs a favorable/unfavorable call, and coercing an ambiguous "
            "outcome to either one fabricates the input to a fairness statistic"
        ])
    return bool(obligation.favorable)


def to_cohort_decision(obligation: OutcomeObligation, group_distribution):
    """Turn a RESOLVED obligation into the unit C2 dimension 4 tests.

    This is the return path cohort-level bias testing has been
    structurally waiting for: check_statistical_outcome_equity has
    always taken already-resolved outcomes, and until now nothing in the
    system produced any.

    Refuses anything not RESOLVED with a real favorable/unfavorable
    call -- see cohort_favorable, which this delegates to. An obligation
    that is still open, or that resolved to genuinely ambiguous, is NOT
    a quiet False -- feeding a placeholder into a four-fifths test would
    produce a disparate-impact number computed partly from decisions
    nobody has measured yet, which is worse than having no number."""
    from regulatory_checks import CohortDecision

    favorable_outcome = cohort_favorable(obligation)
    return CohortDecision(
        subject_id=str(obligation.subject_id or obligation.decision_hash),
        favorable_outcome=favorable_outcome,
        group_distribution=group_distribution,
    )
