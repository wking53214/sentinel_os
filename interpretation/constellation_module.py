"""
CONSTELLATION Module - Two-Tier Plug-n-Play Adapters for a Satellite Cluster
============================================================================

Same OBSERVE platform, fourth domain. But a constellation (Starlink-like) is
the first domain that is NOT reducible to one-entity-at-a-time monitoring, and
that forces a genuinely new architectural piece.

Everything before this — patients, vehicles, flight computers — was PER-ENTITY:
many entities, but each assessed in isolation (OBSERVE even had to FIX cross-
patient leakage to keep them isolated). A constellation has risks that exist
only at the population level:

  - a COVERAGE gap can open while every satellite reads healthy (orbital phasing)
  - two perfectly healthy satellites can be on a COLLISION conjunction
  - the inter-satellite mesh can PARTITION though every node is nominal
  - many satellites degrading TOGETHER signals a systemic cause (solar storm,
    bad software push) invisible in any single satellite
  - too many simultaneous escalations exceed the operator's capacity to respond

So this module is TWO TIERS:
  Tier 1  per-satellite adapters  -> the familiar 7-engine pattern, one entity
  Tier 2  fleet adapters          -> emergent risk across the population   [FLEET-TIER]

The headline: Tier 2 fleet risk is computed INDEPENDENTLY of Tier 1, so the
fleet verdict can be CRITICAL while every individual satellite is STABLE. That
is precisely the risk the per-entity tier cannot see, and it is the point.

[BACK-PORT] OBSERVE is per-patient isolated, correctly — but that makes it
blind to WARD/POPULATION signals: an outbreak (correlated deterioration), a
systematic sensor/calibration fault across many beds, or simultaneous
escalations exceeding unit staffing. A fleet tier back-ports as a ward tier,
and it does NOT conflict with the isolation fix: isolate at Tier 1, aggregate
at Tier 2. Both are needed.

This module's PLATFORM already carries the refinements the compute probe
surfaced — second-order trajectory and reserve-modulated escalation — reused
here, not re-derived. The probes compound.

Single-file, no third-party deps. `python3 constellation_module.py`
NOTE: simplified orbital mechanics (linear closest-approach, not covariance
propagation). Architecture demonstration, not flight-dynamics software.
"""

from __future__ import annotations
import hashlib, json, logging, math, copy
from collections import OrderedDict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any, Callable, Tuple

logger = logging.getLogger("CONSTELLATION")

# ============================================================================
# CONTRACTS & CALIBRATION  (identical to OBSERVE / refined platform)
# ============================================================================

class OperationalRegime(Enum):
    STABLE = "stable"
    CAUTION = "caution"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class RiskOutput:
    engine_name: str
    risk_score: float
    confidence: float
    regime_classification: Dict[str, float]
    triggered_rules: List[str]
    timestamp: datetime
    debug_info: Dict[str, Any] = field(default_factory=dict)
    abstained: bool = False


@dataclass
class Verdict:
    entity_id: str
    risk_score: float
    regime: OperationalRegime
    confidence: float
    entropy: float
    active_engines: List[str]
    triggered_rules: List[str]
    timestamp: datetime
    audit_hash: str = ""
    escalation_required: bool = False
    reserve_factor: float = 1.0


def regime_distribution(risk_score: float, critical_floor: float = 0.01) -> Dict[str, float]:
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


# Power envelope depends on whether the satellite is in eclipse or sunlight
# (the analogue of age / road-type / launch-phase: context-relative norms).
ORBIT_NORMS = {
    "sunlit":  {"expected_net_w": 120.0, "net_std": 60.0},   # should be charging
    "eclipse": {"expected_net_w": -80.0, "net_std": 40.0},   # discharging on battery
}

# small vector helpers (positions/velocities are 3-tuples)
def _sub(a, b): return tuple(x - y for x, y in zip(a, b))
def _add(a, b): return tuple(x + y for x, y in zip(a, b))
def _scale(a, s): return tuple(x * s for x in a)
def _dot(a, b): return sum(x * y for x, y in zip(a, b))
def _norm(a): return math.sqrt(_dot(a, a))


# ============================================================================
# TIER 1 — PER-SATELLITE STATE + ADAPTERS
# ============================================================================

@dataclass(frozen=True)
class SatelliteState:
    sat_id: str
    timestamp: datetime
    plane_id: int
    slot: int
    eclipse: bool
    battery_soc: float                # 0..1
    solar_input_w: float
    load_w: float
    temperature_c: float
    propellant_kg: float
    propellant_capacity_kg: float
    reaction_wheel_momentum: float    # 0..1 of saturation
    attitude_error_deg: float
    downlink_margin_db: float
    position_km: Tuple[float, float, float]
    velocity_kms: Tuple[float, float, float]
    isl_neighbors: Tuple[str, ...] = ()
    context: Dict[str, Any] = field(default_factory=dict)


