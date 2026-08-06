"""
OBSERVE Clinical AI System - Consolidated (v2)
================================================

Complete pediatric risk assessment system.
Merges: observe_engine, clinical_policy, all 6 adapters, audit, and scheduler.

INCORPORATES ALL SESSION FIXES:
- Trajectory: per-minute time units, momentum initialization, proper regime distributions
- Heuristic: data-completeness confidence, age-adjusted norms, proper regime distributions
- Drift: None-safe early exit, explicit critical floor
- Behavioral: independent dangerous pattern detection, alert=None handling
- Bayesian: age-group lookup, module-level imports, continuous likelihood
- Adversarial: streak detection, time-aware thresholds, float-safe comparisons
- Scheduler/Queue/Store: retry-requeue, CancelledError handling, TTL eviction, deep-copy export

Single-file deployment.
"""

from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import math
from collections import OrderedDict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any, Callable
from uuid import uuid4
import copy

logger = logging.getLogger("OBSERVE")

# ============================================================================
# ENUMS & DATA CONTRACTS
# ============================================================================

class OperationalRegime(Enum):
    STABLE = "stable"
    CAUTION = "caution"
    WARNING = "warning"
    CRITICAL = "critical"

class JobStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    # NOTE: 'reconciled' was removed as a JobStatus value — reconciliation is
    # tracked independently by ProvisionalStore (is_provisional/reconciled_at),
    # which is the correct ownership boundary. A job is COMPLETED or FAILED;
    # whether its provisional verdict has been reconciled is a separate concern.

@dataclass(frozen=True)
class VitalsSnapshot:
    patient_id: str
    timestamp: datetime
    heart_rate: float
    oxygen_saturation: float
    respiratory_rate: float
    temperature: float
    context: Dict[str, Any] = field(default_factory=dict)

@dataclass
class RiskOutput:
    engine_name: str
    risk_score: float
    confidence: float
    regime_classification: Dict[str, float]
    triggered_rules: List[str]
    timestamp: datetime
    debug_info: Dict[str, Any] = field(default_factory=dict)  # FIX: diagnostic floats here, not triggered_rules
    abstained: bool = False  # True when the engine had no data to assess (excluded from fusion,
    # so a chorus of "no data" abstentions can't dilute a real high-confidence detection)

@dataclass
class FusedVerdict:
    risk_score: float
    regime: OperationalRegime
    confidence: float
    entropy: float
    active_engines: List[str]
    triggered_rules: List[str]
    timestamp: datetime
    audit_hash: str = ""
    escalation_required: bool = False  # True only for THIS evaluation's NEW escalation
    # (regime may remain WARNING/CRITICAL on later calls due to escalation_locked
    # cooldown even when escalation_required=False on those calls)

@dataclass
class ScheduledJob:
    patient_id: str
    vitals_snapshot: Dict[str, Any]
    job_id: str = field(default_factory=lambda: str(uuid4())[:8])
    status: JobStatus = JobStatus.QUEUED
    retry_count: int = 0
    max_retries: int = 3
    final_result: Optional[Dict[str, Any]] = None

# ============================================================================
# CLINICAL POLICY: AGE-ADJUSTED PEDIATRIC NORMS
# ============================================================================

PEDIATRIC_NORMS = {
    "neonatal": {"hr_high": 160, "hr_low": 80, "rr_high": 50, "o2_low": 90, "temp_high": 38.5},
    "infant":   {"hr_high": 150, "hr_low": 90, "rr_high": 45, "o2_low": 90, "temp_high": 39.0},
    "toddler":  {"hr_high": 140, "hr_low": 95, "rr_high": 40, "o2_low": 91, "temp_high": 39.0},
    "child":    {"hr_high": 130, "hr_low": 100, "rr_high": 35, "o2_low": 92, "temp_high": 39.5},
    "generic":  {"hr_high": 140, "hr_low": 95, "rr_high": 40, "o2_low": 91, "temp_high": 39.0},
}

# FIX: explicit named threshold (was a fragile/magic 2.0 in z-score comparisons).
# DriftThresholds.DRIFT_SIGMA in the original spec — baseline shift exceeding this
# many standard deviations from recent rolling history triggers a drift signal.
DRIFT_SIGMA_THRESHOLD = 2.0


def get_age_group(age_months: Optional[int]) -> str:
    """Map age in months to clinical age group. Returns 'generic' if age unknown."""
    if age_months is None:
        return "generic"
    if age_months < 3:
        return "neonatal"
    if age_months < 12:
        return "infant"
    if age_months < 36:
        return "toddler"
    return "child"


def regime_distribution(risk_score: float, critical_floor: float = 0.01) -> Dict[str, float]:
    """
    Risk-stratified regime probability distribution.
    FIX: Replaces broken linear-scaling distributions (where 'critical' was
    capped artificially low even at risk_score=1.0). Now critical probability
    properly dominates at high risk. critical_floor ensures critical never
    truly hits zero (residual clinical uncertainty).
    """
    if risk_score >= 0.75:
        d = {"stable": 0.05, "caution": 0.10, "warning": 0.25, "critical": 0.60}
    elif risk_score >= 0.50:
        d = {"stable": 0.10, "caution": 0.20, "warning": 0.55, "critical": 0.15}
    elif risk_score >= 0.25:
        d = {"stable": 0.35, "caution": 0.50, "warning": 0.12, "critical": 0.03}
    else:
        d = {"stable": 0.88, "caution": 0.08, "warning": 0.03, "critical": 0.01}

    if d["critical"] < critical_floor:
        deficit = critical_floor - d["critical"]
        d["critical"] = critical_floor
        d["stable"] = max(0.0, d["stable"] - deficit)

    return d

# ============================================================================
# RISK ASSESSMENT ADAPTERS (all 6 engines, with session fixes applied)
# ============================================================================

