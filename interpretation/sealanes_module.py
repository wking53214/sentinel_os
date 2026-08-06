"""
SEALANES Module - Commodity Shipping-Lane Resilience + Adaptive Adversary
=========================================================================

Fifth domain on the same OBSERVE platform. Goal: stability of global commodity
flows and the ability to plan for disruptions (e.g. a Strait of Hormuz closure
during a Gulf conflict). It is decision SUPPORT — situational risk + contingency
recommendations — not a literal controller of the world's ships.

Two things make this domain distinct.

(1) It is a FLOW network, not just a population. The constellation gave us a
    fleet tier (emergent population risk). Shipping extends that to a FLOW tier:
    risk is whether the network can still DELIVER required commodity volumes
    under disruption — rerouting, chokepoint dependency, and CASCADE (closing
    one chokepoint dumps load onto alternates that then congest). Hormuz is the
    sharp case: it has essentially no maritime bypass, so its closure is a HARD
    shortfall, not a reroutable congestion like Suez.

(2) It contains an ADAPTIVE ADVERSARY. A chokepoint closure is a disruption you
    route around. But a state ACTOR using chokepoints as coercion RESPONDS to
    your reroute. That is the one shape none of the prior four domains had:
    risk that depends on the INTERACTION between your mitigation and the
    adversary's counter, so a reroute that looks safe on a static map can be
    self-defeating. [NEW-SHAPE] adaptive_adversary models this explicitly and
    contrasts naive (static) risk with adversary-aware risk.

[BACK-PORT] The flow/cascade tier extends the constellation's fleet tier. The
adaptive-adversary shape, by contrast, has only a NARROW clinical analogue:
antimicrobial stewardship (overuse -> resistance evolves -> your mitigation
degrades the environment's response) is genuinely adversarial; most clinical
risk is not. That narrowness is itself the signal that the cross-domain method
is near its ceiling — broad structural finds are giving way to specialized ones.

Platform reused as-is (incl. the compute probe's second-order trajectory and
reserve-modulated escalation, and the constellation's two-tier pattern).

Single file, no deps. `python3 sealanes_module.py`
NOTE: illustrative indices and a toy adversary model. Architecture demo, not an
intelligence or energy-economics product.
"""

from __future__ import annotations
import hashlib, json, logging, math, copy
from collections import OrderedDict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any, Callable, Tuple

logger = logging.getLogger("SEALANES")

# ============================================================================
# CONTRACTS & CALIBRATION  (identical to OBSERVE / refined platform)
# ============================================================================

class OperationalRegime(Enum):
    STABLE = "stable"; CAUTION = "caution"; WARNING = "warning"; CRITICAL = "critical"


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
        deficit = critical_floor - d["critical"]; d["critical"] = critical_floor
        d["stable"] = max(0.0, d["stable"] - deficit)
    return d


# Each chokepoint has a peacetime baseline (the analogue of age / phase norms).
# war_risk_premium is % of hull value; calm Hormuz ~0.05%, crisis 1-3%+.
CHOKEPOINT_BASELINE = {
    "Hormuz":            {"premium_mean": 0.10, "premium_std": 0.10},
    "Bab_el_Mandeb":     {"premium_mean": 0.15, "premium_std": 0.15},
    "Suez":              {"premium_mean": 0.05, "premium_std": 0.05},
    "Malacca":           {"premium_mean": 0.05, "premium_std": 0.05},
    "Panama":            {"premium_mean": 0.03, "premium_std": 0.03},
    "Gibraltar":         {"premium_mean": 0.02, "premium_std": 0.02},
    "Bosphorus":         {"premium_mean": 0.08, "premium_std": 0.08},
    "Cape_of_Good_Hope": {"premium_mean": 0.02, "premium_std": 0.02},
    "_default":          {"premium_mean": 0.05, "premium_std": 0.05},
}
def baseline(cp): return CHOKEPOINT_BASELINE.get(cp, CHOKEPOINT_BASELINE["_default"])


# ============================================================================
# TIER 1 — PER-CHOKEPOINT STATE + ADAPTERS
# ============================================================================