class SatAdapters:

    @staticmethod
    def heuristic(s: SatelliteState) -> RiskOutput:
        t, score = [], 0.0
        if s.battery_soc < 0.10:
            t.append(f"HARD_RULE: battery_critical ({s.battery_soc*100:.0f}%)"); score += 0.55
        elif s.battery_soc < 0.20:
            t.append(f"LOW_BATTERY: {s.battery_soc*100:.0f}%"); score += 0.2
        prop_frac = s.propellant_kg / s.propellant_capacity_kg if s.propellant_capacity_kg > 0 else 1.0
        if prop_frac < 0.02:
            t.append(f"HARD_RULE: propellant_exhausted ({prop_frac*100:.1f}%)"); score += 0.5
        elif prop_frac < 0.10:
            t.append(f"LOW_PROPELLANT: {prop_frac*100:.0f}%"); score += 0.15
        if s.temperature_c > 60 or s.temperature_c < -30:
            t.append(f"HARD_RULE: thermal_limit ({s.temperature_c:.0f}C)"); score += 0.5
        elif s.temperature_c > 50 or s.temperature_c < -20:
            t.append(f"THERMAL_MARGIN: {s.temperature_c:.0f}C"); score += 0.15
        if s.attitude_error_deg > 20:
            t.append(f"HARD_RULE: attitude_lost ({s.attitude_error_deg:.0f} deg)"); score += 0.5
        elif s.attitude_error_deg > 5:
            t.append(f"ATTITUDE_DRIFT: {s.attitude_error_deg:.0f} deg"); score += 0.15
        if s.reaction_wheel_momentum > 0.95:
            t.append(f"HARD_RULE: wheel_saturated ({s.reaction_wheel_momentum*100:.0f}%)"); score += 0.5
        elif s.reaction_wheel_momentum > 0.80:
            t.append(f"HIGH_MOMENTUM: {s.reaction_wheel_momentum*100:.0f}%"); score += 0.15
        if s.downlink_margin_db < 0:
            t.append(f"LINK_LOST: {s.downlink_margin_db:.1f}dB"); score += 0.2
        return RiskOutput("heuristic", min(score, 1.0), 0.90,
                          regime_distribution(min(score, 1.0)), t, datetime.now(timezone.utc))

    @staticmethod
    def bayesian(s: SatelliteState) -> RiskOutput:
        norms = ORBIT_NORMS["eclipse" if s.eclipse else "sunlit"]
        net = s.solar_input_w - s.load_w
        z = (net - norms["expected_net_w"]) / norms["net_std"]
        t, score = [], 0.0
        # Anomalous only when net power is WORSE than expected for the regime.
        if z < -2.0:
            t.append(f"POWER_ANOMALY: net {net:.0f}W is {abs(z):.1f} SD below {'eclipse' if s.eclipse else 'sunlit'} norm")
            score += 0.4 * (1.0 - math.exp(-0.3 * (abs(z) - 2.0)))
        out = RiskOutput("bayesian", min(score, 1.0), 0.85,
                         regime_distribution(min(score, 1.0)), t, datetime.now(timezone.utc))
        out.debug_info = {"net_w": round(net, 1), "z": round(z, 2)}
        return out

    @staticmethod
    def trajectory(s: SatelliteState) -> RiskOutput:
        # momentum + second-order accel (reused from the compute probe)
        hist = s.context.get("battery_history") or []
        dt = s.context.get("time_delta_seconds", 1.0)
        series = list(hist) + [s.battery_soc]
        if len(series) < 2 or dt <= 0:
            return RiskOutput("trajectory", 0.0, 0.2, {"stable": 1.0},
                              ["Insufficient history for trajectory analysis"],
                              datetime.now(timezone.utc), abstained=True)
        vel = (series[-1] - series[-2]) / dt
        t, score, debug = [], 0.0, {"battery_vel": round(vel, 4)}
        if vel < -0.01:  # draining
            t.append(f"BATTERY_DRAIN: {vel*100:.2f}%/s"); score += 0.25
            if len(series) >= 3:
                vel_prev = (series[-2] - series[-3]) / dt
                acc = (vel - vel_prev) / dt
                debug["battery_accel"] = round(acc, 5)
                if acc < 0 and vel < 0:  # draining faster and faster
                    t.append(f"ACCELERATING_DRAIN: {acc*100:.3f}%/s^2"); score += 0.3
        out = RiskOutput("trajectory", min(score, 1.0), 0.80,
                         regime_distribution(min(score, 1.0)), t, datetime.now(timezone.utc))
        out.debug_info = debug
        return out

    @staticmethod
    def behavioral(s: SatelliteState) -> RiskOutput:
        t, score, danger = [], 0.0, []
        # POWER_COLLAPSE: in eclipse, low battery AND draining => may not survive the pass.
        if s.eclipse and s.battery_soc < 0.25 and (s.solar_input_w - s.load_w) < -20:
            danger.append("POWER_COLLAPSE")
            t.append(f"DANGEROUS_PATTERN: power_collapse (eclipse, {s.battery_soc*100:.0f}% draining)")
            score = max(score, 0.9)
        # WHEEL_SATURATION: no momentum authority => attitude control lost.
        if s.reaction_wheel_momentum >= 0.95:
            danger.append("WHEEL_SATURATION")
            t.append(f"DANGEROUS_PATTERN: wheel_saturation ({s.reaction_wheel_momentum*100:.0f}%)")
            score = max(score, 0.88)
        # TUMBLING: large attitude error (uncontrolled pointing).
        if s.attitude_error_deg > 20:
            danger.append("TUMBLING")
            t.append(f"DANGEROUS_PATTERN: tumbling ({s.attitude_error_deg:.0f} deg)")
            score = max(score, 0.87)
        if not danger:
            return RiskOutput("behavioral", 0.0, 0.78, {"stable": 1.0},
                              ["No named hazard pattern"], datetime.now(timezone.utc), abstained=True)
        return RiskOutput("behavioral", min(score, 1.0), 0.92,
                          regime_distribution(min(score, 1.0)), t, datetime.now(timezone.utc))

    @staticmethod
    def drift(s: SatelliteState) -> RiskOutput:
        hist = s.context.get("solar_input_history", [])
        if len(hist) < 10:
            return RiskOutput("drift", 0.0, 0.3, {"stable": 1.0},
                              ["Insufficient history for drift detection"],
                              datetime.now(timezone.utc), abstained=True)
        n = len(hist); xs = list(range(n)); mx = sum(xs)/n; my = sum(hist)/n
        denom = sum((x-mx)**2 for x in xs) or 1.0
        slope = sum((xs[i]-mx)*(hist[i]-my) for i in range(n))/denom
        t, score = [], 0.0
        if slope < -0.5:  # solar input declining over the window => panel degradation
            t.append(f"PANEL_DEGRADATION: solar input {slope:.2f} W/sample sustained decline")
            score += 0.3
        if score < 0.01:
            return RiskOutput("drift", 0.0, 0.5, {"stable": 1.0},
                              ["No sustained drift"], datetime.now(timezone.utc), abstained=True)
        return RiskOutput("drift", min(score, 1.0), 0.75,
                          regime_distribution(min(score, 1.0), critical_floor=0.02),
                          t, datetime.now(timezone.utc))

    @staticmethod
    def adversarial(s: SatelliteState) -> RiskOutput:
        t, score = [], 0.0
        prev = s.context.get("previous") or {}
        if "position_km" in prev:
            jump = _norm(_sub(s.position_km, prev["position_km"]))
            dt = s.context.get("time_delta_seconds", 1.0)
            if dt > 0 and jump / dt > 12.0:  # >12 km/s implied step => bad ephemeris/spoof
                t.append(f"EPHEMERIS_JUMP: {jump/dt:.0f} km/s implied")
                score += 0.35
        recent = s.context.get("recent_telemetry", [])
        if len(recent) >= 5 and len(set(recent[-5:])) == 1:
            t.append(f"STUCK_TELEMETRY: {len(recent[-5:])} identical frames"); score += 0.3
        if s.context.get("uplink_jamming_db") and s.context["uplink_jamming_db"] > 10:
            t.append(f"UPLINK_JAMMING: {s.context['uplink_jamming_db']:.0f}dB above noise"); score += 0.3
        if score < 0.01:
            return RiskOutput("adversarial", 0.0, 0.70, {"stable": 1.0},
                              ["No telemetry-fault signature"],
                              datetime.now(timezone.utc), abstained=True)
        return RiskOutput("adversarial", min(score, 1.0), 0.70,
                          regime_distribution(min(score, 1.0)), t, datetime.now(timezone.utc))

    @staticmethod
    def reserve(s: SatelliteState) -> RiskOutput:
        # Consumable reserve: propellant (unreplenishable) + battery depth.
        prop_frac = s.propellant_kg / s.propellant_capacity_kg if s.propellant_capacity_kg > 0 else 1.0
        reserve_factor = max(0.0, min(1.0, prop_frac))
        t, score = [], 0.0
        if prop_frac < 0.5:
            t.append(f"PROPELLANT_RESERVE: {prop_frac*100:.0f}% remaining (unreplenishable)")
            score += min(0.7, (0.5 - prop_frac) * 1.4)
        if s.battery_soc < 0.4:
            t.append(f"BATTERY_RESERVE: {s.battery_soc*100:.0f}% depth remaining")
            score += min(0.3, (0.4 - s.battery_soc))
            reserve_factor = min(reserve_factor, s.battery_soc / 0.4)
        debug = {"reserve_factor": round(reserve_factor, 3)}
        if score < 0.01:
            out = RiskOutput("reserve", 0.0, 0.30, {"stable": 1.0},
                             ["Full reserve; engine abstains"],
                             datetime.now(timezone.utc), abstained=True)
            out.debug_info = debug
            return out
        out = RiskOutput("reserve", min(score, 1.0), 0.82,
                         regime_distribution(min(score, 1.0)), t, datetime.now(timezone.utc))
        out.debug_info = debug
        return out