class RiskAdapters:
    """All 6 risk assessment engines, consolidated with fixes from code review."""

    @staticmethod
    def heuristic(vitals: VitalsSnapshot) -> RiskOutput:
        """FIX: confidence = data-completeness, not history_quality formula. Age-adjusted norms."""
        triggered = []
        score = 0.0

        age_months = vitals.context.get("age_months")
        has_history = "previous_o2" in vitals.context and "previous_hr" in vitals.context

        if age_months is None:
            logger.warning(f"HEURISTIC: age_months missing for {vitals.patient_id}; using generic norms")

        age_group = get_age_group(age_months)
        thresh = PEDIATRIC_NORMS[age_group]

        if vitals.oxygen_saturation < 88.0:
            triggered.append(f"CRITICAL_O2: {vitals.oxygen_saturation}% (<88%)")
            score += 0.5
        elif vitals.oxygen_saturation < thresh["o2_low"]:
            triggered.append(f"WARNING_O2: {vitals.oxygen_saturation}% (<{thresh['o2_low']}%)")
            score += 0.2

        if vitals.heart_rate > thresh["hr_high"]:
            triggered.append(f"TACHYCARDIA: {vitals.heart_rate} > {thresh['hr_high']}")
            score += 0.2
        elif vitals.heart_rate < thresh["hr_low"]:
            triggered.append(f"BRADYCARDIA: {vitals.heart_rate} < {thresh['hr_low']}")
            score += 0.3

        if vitals.respiratory_rate > thresh["rr_high"]:
            triggered.append(f"TACHYPNEA: {vitals.respiratory_rate} > {thresh['rr_high']}")
            score += 0.15

        if vitals.temperature > thresh["temp_high"]:
            triggered.append(f"FEVER: {vitals.temperature}°C > {thresh['temp_high']}°C")
            score += 0.1
        elif vitals.temperature < 35.0:
            triggered.append(f"HYPOTHERMIA: {vitals.temperature}°C < 35.0°C")
            score += 0.4

        score = min(score, 1.0)

        if age_months is not None and has_history:
            confidence = 0.95
        elif age_months is not None:
            confidence = 0.85
        else:
            confidence = 0.70

        return RiskOutput("heuristic", score, confidence, regime_distribution(score), triggered, datetime.now(timezone.utc))

    @staticmethod
    def bayesian(vitals: VitalsSnapshot) -> RiskOutput:
        """FIX: age-group lookup (no hardcoded toddler norms), continuous sigmoid likelihood."""
        triggered = []
        debug_info = {}

        age_months = vitals.context.get("age_months")
        age_group = get_age_group(age_months)
        thresh = PEDIATRIC_NORMS[age_group]

        o2_mean, o2_std = (thresh["o2_low"] + 100) / 2.0, 3.0
        hr_mean, hr_std = (thresh["hr_high"] + thresh["hr_low"]) / 2.0, (thresh["hr_high"] - thresh["hr_low"]) / 4.0

        z_o2 = (vitals.oxygen_saturation - o2_mean) / o2_std
        z_hr = (vitals.heart_rate - hr_mean) / hr_std

        debug_info["z_o2"] = round(z_o2, 3)
        debug_info["z_hr"] = round(z_hr, 3)

        # FIX: continuous sigmoid-like likelihood instead of step-function cliff
        likelihood_critical = 1.0 - math.exp(-0.15 * (max(0, -z_o2)) ** 2)
        # FIX: deviation-increasing warning likelihood (was max(z,3) which capped large z)
        likelihood_warning = 1.0 - math.exp(-0.10 * (z_hr ** 2))

        score = 0.0
        if z_o2 < -2.0:
            triggered.append(f"O2_DEVIATION: {vitals.oxygen_saturation}% is {abs(z_o2):.1f}SD below expected")
            score += 0.4 * likelihood_critical

        if abs(z_hr) > 2.0:
            triggered.append(f"HR_DEVIATION: {vitals.heart_rate}bpm is {abs(z_hr):.1f}SD from expected")
            score += 0.3 * likelihood_warning

        score = min(score, 1.0)

        # NOTE: confidence floor reflects prior strength, not necessarily evidence strength.
        # A confident "stable" verdict with no abnormal z-scores still carries 0.85 confidence
        # because absence-of-deviation is itself informative under this model.
        confidence = 0.85

        out = RiskOutput("bayesian", score, confidence, regime_distribution(score), triggered, datetime.now(timezone.utc))
        out.debug_info = debug_info  # FIX: diagnostics go here, not triggered_rules
        return out

    @staticmethod
    def trajectory(vitals: VitalsSnapshot) -> RiskOutput:
        """FIX: per-minute units, initialize all momentum vars, proper regime distribution,
        early-return on insufficient history with LOW (not medium) confidence."""
        previous_o2 = vitals.context.get("previous_o2")
        previous_hr = vitals.context.get("previous_hr")
        previous_rr = vitals.context.get("previous_rr")
        previous_temp = vitals.context.get("previous_temp")
        time_delta = vitals.context.get("time_delta_seconds", 60)

        if time_delta <= 0 or (previous_o2 is None and previous_hr is None and previous_rr is None):
            return RiskOutput(
                "trajectory", 0.0, 0.2, {"stable": 1.0, "caution": 0.0, "warning": 0.0, "critical": 0.0},
                ["Insufficient history for momentum analysis"], datetime.now(timezone.utc),
                abstained=True,
            )

        # FIX: initialize ALL momentum vars before conditional use (prevents NameError)
        o2_momentum = hr_momentum = rr_momentum = temp_momentum = None
        triggered = []
        score = 0.0
        dt_min = time_delta / 60.0  # FIX: per-minute, not per-second

        if previous_o2 is not None:
            o2_momentum = (vitals.oxygen_saturation - previous_o2) / dt_min
            if o2_momentum < -3.0:  # FIX: 3%/min (was unrealistic 0.5%/sec)
                triggered.append(f"O2_MOMENTUM: {o2_momentum:.2f}%/min drop")
                score += 0.4

        if previous_hr is not None:
            hr_momentum = (vitals.heart_rate - previous_hr) / dt_min
            if hr_momentum > 20.0:  # FIX: 20bpm/min (was unrealistic 2bpm/sec)
                triggered.append(f"HR_MOMENTUM: +{hr_momentum:.2f}bpm/min")
                score += 0.3
            if hr_momentum < -30.0:
                triggered.append(f"HR_DECELERATION: {hr_momentum:.2f}bpm/min")
                score += 0.4

        if previous_rr is not None:
            rr_momentum = (vitals.respiratory_rate - previous_rr) / dt_min
            if rr_momentum > 5.0:  # FIX: 5/min (was unrealistic 1/sec)
                triggered.append(f"RR_MOMENTUM: +{rr_momentum:.2f}/min")
                score += 0.25

        if previous_temp is not None:
            temp_momentum = (vitals.temperature - previous_temp) / dt_min
            if temp_momentum < -1.0:
                triggered.append(f"TEMP_DROP: {temp_momentum:.3f}°C/min")
                score += 0.3

        # FIX: bad_trends excludes temp (documented — temp moves slowly); no NameError risk
        bad_trends = sum([
            o2_momentum is not None and o2_momentum < -3.0,
            hr_momentum is not None and hr_momentum > 20.0,
            rr_momentum is not None and rr_momentum > 5.0,
        ])
        if bad_trends >= 2:
            triggered.append(f"MULTI_TREND_DETERIORATION: {bad_trends} vitals worsening simultaneously")
            score += 0.2

        score = min(score, 1.0)

        history_quality = sum(x is not None for x in [previous_o2, previous_hr, previous_rr, previous_temp]) / 4.0
        confidence = 0.6 + (0.3 * history_quality)

        return RiskOutput("trajectory", score, confidence, regime_distribution(score), triggered, datetime.now(timezone.utc))

    @staticmethod
    def drift(vitals: VitalsSnapshot) -> RiskOutput:
        """FIX: None-safe early exit (not falsy-0.0 check), documented critical floor."""
        baseline_o2 = vitals.context.get("baseline_o2")
        baseline_hr = vitals.context.get("baseline_hr")
        history_o2 = vitals.context.get("history_o2", [])
        history_hr = vitals.context.get("history_hr", [])

        # FIX: proper None guards — a baseline of 0.0 previously short-circuited incorrectly
        has_baseline = any(v is not None for v in [baseline_o2, baseline_hr])
        has_history = len(history_o2) >= 5 or len(history_hr) >= 5

        if not has_baseline or not has_history:
            return RiskOutput(
                "drift", 0.0, 0.3, {"stable": 1.0, "caution": 0.0, "warning": 0.0, "critical": 0.0},
                ["Insufficient history for drift detection"], datetime.now(timezone.utc),
                abstained=True,
            )

        triggered = []
        score = 0.0

        if baseline_o2 is not None and len(history_o2) >= 5:
            mean_o2 = sum(history_o2) / len(history_o2)
            var_o2 = sum((x - mean_o2) ** 2 for x in history_o2) / len(history_o2)
            std_o2 = math.sqrt(var_o2)
            z = abs(baseline_o2 - mean_o2) / std_o2 if std_o2 > 0 else 0
            if z > DRIFT_SIGMA_THRESHOLD:
                triggered.append(f"O2_DRIFT: baseline {baseline_o2}% is {z:.2f}SD from mean {mean_o2:.1f}%")
                score += 0.3

        if baseline_hr is not None and len(history_hr) >= 5:
            mean_hr = sum(history_hr) / len(history_hr)
            var_hr = sum((x - mean_hr) ** 2 for x in history_hr) / len(history_hr)
            std_hr = math.sqrt(var_hr)
            z = abs(baseline_hr - mean_hr) / std_hr if std_hr > 0 else 0
            if z > DRIFT_SIGMA_THRESHOLD:
                triggered.append(f"HR_DRIFT: baseline {baseline_hr} is {z:.2f}SD from mean {mean_hr:.0f}")
                score += 0.25

        score = min(score, 1.0)
        max_hist = max(len(history_o2), len(history_hr))
        confidence = 0.95 if max_hist >= 100 else 0.85 if max_hist >= 50 else 0.70 if max_hist >= 10 else 0.50

        # FIX: explicit critical_floor=0.02 documented — drift never reduces critical risk to exactly 0
        return RiskOutput("drift", score, confidence, regime_distribution(score, critical_floor=0.02), triggered, datetime.now(timezone.utc))

    @staticmethod
    def behavioral_vaccine(vitals: VitalsSnapshot) -> RiskOutput:
        """FIX: independent dangerous-pattern detection (not elif chain),
        alert=None excluded (was default=True), base risk computed internally."""
        triggered = []

        # FIX: base risk computed independently — not dependent on upstream context
        score = 0.0
        if vitals.oxygen_saturation < 90.0:
            score += 0.4
        elif vitals.oxygen_saturation < 92.0:
            score += 0.2
        if vitals.heart_rate > 150 or vitals.heart_rate < 80:
            score += 0.3
        initial_risk = min(score, 1.0)
        triggered.append(f"BASE_RISK: {initial_risk:.2f}")

        # FIX: dangerous patterns checked INDEPENDENTLY (all can fire, not elif)
        dangerous_fired = []

        if (vitals.oxygen_saturation < 92.0 and vitals.heart_rate > 140
                and vitals.respiratory_rate > 35 and vitals.temperature > 38.5):
            dangerous_fired.append("SEPTIC_SHOCK")
            triggered.append("DANGEROUS_PATTERN: septic_shock (O2low+HRhigh+RRhigh+fever)")
            score += 0.40

        if vitals.oxygen_saturation < 90.0 and vitals.respiratory_rate > 45:
            dangerous_fired.append("RESPIRATORY_DISTRESS")
            triggered.append("DANGEROUS_PATTERN: respiratory_distress (O2<90 + RR>45)")
            score += 0.35

        if vitals.heart_rate > 150 and vitals.oxygen_saturation < 88.0:
            dangerous_fired.append("HYPOVOLEMIC_SHOCK")
            triggered.append("DANGEROUS_PATTERN: hypovolemic_shock (HR>150 + O2<88)")
            score += 0.35

        # FIX: alert default is None (was True) — benign reductions only apply when
        # no dangerous pattern fired AND alert context is explicitly known
        alert_status = vitals.context.get("alert")  # None if unknown

        if not dangerous_fired:
            if alert_status is not None:
                if vitals.temperature > 38.5 and vitals.heart_rate > 130 and vitals.respiratory_rate > 28:
                    triggered.append("BENIGN_PATTERN: fever_response")
                    score = max(score - 0.15, initial_risk * 0.5)
                if alert_status == "crying" and vitals.heart_rate > 140:
                    triggered.append("BENIGN_PATTERN: crying_baby")
                    score = max(score - 0.10, initial_risk * 0.5)
        else:
            triggered.append(f"DANGEROUS_PATTERNS_ACTIVE: suppressed benign reductions ({len(dangerous_fired)} signs)")

        score = max(0.0, min(score, 1.0))

        # FIX: confidence depends on whether alert context is known
        confidence = 0.90 if alert_status is not None else 0.75

        return RiskOutput("behavioral", score, confidence, regime_distribution(score), triggered, datetime.now(timezone.utc))

    @staticmethod
    def adversarial(vitals: VitalsSnapshot) -> RiskOutput:
        """FIX: streak detection (not single-pair), proper variance over real window,
        time-aware rate thresholds, float-safe comparison, no stable-floor special case.
        NOTE: low-O2+low-HR is CLINICAL (handled elsewhere), not flagged as adversarial."""
        triggered = []
        score = 0.0

        # FIX: streak detection — require 5+ identical consecutive readings, not a single pair
        recent_readings = vitals.context.get("recent_o2_readings", [])
        if len(recent_readings) >= 5:
            last_five = recent_readings[-5:]
            if len(set(last_five)) == 1:
                triggered.append(f"CONSTANT_VALUE_STREAK: {len(last_five)} identical O2 readings ({last_five[0]}%)")
                score += 0.3

        # FIX: proper variance over last-10 window (was a broken single-pair formula)
        if len(recent_readings) >= 10:
            window = recent_readings[-10:]
            mean = sum(window) / len(window)
            variance = sum((x - mean) ** 2 for x in window) / len(window)
            if variance < 0.01 and len(set(window)) > 1:
                triggered.append(f"LOW_VARIANCE_SENSOR: variance={variance:.4f} over 10 readings")
                score += 0.15

        if vitals.oxygen_saturation > 100.0 or vitals.oxygen_saturation < 0.0:
            triggered.append(f"OUT_OF_RANGE_O2: {vitals.oxygen_saturation}%")
            score += 0.3

        # FIX: time-aware percentage-change threshold (was ignoring time delta entirely)
        prev_o2 = vitals.context.get("previous_o2")
        time_delta = vitals.context.get("time_delta_seconds", 60)
        if prev_o2 is not None and time_delta > 0:
            pct_change_per_min = abs(vitals.oxygen_saturation - prev_o2) / (time_delta / 60.0)
            if pct_change_per_min > 15.0:  # >15%/min exceeds physiological plausibility
                triggered.append(f"IMPLAUSIBLE_RATE: {pct_change_per_min:.1f}%/min exceeds physiological limits")
                score += 0.25

        # NOTE: FIX — low O2 + low HR is a CLINICAL DANGER SIGN (e.g. pre-arrest
        # bradycardia + hypoxia), NOT an adversarial/sensor-fault pattern. Deliberately
        # NOT flagged here; handled by heuristic/behavioral adapters.

        score = min(score, 1.0)

        # FIX: float-safe comparison (was `risk_score == 0.0`)
        if score < 0.01:
            regimes = {"stable": 1.0, "caution": 0.0, "warning": 0.0, "critical": 0.0}
        else:
            # FIX: no special-cased "stable floor" — same distribution as other adapters
            regimes = regime_distribution(score)

        return RiskOutput("adversarial", score, 0.70, regimes, triggered, datetime.now(timezone.utc))


