"""
scenarios.py -- the test-scenario library and its approval gate.

WHAT A SCENARIO IS
-------------------
A scenario is a single concrete situation posed to Sentinel, plus the
answer the business decided is correct under its chosen reading of a
regulation. It is not a unit test of code. It is a probe of
INTERPRETATION: "given these facts, does Sentinel still answer the way
Legal said it should?"

Scenarios exist because regulations are not black and white. Where a
regulation is genuinely open, the business picks a reading. That
reading is then only real if it can be checked, and this is what
checks it.

WHY AI GENERATES THEM AND A HUMAN APPROVES THEM
------------------------------------------------
An AI is good at inventing edge cases a person would not think of and
bad at deciding which answer is legally correct. So generation is
fully automated and approval is fully human. A generated scenario
lands as PROPOSED and can never run. Legal moves it to APPROVED and,
in the same act, LOCKS the expected answer. Rejected scenarios are
kept, not deleted, so the record shows what was considered and
declined.

HASH BINDING (why an approved scenario cannot drift)
-----------------------------------------------------
Approval covers an exact set of facts and an exact expected answer. If
either changes afterward, the approval is void. Each scenario carries
a content hash over its substantive fields; the harness recomputes
that hash before running and refuses any scenario whose hash no longer
matches what was approved. Editing an approved scenario is therefore
not "an edit" -- it is a new scenario needing new approval. This is
the same posture cassettes already use: silent modification is refused
rather than detected later.

NO SILENT SKIPS
----------------
A scenario that cannot run (not approved, hash mismatch, unparseable)
is reported with a reason. It is never quietly dropped from the count,
because a shrinking denominator is the easiest way to make drift
disappear without fixing anything.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

# Lifecycle states. A scenario is only runnable in APPROVED.
STATUS_PROPOSED = "PROPOSED"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_RETIRED = "RETIRED"

RUNNABLE_STATUSES = frozenset({STATUS_APPROVED})


class ScenarioIntegrityError(Exception):
    """Raised when a scenario's recorded hash does not match its content."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(payload: Mapping[str, Any]) -> str:
    """Stable JSON for hashing: sorted keys, no incidental whitespace."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


@dataclass
class Scenario:
    """One approved-or-proposed interpretation probe.

    regulation_id -- which regulation this probes.
    zone          -- the named ambiguity zone inside that regulation.
                     Zones are how drift gets localized: "our reading of
                     the proxy-correlation zone slipped" is actionable,
                     "something drifted" is not.
    situation     -- the facts, as a flat mapping. Kept structured
                     rather than prose so the harness can hand it to a
                     resolver without another parsing step.
    options       -- the answers on offer, e.g. ["A", "B"].
    expected      -- the option Legal locked in at approval time.
                     None until approved.
    """

    regulation_id: str
    zone: str
    question: str
    situation: Dict[str, Any]
    options: List[str]
    scenario_id: str = field(default_factory=lambda: f"scn-{uuid.uuid4().hex[:12]}")
    expected: Optional[str] = None
    legal_rationale: Optional[str] = None
    status: str = STATUS_PROPOSED
    generated_by: str = "unspecified"
    generated_at: str = field(default_factory=_utc_now)
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    rejected_reason: Optional[str] = None
    content_hash: Optional[str] = None

    # -- hashing -------------------------------------------------------

    def hashable_content(self) -> Dict[str, Any]:
        """The fields approval actually covers.

        Deliberately excludes status, approver, timestamps and the hash
        itself: approving a scenario must not change what was approved.
        """
        return {
            "scenario_id": self.scenario_id,
            "regulation_id": self.regulation_id,
            "zone": self.zone,
            "question": self.question,
            "situation": self.situation,
            "options": sorted(self.options),
            "expected": self.expected,
        }

    def compute_hash(self) -> str:
        return hashlib.sha256(_canonical(self.hashable_content()).encode("utf-8")).hexdigest()

    def verify_hash(self) -> None:
        """Raise if the recorded hash no longer matches the content."""
        if self.content_hash is None:
            raise ScenarioIntegrityError(
                f"{self.scenario_id}: no content hash recorded; scenario was never sealed"
            )
        actual = self.compute_hash()
        if actual != self.content_hash:
            raise ScenarioIntegrityError(
                f"{self.scenario_id}: content changed after approval "
                f"(approved {self.content_hash[:12]}, now {actual[:12]})"
            )

    # -- lifecycle -----------------------------------------------------

    def approve(self, expected: str, approver: str, rationale: str) -> "Scenario":
        """Legal locks the expected answer and seals the scenario.

        The expected answer is supplied HERE, not at generation time, so
        that the human decision and the human identity are recorded in
        the same act. An AI-suggested answer is a suggestion; only this
        call makes an answer authoritative.
        """
        if self.status == STATUS_APPROVED:
            raise ValueError(f"{self.scenario_id}: already approved; create a new scenario instead")
        if expected not in self.options:
            raise ValueError(
                f"{self.scenario_id}: expected {expected!r} is not among options {self.options}"
            )
        if not approver.strip():
            raise ValueError(f"{self.scenario_id}: approver identity is required")
        self.expected = expected
        self.legal_rationale = rationale
        self.status = STATUS_APPROVED
        self.approved_by = approver
        self.approved_at = _utc_now()
        self.content_hash = self.compute_hash()
        return self

    def reject(self, approver: str, reason: str) -> "Scenario":
        """Decline a scenario. Kept, not deleted: the record of what was
        considered and turned down is itself governance evidence."""
        self.status = STATUS_REJECTED
        self.approved_by = approver
        self.approved_at = _utc_now()
        self.rejected_reason = reason
        return self

    def retire(self, approver: str, reason: str) -> "Scenario":
        """Take an approved scenario out of rotation without deleting it
        (regulation superseded, zone no longer exists)."""
        self.status = STATUS_RETIRED
        self.rejected_reason = reason
        self.approved_by = approver
        self.approved_at = _utc_now()
        return self

    @property
    def is_runnable(self) -> bool:
        return self.status in RUNNABLE_STATUSES and self.expected is not None

    # -- serialization -------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Scenario":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in payload.items() if k in known})


class ScenarioLibrary:
    """The set of scenarios for one or more regulations.

    Backed by a plain JSON file by default. The storage is deliberately
    dull and swappable: the governance value is in the approval gate and
    the hash binding, not in where the rows happen to sit. Point it at
    Postgres later without touching anything above it.
    """

    def __init__(self, scenarios: Optional[Iterable[Scenario]] = None):
        self._by_id: Dict[str, Scenario] = {}
        for scenario in scenarios or []:
            self.add(scenario)

    def add(self, scenario: Scenario) -> Scenario:
        if scenario.scenario_id in self._by_id:
            raise ValueError(f"duplicate scenario_id {scenario.scenario_id}")
        self._by_id[scenario.scenario_id] = scenario
        return scenario

    def get(self, scenario_id: str) -> Scenario:
        return self._by_id[scenario_id]

    def all(self) -> List[Scenario]:
        return sorted(self._by_id.values(), key=lambda s: s.scenario_id)

    def pending_approval(self, regulation_id: Optional[str] = None) -> List[Scenario]:
        return [
            s for s in self.all()
            if s.status == STATUS_PROPOSED
            and (regulation_id is None or s.regulation_id == regulation_id)
        ]

    def runnable(self, regulation_id: Optional[str] = None) -> List[Scenario]:
        return [
            s for s in self.all()
            if s.is_runnable
            and (regulation_id is None or s.regulation_id == regulation_id)
        ]

    def zones(self, regulation_id: Optional[str] = None) -> List[str]:
        return sorted({
            s.zone for s in self.all()
            if regulation_id is None or s.regulation_id == regulation_id
        })

    # -- persistence ---------------------------------------------------

    def to_json(self) -> str:
        return json.dumps(
            {"scenarios": [s.to_dict() for s in self.all()]},
            indent=2,
            sort_keys=True,
        )

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(self.to_json())

    @classmethod
    def load(cls, path: str) -> "ScenarioLibrary":
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return cls(Scenario.from_dict(row) for row in payload.get("scenarios", []))