# ============================================================================
# FUSION + ESCALATION POLICY  (refined platform: abstain + deterministic floor
# + reserve-modulated dwell, both carried over from the compute probe)
# ============================================================================

class BayesianFusion:
    @staticmethod
    def fuse(outputs: List[RiskOutput]):
        if not outputs:
            return 0.0, 0.0, {"stable": 1.0}, "No outputs"
        active = [o for o in outputs if not o.abstained] or list(outputs)
        tc = sum(o.confidence for o in active)
        fused = sum(o.risk_score * o.confidence for o in active) / tc if tc > 0 else 0.0
        def _det(o): return any(r.startswith("DANGEROUS_PATTERN:") or r.startswith("HARD_RULE:") for r in o.triggered_rules)
        for o in active:
            if _det(o):
                fused = max(fused, o.risk_score)
        probs = regime_distribution(fused)
        entropy = -sum(p * math.log2(p) for p in probs.values() if p > 0)
        return fused, entropy, probs, f"Fused {len(active)} active"


class EscalationPolicy:
    def __init__(self, dwell_threshold=2, lock_seconds=10.0):
        self.dwell_threshold = dwell_threshold; self.lock_seconds = lock_seconds
        self.current_regime = OperationalRegime.STABLE
        self.pending_regime = None; self.dwell_count = 0
        self.escalation_locked = False; self.last_escalation_time = None

    def evaluate(self, new_regime, timestamp, reserve_factor=1.0):
        eff = max(1, math.ceil(self.dwell_threshold * max(0.05, reserve_factor)))
        if self.escalation_locked and self.last_escalation_time:
            if (timestamp - self.last_escalation_time).total_seconds() < self.lock_seconds:
                return self.current_regime, False
            self.escalation_locked = False
        if new_regime == self.current_regime:
            self.pending_regime = None; self.dwell_count = 0
            return self.current_regime, False
        if new_regime == self.pending_regime:
            self.dwell_count += 1
        else:
            self.pending_regime = new_regime; self.dwell_count = 1
        if self.dwell_count >= eff:
            esc = new_regime.value in ("warning", "critical") and self.current_regime.value in ("stable", "caution")
            self.current_regime = new_regime; self.pending_regime = None; self.dwell_count = 0
            if esc:
                self.escalation_locked = True; self.last_escalation_time = timestamp
            return new_regime, esc
        return self.current_regime, False