# ============================================================================
# PHYSIOLOGICAL TELEMETRY DETECTION (gating for advanced adapter)
# ============================================================================

# These are the "rich" physiological signals the physiological_reserve adapter
# consumes. None come from a basic bedside monitor — they require NIRS perfusion,
# continuous lactate, HRV-derived autonomic tone, etc. The adapter only contributes
# risk from axes whose telemetry is ACTUALLY present (see _PHYSIO_AXIS_KEYS).
_PHYSIO_AXIS_KEYS = {
    "topology": ["organ_coupling_index", "organ_failures"],
    "capacity": ["perfusion_index", "metabolic_load_index", "oxygen_demand_index"],
    "resource": ["reserve_index", "substrate_level", "tissue_viability", "energy_ratio"],
    "integrity": ["integrity_events", "critical_integrity_events", "infection_burden_index"],
    "phase": ["phase", "compensation_index", "decomp_index"],
}


def _has_physiological_telemetry(vitals: "VitalsSnapshot") -> bool:
    """True if ANY rich physiological axis has at least one real input present."""
    ctx = vitals.context
    for keys in _PHYSIO_AXIS_KEYS.values():
        if any(k in ctx for k in keys):
            return True
    return False


class RiskAdaptersPhysiological:
    """
    Advanced physiological-reserve adapter (the 7th engine).

    Adapted from a six-axis systems-physiology decomposition. Two critical
    departures from the original prototype that make it safe to fuse:

    1. AXIS GATING: each of the six axes (topology, capacity, resource,
       integrity, phase, instability) only contributes risk if its telemetry
       is ACTUALLY present in context. Absent axes contribute nothing and do
       NOT dilute the score toward a false "all systems perfect" reading.
       (The prototype defaulted every missing input to perfect health, which
       made five of six axes permanently inert and pinned future_harm ~0.)

    2. CALIBRATED REGIME MAPPING: risk_score is piped through the platform's
       shared regime_distribution() — NOT a bespoke softmax. The prototype's
       softmax over near-constant scores produced a ~uniform distribution that
       rated a critically hypoxic child at 38% "stable" / 20% "critical",
       barely distinguishable from a healthy baby. regime_distribution() makes
       a high risk_score actually map to a critical-dominant distribution.

    instability (HR-variability over the recent window) is the one axis built
    on data a standard monitor already provides, so it always contributes when
    hr_history is present, regardless of the rich-telemetry axes.
    """

    @staticmethod
    def _topology_risk(ctx: Dict[str, Any]) -> Optional[float]:
        if not any(k in ctx for k in _PHYSIO_AXIS_KEYS["topology"]):
            return None
        coupling = ctx.get("organ_coupling_index", 1.0)
        failures = ctx.get("organ_failures", 0)
        total = ctx.get("total_organs", 6)
        coupling_risk = max(0.0, min(1.0, 1.0 - coupling))
        loss_risk = max(0.0, min(1.0, failures / max(total, 1)))
        return max(0.0, min(1.0, 0.6 * coupling_risk + 0.4 * loss_risk))

    @staticmethod
    def _capacity_risk(ctx: Dict[str, Any]) -> Optional[float]:
        if not any(k in ctx for k in _PHYSIO_AXIS_KEYS["capacity"]):
            return None
        perfusion = ctx.get("perfusion_index", 1.0)
        load = ctx.get("metabolic_load_index", 1.0)
        demand = ctx.get("oxygen_demand_index", 1.0)
        reserve_risk = 1.0 if load <= 0 else max(0.0, min(1.0, 1.0 - perfusion / load))
        imbalance_risk = 1.0 if demand <= 0 else max(0.0, min(1.0, abs(perfusion - demand) / demand))
        return max(0.0, min(1.0, 0.6 * reserve_risk + 0.4 * imbalance_risk))

    @staticmethod
    def _resource_risk(ctx: Dict[str, Any]) -> Optional[float]:
        if not any(k in ctx for k in _PHYSIO_AXIS_KEYS["resource"]):
            return None
        reserve = ctx.get("reserve_index", 1.0)
        substrate = ctx.get("substrate_level", 1.0)
        viability = ctx.get("tissue_viability", 1.0)
        energy = ctx.get("energy_ratio", 1.0)
        reserve_risk = max(0.0, min(1.0, 1.0 - reserve))
        bio = max(0.0, min(1.0, 0.4 * (1 - substrate) + 0.3 * (1 - viability) + 0.3 * (1 - energy)))
        return max(0.0, min(1.0, 0.5 * reserve_risk + 0.5 * bio))

    @staticmethod
    def _integrity_risk(ctx: Dict[str, Any]) -> Optional[float]:
        if not any(k in ctx for k in _PHYSIO_AXIS_KEYS["integrity"]):
            return None
        events = ctx.get("integrity_events", 0.0)
        critical = ctx.get("critical_integrity_events", 0.0)
        infection = ctx.get("infection_burden_index", 0.0)
        event_risk = max(0.0, min(1.0, 0.10 * events + 0.30 * critical))
        return max(0.0, min(1.0, 0.6 * event_risk + 0.4 * max(0.0, min(1.0, infection))))

    @staticmethod
    def _phase_risk(ctx: Dict[str, Any]) -> Optional[float]:
        if not any(k in ctx for k in _PHYSIO_AXIS_KEYS["phase"]):
            return None
        phase = ctx.get("phase", "stable") or "stable"
        base = {"stable": 0.1, "compensation": 0.3, "decompensation": 0.6, "collapse": 0.9}.get(phase, 0.3)
        comp = max(0.0, min(1.0, ctx.get("compensation_index", 0.0)))
        decomp = max(0.0, min(1.0, ctx.get("decomp_index", 0.0)))
        return max(0.0, min(1.0, base + 0.3 * decomp - 0.2 * comp))

    @staticmethod
    def _instability_risk(ctx: Dict[str, Any]) -> Optional[float]:
        history = ctx.get("hr_history", [])
        if len(history) < 3:
            return None
        diffs = [abs(history[i] - history[i - 1]) for i in range(1, len(history))]
        mean_diff = sum(diffs) / len(diffs)
        return max(0.0, min(1.0, mean_diff / 30.0))

    @staticmethod
    def physiological_reserve(vitals: "VitalsSnapshot") -> "RiskOutput":
        ctx = vitals.context
        triggered = []
        debug_info = {}

        # Compute each axis; None => telemetry absent => axis excluded from the mean
        axes = {
            "topology": RiskAdaptersPhysiological._topology_risk(ctx),
            "capacity": RiskAdaptersPhysiological._capacity_risk(ctx),
            "resource": RiskAdaptersPhysiological._resource_risk(ctx),
            "integrity": RiskAdaptersPhysiological._integrity_risk(ctx),
            "phase": RiskAdaptersPhysiological._phase_risk(ctx),
            "instability": RiskAdaptersPhysiological._instability_risk(ctx),
        }

        present = {k: v for k, v in axes.items() if v is not None}
        debug_info["axes_present"] = list(present.keys())
        debug_info["axes_values"] = {k: round(v, 3) for k, v in present.items()}

        if not present:
            # No rich telemetry at all — adapter abstains with LOW confidence so it
            # does not skew fusion. (Critically: it does NOT emit a confident "stable".)
            return RiskOutput(
                "physiological_reserve", 0.0, 0.2,
                {"stable": 1.0, "caution": 0.0, "warning": 0.0, "critical": 0.0},
                ["No rich physiological telemetry available; adapter abstains"],
                datetime.now(timezone.utc), debug_info,
                abstained=True,
            )

        # Risk = mean of PRESENT axes only (absent axes neither help nor hurt)
        risk_score = sum(present.values()) / len(present)
        risk_score = max(0.0, min(1.0, risk_score))

        for name, val in present.items():
            if val >= 0.5:
                triggered.append(f"PHYSIO_{name.upper()}: risk={val:.2f} (axis elevated)")

        # Confidence scales with how many axes we could actually measure:
        # 1 axis -> 0.45, 6 axes -> 0.95. More instrumentation => more trustworthy.
        confidence = 0.45 + 0.50 * (len(present) / 6.0)

        return RiskOutput(
            "physiological_reserve", risk_score, confidence,
            regime_distribution(risk_score),  # CALIBRATED — shared platform mapping
            triggered or [f"Physiological reserve assessed across {len(present)} axes"],
            datetime.now(timezone.utc), debug_info,
        )