@dataclass(frozen=True)
class ChokepointState:
    name: str
    timestamp: datetime
    region: str
    status: str                       # "open" | "restricted" | "closed"
    throughput_current: float         # commodity units/day moving now
    throughput_capacity: float        # max units/day
    war_risk_premium_pct: float       # insurance war-risk rate (% hull value)
    military_activity_index: float    # 0..1
    diplomatic_tension_index: float   # 0..1
    recent_incidents: int             # attacks/seizures/mines in window
    controlling_actor: Optional[str] = None  # state actor with coercive leverage
    context: Dict[str, Any] = field(default_factory=dict)


class LaneAdapters:

    @staticmethod
    def heuristic(s: ChokepointState) -> RiskOutput:
        t, score = [], 0.0
        if s.status == "closed":
            t.append(f"HARD_RULE: chokepoint_closed ({s.name})"); score += 0.6
        elif s.status == "restricted":
            t.append(f"RESTRICTED_TRANSIT: {s.name}"); score += 0.3
        if s.war_risk_premium_pct > 1.0:
            t.append(f"HARD_RULE: war_risk_premium_spike ({s.war_risk_premium_pct:.2f}%)"); score += 0.5
        elif s.war_risk_premium_pct > 0.5:
            t.append(f"ELEVATED_PREMIUM: {s.war_risk_premium_pct:.2f}%"); score += 0.2
        if s.recent_incidents >= 3:
            t.append(f"HARD_RULE: incident_cluster ({s.recent_incidents} recent)"); score += 0.5
        elif s.recent_incidents >= 1:
            t.append(f"INCIDENT: {s.recent_incidents} recent"); score += 0.2
        util = s.throughput_current / s.throughput_capacity if s.throughput_capacity > 0 else 0.0
        if util > 0.95:
            t.append(f"AT_CAPACITY: {util*100:.0f}%"); score += 0.2
        return RiskOutput("heuristic", min(score, 1.0), 0.90,
                          regime_distribution(min(score, 1.0)), t, datetime.now(timezone.utc))

    @staticmethod
    def bayesian(s: ChokepointState) -> RiskOutput:
        b = baseline(s.name)
        z = (s.war_risk_premium_pct - b["premium_mean"]) / b["premium_std"]
        t, score = [], 0.0
        if z > 2.0:
            t.append(f"PREMIUM_DEVIATION: {abs(z):.1f} SD above {s.name} baseline")
            score += 0.4 * (1.0 - math.exp(-0.25 * (abs(z) - 2.0)))
        out = RiskOutput("bayesian", min(score, 1.0), 0.85,
                         regime_distribution(min(score, 1.0)), t, datetime.now(timezone.utc))
        out.debug_info = {"z_premium": round(z, 2)}
        return out

    @staticmethod
    def trajectory(s: ChokepointState) -> RiskOutput:
        # premium momentum + second-order escalation accel (reused from compute)
        hist = s.context.get("premium_history") or []
        dt = s.context.get("time_delta_days", 1.0)
        series = list(hist) + [s.war_risk_premium_pct]
        if len(series) < 2 or dt <= 0:
            return RiskOutput("trajectory", 0.0, 0.2, {"stable": 1.0},
                              ["Insufficient history for trajectory analysis"],
                              datetime.now(timezone.utc), abstained=True)
        vel = (series[-1] - series[-2]) / dt
        t, score, debug = [], 0.0, {"premium_vel": round(vel, 3)}
        if vel > 0.1:
            t.append(f"ESCALATING_PREMIUM: +{vel:.2f}%/day"); score += 0.25
            if len(series) >= 3:
                vel_prev = (series[-2] - series[-3]) / dt
                acc = (vel - vel_prev) / dt
                debug["premium_accel"] = round(acc, 3)
                if acc > 0 and vel > 0:
                    t.append(f"ACCELERATING_ESCALATION: +{acc:.2f}%/day^2"); score += 0.3
        out = RiskOutput("trajectory", min(score, 1.0), 0.80,
                         regime_distribution(min(score, 1.0)), t, datetime.now(timezone.utc))
        out.debug_info = debug
        return out

    @staticmethod
    def behavioral(s: ChokepointState) -> RiskOutput:
        t, score, danger = [], 0.0, []
        if s.status == "closed" and s.military_activity_index > 0.6:
            danger.append("BLOCKADE")
            t.append(f"DANGEROUS_PATTERN: blockade ({s.name}, military {s.military_activity_index:.2f})")
            score = max(score, 0.92)
        if s.recent_incidents >= 2 and s.war_risk_premium_pct > 0.4:
            danger.append("TANKER_WAR")
            t.append(f"DANGEROUS_PATTERN: tanker_war ({s.recent_incidents} incidents, premium {s.war_risk_premium_pct:.2f}%)")
            score = max(score, 0.88)
        if s.status == "restricted" and s.diplomatic_tension_index > 0.7 and s.military_activity_index > 0.5:
            danger.append("CREEPING_CLOSURE")
            t.append(f"DANGEROUS_PATTERN: creeping_closure ({s.name})")
            score = max(score, 0.82)
        if not danger:
            return RiskOutput("behavioral", 0.0, 0.78, {"stable": 1.0},
                              ["No named hazard pattern"], datetime.now(timezone.utc), abstained=True)
        return RiskOutput("behavioral", min(score, 1.0), 0.92,
                          regime_distribution(min(score, 1.0)), t, datetime.now(timezone.utc))

    @staticmethod
    def drift(s: ChokepointState) -> RiskOutput:
        hist = s.context.get("tension_history", [])
        if len(hist) < 10:
            return RiskOutput("drift", 0.0, 0.3, {"stable": 1.0},
                              ["Insufficient history for drift detection"],
                              datetime.now(timezone.utc), abstained=True)
        n = len(hist); xs = list(range(n)); mx = sum(xs)/n; my = sum(hist)/n
        denom = sum((x-mx)**2 for x in xs) or 1.0
        slope = sum((xs[i]-mx)*(hist[i]-my) for i in range(n))/denom
        t, score = [], 0.0
        if slope > 0.01:
            t.append(f"REGIONAL_DESTABILIZATION: tension +{slope:.3f}/period sustained"); score += 0.3
        if score < 0.01:
            return RiskOutput("drift", 0.0, 0.5, {"stable": 1.0},
                              ["No sustained drift"], datetime.now(timezone.utc), abstained=True)
        return RiskOutput("drift", min(score, 1.0), 0.75,
                          regime_distribution(min(score, 1.0), critical_floor=0.02),
                          t, datetime.now(timezone.utc))

    @staticmethod
    def adversarial(s: ChokepointState) -> RiskOutput:
        # Data-integrity: AIS spoofing / dark vessels (sanctions evasion, deception).
        t, score = [], 0.0
        dark = s.context.get("dark_vessel_fraction")
        if dark is not None and dark > 0.15:
            t.append(f"DARK_VESSELS: {dark*100:.0f}% transiting with AIS off"); score += 0.3
        spoof = s.context.get("ais_spoof_reports", 0)
        if spoof >= 3:
            t.append(f"AIS_SPOOFING: {spoof} position-spoof reports"); score += 0.3
        if score < 0.01:
            return RiskOutput("adversarial", 0.0, 0.70, {"stable": 1.0},
                              ["No data-integrity anomaly"],
                              datetime.now(timezone.utc), abstained=True)
        return RiskOutput("adversarial", min(score, 1.0), 0.70,
                          regime_distribution(min(score, 1.0)), t, datetime.now(timezone.utc))

    @staticmethod
    def reserve(s: ChokepointState) -> RiskOutput:
        # Buffer for commodities dependent on this chokepoint (days-of-supply).
        dos = s.context.get("days_of_supply")
        if dos is None:
            return RiskOutput("reserve", 0.0, 0.25, {"stable": 1.0},
                              ["No buffer telemetry; engine abstains"],
                              datetime.now(timezone.utc), abstained=True)
        target = s.context.get("target_days_of_supply", 90.0)
        reserve_factor = max(0.0, min(1.0, dos / target)) if target > 0 else 1.0
        t, score = [], 0.0
        if dos < target:
            t.append(f"BUFFER_BELOW_TARGET: {dos:.0f} of {target:.0f} days")
            score += min(0.7, (target - dos) / target)
        debug = {"reserve_factor": round(reserve_factor, 3)}
        if score < 0.01:
            out = RiskOutput("reserve", 0.0, 0.30, {"stable": 1.0},
                             ["Buffer at target; engine abstains"],
                             datetime.now(timezone.utc), abstained=True)
            out.debug_info = debug; return out
        out = RiskOutput("reserve", min(score, 1.0), 0.82,
                         regime_distribution(min(score, 1.0)), t, datetime.now(timezone.utc))
        out.debug_info = debug; return out