class ImmutableAuditLedger:
    def __init__(self):
        self.entries = []; self.chain_head = "0" * 64
    def append(self, entity_id, action, data):
        e = {"timestamp": datetime.now(timezone.utc).isoformat(), "entity_id": entity_id,
             "action": action, "data": copy.deepcopy(data), "previous_hash": self.chain_head}
        h = hashlib.sha256(json.dumps(e, sort_keys=True, default=str).encode()).hexdigest()
        e["immutable_hash"] = h; self.entries.append(e); self.chain_head = h
        return h
    def verify_integrity(self):
        prev = "0" * 64
        for e in self.entries:
            if e["previous_hash"] != prev:
                return False
            tgt = copy.deepcopy(e); stored = tgt.pop("immutable_hash")
            if stored != hashlib.sha256(json.dumps(tgt, sort_keys=True, default=str).encode()).hexdigest():
                return False
            prev = stored
        return True


# ============================================================================
# TIER 1 ORCHESTRATOR — per-satellite health
# ============================================================================

class SatelliteHealthEngine:
    ENGINE_MAP = {
        "heuristic": SatAdapters.heuristic, "bayesian": SatAdapters.bayesian,
        "trajectory": SatAdapters.trajectory, "drift": SatAdapters.drift,
        "behavioral": SatAdapters.behavioral, "adversarial": SatAdapters.adversarial,
        "reserve": SatAdapters.reserve,
    }

    def __init__(self, ledger: ImmutableAuditLedger, max_tracked=20000):
        self.audit_ledger = ledger
        self._policies: "OrderedDict[str, EscalationPolicy]" = OrderedDict()
        self._entropy: "OrderedDict[str, float]" = OrderedDict()
        self.max_tracked = max_tracked

    def _get_policy(self, sid):
        if sid not in self._policies:
            self._policies[sid] = EscalationPolicy(); self._entropy[sid] = 0.0
        self._policies.move_to_end(sid); 
        if sid in self._entropy: self._entropy.move_to_end(sid)
        while len(self._policies) > self.max_tracked:
            old, _ = self._policies.popitem(last=False); self._entropy.pop(old, None)
        return self._policies[sid]

    def select_engines(self, s: SatelliteState) -> List[str]:
        eng = ["heuristic", "behavioral", "reserve"]
        if self._entropy.get(s.sat_id, 0.0) > 0.6 or s.context.get("force_heavy"):
            eng += ["bayesian", "drift"]
        if s.context.get("battery_history"):
            eng.append("trajectory")
        if s.context.get("previous") or s.context.get("recent_telemetry") or s.context.get("uplink_jamming_db") is not None:
            eng.append("adversarial")
        if s.context.get("solar_input_history"):
            eng.append("drift")
        seen, out = set(), []
        for e in eng:
            if e not in seen: seen.add(e); out.append(e)
        return out

    def evaluate(self, s: SatelliteState) -> Verdict:
        policy = self._get_policy(s.sat_id)
        selected = self.select_engines(s)
        outputs = [self.ENGINE_MAP[n](s) for n in selected]
        fused, entropy, probs, _ = BayesianFusion.fuse(outputs)
        self._entropy[s.sat_id] = entropy
        candidate = OperationalRegime(max(probs, key=probs.get))
        rf = 1.0
        for o in outputs:
            if o.engine_name == "reserve" and "reserve_factor" in o.debug_info:
                rf = o.debug_info["reserve_factor"]
        active = [o for o in outputs if not o.abstained]
        det = any(r.startswith("HARD_RULE:") or r.startswith("DANGEROUS_PATTERN:")
                  for o in active for r in o.triggered_rules)
        if det and candidate.value in ("warning", "critical"):
            esc = policy.current_regime.value in ("stable", "caution")
            final = candidate; policy.current_regime = final
            policy.pending_regime = None; policy.dwell_count = 0
            if esc:
                policy.escalation_locked = True; policy.last_escalation_time = s.timestamp
        else:
            final, esc = policy.evaluate(candidate, s.timestamp, rf)
        rules = [r for o in outputs for r in o.triggered_rules]
        conf = sum(o.confidence for o in active) / len(active) if active else 0.0
        v = Verdict(s.sat_id, fused, final, conf, entropy,
                    [o.engine_name for o in active], rules, datetime.now(timezone.utc),
                    escalation_required=esc, reserve_factor=rf)
        v.audit_hash = self.audit_ledger.append(s.sat_id, "sat_assessment",
            {"regime": final.value, "risk": fused, "escalation": esc, "rules": rules})
        return v