# ============================================================================
# BAYESIAN FUSION (entropy-based engine selection feedback)
# ============================================================================

class BayesianFusion:
    """Confidence-weighted fusion of multiple engine outputs."""

    @staticmethod
    def fuse(outputs: List[RiskOutput]) -> tuple[float, float, Dict[str, float], str]:
        if not outputs:
            return 0.0, 0.0, {"stable": 1.0, "caution": 0.0, "warning": 0.0, "critical": 0.0}, "No outputs"

        # CRITICAL FIX: exclude abstaining engines from fusion entirely. An engine that
        # returns abstained=True is saying "I have no data to assess this patient" — which
        # is fundamentally different from "this patient looks stable." Previously, three
        # low-confidence abstentions (trajectory/drift/physiological with no telemetry) could
        # outvote a single high-confidence septic-shock detection via confidence-weighted
        # averaging, dragging a genuinely critical patient back to "stable." We now fuse only
        # over engines that actually assessed the patient. If ALL abstained, we fall back to
        # the full set (degenerate case) so we never divide by zero or return nothing.
        active = [o for o in outputs if not o.abstained]
        fusion_set = active if active else outputs
        abstained_names = [o.engine_name for o in outputs if o.abstained]

        total_conf = sum(o.confidence for o in fusion_set)
        fused_risk = sum(o.risk_score * o.confidence for o in fusion_set) / total_conf if total_conf > 0 else 0.0

        regime_probs = {r: 0.0 for r in ["stable", "caution", "warning", "critical"]}
        for out in fusion_set:
            for regime, prob in out.regime_classification.items():
                regime_probs[regime] += prob * out.confidence
        regime_probs = {k: v / total_conf for k, v in regime_probs.items()} if total_conf > 0 else regime_probs

        # True Shannon entropy
        entropy = -sum(p * math.log2(p) for p in regime_probs.values() if p > 0)

        # CLINICAL SYNDROME FLOOR: a confirmed dangerous pattern (septic shock,
        # respiratory distress, hypovolemic shock — emitted by the behavioral adapter
        # with a "DANGEROUS_PATTERN:" tag) is a named clinical syndrome, not a vote to
        # be averaged. If any active engine reports one, the fused risk takes the MAX of
        # (confidence-weighted average, that engine's own risk_score). This prevents a
        # high-risk syndrome detection from being diluted below its detected severity by
        # other engines that assess different axes. Mirrors the hard-rule bypass rationale.
        syndrome_floor = 0.0
        for o in fusion_set:
            if any("DANGEROUS_PATTERN:" in r for r in o.triggered_rules):
                syndrome_floor = max(syndrome_floor, o.risk_score)
        if syndrome_floor > fused_risk:
            rationale_floor = f"; syndrome floor raised risk {fused_risk:.2f}->{syndrome_floor:.2f}"
            fused_risk = syndrome_floor
            # Re-derive regime distribution from the floored risk so regime tracks risk
            regime_probs = regime_distribution(fused_risk)
            entropy = -sum(p * math.log2(p) for p in regime_probs.values() if p > 0)
        else:
            rationale_floor = ""

        names = ", ".join(o.engine_name for o in fusion_set)
        rationale = f"Fused {len(fusion_set)} active engines ({names})"
        if abstained_names:
            rationale += f"; {len(abstained_names)} abstained ({', '.join(abstained_names)})"
        rationale += rationale_floor
        return fused_risk, entropy, regime_probs, rationale


