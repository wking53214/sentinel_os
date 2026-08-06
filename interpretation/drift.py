"""
drift.py -- did our reading of the regulation slip?

WHAT DRIFT IS HERE
-------------------
Drift is Sentinel answering a locked scenario differently than Legal
locked it. It is measured per ZONE, not per regulation, because "our
reading of the proxy-correlation zone slipped from 100% to 88%" is
something a person can act on, and "something drifted" is not.

TOLERANCE IS A BUSINESS SETTING, NOT AN ENGINEERING ONE
--------------------------------------------------------
There is no defensible universal answer to "how much drift is
acceptable". It depends on what a wrong call in that zone costs. So the
default here is the conservative one: STRICT, meaning any mismatch at
all raises a flag. That default is chosen so the first year of running
this produces data, not comfort.

The intended path is calibration, not permanent strictness. After a few
months, calibration_suggestion() reports what each zone has actually
been doing, and the business sets a real number per zone with that in
hand. A threshold picked from evidence is a governance decision; a
threshold picked on day one is a guess wearing a number.

THREE STATES, AND WHY "WATCH" EXISTS
-------------------------------------
  OK     -- alignment at or above the zone's threshold.
  WATCH  -- below threshold but above the breach line.
  BREACH -- below the breach line, or any mismatch under STRICT.

WATCH exists so that a slow slide gets noticed while it is still slow.
A two-state system only tells you after it is already a problem, which
in a domain with a multi-week outcome lag is too late to be useful.

WHAT THIS MODULE REFUSES TO DO
-------------------------------
It does not score a zone with no decided scenarios. An empty zone
returns alignment None and state UNKNOWN, never 100%. Silence is not
agreement, and a zone whose scenarios all errored out is a broken zone,
not a healthy one.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from .harness import TestRun

STATE_OK = "OK"
STATE_WATCH = "WATCH"
STATE_BREACH = "BREACH"
STATE_UNKNOWN = "UNKNOWN"

RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"

# Conservative default: any mismatch is a breach. See module docstring.
STRICT = "STRICT"


@dataclass
class ZoneTolerance:
    """What counts as acceptable in one ambiguity zone.

    watch_below  -- alignment under this goes to WATCH.
    breach_below -- alignment under this goes to BREACH.

    Under STRICT both are ignored and a single mismatch breaches.
    """

    zone: str
    mode: str = STRICT
    watch_below: float = 1.0
    breach_below: float = 0.95
    set_by: Optional[str] = None
    set_at: Optional[str] = None
    rationale: Optional[str] = None

    def __post_init__(self):
        if self.mode != STRICT and not (0.0 <= self.breach_below <= self.watch_below <= 1.0):
            raise ValueError(
                f"{self.zone}: need 0 <= breach_below <= watch_below <= 1, "
                f"got breach={self.breach_below} watch={self.watch_below}"
            )


@dataclass
class ToleranceConfig:
    """Per-zone tolerances plus the fallback for zones nobody has set yet.

    A newly discovered zone inherits the default, which is STRICT. New
    zones therefore start loud. That is deliberate: an unconfigured zone
    is one nobody has thought about, and it should demand attention
    rather than quietly pass.
    """

    default: ZoneTolerance = field(
        default_factory=lambda: ZoneTolerance(zone="__default__", mode=STRICT)
    )
    per_zone: Dict[str, ZoneTolerance] = field(default_factory=dict)

    def for_zone(self, zone: str) -> ZoneTolerance:
        return self.per_zone.get(zone, self.default)

    def set_zone(self, tolerance: ZoneTolerance) -> None:
        self.per_zone[tolerance.zone] = tolerance

    def to_dict(self) -> Dict[str, Any]:
        return {
            "default": asdict(self.default),
            "per_zone": {z: asdict(t) for z, t in sorted(self.per_zone.items())},
        }


@dataclass
class ZoneDrift:
    zone: str
    decided: int
    matched: int
    mismatched: int
    indeterminate: int
    errored: int
    alignment: Optional[float]
    baseline: Optional[float]
    delta: Optional[float]
    state: str
    tolerance_mode: str
    flagged: bool
    mismatch_examples: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DriftReport:
    """One month's answer to: is our reading holding?"""

    regulation_id: str
    interpretation_version: str
    run_id: str
    generated_at: str
    zones: List[ZoneDrift]
    overall_alignment: Optional[float]
    risk_level: str
    tolerance: Dict[str, Any]

    @property
    def flagged_zones(self) -> List[ZoneDrift]:
        return [z for z in self.zones if z.flagged]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regulation_id": self.regulation_id,
            "interpretation_version": self.interpretation_version,
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "overall_alignment": self.overall_alignment,
            "risk_level": self.risk_level,
            "zones": [z.to_dict() for z in self.zones],
            "flagged_zone_names": [z.zone for z in self.flagged_zones],
            "tolerance": self.tolerance,
        }