# ============================================================================
# FUSION + ESCALATION + AUDIT  (refined platform, unchanged)
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
    def __init__(self): self.entries = []; self.chain_head = "0" * 64
    def append(self, eid, action, data):
        e = {"timestamp": datetime.now(timezone.utc).isoformat(), "entity_id": eid,
             "action": action, "data": copy.deepcopy(data), "previous_hash": self.chain_head}
        h = hashlib.sha256(json.dumps(e, sort_keys=True, default=str).encode()).hexdigest()
        e["immutable_hash"] = h; self.entries.append(e); self.chain_head = h; return h
    def verify_integrity(self):
        prev = "0" * 64
        for e in self.entries:
            if e["previous_hash"] != prev: return False
            tgt = copy.deepcopy(e); stored = tgt.pop("immutable_hash")
            if stored != hashlib.sha256(json.dumps(tgt, sort_keys=True, default=str).encode()).hexdigest():
                return False
            prev = stored
        return True


# ============================================================================
# TIER 1 ORCHESTRATOR — per-chokepoint risk
# ============================================================================

class ChokepointEngine:
    ENGINE_MAP = {
        "heuristic": LaneAdapters.heuristic, "bayesian": LaneAdapters.bayesian,
        "trajectory": LaneAdapters.trajectory, "drift": LaneAdapters.drift,
        "behavioral": LaneAdapters.behavioral, "adversarial": LaneAdapters.adversarial,
        "reserve": LaneAdapters.reserve,
    }
    def __init__(self, ledger): 
        self.audit_ledger = ledger; self._policies = OrderedDict(); self._entropy = OrderedDict()
    def _get_policy(self, cid):
        if cid not in self._policies:
            self._policies[cid] = EscalationPolicy(); self._entropy[cid] = 0.0
        return self._policies[cid]
    def select(self, s):
        eng = ["heuristic", "behavioral", "reserve"]
        if self._entropy.get(s.name, 0.0) > 0.6 or s.context.get("force_heavy"): eng += ["bayesian", "drift"]
        if s.context.get("premium_history"): eng.append("trajectory")
        if s.context.get("tension_history"): eng.append("drift")
        if s.context.get("dark_vessel_fraction") is not None or s.context.get("ais_spoof_reports"): eng.append("adversarial")
        seen, out = set(), []
        for e in eng:
            if e not in seen: seen.add(e); out.append(e)
        return out
    def evaluate(self, s: ChokepointState) -> Verdict:
        policy = self._get_policy(s.name)
        outputs = [self.ENGINE_MAP[n](s) for n in self.select(s)]
        fused, entropy, probs, _ = BayesianFusion.fuse(outputs)
        self._entropy[s.name] = entropy
        candidate = OperationalRegime(max(probs, key=probs.get))
        rf = 1.0
        for o in outputs:
            if o.engine_name == "reserve" and "reserve_factor" in o.debug_info: rf = o.debug_info["reserve_factor"]
        active = [o for o in outputs if not o.abstained]
        det = any(r.startswith("HARD_RULE:") or r.startswith("DANGEROUS_PATTERN:") for o in active for r in o.triggered_rules)
        if det and candidate.value in ("warning", "critical"):
            esc = policy.current_regime.value in ("stable", "caution")
            final = candidate; policy.current_regime = final; policy.pending_regime = None; policy.dwell_count = 0
            if esc: policy.escalation_locked = True; policy.last_escalation_time = s.timestamp
        else:
            final, esc = policy.evaluate(candidate, s.timestamp, rf)
        rules = [r for o in outputs for r in o.triggered_rules]
        conf = sum(o.confidence for o in active)/len(active) if active else 0.0
        v = Verdict(s.name, fused, final, conf, entropy, [o.engine_name for o in active],
                    rules, datetime.now(timezone.utc), escalation_required=esc, reserve_factor=rf)
        v.audit_hash = self.audit_ledger.append(s.name, "chokepoint_assessment",
            {"regime": final.value, "risk": fused, "rules": rules})
        return v