# ============================================================================
# ESCALATION POLICY (hysteresis + dwell)
# ============================================================================

class EscalationPolicy:
    """Hysteresis + dwell logic to prevent regime thrashing."""

    def __init__(self, dwell_threshold: int = 2, lock_seconds: int = 300):
        self.dwell_threshold = dwell_threshold
        self.lock_seconds = lock_seconds
        self.current_regime = OperationalRegime.STABLE
        self.pending_regime: Optional[OperationalRegime] = None
        self.dwell_count = 0
        self.escalation_locked = False
        self.last_escalation_time: Optional[datetime] = None

    def evaluate(self, new_regime: OperationalRegime, timestamp: datetime) -> tuple[OperationalRegime, bool]:
        if self.escalation_locked and self.last_escalation_time:
            elapsed = (timestamp - self.last_escalation_time).total_seconds()
            if elapsed < self.lock_seconds:
                return self.current_regime, False
            self.escalation_locked = False

        if new_regime == self.current_regime:
            self.pending_regime = None
            self.dwell_count = 0
            return self.current_regime, False

        # FIX: proper dwell accumulation — increments when the SAME new regime
        # is observed across consecutive calls (was reset incorrectly before)
        if new_regime == self.pending_regime:
            self.dwell_count += 1
        else:
            self.pending_regime = new_regime
            self.dwell_count = 1

        if self.dwell_count >= self.dwell_threshold:
            escalation = (new_regime.value in ("warning", "critical")
                           and self.current_regime.value in ("stable", "caution"))
            self.current_regime = new_regime
            self.pending_regime = None
            self.dwell_count = 0
            if escalation:
                self.escalation_locked = True
                self.last_escalation_time = timestamp
            return new_regime, escalation

        return self.current_regime, False


