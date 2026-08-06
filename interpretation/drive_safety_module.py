"""
DRIVE Safety Module - Plug-n-Play Adapter Set for Autonomous Driving
=====================================================================

This is the OBSERVE platform with the clinical adapters swapped out for
driving-domain adapters. NOTHING about the platform changes:

  - the RiskOutput contract is identical
  - regime_distribution() is the SAME calibration function (same thresholds)
  - BayesianFusion.fuse() is unchanged (abstention exclusion + danger floor)
  - EscalationPolicy is unchanged (per-actor state, dwell, bypass)
  - the SHA-256 audit chain is unchanged

Only the adapters (the domain logic) are new. That is the whole point of the
contract: swap the engines, keep the substrate.

Lessons baked in from the bad PediatricStabilityAdapter review:
  1. EVERY axis is gated on real telemetry being present. Missing data => the
     engine abstains; it never defaults an input to "perfectly safe" and then
     scores low.
  2. Severity comes from regime_distribution(), NOT a softmax over near-constant
     scores (which collapses to ~uniform and rates a critical state "nominal").
  3. Named danger patterns emit DANGEROUS_PATTERN tags and FLOOR the fused risk,
     so one confident detector can't be averaged away by quiet ones.
  4. triggered_rules is populated on every contribution, for the audit trail.
  5. confidence reflects data completeness, not a formula that stays high while
     the engine is effectively blind.

Single-file, no third-party deps. Run it: `python3 drive_safety_module.py`
"""

from __future__ import annotations
import hashlib
import json
import logging
import math
import copy
from collections import OrderedDict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any, Callable

logger = logging.getLogger("DRIVE")
G = 9.81  # m/s^2, for grip-limit physics

# ============================================================================
# ENUMS & DATA CONTRACTS  (identical shape to OBSERVE)
# ============================================================================

class OperationalRegime(Enum):
    STABLE = "stable"      # nominal driving
    CAUTION = "caution"    # benign degradation, worth watching
    WARNING = "warning"    # intervene soon (slow, increase gap, hand back)
    CRITICAL = "critical"  # imminent hazard, act now


@dataclass(frozen=True)
class VehicleState:
    """The 'vitals snapshot' of the vehicle at one instant."""
    vehicle_id: str
    timestamp: datetime
    ego_speed_mps: float                       # own speed (m/s)
    speed_limit_mps: float                      # posted limit (m/s)
    lateral_accel_mps2: float                   # cornering force (m/s^2)
    lane_offset_m: float                        # lateral error from lane center (m)
    # Optional perception fields — None means "no such object / no reading".
    # Note: None is NOT 0. A missing lead vehicle is not a lead vehicle at 0m.
    lead_distance_m: Optional[float] = None     # gap to lead vehicle (m)
    lead_rel_speed_mps: Optional[float] = None  # closing speed; negative = approaching
    nearest_vru_distance_m: Optional[float] = None  # pedestrian/cyclist gap (m)
    nearest_vru_ttc_s: Optional[float] = None       # time-to-collision w/ VRU (s)
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskOutput:
    """IDENTICAL contract to OBSERVE. abstained=True => excluded from fusion."""
    engine_name: str
    risk_score: float
    confidence: float
    regime_classification: Dict[str, float]
    triggered_rules: List[str]
    timestamp: datetime
    debug_info: Dict[str, Any] = field(default_factory=dict)
    abstained: bool = False


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
    escalation_required: bool = False


# ============================================================================
# CALIBRATION  (IDENTICAL to OBSERVE — same function, same thresholds)
# ============================================================================