# ============================================================================
# TIER 2 — FLOW NETWORK + FLOW/ADVERSARY ENGINES  [FLEET/FLOW-TIER]
# ============================================================================

@dataclass
class CommodityFlow:
    name: str
    volume: float                      # units/day demanded
    primary: List[str]                 # ordered chokepoints on the main route
    alternatives: List[Dict[str, Any]] # each {"chokepoints":[...], "capacity":float, "delay_days":int}


@dataclass
class NetworkConfig:
    dependency_fraction: float = 0.25   # >this share of total flow on one chokepoint => concentration risk
    operator_capacity: int = 2          # simultaneous major reroutes the system can coordinate


def _open_enough(states: Dict[str, ChokepointState], cps: List[str]) -> bool:
    """A route is usable only if every chokepoint on it is not closed."""
    return all(states[c].status != "closed" for c in cps if c in states)


class FlowAdapters:

    @staticmethod
    def chokepoint_dependency(states, verdicts, flows, cfg) -> RiskOutput:
        total = sum(f.volume for f in flows) or 1.0
        # Volume that has NO alternative avoiding a given chokepoint => hard dependency.
        dep: Dict[str, float] = {}
        for f in flows:
            for c in f.primary:
                avoidable = any(c not in alt["chokepoints"] for alt in f.alternatives)
                if not avoidable:
                    dep[c] = dep.get(c, 0.0) + f.volume
        t, worst = [], 0.0
        for c, vol in sorted(dep.items(), key=lambda kv: -kv[1]):
            frac = vol / total
            if frac >= cfg.dependency_fraction:
                t.append(f"SINGLE_POINT_DEPENDENCY: {c} carries {frac*100:.0f}% of flow with no bypass")
                # Standing structural vulnerability: surface at caution level only;
                # the live engines (shortfall, adversary) drive warning/critical.
                worst = max(worst, min(0.35, 0.20 + 0.20 * frac))
        if not t:
            return RiskOutput("chokepoint_dependency", 0.0, 0.85, {"stable": 1.0},
                              ["No single-point flow dependency"], datetime.now(timezone.utc), abstained=True)
        return RiskOutput("chokepoint_dependency", worst, 0.85, regime_distribution(worst), t, datetime.now(timezone.utc))

    @staticmethod
    def flow_shortfall_and_cascade(states, verdicts, flows, cfg) -> RiskOutput:
        """Route each flow given current statuses. Unmet demand => hard shortfall.
        Rerouted volume => cascade load on the alternate's chokepoints."""
        t, worst = [], 0.0
        cascade_load: Dict[str, float] = {}
        total_shortfall = 0.0
        for f in flows:
            if _open_enough(states, f.primary):
                continue  # primary route works
            # primary blocked: try alternatives in order
            placed = False
            for alt in f.alternatives:
                if _open_enough(states, alt["chokepoints"]) and alt["capacity"] >= f.volume:
                    for c in alt["chokepoints"]:
                        cascade_load[c] = cascade_load.get(c, 0.0) + f.volume
                    t.append(f"REROUTE: {f.name} via {'+'.join(alt['chokepoints'])} (+{alt['delay_days']}d delay)")
                    placed = True
                    break
            if not placed:
                total_shortfall += f.volume
                t.append(f"DANGEROUS_PATTERN: flow_shortfall {f.name} ({f.volume:.1f} units/day undeliverable, no viable reroute)")
                worst = max(worst, 0.92)
        # Cascade: does rerouted load overload the alternate chokepoints?
        for c, load in cascade_load.items():
            if c in states:
                residual = max(0.0, states[c].throughput_capacity - states[c].throughput_current)
                if load > residual:
                    t.append(f"CASCADE_CONGESTION: {c} reroute load {load:.1f} exceeds residual {residual:.1f}")
                    worst = max(worst, 0.6)
        if not t:
            return RiskOutput("flow_shortfall", 0.0, 0.85, {"stable": 1.0},
                              ["All commodity flows delivered on primary routes"],
                              datetime.now(timezone.utc), abstained=True)
        out = RiskOutput("flow_shortfall", worst, 0.85, regime_distribution(worst), t, datetime.now(timezone.utc))
        out.debug_info = {"total_shortfall": round(total_shortfall, 1)}
        return out

    @staticmethod
    def correlated_disruption(states, verdicts, flows, cfg) -> RiskOutput:
        # Multiple chokepoints in the same region hot at once => regional crisis.
        by_region: Dict[str, List[str]] = {}
        for c, v in verdicts.items():
            if v.regime in (OperationalRegime.WARNING, OperationalRegime.CRITICAL):
                by_region.setdefault(states[c].region, []).append(c)
        t, worst = [], 0.0
        for region, cps in by_region.items():
            if len(cps) >= 2:
                t.append(f"REGIONAL_CRISIS: {len(cps)} chokepoints elevated in {region} ({', '.join(cps)})")
                worst = max(worst, min(0.8, 0.4 + 0.2 * len(cps)))
        if not t:
            return RiskOutput("correlated_disruption", 0.0, 0.8, {"stable": 1.0},
                              ["No multi-chokepoint regional crisis"], datetime.now(timezone.utc), abstained=True)
        return RiskOutput("correlated_disruption", worst, 0.8, regime_distribution(worst), t, datetime.now(timezone.utc))

    @staticmethod
    def adaptive_adversary(states, verdicts, flows, cfg) -> RiskOutput:
        """[NEW-SHAPE] Risk that depends on the adversary's RESPONSE to our reroute.

        For each actor that controls >1 chokepoint currently under stress, the
        naive view treats those chokepoints as independent disruptions. The
        adversary-aware view recognizes the actor can act on ALL of them in a
        coordinated way and, crucially, can THREATEN THE REROUTE: if a flow's
        only alternatives run through other chokepoints the same actor controls,
        rerouting is fragile or self-defeating. Output contrasts the two and, if
        they diverge, recommends mitigation that does not feed the adversary."""
        # Map actor -> stressed chokepoints they control.
        actor_cps: Dict[str, List[str]] = {}
        for c, st in states.items():
            if st.controlling_actor and (st.diplomatic_tension_index > 0.5 or st.military_activity_index > 0.5
                                         or st.status != "open"):
                actor_cps.setdefault(st.controlling_actor, []).append(c)

        t, worst = [], 0.0
        for actor, cps in actor_cps.items():
            if len(cps) < 2:
                continue
            controlled = set(cps) | {c for c, st in states.items() if st.controlling_actor == actor}
            # Naive: worst single chokepoint risk among the actor's stressed set.
            naive = max(verdicts[c].risk_score for c in cps)
            # Adversary-aware: do the reroutes for flows hitting these chokepoints
            # also pass through chokepoints the SAME actor controls?
            fragile_flows = []
            for f in flows:
                if not any(c in f.primary for c in cps):
                    continue
                # Does EVERY alternative still touch a chokepoint this actor controls?
                if f.alternatives and all(any(c in controlled for c in alt["chokepoints"]) for alt in f.alternatives):
                    fragile_flows.append(f.name)
                elif not f.alternatives:
                    fragile_flows.append(f.name)
            # Coordinated multi-chokepoint pressure raises the floor above naive.
            aware = min(1.0, naive + 0.15 * (len(cps) - 1) + (0.2 if fragile_flows else 0.0))
            t.append(f"COORDINATED_ADVERSARY: '{actor}' holds {len(cps)} stressed chokepoints "
                     f"({', '.join(cps)}); naive_risk={naive:.2f} adversary_aware_risk={aware:.2f}")
            if fragile_flows:
                t.append(f"FRAGILE_REROUTE: reroutes for {', '.join(fragile_flows)} stay within {actor}'s "
                         f"control -> rerouting is self-defeating; diversify + draw strategic buffers")
                worst = max(worst, min(0.85, aware))
            else:
                worst = max(worst, min(0.7, aware))
        if not t:
            return RiskOutput("adaptive_adversary", 0.0, 0.78, {"stable": 1.0},
                              ["No coordinated multi-chokepoint adversary"],
                              datetime.now(timezone.utc), abstained=True)
        return RiskOutput("adaptive_adversary", worst, 0.78, regime_distribution(worst), t, datetime.now(timezone.utc))


