"""
realignment.py -- the annual meeting's output, and its upgrade path.

THE SHAPE (OPTION D: HYBRID)
-----------------------------
Four blocks, in the order a human actually reads them:

  quick_view      -- the checklist. Sign-off, decision, drift at a
                     glance. This is what gets read aloud in the room.
  narrative       -- why. Prose, written by people, explaining the
                     reasoning behind the decision. Five years from now
                     this is the only part that answers "what were they
                     thinking".
  structured_data -- the evidence. Test counts, per-zone alignment,
                     deployed checks. Machine-queryable.
  audit_trail     -- who approved it, when, and a hash sealing the
                     whole record.

The split exists because the audiences differ. A regulator wants the
structured block. A new hire two years from now needs the narrative. The
room needs the checklist. One format serving all three serves none.

THE UPGRADE PATH (OPTION C: STRUCTURED TRAIL)
----------------------------------------------
Three fields are present from day one and empty until they are needed:
history, version_changes and regulatory_events. Filling them in is the
whole migration. There is no reformatting step and no rewrite of old
records, because the day-one shape already has the slots.

That is the point of building D this way rather than building A and
regretting it. RealignmentTrail assembles the C-shaped longitudinal
view from a series of D records whenever the business wants it.

WHY THE HASH COVERS THE DECISION AND NOT THE PROSE STYLE
---------------------------------------------------------
The seal is computed over the substantive content: the decision, the
drift evidence, the interpretation version, the approvers. Fixing a
typo in the narrative should not invalidate a legal sign-off, and
changing the decision absolutely should. The hashable set is chosen on
that line.

BACKWARD COMPATIBILITY IS EXPLICIT
-----------------------------------
When an interpretation moves from v1 to v2, decisions already made under
v1 stay under v1. The record says so, names the affected date range, and
records whether anyone chose to go back and re-test them. Wm's rule: you
go back as far as the business wants to, and that choice is recorded
rather than assumed either way.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from .drift import DriftReport

DECISION_KEEP = "KEEP"
DECISION_UPDATE = "UPDATE"
DECISION_RETIRE = "RETIRE"
DECISION_REVIEW = "REQUIRES_REVIEW"

VALID_DECISIONS = frozenset({DECISION_KEEP, DECISION_UPDATE, DECISION_RETIRE, DECISION_REVIEW})

SIGNOFF_APPROVED = "APPROVED"
SIGNOFF_REQUIRES_REVIEW = "REQUIRES_REVIEW"
SIGNOFF_CHANGED = "CHANGED"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


@dataclass
class Approver:
    name: str
    title: str
    date: str = field(default_factory=_utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RegulatoryEvent:
    """Something the agency did since the last realignment.

    Present in the day-one shape because these are exactly what the
    annual meeting is trying to reconstruct, and reconstructing them
    from memory a year later does not work.
    """

    date: str
    agency: str
    event: str
    impact_on_interpretation: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VersionChange:
    """The v1-to-v2 delta and what was done about the decisions in between."""

    from_version: str
    to_version: str
    activation_date: str
    reason: str
    changes: List[str] = field(default_factory=list)
    decisions_affected: Optional[int] = None
    affected_date_range: Optional[str] = None
    retroactive_retest: str = "NOT_REQUESTED"
    retroactive_scope: Optional[str] = None
    known_deltas: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RealignmentRecord:
    """One regulation's annual (or triggered) realignment outcome."""

    record_id: str
    regulation_id: str
    interpretation_version: str
    realignment_date: str
    activation_date: str

    legal_sign_off: str
    decision: str

    context: str
    business_rationale: str
    legal_assessment: str

    drift: Optional[DriftReport] = None
    checks_deployed: List[str] = field(default_factory=list)
    checks_config: Dict[str, Any] = field(default_factory=dict)
    outcome_correlation: Dict[str, Any] = field(default_factory=dict)

    approved_by: List[Approver] = field(default_factory=list)
    decision_rationale: str = ""

    # --- Option C slots. Empty until used; no migration needed later. ---
    regulatory_events: List[RegulatoryEvent] = field(default_factory=list)
    version_change: Optional[VersionChange] = None
    open_questions: List[str] = field(default_factory=list)

    trigger: str = "ANNUAL"
    record_hash: Optional[str] = None

    def __post_init__(self):
        if self.decision not in VALID_DECISIONS:
            raise ValueError(f"decision must be one of {sorted(VALID_DECISIONS)}, got {self.decision!r}")
        if self.decision == DECISION_UPDATE and self.version_change is None:
            raise ValueError(
                "decision=UPDATE requires a version_change describing the delta "
                "and what happens to decisions made under the prior version"
            )
        if not self.approved_by:
            raise ValueError("a realignment record with no approver is not a governance record")

    # -- the four blocks -----------------------------------------------

    def quick_view(self) -> Dict[str, Any]:
        drift_summary: Dict[str, Any] = {}
        risk = "UNKNOWN"
        if self.drift:
            risk = self.drift.risk_level
            for zone in self.drift.zones:
                drift_summary[zone.zone] = (
                    f"{zone.alignment:.0%}" if zone.alignment is not None else "no data"
                )
        return {
            "legal_sign_off": self.legal_sign_off,
            "decision": self.decision,
            "drift_summary": drift_summary,
            "flagged_zones": [z.zone for z in self.drift.flagged_zones] if self.drift else [],
            "risk_level": risk,
        }

    def narrative(self) -> Dict[str, str]:
        return {
            "context": self.context,
            "business_rationale": self.business_rationale,
            "legal_assessment": self.legal_assessment,
        }

    def structured_data(self) -> Dict[str, Any]:
        return {
            "drift": self.drift.to_dict() if self.drift else None,
            "outcome_correlation": self.outcome_correlation,
            "interpretation_details": {
                "version": self.interpretation_version,
                "activation_date": self.activation_date,
                "checks_deployed": sorted(self.checks_deployed),
                "checks_config": self.checks_config,
            },
            "regulatory_events": [e.to_dict() for e in self.regulatory_events],
            "version_change": self.version_change.to_dict() if self.version_change else None,
            "open_questions": self.open_questions,
        }

    def audit_trail(self) -> Dict[str, Any]:
        return {
            "approved_by": [a.to_dict() for a in self.approved_by],
            "decision_rationale": self.decision_rationale,
            "trigger": self.trigger,
            "hash": self.record_hash,
        }

    # -- sealing -------------------------------------------------------

    def hashable_content(self) -> Dict[str, Any]:
        """Substance only. Narrative prose is excluded on purpose: a typo
        fix must not void a legal sign-off, and a changed decision must."""
        return {
            "record_id": self.record_id,
            "regulation_id": self.regulation_id,
            "interpretation_version": self.interpretation_version,
            "realignment_date": self.realignment_date,
            "activation_date": self.activation_date,
            "legal_sign_off": self.legal_sign_off,
            "decision": self.decision,
            "checks_deployed": sorted(self.checks_deployed),
            "checks_config": self.checks_config,
            "drift": self.drift.to_dict() if self.drift else None,
            "version_change": self.version_change.to_dict() if self.version_change else None,
            "approved_by": [a.to_dict() for a in self.approved_by],
        }

    def seal(self) -> str:
        self.record_hash = hashlib.sha256(
            _canonical(self.hashable_content()).encode("utf-8")
        ).hexdigest()
        return self.record_hash

    def verify(self) -> bool:
        if self.record_hash is None:
            return False
        current = hashlib.sha256(
            _canonical(self.hashable_content()).encode("utf-8")
        ).hexdigest()
        return current == self.record_hash

    # -- serialization -------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metadata": {
                "record_id": self.record_id,
                "regulation_id": self.regulation_id,
                "interpretation_version": self.interpretation_version,
                "realignment_date": self.realignment_date,
                "activation_date": self.activation_date,
                "trigger": self.trigger,
            },
            "quick_view": self.quick_view(),
            "narrative": self.narrative(),
            "structured_data": self.structured_data(),
            "audit_trail": self.audit_trail(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


class RealignmentTrail:
    """The Option C longitudinal view, assembled from Option D records.

    This is the migration. Nothing is rewritten and no old record is
    reformatted; the trail is a read over records that already carried
    the right slots. Call it when the business wants the multi-year
    picture, keep writing D records either way.
    """

    def __init__(self, records: Optional[Sequence[RealignmentRecord]] = None):
        self._records: List[RealignmentRecord] = list(records or [])

    def add(self, record: RealignmentRecord) -> None:
        self._records.append(record)

    def for_regulation(self, regulation_id: str) -> List[RealignmentRecord]:
        return sorted(
            (r for r in self._records if r.regulation_id == regulation_id),
            key=lambda r: r.realignment_date,
        )

    def history(self, regulation_id: str) -> List[Dict[str, Any]]:
        return [
            {
                "realignment_date": r.realignment_date,
                "record_id": r.record_id,
                "interpretation_version": r.interpretation_version,
                "decision": r.decision,
                "risk_level": r.drift.risk_level if r.drift else "UNKNOWN",
                "overall_alignment": r.drift.overall_alignment if r.drift else None,
                "sealed": r.verify(),
            }
            for r in self.for_regulation(regulation_id)
        ]

    def zone_trend(self, regulation_id: str, zone: str) -> List[Dict[str, Any]]:
        """One zone's alignment across every realignment on record.

        This is what makes 'subtle drift accumulating over years' visible.
        Any single year can look fine while the five-year line slopes down,
        and the annual meeting exists precisely to catch that.
        """
        trend: List[Dict[str, Any]] = []
        for record in self.for_regulation(regulation_id):
            if not record.drift:
                continue
            for zone_drift in record.drift.zones:
                if zone_drift.zone == zone:
                    trend.append({
                        "date": record.realignment_date,
                        "interpretation_version": record.interpretation_version,
                        "alignment": zone_drift.alignment,
                        "state": zone_drift.state,
                    })
        return trend

    def version_changes(self, regulation_id: str) -> List[Dict[str, Any]]:
        return [
            r.version_change.to_dict()
            for r in self.for_regulation(regulation_id)
            if r.version_change is not None
        ]

    def regulatory_events(self, regulation_id: str) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for record in self.for_regulation(regulation_id):
            events.extend(e.to_dict() for e in record.regulatory_events)
        return sorted(events, key=lambda e: e["date"])

    def unsealed(self) -> List[str]:
        """Records whose hash no longer verifies. Empty is the only
        acceptable answer walking into an annual meeting."""
        return [r.record_id for r in self._records if not r.verify()]

    def to_structured_view(self, regulation_id: str) -> Dict[str, Any]:
        """The full Option C document for one regulation."""
        records = self.for_regulation(regulation_id)
        zones = sorted({
            z.zone for r in records if r.drift for z in r.drift.zones
        })
        return {
            "regulation_id": regulation_id,
            "generated_at": _utc_now(),
            "record_count": len(records),
            "history": self.history(regulation_id),
            "zone_trends": {z: self.zone_trend(regulation_id, z) for z in zones},
            "version_changes": self.version_changes(regulation_id),
            "regulatory_events": self.regulatory_events(regulation_id),
            "integrity": {
                "unsealed_records": [
                    r.record_id for r in records if not r.verify()
                ],
            },
        }