# ============================================================================
# AUDIT LEDGER (SHA256 cryptographic chain)
# ============================================================================

class ImmutableAuditLedger:
    def __init__(self):
        self.entries: List[Dict[str, Any]] = []
        self.chain_head = "0" * 64

    def append(self, patient_id: str, action: str, data: Dict[str, Any]) -> str:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "patient_id": patient_id,
            "action": action,
            "data": copy.deepcopy(data),
            "previous_hash": self.chain_head,
        }
        block_hash = hashlib.sha256(json.dumps(entry, sort_keys=True, default=str).encode()).hexdigest()
        entry["immutable_hash"] = block_hash
        self.entries.append(entry)
        self.chain_head = block_hash
        return block_hash

    def verify_integrity(self) -> bool:
        expected_prev = "0" * 64
        for entry in self.entries:
            if entry["previous_hash"] != expected_prev:
                return False
            target = copy.deepcopy(entry)
            stored_hash = target.pop("immutable_hash")
            recalced = hashlib.sha256(json.dumps(target, sort_keys=True, default=str).encode()).hexdigest()
            if stored_hash != recalced:
                return False
            expected_prev = stored_hash
        return True

    def query_patient(self, patient_id: str) -> List[Dict[str, Any]]:
        return [e for e in self.entries if e["patient_id"] == patient_id]

    def export_json(self) -> str:
        return json.dumps(self.entries, indent=2, default=str)


# ============================================================================
# ASYNC SCHEDULER — FIX: retry-requeue, CancelledError re-raised,
#   finally-block guarantees, queue_size() instead of direct .queue access
# ============================================================================

class JobQueue:
    """FIX: queue initialized in constructor (no init-on-first-use race condition).
    FIX: bounded job storage — completed_jobs evicted FIFO past max_completed_history."""

    def __init__(self, max_completed_history: int = 1000):
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.jobs: Dict[str, ScheduledJob] = {}
        self.completed_jobs: "OrderedDict[str, ScheduledJob]" = OrderedDict()
        self.max_completed_history = max_completed_history
        self._lock = asyncio.Lock()

    def _evict_old_completed(self) -> None:
        """FIX: prevent unbounded growth of jobs/completed_jobs over long runs."""
        while len(self.completed_jobs) > self.max_completed_history:
            old_job_id, _ = self.completed_jobs.popitem(last=False)
            self.jobs.pop(old_job_id, None)

    async def submit(self, patient_id: str, vitals: Dict[str, Any], priority: int = 2) -> str:
        async with self._lock:
            job = ScheduledJob(patient_id=patient_id, vitals_snapshot=vitals)
            self.jobs[job.job_id] = job
            await self.queue.put((priority, job.job_id))
            return job.job_id

    async def mark_failed_and_requeue(self, job_id: str, priority: int = 1) -> None:
        """FIX: mark_failed now actually re-enqueues (was a no-op before)."""
        async with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                logger.warning(f"mark_failed: unknown job_id {job_id}")
                return
            if job.retry_count < job.max_retries:
                job.retry_count += 1
                job.status = JobStatus.QUEUED
                await self.queue.put((priority, job_id))
            else:
                job.status = JobStatus.FAILED
                self.completed_jobs[job_id] = job
                self._evict_old_completed()

    async def complete(self, job_id: str, result: Dict[str, Any]) -> None:
        async with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                logger.warning(f"complete: unknown job_id {job_id}")
                return
            job.status = JobStatus.COMPLETED
            job.final_result = result
            self.completed_jobs[job_id] = job
            self._evict_old_completed()

    def queue_size(self) -> int:
        """FIX: safe accessor — external callers should never touch .queue directly."""
        return self.queue.qsize()

    def export_summary(self) -> Dict[str, Any]:
        """FIX: only primitive types returned — no raw datetime serialization issues."""
        return {
            "total_jobs": len(self.jobs),
            "completed": sum(1 for j in self.completed_jobs.values() if j.status == JobStatus.COMPLETED),
            "failed": sum(1 for j in self.completed_jobs.values() if j.status == JobStatus.FAILED),
            "queue_depth": self.queue_size(),
        }