# ============================================================================
# TIER 2 — FLEET STATE + FLEET ADAPTERS  [FLEET-TIER, the new piece]
# ============================================================================

@dataclass
class FleetConfig:
    required_per_plane: int = 3          # healthy sats per plane for continuous coverage
    conjunction_horizon_s: float = 600.0 # screen this far ahead
    conjunction_critical_km: float = 1.0
    conjunction_warning_km: float = 5.0
    operator_capacity: int = 3           # sats that can be actively commanded at once
    correlated_fraction: float = 0.30    # >this share sharing an anomaly => systemic


class FleetAdapters:
    """Each adapter takes the population and emits a RiskOutput. Acute fleet
    hazards emit DANGEROUS_PATTERN so they floor, exactly like Tier 1."""

    @staticmethod
    def coverage(states, verdicts, cfg: FleetConfig) -> RiskOutput:
        t, worst = [], 0.0
        planes: Dict[int, List[str]] = {}
        for s in states:
            planes.setdefault(s.plane_id, []).append(s.sat_id)
        for pid, ids in sorted(planes.items()):
            healthy = sum(1 for sid in ids if verdicts[sid].regime in (OperationalRegime.STABLE, OperationalRegime.CAUTION))
            if healthy < cfg.required_per_plane:
                deficit = cfg.required_per_plane - healthy
                t.append(f"DANGEROUS_PATTERN: coverage_gap plane {pid} ({healthy}/{cfg.required_per_plane} healthy)")
                worst = max(worst, min(1.0, 0.6 + 0.2 * deficit))
        if not t:
            return RiskOutput("coverage", 0.0, 0.85, {"stable": 1.0},
                              ["Coverage nominal across all planes"],
                              datetime.now(timezone.utc), abstained=True)
        return RiskOutput("coverage", worst, 0.85, regime_distribution(worst), t, datetime.now(timezone.utc))

    @staticmethod
    def conjunction(states, verdicts, cfg: FleetConfig) -> RiskOutput:
        # Pairwise linear closest-approach screening. THIS is the emergent risk:
        # both satellites can be perfectly healthy and still be about to collide.
        t, worst = [], 0.0
        n = len(states)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = states[i], states[j]
                r = _sub(a.position_km, b.position_km)
                v = _sub(a.velocity_kms, b.velocity_kms)
                vv = _dot(v, v)
                tstar = 0.0 if vv < 1e-9 else -_dot(r, v) / vv
                if tstar < 0 or tstar > cfg.conjunction_horizon_s:
                    continue  # moving apart, or closest approach beyond the horizon
                miss = _norm(_add(r, _scale(v, tstar)))
                if miss < cfg.conjunction_critical_km:
                    t.append(f"DANGEROUS_PATTERN: conjunction {a.sat_id}~{b.sat_id} "
                             f"miss {miss:.2f}km in {tstar:.0f}s")
                    worst = max(worst, 0.95)
                elif miss < cfg.conjunction_warning_km:
                    t.append(f"CLOSE_APPROACH: {a.sat_id}~{b.sat_id} miss {miss:.2f}km in {tstar:.0f}s")
                    worst = max(worst, 0.55)
        if not t:
            return RiskOutput("conjunction", 0.0, 0.85, {"stable": 1.0},
                              ["No conjunctions within horizon"],
                              datetime.now(timezone.utc), abstained=True)
        return RiskOutput("conjunction", worst, 0.85, regime_distribution(worst), t, datetime.now(timezone.utc))

    @staticmethod
    def mesh(states, verdicts, cfg: FleetConfig) -> RiskOutput:
        # Connectivity over inter-satellite links among non-failed nodes.
        alive = {s.sat_id for s in states if verdicts[s.sat_id].regime != OperationalRegime.CRITICAL}
        adj: Dict[str, set] = {sid: set() for sid in alive}
        for s in states:
            if s.sat_id not in alive:
                continue
            for nb in s.isl_neighbors:
                if nb in alive:
                    adj[s.sat_id].add(nb); adj.setdefault(nb, set()).add(s.sat_id)
        if not alive:
            return RiskOutput("mesh", 0.0, 0.8, {"stable": 1.0}, ["No live nodes"],
                              datetime.now(timezone.utc), abstained=True)
        start = next(iter(alive)); seen = {start}; q = deque([start])
        while q:
            cur = q.popleft()
            for nb in adj.get(cur, ()):
                if nb not in seen:
                    seen.add(nb); q.append(nb)
        if len(seen) < len(alive):
            unreachable = len(alive) - len(seen)
            t = [f"DANGEROUS_PATTERN: mesh_partition ({unreachable} of {len(alive)} nodes isolated)"]
            return RiskOutput("mesh", 0.85, 0.85, regime_distribution(0.85), t, datetime.now(timezone.utc))
        return RiskOutput("mesh", 0.0, 0.85, {"stable": 1.0},
                          ["Mesh fully connected"], datetime.now(timezone.utc), abstained=True)

    @staticmethod
    def correlated_anomaly(states, verdicts, cfg: FleetConfig) -> RiskOutput:
        # Many satellites showing the SAME anomaly at once => systemic cause.
        # [BACK-PORT] this is the outbreak / fleet-wide-sensor-fault detector.
        n = len(states)
        if n == 0:
            return RiskOutput("correlated_anomaly", 0.0, 0.8, {"stable": 1.0}, ["empty fleet"],
                              datetime.now(timezone.utc), abstained=True)
        BENIGN = ("No ", "Insufficient", "Full reserve", "nominal", "abstains",
                  "Within", "adequate", "connected", "No sustained")
        counts: Dict[str, int] = {}
        for s in states:
            fams = set()
            for r in verdicts[s.sat_id].triggered_rules:
                if any(b in r for b in BENIGN):
                    continue
                head, _, rest = r.partition(":")
                if head in ("HARD_RULE", "DANGEROUS_PATTERN"):
                    fam = rest.strip().split()[0] if rest.strip() else head
                else:
                    fam = head.strip()
                if fam:
                    fams.add(fam)
            for fam in fams:
                counts[fam] = counts.get(fam, 0) + 1
        t, worst = [], 0.0
        for fam, c in sorted(counts.items(), key=lambda kv: -kv[1]):
            frac = c / n
            if frac >= cfg.correlated_fraction and c >= 2:
                t.append(f"SYSTEMIC_ANOMALY: '{fam}' across {c}/{n} satellites ({frac*100:.0f}%)")
                worst = max(worst, min(0.8, 0.4 + frac))
        if not t:
            return RiskOutput("correlated_anomaly", 0.0, 0.8, {"stable": 1.0},
                              ["No correlated/systemic anomaly"],
                              datetime.now(timezone.utc), abstained=True)
        return RiskOutput("correlated_anomaly", worst, 0.8, regime_distribution(worst), t, datetime.now(timezone.utc))

    @staticmethod
    def capacity(states, verdicts, cfg: FleetConfig) -> RiskOutput:
        # Too many simultaneous escalations exceed the operator's ability to act.
        # [BACK-PORT] simultaneous patient escalations vs unit staffing.
        escalating = [s.sat_id for s in states
                      if verdicts[s.sat_id].regime in (OperationalRegime.WARNING, OperationalRegime.CRITICAL)]
        if len(escalating) <= cfg.operator_capacity:
            return RiskOutput("capacity", 0.0, 0.85, {"stable": 1.0},
                              ["Response capacity adequate"],
                              datetime.now(timezone.utc), abstained=True)
        over = len(escalating) - cfg.operator_capacity
        t = [f"DANGEROUS_PATTERN: capacity_saturation ({len(escalating)} escalating, capacity {cfg.operator_capacity})"]
        return RiskOutput("capacity", min(1.0, 0.6 + 0.1 * over), 0.85,
                          regime_distribution(min(1.0, 0.6 + 0.1 * over)), t, datetime.now(timezone.utc))