# ============================================================================
# TIER 2 ORCHESTRATOR — network monitor
# ============================================================================

@dataclass
class NetworkVerdict:
    risk_score: float
    regime: OperationalRegime
    escalation_required: bool
    chokepoints_critical: int
    chokepoints_warning: int
    network_rules: List[str]
    per_engine: Dict[str, float]
    reserve_factor: float
    timestamp: datetime
    audit_hash: str = ""


class SeaLaneMonitor:
    FLOW_ENGINES = {
        "chokepoint_dependency": FlowAdapters.chokepoint_dependency,
        "flow_shortfall": FlowAdapters.flow_shortfall_and_cascade,
        "correlated_disruption": FlowAdapters.correlated_disruption,
        "adaptive_adversary": FlowAdapters.adaptive_adversary,
    }
    def __init__(self, flows: List[CommodityFlow], cfg: NetworkConfig = NetworkConfig()):
        self.flows = flows; self.cfg = cfg
        self.audit_ledger = ImmutableAuditLedger()
        self.chokepoint_engine = ChokepointEngine(self.audit_ledger)
        self.network_policy = EscalationPolicy()

    def evaluate(self, chokepoints: List[ChokepointState], timestamp: datetime):
        states = {c.name: c for c in chokepoints}
        verdicts = {c.name: self.chokepoint_engine.evaluate(c) for c in chokepoints}
        outputs = [fn(states, verdicts, self.flows, self.cfg) for fn in self.FLOW_ENGINES.values()]
        fused, entropy, probs, _ = BayesianFusion.fuse(outputs)
        candidate = OperationalRegime(max(probs, key=probs.get))

        # System buffer thinness modulates escalation (reused from compute probe).
        rf = 1.0
        for c in chokepoints:
            dos = c.context.get("days_of_supply"); tgt = c.context.get("target_days_of_supply", 90.0)
            if dos is not None and tgt > 0:
                rf = min(rf, max(0.0, min(1.0, dos / tgt)))

        active = [o for o in outputs if not o.abstained]
        danger = any(r.startswith("DANGEROUS_PATTERN:") for o in active for r in o.triggered_rules)
        if danger and candidate.value in ("warning", "critical"):
            esc = self.network_policy.current_regime.value in ("stable", "caution")
            final = candidate; self.network_policy.current_regime = final
            self.network_policy.pending_regime = None; self.network_policy.dwell_count = 0
            if esc: self.network_policy.escalation_locked = True; self.network_policy.last_escalation_time = timestamp
        else:
            final, esc = self.network_policy.evaluate(candidate, timestamp, rf)

        rules = [r for o in outputs for r in o.triggered_rules
                 if not r.startswith("No ") and "delivered on primary" not in r]
        nv = NetworkVerdict(
            risk_score=fused, regime=final, escalation_required=esc,
            chokepoints_critical=sum(1 for v in verdicts.values() if v.regime == OperationalRegime.CRITICAL),
            chokepoints_warning=sum(1 for v in verdicts.values() if v.regime == OperationalRegime.WARNING),
            network_rules=rules,
            per_engine={o.engine_name: round(o.risk_score, 2) for o in outputs},
            reserve_factor=round(rf, 2), timestamp=datetime.now(timezone.utc))
        nv.audit_hash = self.audit_ledger.append("NETWORK", "network_assessment",
            {"regime": final.value, "risk": fused, "escalation": esc, "rules": rules})
        return nv, verdicts