class AsyncJobScheduler:
    """
    Prioritized async job execution.
    FIXES: CancelledError re-raised after task_done(); errors from gather()
    are inspected and logged (not swallowed).
    """

    def __init__(self, num_workers: int = 2):
        self.job_queue = JobQueue()
        self.num_workers = num_workers
        self.tasks: List[asyncio.Task] = []
        self.is_running = False

    async def start(self, execution_router: Callable[[Dict[str, Any]], Any]) -> None:
        self.is_running = True
        for i in range(self.num_workers):
            self.tasks.append(asyncio.create_task(self._worker_loop(i, execution_router)))

    async def submit_job(self, patient_id: str, vitals: Dict[str, Any], priority: int = 2) -> str:
        return await self.job_queue.submit(patient_id, vitals, priority)

    async def _worker_loop(self, worker_id: int, execution_router: Callable[[Dict[str, Any]], Any]) -> None:
        while self.is_running:
            try:
                priority, job_id = await self.job_queue.queue.get()
            except asyncio.CancelledError:
                raise  # FIX: re-raise so gather() sees cancellation, not silent stop

            try:
                async with self.job_queue._lock:
                    job = self.job_queue.jobs.get(job_id)
                    if not job:
                        continue
                    job.status = JobStatus.RUNNING

                try:
                    result = await execution_router(job.vitals_snapshot)
                    result_dict = asdict(result) if hasattr(result, "regime") else result
                    await self.job_queue.complete(job_id, result_dict)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Worker-{worker_id} error on job {job_id}: {e}")
                    await self.job_queue.mark_failed_and_requeue(job_id)
            finally:
                # FIX: guaranteed task_done() regardless of success/failure/cancel
                self.job_queue.queue.task_done()

    async def stop(self) -> None:
        self.is_running = False
        for t in self.tasks:
            t.cancel()
        results = await asyncio.gather(*self.tasks, return_exceptions=True)
        # FIX: inspect gather results — log any non-cancellation exceptions
        for r in results:
            if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                logger.error(f"Worker task raised on shutdown: {r}")


# ============================================================================
# PROVISIONAL STORE — FIX: TTL eviction, deep-copy export, job_id validation
# ============================================================================

class ProvisionalStore:
    """Time-bounded volatile cache for fast provisional verdicts."""

    def __init__(self, ttl_seconds: int = 300, max_capacity: int = 5000):
        self.provisionals: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self.ttl_seconds = ttl_seconds
        self.max_capacity = max_capacity

    def store(self, patient_id: str, job_id: str, risk_score: float, regime: str) -> None:
        self._evict_expired()
        if len(self.provisionals) >= self.max_capacity:
            self.provisionals.popitem(last=False)  # FIX: bounded growth, FIFO eviction

        self.provisionals[patient_id] = {
            "job_id": job_id,
            "risk_score": risk_score,
            "regime": regime,
            "is_provisional": True,
            "stored_at": datetime.now(timezone.utc),
        }

    def get(self, patient_id: str) -> Optional[Dict[str, Any]]:
        self._evict_expired()
        return self.provisionals.get(patient_id)

    def reconcile(self, patient_id: str, job_id: str, final_result: Dict[str, Any]) -> bool:
        """FIX: validates job_id matches before reconciling; logs + returns False if unknown."""
        self._evict_expired()
        entry = self.provisionals.get(patient_id)
        if entry is None:
            logger.warning(f"reconcile: unknown patient {patient_id} (job {job_id})")
            return False
        if entry.get("job_id") != job_id:
            logger.warning(f"reconcile: job_id mismatch for {patient_id} (expected {entry.get('job_id')}, got {job_id})")
            return False

        entry["is_provisional"] = False
        entry["final_result"] = final_result
        entry["reconciled_at"] = datetime.now(timezone.utc)
        return True

    def export_all(self) -> Dict[str, Any]:
        """FIX: deep copy — callers cannot mutate internal state via export."""
        return copy.deepcopy(dict(self.provisionals))

    def _evict_expired(self) -> None:
        now = datetime.now(timezone.utc)
        expired = [
            k for k, v in self.provisionals.items()
            if (now - v["stored_at"]).total_seconds() > self.ttl_seconds
        ]
        for k in expired:
            self.provisionals.pop(k, None)


# ============================================================================
# OBSERVE ENGINE (Main Orchestrator)
# ============================================================================