# ============================================================================
# TIER 2 ORCHESTRATOR — fleet monitor
# ============================================================================

@dataclass
class FleetVerdict:
    risk_score: float
    regime: OperationalRegime
    escalation_required: bool
    fleet_size: int
    sats_critical: int
    sats_warning: int
    fleet_rules: List[str]
    per_engine: Dict[str, float]
    timestamp: datetime
    audit_hash: str = ""


class FleetMonitor:
    FLEET_ENGINES = {
        "coverage": FleetAdapters.coverage,
        "conjunction": FleetAdapters.conjunction,
        "mesh": FleetAdapters.mesh,
        "correlated_anomaly": FleetAdapters.correlated_anomaly,
        "capacity": FleetAdapters.capacity,
    }

    def __init__(self, cfg: FleetConfig = FleetConfig()):
        self.cfg = cfg
        self.audit_ledger = ImmutableAuditLedger()
        self.sat_engine = SatelliteHealthEngine(self.audit_ledger)
        self.fleet_policy = EscalationPolicy()

    def evaluate(self, states: List[SatelliteState], timestamp: datetime):
        # Tier 1: per-satellite verdicts (isolated).
        verdicts = {s.sat_id: self.sat_engine.evaluate(s) for s in states}

        # Tier 2: emergent fleet risk, computed across the population.
        outputs = [fn(states, verdicts, self.cfg) for fn in self.FLEET_ENGINES.values()]
        fused, entropy, probs, _ = BayesianFusion.fuse(outputs)
        candidate = OperationalRegime(max(probs, key=probs.get))

        active = [o for o in outputs if not o.abstained]
        danger = any(r.startswith("DANGEROUS_PATTERN:") for o in active for r in o.triggered_rules)
        if danger and candidate.value in ("warning", "critical"):
            esc = self.fleet_policy.current_regime.value in ("stable", "caution")
            final = candidate; self.fleet_policy.current_regime = final
            self.fleet_policy.pending_regime = None; self.fleet_policy.dwell_count = 0
            if esc:
                self.fleet_policy.escalation_locked = True; self.fleet_policy.last_escalation_time = timestamp
        else:
            final, esc = self.fleet_policy.evaluate(candidate, timestamp)

        fleet_rules = [r for o in outputs for r in o.triggered_rules
                       if "nominal" not in r and "No " not in r and "adequate" not in r and "connected" not in r]
        fv = FleetVerdict(
            risk_score=fused, regime=final, escalation_required=esc,
            fleet_size=len(states),
            sats_critical=sum(1 for v in verdicts.values() if v.regime == OperationalRegime.CRITICAL),
            sats_warning=sum(1 for v in verdicts.values() if v.regime == OperationalRegime.WARNING),
            fleet_rules=fleet_rules,
            per_engine={o.engine_name: round(o.risk_score, 2) for o in outputs},
            timestamp=datetime.now(timezone.utc),
        )
        fv.audit_hash = self.audit_ledger.append("FLEET", "fleet_assessment",
            {"regime": final.value, "risk": fused, "escalation": esc, "rules": fleet_rules})
        return fv, verdicts