def regime_distribution(risk_score: float, critical_floor: float = 0.01) -> Dict[str, float]:
    """
    Shared risk->regime calibration. This is the SAME function the clinical
    engines use. An adapter must pipe its scalar risk through here rather than
    inventing its own mapping (the mistake that made the bad adapter rate a
    critically ill patient 'stable').
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


# Road-type operating envelopes (the driving analogue of PEDIATRIC_NORMS).
# speed_mean/std in m/s; lat_accel_typ is a comfortable cornering force.
ROAD_NORMS = {
    "residential": {"speed_mean": 11.0, "speed_std": 3.0,  "lat_accel_typ": 2.0},
    "urban":       {"speed_mean": 14.0, "speed_std": 4.0,  "lat_accel_typ": 2.5},
    "rural":       {"speed_mean": 22.0, "speed_std": 5.0,  "lat_accel_typ": 3.0},
    "highway":     {"speed_mean": 30.0, "speed_std": 5.0,  "lat_accel_typ": 2.5},
    "generic":     {"speed_mean": 18.0, "speed_std": 6.0,  "lat_accel_typ": 2.5},
}


def get_road_norms(road_type: Optional[str]) -> Dict[str, float]:
    return ROAD_NORMS.get(road_type or "generic", ROAD_NORMS["generic"])


def _grip_limit_mps2(friction: Optional[float]) -> Optional[float]:
    """Max sustainable lateral accel ~= mu * g. None friction => unknown."""
    if friction is None:
        return None
    return max(0.0, friction) * G


# ============================================================================
# DRIVING RISK ADAPTERS  (the only domain-specific code in the module)
# ============================================================================

class DriveAdapters:
    """Seven driving engines, each conforming to the RiskOutput contract."""

    # ------------------------------------------------------------------
    # 1. HEURISTIC — hard rules on headway, speed, grip, lane-keeping.
    #    Always runs. Emits risk>=0.5 on a hard rule (drives the bypass).
    # ------------------------------------------------------------------
    @staticmethod
    def heuristic(state: VehicleState) -> RiskOutput:
        triggered: List[str] = []
        score = 0.0
        norms = get_road_norms(state.context.get("road_type"))
        friction = state.context.get("friction_estimate")
        grip = _grip_limit_mps2(friction)

        # Time headway to lead vehicle (the "2-second rule").
        if state.lead_distance_m is not None and state.ego_speed_mps > 0.5:
            headway = state.lead_distance_m / state.ego_speed_mps
            if headway < 0.5:
                triggered.append(f"HARD_RULE: critical_headway {headway:.2f}s (<0.5s)")
                score += 0.5
            elif headway < 1.0:
                triggered.append(f"WARNING_HEADWAY: {headway:.2f}s (<1.0s)")
                score += 0.3
            elif headway < 2.0:
                triggered.append(f"SHORT_HEADWAY: {headway:.2f}s (<2.0s)")
                score += 0.12

        # Speed over the posted limit.
        if state.speed_limit_mps > 0:
            over = state.ego_speed_mps - state.speed_limit_mps
            if over > 8.0:        # ~29 km/h over
                triggered.append(f"GROSS_SPEEDING: +{over:.1f} m/s over limit")
                score += 0.3
            elif over > 3.0:      # ~11 km/h over
                triggered.append(f"SPEEDING: +{over:.1f} m/s over limit")
                score += 0.12

        # Lateral accel vs available grip (loss-of-traction precursor).
        if grip is not None and grip > 0:
            ratio = abs(state.lateral_accel_mps2) / grip
            if ratio > 0.95:
                triggered.append(f"HARD_RULE: at_grip_limit lat_accel {abs(state.lateral_accel_mps2):.1f} ~ grip {grip:.1f}")
                score += 0.5
            elif ratio > 0.75:
                triggered.append(f"HIGH_LATERAL_LOAD: {ratio*100:.0f}% of grip")
                score += 0.2

        # Lane-keeping.
        off = abs(state.lane_offset_m)
        if off > 1.0:
            triggered.append(f"LANE_DEPARTURE: {off:.2f}m off-center (>1.0m)")
            score += 0.4
        elif off > 0.5:
            triggered.append(f"LANE_DRIFT: {off:.2f}m off-center (>0.5m)")
            score += 0.15

        score = min(score, 1.0)

        # Confidence = data completeness (NOT a formula that stays high blind).
        have = sum(x is not None for x in [
            state.lead_distance_m, friction, state.context.get("road_type")
        ])
        confidence = 0.70 + 0.08 * have  # 0.70..0.94

        return RiskOutput("heuristic", score, confidence,
                          regime_distribution(score), triggered,
                          datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # 2. BAYESIAN — deviation from the road-type operating envelope.
    # ------------------------------------------------------------------
    @staticmethod
    def bayesian(state: VehicleState) -> RiskOutput:
        triggered: List[str] = []
        debug: Dict[str, Any] = {}
        norms = get_road_norms(state.context.get("road_type"))

        z_speed = (state.ego_speed_mps - norms["speed_mean"]) / norms["speed_std"]
        debug["z_speed"] = round(z_speed, 3)

        # Continuous likelihood (smooth), not a step threshold.
        like_speed = 1.0 - math.exp(-0.10 * (max(0.0, z_speed) ** 2))

        score = 0.0
        if z_speed > 2.0:
            triggered.append(f"SPEED_DEVIATION: {abs(z_speed):.1f} SD above envelope mean")
            score += 0.35 * like_speed

        lat_typ = norms["lat_accel_typ"]
        if lat_typ > 0:
            lat_excess = abs(state.lateral_accel_mps2) / lat_typ
            debug["lat_excess_ratio"] = round(lat_excess, 3)
            if lat_excess > 2.0:
                triggered.append(f"LATERAL_DEVIATION: {lat_excess:.1f}x typical cornering")
                score += 0.30 * (1.0 - math.exp(-0.5 * (lat_excess - 2.0)))

        score = min(score, 1.0)
        out = RiskOutput("bayesian", score, 0.85,
                         regime_distribution(score), triggered,
                         datetime.now(timezone.utc))
        out.debug_info = debug
        return out

    # ------------------------------------------------------------------
    # 3. TRAJECTORY — momentum / closing dynamics. ABSTAINS without history.
    # ------------------------------------------------------------------
    @staticmethod
    def trajectory(state: VehicleState) -> RiskOutput:
        prev = state.context.get("previous")  # dict of the last frame, or None
        dt = state.context.get("time_delta_seconds", 0.1)

        if not prev or dt <= 0:
            return RiskOutput("trajectory", 0.0, 0.2, {"stable": 1.0},
                              ["Insufficient history for trajectory analysis"],
                              datetime.now(timezone.utc), abstained=True)

        triggered: List[str] = []
        score = 0.0

        # Time-to-collision with the lead vehicle, only when actually closing.
        if state.lead_distance_m is not None and state.lead_rel_speed_mps is not None:
            closing = -state.lead_rel_speed_mps  # positive = gap shrinking
            if closing > 0.1:
                ttc = state.lead_distance_m / closing
                if ttc < 1.5:
                    triggered.append(f"TTC_LEAD: {ttc:.2f}s to lead at current closing rate")
                    score += 0.5
                elif ttc < 3.0:
                    triggered.append(f"CLOSING_LEAD: {ttc:.2f}s TTC")
                    score += 0.25

        # Longitudinal jerk / hard deceleration trend.
        if "ego_speed_mps" in prev:
            accel = (state.ego_speed_mps - prev["ego_speed_mps"]) / dt
            if accel < -5.0:
                triggered.append(f"HARD_DECEL: {accel:.1f} m/s^2")
                score += 0.2

        # Lane-offset rate (how fast we're leaving the lane).
        if "lane_offset_m" in prev:
            off_rate = (abs(state.lane_offset_m) - abs(prev["lane_offset_m"])) / dt
            if off_rate > 0.5:
                triggered.append(f"LANE_DEPARTURE_RATE: {off_rate:.2f} m/s outward")
                score += 0.25

        score = min(score, 1.0)
        return RiskOutput("trajectory", score, 0.80,
                          regime_distribution(score), triggered,
                          datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # 4. DRIFT — slow baseline shift (sensor calibration, lane-keeping bias).
    #    ABSTAINS without enough history.
    # ------------------------------------------------------------------
    @staticmethod
    def drift(state: VehicleState) -> RiskOutput:
        hist = state.context.get("lane_offset_history", [])
        if len(hist) < 10:
            return RiskOutput("drift", 0.0, 0.3, {"stable": 1.0},
                              ["Insufficient history for drift detection"],
                              datetime.now(timezone.utc), abstained=True)

        triggered: List[str] = []
        score = 0.0
        mean = sum(hist) / len(hist)
        var = sum((x - mean) ** 2 for x in hist) / len(hist)
        std = math.sqrt(var)

        # A persistent non-zero mean offset = systematic lane-keeping bias.
        if abs(mean) > 0.4:
            triggered.append(f"PERSISTENT_LANE_BIAS: mean offset {mean:.2f}m over window")
            score += 0.3
        # Growing variance = degrading control / sensor noise.
        if std > 0.5:
            triggered.append(f"CONTROL_VARIANCE: lane std {std:.2f}m (noisy tracking)")
            score += 0.2

        score = min(score, 1.0)
        conf = 0.85 if len(hist) >= 50 else 0.70
        return RiskOutput("drift", score, conf,
                          regime_distribution(score, critical_floor=0.02),
                          triggered, datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # 5. BEHAVIORAL — named hazard patterns. Emit DANGEROUS_PATTERN tags that
    #    FLOOR the fused risk (the 'septic shock' mechanism, driving edition).
    # ------------------------------------------------------------------
    @staticmethod
    def behavioral(state: VehicleState) -> RiskOutput:
        triggered: List[str] = []
        score = 0.0
        dangerous: List[str] = []
        friction = state.context.get("friction_estimate")
        grip = _grip_limit_mps2(friction)

        # Pedestrian / cyclist in path — highest priority.
        if state.nearest_vru_ttc_s is not None and state.nearest_vru_ttc_s < 2.5:
            dangerous.append("VRU_IN_PATH")
            triggered.append(f"DANGEROUS_PATTERN: vru_in_path (TTC {state.nearest_vru_ttc_s:.1f}s)")
            score = max(score, 0.95)

        # Imminent rear-end with lead vehicle.
        if (state.lead_distance_m is not None and state.lead_rel_speed_mps is not None):
            closing = -state.lead_rel_speed_mps
            if closing > 0.1:
                ttc = state.lead_distance_m / closing
                if ttc < 1.5:
                    dangerous.append("IMMINENT_COLLISION")
                    triggered.append(f"DANGEROUS_PATTERN: imminent_collision (lead TTC {ttc:.1f}s)")
                    score = max(score, 0.90)

        # Loss of traction: lateral demand exceeds available grip.
        if grip is not None and grip > 0 and abs(state.lateral_accel_mps2) > grip:
            dangerous.append("LOSS_OF_TRACTION")
            triggered.append(
                f"DANGEROUS_PATTERN: loss_of_traction "
                f"(lat {abs(state.lateral_accel_mps2):.1f} > grip {grip:.1f})")
            score = max(score, 0.88)

        # Run-off-road: large offset AND moving further out.
        prev = state.context.get("previous") or {}
        prev_off = abs(prev.get("lane_offset_m", state.lane_offset_m))
        if abs(state.lane_offset_m) > 1.2 and abs(state.lane_offset_m) >= prev_off:
            dangerous.append("RUN_OFF_ROAD")
            triggered.append(f"DANGEROUS_PATTERN: run_off_road ({state.lane_offset_m:.2f}m and worsening)")
            score = max(score, 0.85)

        if not dangerous:
            # Specialist detector: no pattern => no opinion, not "zero risk".
            return RiskOutput("behavioral", 0.0, 0.78, {"stable": 1.0},
                              ["No named hazard pattern"],
                              datetime.now(timezone.utc), abstained=True)

        return RiskOutput("behavioral", min(score, 1.0), 0.92,
                          regime_distribution(min(score, 1.0)), triggered,
                          datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # 6. ADVERSARIAL — sensor faults / spoofing. NOT real-world danger.
    #    (stuck sensor, implausible jumps, cross-sensor disagreement)
    # ------------------------------------------------------------------
    @staticmethod
    def adversarial(state: VehicleState) -> RiskOutput:
        triggered: List[str] = []
        score = 0.0

        # Stuck speed sensor: identical streak.
        recent = state.context.get("recent_speed_readings", [])
        if len(recent) >= 5 and len(set(recent[-5:])) == 1 and state.ego_speed_mps > 0:
            triggered.append(f"STUCK_SPEED_SENSOR: {len(recent[-5:])} identical readings")
            score += 0.3

        # GPS/odometry disagreement (spoofing or fault).
        gps_speed = state.context.get("gps_speed_mps")
        if gps_speed is not None:
            disagree = abs(gps_speed - state.ego_speed_mps)
            if disagree > 5.0:
                triggered.append(f"SENSOR_DISAGREEMENT: wheel vs GPS {disagree:.1f} m/s apart")
                score += 0.3

        # Implausible position jump (GPS teleport).
        jump = state.context.get("position_jump_m")
        dt = state.context.get("time_delta_seconds", 0.1)
        if jump is not None and dt > 0:
            implied = jump / dt
            if implied > 100.0:  # >360 km/h implied => not physical
                triggered.append(f"IMPLAUSIBLE_JUMP: {implied:.0f} m/s implied by GPS step")
                score += 0.3

        # Out-of-range.
        if state.ego_speed_mps < 0 or state.ego_speed_mps > 120:
            triggered.append(f"OUT_OF_RANGE_SPEED: {state.ego_speed_mps} m/s")
            score += 0.3

        score = min(score, 1.0)
        if score < 0.01:
            regimes = {"stable": 1.0, "caution": 0.0, "warning": 0.0, "critical": 0.0}
            return RiskOutput("adversarial", 0.0, 0.70, regimes,
                              ["No sensor-fault signature"],
                              datetime.now(timezone.utc), abstained=True)
        return RiskOutput("adversarial", score, 0.70,
                          regime_distribution(score), triggered,
                          datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # 7. OPERATIONAL ENVELOPE (ODD reserve) — the gated 7th engine, analogue
    #    of physiological_reserve. EACH axis gated on real telemetry presence;
    #    ABSTAINS entirely if no ODD data is provided (no phantom 'perfect'
    #    defaults — the exact failure of the bad clinical adapter).
    # ------------------------------------------------------------------
    @staticmethod
    def operational_envelope(state: VehicleState) -> RiskOutput:
        triggered: List[str] = []
        debug: Dict[str, Any] = {}
        contributions: List[float] = []

        visibility = state.context.get("visibility_m")          # meters
        sensor_health = state.context.get("sensor_health")      # 0..1
        friction = state.context.get("friction_estimate")       # 0..1

        # Visibility vs stopping distance need (rough): need ~ v^2/(2*a) + v*reaction.
        if visibility is not None:
            stop_need = (state.ego_speed_mps ** 2) / (2 * 4.0) + state.ego_speed_mps * 1.0
            debug["stopping_need_m"] = round(stop_need, 1)
            if visibility < stop_need:
                deficit = (stop_need - visibility) / max(stop_need, 1.0)
                triggered.append(f"OUTRUNNING_SENSORS: visibility {visibility:.0f}m < stop-need {stop_need:.0f}m")
                contributions.append(min(0.6, deficit))

        # Degraded sensor health.
        if sensor_health is not None:
            if sensor_health < 0.6:
                triggered.append(f"DEGRADED_SENSORS: health {sensor_health:.2f}")
                contributions.append(0.5 * (0.6 - sensor_health) / 0.6)

        # Low friction relative to speed (winter ODD edge).
        if friction is not None and friction < 0.4 and state.ego_speed_mps > 15.0:
            triggered.append(f"LOW_FRICTION_AT_SPEED: mu {friction:.2f} at {state.ego_speed_mps:.0f} m/s")
            contributions.append(min(0.6, (0.4 - friction) * 2.0))

        # If NO ODD telemetry was provided at all, abstain — do not fabricate safety.
        if visibility is None and sensor_health is None and friction is None:
            return RiskOutput("operational_envelope", 0.0, 0.25, {"stable": 1.0},
                              ["No ODD telemetry; engine abstains"],
                              datetime.now(timezone.utc), abstained=True)

        score = min(sum(contributions), 1.0)
        # Nothing to flag => abstain (a clear ODD check is not a "zero risk" vote;
        # voting 0 here would dilute the graded tiers and squeeze out 'caution').
        if score < 0.01:
            return RiskOutput("operational_envelope", 0.0, 0.25, {"stable": 1.0},
                              ["Within operational design domain; engine abstains"],
                              datetime.now(timezone.utc), abstained=True)
        out = RiskOutput("operational_envelope", score, 0.80,
                         regime_distribution(score), triggered,
                         datetime.now(timezone.utc))
        out.debug_info = debug
        return out


# ============================================================================
# FUSION  (IDENTICAL to the fixed OBSERVE fusion: abstain-exclusion + floor)
# ============================================================================

class BayesianFusion:
    @staticmethod
    def fuse(outputs: List[RiskOutput]):
        if not outputs:
            return 0.0, 0.0, {"stable": 1.0, "caution": 0.0, "warning": 0.0, "critical": 0.0}, "No outputs"

        # 1. Exclude abstaining engines entirely; fall back to all if none active.
        active = [o for o in outputs if not o.abstained]
        if not active:
            active = list(outputs)

        total_conf = sum(o.confidence for o in active)
        fused_risk = (sum(o.risk_score * o.confidence for o in active) / total_conf
                      if total_conf > 0 else 0.0)

        # 2. Deterministic floor: a named hazard pattern OR a heuristic hard rule
        #    cannot be averaged away by quieter, probabilistic engines.
        #    (OBSERVE currently floors only on DANGEROUS_PATTERN — this extends it
        #    to HARD_RULE and is worth back-porting there.)
        def _deterministic(o: RiskOutput) -> bool:
            return any(r.startswith("DANGEROUS_PATTERN:") or r.startswith("HARD_RULE:")
                       for r in o.triggered_rules)

        for o in active:
            if _deterministic(o):
                fused_risk = max(fused_risk, o.risk_score)

        # 3. Derive regime distribution from the (possibly floored) fused risk.
        regime_probs = regime_distribution(fused_risk)

        # 4. Shannon entropy over final regime probabilities.
        entropy = -sum(p * math.log2(p) for p in regime_probs.values() if p > 0)

        names = ", ".join(o.engine_name for o in active)
        return fused_risk, entropy, regime_probs, f"Fused {len(active)} active engines ({names})"


# ============================================================================
# ESCALATION POLICY  (per-vehicle state, dwell, bypass — same as OBSERVE)
# ============================================================================

class EscalationPolicy:
    def __init__(self, dwell_threshold: int = 2, lock_seconds: int = 8):
        self.dwell_threshold = dwell_threshold
        self.lock_seconds = lock_seconds
        self.current_regime = OperationalRegime.STABLE
        self.pending_regime: Optional[OperationalRegime] = None
        self.dwell_count = 0
        self.escalation_locked = False
        self.last_escalation_time: Optional[datetime] = None

    def evaluate(self, new_regime: OperationalRegime, timestamp: datetime):
        if self.escalation_locked and self.last_escalation_time:
            elapsed = (timestamp - self.last_escalation_time).total_seconds()
            if elapsed < self.lock_seconds:
                return self.current_regime, False
            self.escalation_locked = False

        if new_regime == self.current_regime:
            self.pending_regime = None
            self.dwell_count = 0
            return self.current_regime, False

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
# AUDIT LEDGER  (SHA-256 chain — unchanged from OBSERVE)
# ============================================================================

class ImmutableAuditLedger:
    def __init__(self):
        self.entries: List[Dict[str, Any]] = []
        self.chain_head = "0" * 64

    def append(self, vehicle_id: str, action: str, data: Dict[str, Any]) -> str:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "vehicle_id": vehicle_id,
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
            stored = target.pop("immutable_hash")
            recalced = hashlib.sha256(json.dumps(target, sort_keys=True, default=str).encode()).hexdigest()
            if stored != recalced:
                return False
            expected_prev = stored
        return True


# ============================================================================
# DRIVE SAFETY ENGINE  (orchestrator — same shape as ObserveClinicalEngine)
# ============================================================================

class DriveSafetyEngine:
    """select -> evaluate -> fuse -> policy -> audit, with per-vehicle state."""

    ENGINE_MAP: Dict[str, Callable[[VehicleState], RiskOutput]] = {
        "heuristic": DriveAdapters.heuristic,
        "bayesian": DriveAdapters.bayesian,
        "trajectory": DriveAdapters.trajectory,
        "drift": DriveAdapters.drift,
        "behavioral": DriveAdapters.behavioral,
        "adversarial": DriveAdapters.adversarial,
        "operational_envelope": DriveAdapters.operational_envelope,
    }

    def __init__(self, max_tracked_vehicles: int = 10000):
        self.audit_ledger = ImmutableAuditLedger()
        self._policies: "OrderedDict[str, EscalationPolicy]" = OrderedDict()
        self._entropy: "OrderedDict[str, float]" = OrderedDict()
        self.max_tracked_vehicles = max_tracked_vehicles

    def _touch(self, vehicle_id: str):
        for d in (self._policies, self._entropy):
            if vehicle_id in d:
                d.move_to_end(vehicle_id)
        while len(self._policies) > self.max_tracked_vehicles:
            old, _ = self._policies.popitem(last=False)
            self._entropy.pop(old, None)

    def _get_policy(self, vehicle_id: str) -> EscalationPolicy:
        if vehicle_id not in self._policies:
            self._policies[vehicle_id] = EscalationPolicy()
            self._entropy[vehicle_id] = 0.0
        self._touch(vehicle_id)
        return self._policies[vehicle_id]

    def select_engines(self, state: VehicleState) -> List[str]:
        engines = ["heuristic", "behavioral"]  # always-on safety core
        ent = self._entropy.get(state.vehicle_id, 0.0)
        if ent > 0.6 or state.context.get("force_heavy"):
            engines += ["bayesian", "trajectory", "drift"]
        if state.context.get("previous"):
            engines.append("trajectory")
        if state.context.get("recent_speed_readings") or state.context.get("gps_speed_mps") is not None:
            engines.append("adversarial")
        if any(state.context.get(k) is not None for k in ("visibility_m", "sensor_health", "friction_estimate")):
            engines.append("operational_envelope")
        seen, ordered = set(), []
        for e in engines:
            if e not in seen:
                seen.add(e); ordered.append(e)
        return ordered

    def evaluate(self, state: VehicleState) -> FusedVerdict:
        policy = self._get_policy(state.vehicle_id)
        selected = self.select_engines(state)
        outputs = [self.ENGINE_MAP[name](state) for name in selected]

        fused_risk, entropy, regime_probs, _ = BayesianFusion.fuse(outputs)
        self._entropy[state.vehicle_id] = entropy

        candidate = OperationalRegime(max(regime_probs, key=regime_probs.get))

        # Bypass dwell on a deterministic signal: heuristic hard rule OR named pattern.
        active = [o for o in outputs if not o.abstained]
        hard_rule = any(r.startswith("HARD_RULE:") for o in active for r in o.triggered_rules)
        danger = any(r.startswith("DANGEROUS_PATTERN:") for o in active for r in o.triggered_rules)

        if (hard_rule or danger) and candidate.value in ("warning", "critical"):
            escalation = policy.current_regime.value in ("stable", "caution")
            final_regime = candidate
            policy.current_regime = final_regime
            policy.pending_regime = None
            policy.dwell_count = 0
            if escalation:
                policy.escalation_locked = True
                policy.last_escalation_time = state.timestamp
        else:
            final_regime, escalation = policy.evaluate(candidate, state.timestamp)

        all_triggered = [r for o in outputs for r in o.triggered_rules]
        avg_conf = sum(o.confidence for o in active) / len(active) if active else 0.0

        verdict = FusedVerdict(
            risk_score=fused_risk, regime=final_regime, confidence=avg_conf,
            entropy=entropy, active_engines=[o.engine_name for o in active],
            triggered_rules=all_triggered, timestamp=datetime.now(timezone.utc),
            escalation_required=escalation,
        )
        verdict.audit_hash = self.audit_ledger.append(
            state.vehicle_id, "drive_assessment",
            {
                "state": asdict(state),
                "selected_engines": selected,
                "outputs": [{"engine": o.engine_name, "risk": o.risk_score,
                             "confidence": o.confidence, "abstained": o.abstained,
                             "rules": o.triggered_rules} for o in outputs],
                "verdict": {"risk_score": fused_risk, "regime": final_regime.value,
                            "escalation_required": escalation, "entropy": entropy},
            },
        )
        if escalation:
            logger.info(f"ESCALATION vehicle={state.vehicle_id} regime={final_regime.value} risk={fused_risk:.2f}")
        return verdict


# ============================================================================
# SMOKE TEST
# ============================================================================

if __name__ == "__main__":
    engine = DriveSafetyEngine()
    now = datetime.now(timezone.utc)

    def show(label, v):
        print(f"\n=== {label} ===")
        print(f"risk={v.risk_score:.3f} regime={v.regime.value} "
              f"escalate={v.escalation_required} entropy={v.entropy:.2f}")
        print(f"engines={v.active_engines}")
        print(f"rules={[r for r in v.triggered_rules if 'No ' not in r and 'Within' not in r and 'Insufficient' not in r]}")

    # 1. Nominal highway cruise — STABLE.
    show("Nominal highway cruise", engine.evaluate(VehicleState(
        vehicle_id="AV-1", timestamp=now,
        ego_speed_mps=29.0, speed_limit_mps=31.0,
        lateral_accel_mps2=0.5, lane_offset_m=0.1,
        lead_distance_m=80.0, lead_rel_speed_mps=0.0,
        context={"road_type": "highway", "friction_estimate": 0.85,
                 "visibility_m": 200, "sensor_health": 0.98})))

    # 2. Soft concern (mild speeding + lane drift), nothing deterministic.
    #    Fused risk lands in the caution band, but dwell/hysteresis holds the
    #    COMMITTED label at stable until a second confirming frame — this is the
    #    false-alarm suppression for low-grade, single-frame blips.
    v2 = engine.evaluate(VehicleState(
        vehicle_id="AV-2", timestamp=now,
        ego_speed_mps=35.5, speed_limit_mps=31.0,   # +4.5 m/s => SPEEDING (soft)
        lateral_accel_mps2=0.5, lane_offset_m=0.6,  # LANE_DRIFT (soft)
        lead_distance_m=120.0, lead_rel_speed_mps=0.0,
        context={"road_type": "highway", "friction_estimate": 0.85}))
    print(f"\n=== Soft concern: mild speeding + lane drift ===")
    print(f"fused_risk={v2.risk_score:.3f} (caution band) -> committed regime={v2.regime.value} "
          f"(dwell holds a single low-grade frame)")
    print(f"engines={v2.active_engines}")
    print(f"rules={[r for r in v2.triggered_rules if 'No ' not in r and 'Within' not in r and 'Insufficient' not in r]}")

    # 3. Dangerous tailgating (0.45s headway) but NOT closing — WARNING via hard-rule floor.
    show("Dangerous tailgating, steady", engine.evaluate(VehicleState(
        vehicle_id="AV-3", timestamp=now,
        ego_speed_mps=30.0, speed_limit_mps=31.0,
        lateral_accel_mps2=0.4, lane_offset_m=0.1,
        lead_distance_m=13.5, lead_rel_speed_mps=0.0,  # 0.45s headway, not closing
        context={"road_type": "highway", "friction_estimate": 0.85})))

    # 4. Closing hard on lead (TTC ~1.0s) — CRITICAL via imminent-collision pattern.
    show("Closing hard on lead vehicle", engine.evaluate(VehicleState(
        vehicle_id="AV-4", timestamp=now,
        ego_speed_mps=30.0, speed_limit_mps=31.0,
        lateral_accel_mps2=0.4, lane_offset_m=0.1,
        lead_distance_m=15.0, lead_rel_speed_mps=-15.0,  # closing 15 m/s => TTC 1.0s
        context={"road_type": "highway", "friction_estimate": 0.85,
                 "previous": {"ego_speed_mps": 30.0, "lane_offset_m": 0.1},
                 "time_delta_seconds": 0.1})))

    # 5. Pedestrian steps into path — CRITICAL, must floor regardless of dilution.
    show("Pedestrian steps into path", engine.evaluate(VehicleState(
        vehicle_id="AV-5", timestamp=now,
        ego_speed_mps=14.0, speed_limit_mps=14.0,
        lateral_accel_mps2=0.3, lane_offset_m=0.1,
        nearest_vru_distance_m=12.0, nearest_vru_ttc_s=1.4,
        context={"road_type": "urban", "friction_estimate": 0.8})))

    # 6. Icy corner — loss of traction (lateral demand exceeds grip) — CRITICAL.
    show("Icy corner, grip exceeded", engine.evaluate(VehicleState(
        vehicle_id="AV-6", timestamp=now,
        ego_speed_mps=18.0, speed_limit_mps=22.0,
        lateral_accel_mps2=3.0, lane_offset_m=0.3,
        context={"road_type": "rural", "friction_estimate": 0.2})))  # grip ~1.96 m/s^2

    # 7. GPS spoof vs wheel speed — adversarial sensor fault, NOT real danger — STABLE.
    show("GPS spoof vs wheel speed", engine.evaluate(VehicleState(
        vehicle_id="AV-7", timestamp=now,
        ego_speed_mps=20.0, speed_limit_mps=22.0,
        lateral_accel_mps2=0.4, lane_offset_m=0.1,
        lead_distance_m=60.0, lead_rel_speed_mps=0.0,
        context={"road_type": "rural", "friction_estimate": 0.8,
                 "gps_speed_mps": 32.0, "position_jump_m": 40.0,
                 "time_delta_seconds": 0.1})))

    print(f"\nAudit chain valid: {engine.audit_ledger.verify_integrity()} "
          f"({len(engine.audit_ledger.entries)} entries)")