class DriftAnalyzer:
    """Turns a TestRun into a per-zone drift verdict."""

    def __init__(self, tolerance: Optional[ToleranceConfig] = None):
        self._tolerance = tolerance or ToleranceConfig()

    def analyze(
        self,
        run: TestRun,
        baseline: Optional[Dict[str, float]] = None,
    ) -> DriftReport:
        """baseline maps zone -> prior alignment, usually last month's
        report. Absent baseline means delta is None rather than zero:
        an unknown change is not a zero change."""
        baseline = baseline or {}
        zones: List[ZoneDrift] = []

        for zone, results in sorted(run.by_zone().items()):
            matched = sum(1 for r in results if r.result == "MATCH")
            mismatched = sum(1 for r in results if r.result == "MISMATCH")
            indeterminate = sum(1 for r in results if r.result == "INDETERMINATE")
            errored = sum(1 for r in results if r.result == "ERROR")
            decided = matched + mismatched

            alignment = (matched / decided) if decided else None
            prior = baseline.get(zone)
            delta = (alignment - prior) if (alignment is not None and prior is not None) else None

            tol = self._tolerance.for_zone(zone)
            state = self._state(alignment, mismatched, errored, tol)

            zones.append(ZoneDrift(
                zone=zone,
                decided=decided,
                matched=matched,
                mismatched=mismatched,
                indeterminate=indeterminate,
                errored=errored,
                alignment=alignment,
                baseline=prior,
                delta=delta,
                state=state,
                tolerance_mode=tol.mode,
                flagged=state in (STATE_WATCH, STATE_BREACH, STATE_UNKNOWN),
                mismatch_examples=[
                    r.scenario_id for r in results if r.result == "MISMATCH"
                ][:5],
            ))

        return DriftReport(
            regulation_id=run.regulation_id,
            interpretation_version=run.interpretation_version,
            run_id=run.run_id,
            generated_at=datetime.now(timezone.utc).isoformat(),
            zones=zones,
            overall_alignment=run.alignment,
            risk_level=self._risk(zones),
            tolerance=self._tolerance.to_dict(),
        )

    @staticmethod
    def _state(
        alignment: Optional[float],
        mismatched: int,
        errored: int,
        tol: ZoneTolerance,
    ) -> str:
        # A zone where nothing was decided is unknown, never OK. An
        # all-errors zone is broken and must not read as healthy.
        if alignment is None:
            return STATE_UNKNOWN
        if tol.mode == STRICT:
            return STATE_BREACH if mismatched > 0 else STATE_OK
        if alignment < tol.breach_below:
            return STATE_BREACH
        if alignment < tol.watch_below:
            return STATE_WATCH
        return STATE_OK

    @staticmethod
    def _risk(zones: Sequence[ZoneDrift]) -> str:
        if any(z.state == STATE_BREACH for z in zones):
            return RISK_HIGH
        if any(z.state in (STATE_WATCH, STATE_UNKNOWN) for z in zones):
            return RISK_MEDIUM
        return RISK_LOW


def calibration_suggestion(
    history: Sequence[DriftReport],
    min_periods: int = 3,
) -> Dict[str, Dict[str, Any]]:
    """What the zones have actually been doing, so the business can set
    real thresholds instead of guessing.

    This SUGGESTS. It does not set anything. The output is input to a
    human decision, and the decision is what gets recorded with an
    identity and a rationale attached. An automatically-tightening
    threshold would let the system quietly redefine its own passing
    grade, which is exactly the failure this whole subsystem exists to
    catch.
    """
    if len(history) < min_periods:
        return {
            "__status__": {
                "ready": False,
                "reason": f"need {min_periods} observation periods, have {len(history)}",
            }
        }

    observed: Dict[str, List[float]] = {}
    for report in history:
        for zone in report.zones:
            if zone.alignment is not None:
                observed.setdefault(zone.zone, []).append(zone.alignment)

    suggestions: Dict[str, Dict[str, Any]] = {
        "__status__": {"ready": True, "periods_observed": len(history)}
    }
    for zone, values in sorted(observed.items()):
        low = min(values)
        mean = sum(values) / len(values)
        suggestions[zone] = {
            "observed_min": round(low, 4),
            "observed_mean": round(mean, 4),
            "periods": len(values),
            "suggested_watch_below": round(mean, 3),
            "suggested_breach_below": round(low, 3),
            "note": (
                "Suggested watch line sits at the observed mean and the breach "
                "line at the observed floor. Both are descriptions of past "
                "behavior, not a judgment that past behavior was acceptable."
            ),
        }
    return suggestions