# ============================================================================
# SMOKE TEST
# ============================================================================

if __name__ == "__main__":
    now = datetime.now(timezone.utc)

    def healthy_sat(sid, plane, slot, pos, vel, neighbors=()):
        return SatelliteState(
            sat_id=sid, timestamp=now, plane_id=plane, slot=slot, eclipse=False,
            battery_soc=0.95, solar_input_w=200.0, load_w=90.0, temperature_c=15.0,
            propellant_kg=8.0, propellant_capacity_kg=10.0, reaction_wheel_momentum=0.30,
            attitude_error_deg=0.5, downlink_margin_db=6.0, position_km=pos, velocity_kms=vel,
            isl_neighbors=neighbors)

    # A ring of healthy satellites: 3 planes x 4 slots, chained into a connected mesh.
    def build_fleet():
        sats, ids_by_plane = [], {}
        base = 6900.0  # km radius (LEO-ish)
        for p in range(3):
            for k in range(4):
                ang = (k / 4) * 2 * math.pi + p * 0.2
                pos = (base * math.cos(ang), base * math.sin(ang), p * 50.0)
                vel = (-7.5 * math.sin(ang), 7.5 * math.cos(ang), 0.0)
                sid = f"S{p}-{k}"
                ids_by_plane.setdefault(p, []).append(sid)
                sats.append([sid, p, k, pos, vel])
        # build with ring neighbors within each plane + a cross-plane link
        objs = []
        for sid, p, k, pos, vel in sats:
            ring = ids_by_plane[p]
            nbrs = (ring[(k - 1) % 4], ring[(k + 1) % 4], f"S{(p + 1) % 3}-{k}")
            objs.append(healthy_sat(sid, p, k, pos, vel, nbrs))
        return objs

    def show_fleet(label, fv, verdicts):
        print(f"\n=== {label} ===")
        print(f"FLEET: risk={fv.risk_score:.3f} regime={fv.regime.value} escalate={fv.escalation_required} "
              f"| sats critical={fv.sats_critical} warning={fv.sats_warning}/{fv.fleet_size}")
        print(f"  fleet engines: {fv.per_engine}")
        if fv.fleet_rules:
            print(f"  fleet rules: {fv.fleet_rules}")

    # 1. Healthy constellation -> everything stable.
    mon = FleetMonitor()
    fleet = build_fleet()
    fv, vd = mon.evaluate(fleet, now)
    show_fleet("Healthy constellation", fv, vd)

    # 2. One satellite in power collapse -> that sat CRITICAL; fleet notes a degraded member.
    mon2 = FleetMonitor()
    fleet2 = build_fleet()
    bad = fleet2[5]
    fleet2[5] = SatelliteState(**{**asdict(bad), "eclipse": True, "battery_soc": 0.12,
                                  "solar_input_w": 0.0, "load_w": 95.0,
                                  "position_km": bad.position_km, "velocity_kms": bad.velocity_kms,
                                  "isl_neighbors": bad.isl_neighbors})
    fv2, vd2 = mon2.evaluate(fleet2, now)
    show_fleet("One satellite power-collapsing", fv2, vd2)
    print(f"  -> {bad.sat_id} per-sat regime = {vd2[bad.sat_id].regime.value}")

    # 3. MONEY SHOT: every satellite STABLE, but two are on a collision conjunction.
    mon3 = FleetMonitor()
    fleet3 = build_fleet()
    a = fleet3[0]
    b = fleet3[1]
    # place b ~3km from a, with b slightly SLOWER in x so they converge:
    # closest approach ~0.3km in ~100s, both otherwise perfectly healthy.
    a_pos = a.position_km
    b_pos = (a_pos[0] + 3.0, a_pos[1] + 0.3, a_pos[2])
    b_vel = (a.velocity_kms[0] - 0.03, a.velocity_kms[1], a.velocity_kms[2])
    fleet3[0] = SatelliteState(**{**asdict(a), "position_km": a_pos})
    fleet3[1] = SatelliteState(**{**asdict(b), "position_km": b_pos, "velocity_kms": b_vel})
    fv3, vd3 = mon3.evaluate(fleet3, now)
    show_fleet("All sats healthy, but a conjunction", fv3, vd3)
    print(f"  -> per-sat regimes of the pair: {vd3['S0-0'].regime.value}, {vd3['S0-1'].regime.value} "
          f"(both individually fine)")

    # 4. Coverage gap: enough of plane 1 failed that the plane can't cover.
    mon4 = FleetMonitor()
    fleet4 = build_fleet()
    for idx in (4, 5):  # two sats in plane 1 tumbling (critical)
        s = fleet4[idx]
        fleet4[idx] = SatelliteState(**{**asdict(s), "attitude_error_deg": 35.0,
                                        "reaction_wheel_momentum": 0.98})
    fv4, vd4 = mon4.evaluate(fleet4, now)
    show_fleet("Coverage gap in a plane", fv4, vd4)

    # 5. Correlated anomaly: solar storm -> many sats show low-battery at once.
    #    This is a GRADED systemic signal (not an acute bypass hazard), so it
    #    follows dwell; shown across two frames so the fleet regime commits.
    mon5 = FleetMonitor()
    fleet5 = build_fleet()
    for idx in range(6):  # half the fleet hit simultaneously
        s = fleet5[idx]
        fleet5[idx] = SatelliteState(**{**asdict(s), "battery_soc": 0.18})
    mon5.evaluate(fleet5, now)               # frame 1: dwell
    fv5, vd5 = mon5.evaluate(fleet5, now)    # frame 2: commits
    show_fleet("Solar storm: correlated low-battery (frame 2)", fv5, vd5)
    sample = [vd5[f"S0-{k}"].regime.value for k in range(4)]
    print(f"  -> sample individual regimes: {sample} (no single sat critical)")

    print(f"\nAudit chain valid: {mon.audit_ledger.verify_integrity()} "
          f"(healthy run, {len(mon.audit_ledger.entries)} entries)")