class ObserveClinicalEngine:
    """Main orchestrator: select → evaluate → fuse → policy → audit."""

    ENGINE_MAP = {
        "heuristic": RiskAdapters.heuristic,
        "bayesian": RiskAdapters.bayesian,
        "trajectory": RiskAdapters.trajectory,
        "drift": RiskAdapters.drift,
        "behavioral": RiskAdapters.behavioral_vaccine,
        "adversarial": RiskAdapters.adversarial,
        "physiological_reserve": RiskAdaptersPhysiological.physiological_reserve,
    }

    def __init__(self, max_tracked_patients: int = 10000):
        self.audit_ledger = ImmutableAuditLedger()
        self.scheduler = AsyncJobScheduler(num_workers=2)
        self.provisional_store = ProvisionalStore()
        # PER-PATIENT STATE: escalation policy + entropy are keyed by patient_id.
        # CRITICAL FIX: previously these were single shared instances, so one
        # patient's escalation_locked cooldown would suppress a DIFFERENT patient's
        # critical escalation — a patient-safety defect in any multi-bed setting.
        #
        # BOUNDED GROWTH: a long-running monitor would otherwise accumulate per-patient
        # state forever. We cap tracked patients and evict the least-recently-assessed
        # patient (LRU) once the cap is hit. Eviction only drops cooldown/entropy state,
        # never audit records (those live in the immutable ledger). A re-appearing patient
        # simply starts with a fresh clean policy — safe, since a stale 5-min cooldown for
        # a patient not seen in thousands of readings should not persist anyway.
        self._max_tracked_patients = max_tracked_patients
        self._patient_policies: "OrderedDict[str, EscalationPolicy]" = OrderedDict()
        self._patient_entropy: Dict[str, float] = {}

    def _touch_patient(self, patient_id: str) -> None:
        """Mark patient as most-recently-used and evict LRU if over capacity."""
        if patient_id in self._patient_policies:
            self._patient_policies.move_to_end(patient_id)
        while len(self._patient_policies) > self._max_tracked_patients:
            evicted_id, _ = self._patient_policies.popitem(last=False)  # drop LRU
            self._patient_entropy.pop(evicted_id, None)

    def _get_policy(self, patient_id: str) -> EscalationPolicy:
        if patient_id not in self._patient_policies:
            self._patient_policies[patient_id] = EscalationPolicy()
        self._touch_patient(patient_id)
        return self._patient_policies[patient_id]

    def select_engines(self, vitals: VitalsSnapshot) -> List[str]:
        """Deterministic engine selection based on entropy + explicit triggers."""
        engines = ["heuristic"]

        recent_entropy = self._patient_entropy.get(vitals.patient_id, 0.0)
        if recent_entropy > 0.6 or vitals.context.get("force_heavy"):
            engines += ["bayesian", "trajectory", "drift"]

        if "previous_o2" in vitals.context or "previous_hr" in vitals.context:
            if "trajectory" not in engines:
                engines.append("trajectory")

        if vitals.context.get("recent_o2_readings"):
            engines.append("adversarial")

        if vitals.oxygen_saturation < 92.0 or vitals.heart_rate > 140 or vitals.heart_rate < 90:
            engines.append("behavioral")

        # physiological_reserve runs on the heavy path or when richer telemetry is present
        if recent_entropy > 0.6 or vitals.context.get("force_heavy") or _has_physiological_telemetry(vitals):
            engines.append("physiological_reserve")

        seen = set()
        ordered = []
        for e in engines:
            if e not in seen:
                seen.add(e)
                ordered.append(e)
        return ordered

    def evaluate(self, vitals: VitalsSnapshot) -> FusedVerdict:
        """Fast synchronous evaluation: select → run → fuse → policy → audit."""

        policy = self._get_policy(vitals.patient_id)

        selected = self.select_engines(vitals)
        outputs = [self.ENGINE_MAP[name](vitals) for name in selected]

        fused_risk, entropy, regime_probs, rationale = BayesianFusion.fuse(outputs)
        self._patient_entropy[vitals.patient_id] = entropy

        max_regime_name = max(regime_probs, key=regime_probs.get)
        candidate_regime = OperationalRegime(max_regime_name)

        # CLINICAL SAFETY BYPASS: two triggers skip dwell/hysteresis entirely —
        #   (1) a heuristic hard rule (risk >= 0.5: CRITICAL_O2, HYPOTHERMIA, etc.), and
        #   (2) a confirmed dangerous clinical syndrome from the behavioral adapter
        #       (septic shock / respiratory distress / hypovolemic shock).
        # Dwell exists to damp noise-driven thrashing on borderline multi-engine
        # disagreement — but neither a CRITICAL_O2 reading nor a named shock syndrome
        # is noise. Both demand immediate escalation, not a second confirming reading.
        heuristic_output = next((o for o in outputs if o.engine_name == "heuristic"), None)
        hard_rule_fired = heuristic_output is not None and heuristic_output.risk_score >= 0.5
        syndrome_fired = any(
            "DANGEROUS_PATTERN:" in r
            for o in outputs for r in o.triggered_rules
        )
        bypass = hard_rule_fired or syndrome_fired

        if bypass and candidate_regime.value in ("warning", "critical"):
            escalation = policy.current_regime.value in ("stable", "caution")
            final_regime = candidate_regime
            policy.current_regime = final_regime
            policy.pending_regime = None
            policy.dwell_count = 0
            if escalation:
                policy.escalation_locked = True
                policy.last_escalation_time = vitals.timestamp
            reason = "hard-rule" if hard_rule_fired else "dangerous-syndrome"
            all_triggered_bypass_note = [f"CLINICAL_SAFETY_BYPASS: {reason} trigger skipped dwell confirmation"]
        else:
            final_regime, escalation = policy.evaluate(candidate_regime, vitals.timestamp)
            all_triggered_bypass_note = []

        all_triggered = all_triggered_bypass_note + [r for o in outputs for r in o.triggered_rules]
        avg_confidence = sum(o.confidence for o in outputs) / len(outputs) if outputs else 0.0

        verdict = FusedVerdict(
            risk_score=fused_risk,
            regime=final_regime,
            confidence=avg_confidence,
            entropy=entropy,
            active_engines=selected,
            triggered_rules=all_triggered,
            timestamp=datetime.now(timezone.utc),
            escalation_required=escalation,
        )

        audit_hash = self.audit_ledger.append(
            vitals.patient_id, "clinical_assessment",
            {
                "vitals": asdict(vitals),
                "selected_engines": selected,
                "outputs": [{"engine": o.engine_name, "risk": o.risk_score, "confidence": o.confidence, "rules": o.triggered_rules} for o in outputs],
                "verdict": {"risk_score": fused_risk, "regime": final_regime.value, "escalation_required": escalation, "entropy": entropy},
            },
        )
        verdict.audit_hash = audit_hash

        if escalation:
            logger.info(f"ESCALATION: patient={vitals.patient_id} regime={final_regime.value} risk={fused_risk:.2f}")

        return verdict


if __name__ == "__main__":
    # Smoke test: critical hypoxia + fever case
    vitals = VitalsSnapshot(
        patient_id="P001",
        timestamp=datetime.now(timezone.utc),
        heart_rate=155,
        oxygen_saturation=85.0,
        respiratory_rate=35,
        temperature=38.5,
        context={"age_months": 24, "force_heavy": True},
    )

    engine = ObserveClinicalEngine()
    verdict = engine.evaluate(vitals)
    print(f"Risk: {verdict.risk_score:.2f} | Regime: {verdict.regime.value} | Entropy: {verdict.entropy:.3f}")
    print(f"Engines run: {verdict.active_engines}")
    print(f"Triggered ({len(verdict.triggered_rules)}):")
    for r in verdict.triggered_rules:
        print(f"  - {r}")
    print(f"Audit chain valid: {engine.audit_ledger.verify_integrity()}")

    # Second call to test escalation policy dwell
    verdict2 = engine.evaluate(vitals)
    print(f"\nSecond call (dwell test): Regime={verdict2.regime.value}, Escalation pending in policy state")