# ============================================================================
# SMOKE TEST
# ============================================================================

if __name__ == "__main__":
    now = datetime.now(timezone.utc)

    # A small representative oil-flow network.
    FLOWS = [
        # Gulf crude to Asia: must transit Hormuz (NO sea bypass), then Malacca.
        CommodityFlow("Gulf->Asia crude", 18.0, ["Hormuz", "Malacca"],
                      alternatives=[{"chokepoints": ["Hormuz", "Lombok"], "capacity": 18.0, "delay_days": 3}]),
        # Gulf crude to Europe: Hormuz + Bab-el-Mandeb + Suez; can bypass Suez/BAM via Cape (slow).
        CommodityFlow("Gulf->Europe crude", 12.0, ["Hormuz", "Bab_el_Mandeb", "Suez"],
                      alternatives=[{"chokepoints": ["Hormuz", "Cape_of_Good_Hope"], "capacity": 12.0, "delay_days": 14}]),
        # Asia->Europe goods: Bab-el-Mandeb + Suez; bypass via Cape.
        CommodityFlow("Asia->Europe goods", 10.0, ["Bab_el_Mandeb", "Suez"],
                      alternatives=[{"chokepoints": ["Cape_of_Good_Hope"], "capacity": 10.0, "delay_days": 12}]),
    ]

    def cp(name, region, **kw):
        d = dict(timestamp=now, region=region, status="open", throughput_current=10.0,
                 throughput_capacity=20.0, war_risk_premium_pct=0.05, military_activity_index=0.1,
                 diplomatic_tension_index=0.1, recent_incidents=0, controlling_actor=None, context={})
        d.update(kw)
        return ChokepointState(name=name, region=region, **{k: v for k, v in d.items() if k != "region"})

    def calm_world():
        return [
            cp("Hormuz", "Persian_Gulf", controlling_actor="StateA", context={"days_of_supply": 90, "target_days_of_supply": 90}),
            cp("Bab_el_Mandeb", "Red_Sea", controlling_actor="StateA"),
            cp("Suez", "Red_Sea"),
            cp("Malacca", "SE_Asia"),
            cp("Cape_of_Good_Hope", "S_Africa"),
            cp("Lombok", "SE_Asia"),
        ]

    def show(label, nv, vd):
        print(f"\n=== {label} ===")
        print(f"NETWORK: risk={nv.risk_score:.3f} regime={nv.regime.value} escalate={nv.escalation_required} "
              f"reserve={nv.reserve_factor:.2f} | chokepoints crit={nv.chokepoints_critical} warn={nv.chokepoints_warning}")
        print(f"  flow engines: {nv.per_engine}")
        for r in nv.network_rules:
            print(f"   - {r}")

    # 1. Calm world -> stable.
    mon1 = SeaLaneMonitor(FLOWS); w = calm_world()
    show("Calm seas", *mon1.evaluate(w, now))

    # 2. Hormuz CLOSED -> hard shortfall (no sea bypass for Gulf crude).
    mon2 = SeaLaneMonitor(FLOWS); w2 = calm_world()
    w2[0] = cp("Hormuz", "Persian_Gulf", status="closed", military_activity_index=0.8,
               war_risk_premium_pct=2.5, recent_incidents=4, controlling_actor="StateA",
               context={"days_of_supply": 90, "target_days_of_supply": 90})
    show("Hormuz closed (no maritime bypass)", *mon2.evaluate(w2, now))

    # 3. Bab-el-Mandeb + Suez disrupted (Red Sea) -> reroute via Cape (cascade, not shortfall).
    mon3 = SeaLaneMonitor(FLOWS); w3 = calm_world()
    w3[1] = cp("Bab_el_Mandeb", "Red_Sea", status="closed", recent_incidents=3,
               war_risk_premium_pct=1.2, military_activity_index=0.5, controlling_actor="StateA")
    show("Red Sea closed, reroute around the Cape", *mon3.evaluate(w3, now))

    # 4. [NEW-SHAPE] Hormuz AND Bab-el-Mandeb both stressed, SAME actor -> coordinated.
    #    Graded systemic signal, so shown across two frames to commit past dwell.
    mon4 = SeaLaneMonitor(FLOWS); w4 = calm_world()
    w4[0] = cp("Hormuz", "Persian_Gulf", status="restricted", diplomatic_tension_index=0.8,
               military_activity_index=0.7, war_risk_premium_pct=0.9, recent_incidents=1,
               controlling_actor="StateA", context={"days_of_supply": 90, "target_days_of_supply": 90})
    w4[1] = cp("Bab_el_Mandeb", "Red_Sea", status="restricted", diplomatic_tension_index=0.8,
               military_activity_index=0.6, war_risk_premium_pct=0.8, recent_incidents=2,
               controlling_actor="StateA")
    mon4.evaluate(w4, now)                # frame 1: dwell
    show("Coordinated adversary across two chokepoints (frame 2)", *mon4.evaluate(w4, now))

    # 5. Same coordinated pressure, but THIN strategic buffers -> escalates faster.
    mon5 = SeaLaneMonitor(FLOWS); w5 = calm_world()
    w5[0] = cp("Hormuz", "Persian_Gulf", status="restricted", diplomatic_tension_index=0.8,
               military_activity_index=0.7, war_risk_premium_pct=0.9, recent_incidents=1,
               controlling_actor="StateA", context={"days_of_supply": 25, "target_days_of_supply": 90})
    w5[1] = cp("Bab_el_Mandeb", "Red_Sea", status="restricted", diplomatic_tension_index=0.8,
               military_activity_index=0.6, war_risk_premium_pct=0.8, recent_incidents=2,
               controlling_actor="StateA")
    show("Same pressure, thin buffers (25 of 90 days)", *mon5.evaluate(w5, now))

    print(f"\nAudit chain valid: {mon1.audit_ledger.verify_integrity()} "
          f"(calm run, {len(mon1.audit_ledger.entries)} entries)")
