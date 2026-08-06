"""
SENTINEL-SOC Module - Defensive Detection & Triage for Concurrent Intrusions
============================================================================

Sixth domain on the OBSERVE platform. This is a DEFENSIVE security operations
(blue-team) system: it scores risk from security telemetry, separates
concurrent attacker campaigns, and prioritizes incidents for HUMAN responders.

It detects attacker BEHAVIOR at the MITRE ATT&CK tactic level — the things a
SOC observes (auth anomalies, beaconing regularity, lateral fan-out, mass file
modification, egress staging, telemetry gaps). It contains NO exploit code, no
payloads, and no evasion techniques. It recommends; analysts act. Containment
is explicitly human-gated, because auto-isolating hosts can cause outages.

Built to handle TWO simultaneous state-actor (APT) campaigns. That requirement
is three defensive problems, and the platform already has the parts:

  - ATTRIBUTION  -> separate attacker A from attacker B so neither hides in the
                    other's noise. (the constellation's correlated/cluster tier,
                    plus the clinical per-entity isolation that keeps one
                    campaign's state from contaminating another's)
  - CAPACITY     -> two APTs can swamp the SOC; triage under finite analyst /
                    containment capacity. (the constellation capacity engine +
                    reserve-modulated escalation)
  - DIVERSION    -> [NEW-SHAPE use] a LOUD fast campaign can be cover for a QUIET
                    slow one. Pouring all capacity into the loud attack is what
                    the adversary wants. This is interactive risk: the obvious
                    response degrades your ability to catch the real objective.
                    (the shipping adaptive_adversary engine, applied to defense)

Platform reused as-is: deterministic floor, second-order trajectory,
reserve-modulated escalation, two-tier (per-asset + campaign/network), audit.

Single file, no deps. `python3 sentinel_soc_module.py`
NOTE: detections are illustrative behavioral signatures. Real deployment needs
a SIEM/EDR telemetry pipeline and tuning against real attack data. Defensive
demonstration, not a turnkey product.
"""

from __future__ import annotations
import hashlib, json, logging, math, copy
from collections import OrderedDict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any, Callable, Tuple

logger = logging.getLogger("SENTINEL")

# ============================================================================
# CONTRACTS & CALIBRATION  (identical to the refined platform)
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


# What is normal for an asset of this role (the age / phase / orbit analogue).
ROLE_NORMS = {
    "workstation":       {"egress_mb": 50.0,  "lateral": 2.0},
    "domain_controller": {"egress_mb": 30.0,  "lateral": 25.0},  # DCs talk to many hosts
    "db_server":         {"egress_mb": 400.0, "lateral": 5.0},
    "web_server":        {"egress_mb": 800.0, "lateral": 3.0},
    "_default":          {"egress_mb": 100.0, "lateral": 5.0},
}
def role_norms(role): return ROLE_NORMS.get(role, ROLE_NORMS["_default"])


# ============================================================================
# TIER 1 — PER-ASSET STATE + DETECTION ENGINES
# ============================================================================

@dataclass(frozen=True)
class AssetState:
    asset_id: str
    timestamp: datetime
    asset_type: str                 # "host" | "account" | "service"
    role: str
    failed_auth_rate: float         # failed logons / min
    auth_geo_anomaly: float         # 0..1 impossible-travel / new-geo score
    privilege_escalations: int      # priv-esc events in window
    beaconing_score: float          # 0..1 regularity of outbound external comms (C2-like)
    bytes_egress_mb: float          # outbound data this window
    lateral_connections: int        # internal host-to-host connections initiated
    file_modify_rate: float         # files modified / sec (ransomware indicator)
    process_anomaly: float          # 0..1 unusual-process / LOLBin score
    ioc_matches: int                # known-bad indicator hits
    telemetry_gap: float            # 0..1 fraction of expected logs missing
    indicators: Tuple[str, ...] = ()  # observed infra/IOC tags, for attribution
    context: Dict[str, Any] = field(default_factory=dict)


class SecAdapters:

    @staticmethod
    def heuristic(a: AssetState) -> RiskOutput:
        t, score = [], 0.0
        if a.ioc_matches >= 1:
            t.append(f"HARD_RULE: known_bad_indicator ({a.ioc_matches} IOC hit)"); score += 0.55
        if a.file_modify_rate > 50.0:
            t.append(f"HARD_RULE: mass_file_encryption ({a.file_modify_rate:.0f} files/s)"); score += 0.55
        if a.beaconing_score > 0.90:
            t.append(f"HARD_RULE: confirmed_c2_beaconing ({a.beaconing_score:.2f})"); score += 0.5
        if a.privilege_escalations >= 1 and a.role == "domain_controller":
            t.append(f"HARD_RULE: privilege_escalation_on_DC ({a.privilege_escalations})"); score += 0.5
        if a.failed_auth_rate > 30:
            t.append(f"AUTH_BRUTE_FORCE: {a.failed_auth_rate:.0f} fails/min"); score += 0.2
        nm = role_norms(a.role)
        if a.lateral_connections > max(10, nm["lateral"] * 3):
            t.append(f"LATERAL_FANOUT: {a.lateral_connections} internal connections"); score += 0.2
        if a.bytes_egress_mb > nm["egress_mb"] * 3:
            t.append(f"HIGH_EGRESS: {a.bytes_egress_mb:.0f}MB ({a.bytes_egress_mb/nm['egress_mb']:.1f}x role norm)"); score += 0.2
        return RiskOutput("heuristic", min(score, 1.0), 0.90,
                          regime_distribution(min(score, 1.0)), t, datetime.now(timezone.utc))

    @staticmethod
    def bayesian(a: AssetState) -> RiskOutput:
        # UEBA-style: deviation from what this ROLE normally does.
        nm = role_norms(a.role); t, score = [], 0.0
        eg_ratio = a.bytes_egress_mb / nm["egress_mb"] if nm["egress_mb"] > 0 else 0.0
        lat_ratio = a.lateral_connections / nm["lateral"] if nm["lateral"] > 0 else 0.0
        if eg_ratio > 2.0:
            t.append(f"EGRESS_DEVIATION: {eg_ratio:.1f}x role baseline"); score += 0.25 * (1 - math.exp(-(eg_ratio - 2)))
        if lat_ratio > 2.5:
            t.append(f"LATERAL_DEVIATION: {lat_ratio:.1f}x role baseline"); score += 0.25 * (1 - math.exp(-(lat_ratio - 2.5)))
        if a.auth_geo_anomaly > 0.6:
            t.append(f"AUTH_GEO_ANOMALY: {a.auth_geo_anomaly:.2f} (impossible travel / new geo)"); score += 0.2
        out = RiskOutput("bayesian", min(score, 1.0), 0.85,
                         regime_distribution(min(score, 1.0)), t, datetime.now(timezone.utc))
        out.debug_info = {"egress_ratio": round(eg_ratio, 2), "lateral_ratio": round(lat_ratio, 2)}
        return out

    @staticmethod
    def trajectory(a: AssetState) -> RiskOutput:
        hist = a.context.get("activity_history") or []   # list of per-window activity scalars
        dt = a.context.get("time_delta_min", 5.0)
        series = list(hist) + [a.failed_auth_rate + a.lateral_connections + a.bytes_egress_mb / 100.0]
        if len(series) < 2 or dt <= 0:
            return RiskOutput("trajectory", 0.0, 0.2, {"stable": 1.0},
                              ["Insufficient history for trajectory analysis"],
                              datetime.now(timezone.utc), abstained=True)
        vel = (series[-1] - series[-2]) / dt
        t, score, debug = [], 0.0, {"activity_vel": round(vel, 2)}
        if vel > 5.0:
            t.append(f"ESCALATING_ACTIVITY: +{vel:.1f}/min"); score += 0.25
            if len(series) >= 3:
                vp = (series[-2] - series[-3]) / dt; acc = (vel - vp) / dt
                debug["activity_accel"] = round(acc, 2)
                if acc > 0 and vel > 0:
                    t.append(f"ACCELERATING_INTRUSION: +{acc:.1f}/min^2"); score += 0.3
        out = RiskOutput("trajectory", min(score, 1.0), 0.80,
                         regime_distribution(min(score, 1.0)), t, datetime.now(timezone.utc))
        out.debug_info = debug
        return out

    @staticmethod
    def behavioral(a: AssetState) -> RiskOutput:
        # Named ATT&CK-tactic detections (what the SOC looks for). DANGEROUS_PATTERN => floor.
        t, score, danger = [], 0.0, []
        if a.failed_auth_rate > 20 and a.auth_geo_anomaly > 0.5:
            danger.append("CREDENTIAL_ACCESS")
            t.append(f"DANGEROUS_PATTERN: credential_access (brute force then anomalous success)")
            score = max(score, 0.85)
        if a.beaconing_score > 0.7:
            danger.append("C2_BEACONING")
            t.append(f"DANGEROUS_PATTERN: c2_beaconing (regularity {a.beaconing_score:.2f})")
            score = max(score, 0.85)
        nm = role_norms(a.role)
        if a.lateral_connections > max(10, nm["lateral"] * 3) and a.process_anomaly > 0.4:
            danger.append("LATERAL_MOVEMENT")
            t.append(f"DANGEROUS_PATTERN: lateral_movement ({a.lateral_connections} hosts, anomalous process)")
            score = max(score, 0.86)
        if a.bytes_egress_mb > nm["egress_mb"] * 3 and a.process_anomaly > 0.4:
            danger.append("EXFILTRATION")
            t.append(f"DANGEROUS_PATTERN: exfiltration ({a.bytes_egress_mb:.0f}MB staged egress)")
            score = max(score, 0.88)
        if a.file_modify_rate > 50:
            danger.append("RANSOMWARE")
            t.append(f"DANGEROUS_PATTERN: ransomware (mass encryption {a.file_modify_rate:.0f}/s)")
            score = max(score, 0.92)
        if not danger:
            return RiskOutput("behavioral", 0.0, 0.78, {"stable": 1.0},
                              ["No named attack pattern"], datetime.now(timezone.utc), abstained=True)
        out = RiskOutput("behavioral", min(score, 1.0), 0.92,
                         regime_distribution(min(score, 1.0)), t, datetime.now(timezone.utc))
        out.debug_info = {"ttps": danger}
        return out

    @staticmethod
    def drift(a: AssetState) -> RiskOutput:
        # Low-and-slow: an asset whose baseline activity is creeping up over time
        # (APT establishing a new "normal" to evade point-in-time thresholds).
        hist = a.context.get("baseline_history", [])
        if len(hist) < 10:
            return RiskOutput("drift", 0.0, 0.3, {"stable": 1.0},
                              ["Insufficient history for drift detection"],
                              datetime.now(timezone.utc), abstained=True)
        n = len(hist); xs = list(range(n)); mx = sum(xs)/n; my = sum(hist)/n
        denom = sum((x-mx)**2 for x in xs) or 1.0
        slope = sum((xs[i]-mx)*(hist[i]-my) for i in range(n))/denom
        t, score = [], 0.0
        if slope > 0.02:
            t.append(f"LOW_AND_SLOW_DRIFT: baseline creeping +{slope:.3f}/window (possible stealth persistence)")
            score += 0.35
        if score < 0.01:
            return RiskOutput("drift", 0.0, 0.5, {"stable": 1.0},
                              ["No sustained drift"], datetime.now(timezone.utc), abstained=True)
        return RiskOutput("drift", min(score, 1.0), 0.75,
                          regime_distribution(min(score, 1.0), critical_floor=0.02),
                          t, datetime.now(timezone.utc))

    @staticmethod
    def adversarial(a: AssetState) -> RiskOutput:
        # Anti-evasion DETECTION: are we being blinded? In security (unlike a
        # passive sensor fault) ACTIVE tampering is itself a hostile action, so
        # it floors; a passive telemetry gap stays graded.
        t, score, danger = [], 0.0, []
        if a.context.get("edr_tamper") or a.context.get("log_clear_event"):
            danger.append("defense_evasion")
            t.append("DANGEROUS_PATTERN: defense_evasion (active tampering with monitoring)")
            if a.context.get("edr_tamper"):
                t.append("EDR_TAMPER: endpoint agent stopped/modified")
            if a.context.get("log_clear_event"):
                t.append("LOG_CLEARED: security log cleared")
            score = max(score, 0.82)
        if a.telemetry_gap > 0.3:
            t.append(f"TELEMETRY_GAP: {a.telemetry_gap*100:.0f}% of expected logs missing")
            score += 0.4
        if score < 0.01:
            return RiskOutput("adversarial", 0.0, 0.70, {"stable": 1.0},
                              ["No visibility/evasion anomaly"],
                              datetime.now(timezone.utc), abstained=True)
        out = RiskOutput("adversarial", min(score, 1.0), 0.72,
                         regime_distribution(min(score, 1.0)), t, datetime.now(timezone.utc))
        if danger:
            out.debug_info = {"ttps": danger}
        return out

    @staticmethod
    def reserve(a: AssetState) -> RiskOutput:
        # SOC response capacity for this asset's segment (analysts + containment slots).
        cap = a.context.get("soc_capacity_remaining"); tot = a.context.get("soc_capacity_total", 10.0)
        if cap is None:
            return RiskOutput("reserve", 0.0, 0.25, {"stable": 1.0},
                              ["No capacity telemetry; engine abstains"],
                              datetime.now(timezone.utc), abstained=True)
        reserve_factor = max(0.0, min(1.0, cap / tot)) if tot > 0 else 1.0
        t, score = [], 0.0
        if cap < tot:
            t.append(f"SOC_CAPACITY: {cap:.0f} of {tot:.0f} responder slots free")
            score += min(0.5, (tot - cap) / tot * 0.5)
        debug = {"reserve_factor": round(reserve_factor, 3)}
        if score < 0.01:
            out = RiskOutput("reserve", 0.0, 0.30, {"stable": 1.0},
                             ["Full SOC capacity; engine abstains"],
                             datetime.now(timezone.utc), abstained=True)
            out.debug_info = debug; return out
        out = RiskOutput("reserve", min(score, 1.0), 0.80,
                         regime_distribution(min(score, 1.0)), t, datetime.now(timezone.utc))
        out.debug_info = debug; return out


# ============================================================================
# FUSION + POLICY + AUDIT  (refined platform, unchanged)
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
    def __init__(self, dwell_threshold=2, lock_seconds=30.0):
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
# TIER 1 ORCHESTRATOR — per-asset detection (campaign state kept ISOLATED)
# ============================================================================

class AssetEngine:
    ENGINE_MAP = {
        "heuristic": SecAdapters.heuristic, "bayesian": SecAdapters.bayesian,
        "trajectory": SecAdapters.trajectory, "drift": SecAdapters.drift,
        "behavioral": SecAdapters.behavioral, "adversarial": SecAdapters.adversarial,
        "reserve": SecAdapters.reserve,
    }
    def __init__(self, ledger):
        self.audit_ledger = ledger; self._policies = OrderedDict(); self._entropy = OrderedDict()
    def _get_policy(self, aid):
        if aid not in self._policies:
            self._policies[aid] = EscalationPolicy(); self._entropy[aid] = 0.0
        return self._policies[aid]
    def select(self, a):
        eng = ["heuristic", "behavioral", "reserve"]
        if self._entropy.get(a.asset_id, 0.0) > 0.6 or a.context.get("force_heavy"): eng += ["bayesian", "drift"]
        if a.context.get("activity_history"): eng.append("trajectory")
        if a.context.get("baseline_history"): eng.append("drift")
        if a.telemetry_gap > 0 or a.context.get("edr_tamper") or a.context.get("log_clear_event"): eng.append("adversarial")
        eng.append("bayesian")  # UEBA always informative for security
        seen, out = set(), []
        for e in eng:
            if e not in seen: seen.add(e); out.append(e)
        return out
    def evaluate(self, a: AssetState) -> Verdict:
        policy = self._get_policy(a.asset_id)
        outputs = [self.ENGINE_MAP[n](a) for n in self.select(a)]
        fused, entropy, probs, _ = BayesianFusion.fuse(outputs)
        self._entropy[a.asset_id] = entropy
        candidate = OperationalRegime(max(probs, key=probs.get))
        rf = 1.0
        for o in outputs:
            if o.engine_name == "reserve" and "reserve_factor" in o.debug_info: rf = o.debug_info["reserve_factor"]
        active = [o for o in outputs if not o.abstained]
        det = any(r.startswith("HARD_RULE:") or r.startswith("DANGEROUS_PATTERN:") for o in active for r in o.triggered_rules)
        if det and candidate.value in ("warning", "critical"):
            esc = policy.current_regime.value in ("stable", "caution")
            final = candidate; policy.current_regime = final; policy.pending_regime = None; policy.dwell_count = 0
            if esc: policy.escalation_locked = True; policy.last_escalation_time = a.timestamp
        else:
            final, esc = policy.evaluate(candidate, a.timestamp, rf)
        rules = [r for o in outputs for r in o.triggered_rules]
        conf = sum(o.confidence for o in active)/len(active) if active else 0.0
        ttps = []
        for o in outputs:
            if "ttps" in o.debug_info: ttps += o.debug_info["ttps"]
        v = Verdict(a.asset_id, fused, final, conf, entropy, [o.engine_name for o in active],
                    rules, datetime.now(timezone.utc), escalation_required=esc, reserve_factor=rf)
        v.audit_hash = self.audit_ledger.append(a.asset_id, "asset_assessment",
            {"regime": final.value, "risk": fused, "ttps": ttps})
        return v


# ============================================================================
# TIER 2 — CAMPAIGN / NETWORK TIER  (handles the 2 simultaneous attacks)
# ============================================================================

@dataclass
class Campaign:
    campaign_id: str
    members: List[str]
    indicators: List[str]
    ttps: List[str]
    noise: float          # how loud/obvious (volume of detected activity)
    has_objective: bool   # exfiltration / encryption present (a "payoff" action)
    max_risk: float


@dataclass
class SOCConfig:
    responder_capacity: int = 2     # concurrent incidents the SOC can fully work


def _cluster_campaigns(states: Dict[str, AssetState], verdicts: Dict[str, Verdict]) -> List[Campaign]:
    """Group elevated assets into distinct campaigns by SHARED INDICATORS
    (shared attacker infrastructure = same campaign). Union-find over indicators.
    This is what keeps two concurrent attackers from blurring into one."""
    elevated = [aid for aid, v in verdicts.items()
                if v.regime in (OperationalRegime.WARNING, OperationalRegime.CRITICAL)]
    parent = {aid: aid for aid in elevated}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(x, y): parent[find(x)] = find(y)
    # link assets that share any indicator
    by_ind: Dict[str, List[str]] = {}
    for aid in elevated:
        for ind in states[aid].indicators:
            by_ind.setdefault(ind, []).append(aid)
    for ind, ids in by_ind.items():
        for other in ids[1:]:
            union(ids[0], other)
    groups: Dict[str, List[str]] = {}
    for aid in elevated:
        groups.setdefault(find(aid), []).append(aid)

    campaigns = []
    for i, (_, members) in enumerate(sorted(groups.items()), 1):
        inds, ttps = set(), set()
        noise, has_obj, mx = 0.0, False, 0.0
        for aid in members:
            s, v = states[aid], verdicts[aid]
            inds |= set(s.indicators)
            for r in v.triggered_rules:
                if r.startswith("DANGEROUS_PATTERN:"):
                    ttps.add(r.split(":")[1].strip().split()[0])
            # noise = visible volume; objective = exfil or ransomware present
            noise += s.failed_auth_rate + s.lateral_connections + s.file_modify_rate + s.bytes_egress_mb / 50.0
            if any(k in ttps for k in ("exfiltration", "ransomware")):
                has_obj = True
            mx = max(mx, v.risk_score)
        campaigns.append(Campaign(f"Campaign-{i}", sorted(members), sorted(inds),
                                  sorted(ttps), round(noise, 1), has_obj, round(mx, 2)))
    return campaigns


class NetworkAdapters:

    @staticmethod
    def attribution(states, verdicts, campaigns, cfg) -> RiskOutput:
        if not campaigns:
            return RiskOutput("attribution", 0.0, 0.85, {"stable": 1.0},
                              ["No active campaigns"], datetime.now(timezone.utc), abstained=True)
        t = [f"CAMPAIGNS_ACTIVE: {len(campaigns)} distinct"]
        for c in campaigns:
            t.append(f"  {c.campaign_id}: {len(c.members)} hosts, infra={c.indicators}, "
                     f"ttps={c.ttps}, noise={c.noise}, objective={'yes' if c.has_objective else 'no'}")
        worst = max((c.max_risk for c in campaigns), default=0.0)
        if len(campaigns) >= 2:
            t.append(f"MULTIPLE_CONCURRENT_CAMPAIGNS: {len(campaigns)} simultaneous attackers")
            worst = max(worst, min(0.85, 0.45 + 0.2 * (len(campaigns) - 1)))
        # A confirmed-critical campaign is act-now, not dwell-and-wait: propagate
        # the asset-level criticality to a network-tier danger pattern (bypass).
        for c in campaigns:
            if c.max_risk >= 0.75:
                t.append(f"DANGEROUS_PATTERN: active_critical_campaign ({c.campaign_id}, risk {c.max_risk:.2f}, ttps={c.ttps})")
                worst = max(worst, c.max_risk)
        return RiskOutput("attribution", worst, 0.85, regime_distribution(worst), t, datetime.now(timezone.utc))

    @staticmethod
    def lateral_spread(states, verdicts, campaigns, cfg) -> RiskOutput:
        t, worst = [], 0.0
        for c in campaigns:
            if "lateral_movement" in c.ttps and len(c.members) >= 3:
                t.append(f"SPREADING: {c.campaign_id} lateral across {len(c.members)} hosts (blast radius growing)")
                worst = max(worst, min(0.8, 0.5 + 0.1 * len(c.members)))
        if not t:
            return RiskOutput("lateral_spread", 0.0, 0.8, {"stable": 1.0},
                              ["No active lateral spread"], datetime.now(timezone.utc), abstained=True)
        return RiskOutput("lateral_spread", worst, 0.8, regime_distribution(worst), t, datetime.now(timezone.utc))

    @staticmethod
    def capacity_saturation(states, verdicts, campaigns, cfg) -> RiskOutput:
        n = len(campaigns)
        if n <= cfg.responder_capacity:
            return RiskOutput("capacity_saturation", 0.0, 0.85, {"stable": 1.0},
                              ["Incident load within SOC capacity"], datetime.now(timezone.utc), abstained=True)
        over = n - cfg.responder_capacity
        t = [f"DANGEROUS_PATTERN: capacity_saturation ({n} campaigns vs {cfg.responder_capacity} responder slots)",
             "TRIAGE_REQUIRED: at least one campaign will be under-resourced"]
        return RiskOutput("capacity_saturation", min(1.0, 0.6 + 0.1 * over), 0.85,
                          regime_distribution(min(1.0, 0.6 + 0.1 * over)), t, datetime.now(timezone.utc))

    @staticmethod
    def adaptive_adversary(states, verdicts, campaigns, cfg) -> RiskOutput:
        """[NEW-SHAPE] Diversion: a LOUD campaign can be cover for a QUIET one.
        The obvious response (all capacity on the loud attack) is what the
        adversary wants. Interactive risk: the mitigation degrades your ability
        to catch the real objective. Contrasts naive vs adversary-aware."""
        if len(campaigns) < 2:
            return RiskOutput("adaptive_adversary", 0.0, 0.78, {"stable": 1.0},
                              ["Single or no campaign; no diversion structure"],
                              datetime.now(timezone.utc), abstained=True)
        loud = max(campaigns, key=lambda c: c.noise)
        # candidate "real objective": a quieter campaign that nonetheless has a payoff action
        quiet_objective = [c for c in campaigns if c is not loud and c.has_objective and c.noise < loud.noise * 0.6]
        t = []
        # naive: respond to the single highest-risk (often loudest) incident
        naive = max(c.max_risk for c in campaigns)
        aware = naive
        if quiet_objective:
            q = max(quiet_objective, key=lambda c: c.max_risk)
            # The interactive adjustment: the quiet campaign's STRATEGIC priority
            # exceeds its raw detection salience, precisely because the loud
            # campaign is engineered to pull attention off it.
            aware_priority = min(1.0, q.max_risk + 0.2)
            t.append(f"DANGEROUS_PATTERN: diversion_risk — {loud.campaign_id} is loud (noise={loud.noise}) "
                     f"and may be cover for quiet {q.campaign_id} (noise={q.noise}) pursuing {q.ttps}")
            t.append(f"  {q.campaign_id} detection_risk={q.max_risk:.2f} -> adversary_aware_priority={aware_priority:.2f} "
                     f"(likely the true objective; raw ranking under-weights it because the noise pulls attention)")
            t.append(f"  RECOMMEND: reserve responder capacity for {q.campaign_id}; do NOT commit all capacity to "
                     f"{loud.campaign_id}; treat its timing as suspicious")
            return RiskOutput("adaptive_adversary", min(0.9, aware_priority), 0.78,
                              regime_distribution(min(0.9, aware_priority)), t, datetime.now(timezone.utc))
        # two loud campaigns, no clear diversion -> still flag coordination
        t.append(f"CONCURRENT_CAMPAIGNS_NO_CLEAR_DIVERSION: {len(campaigns)} active; monitor for capacity baiting")
        return RiskOutput("adaptive_adversary", min(0.6, 0.4 + 0.1 * len(campaigns)), 0.78,
                          regime_distribution(min(0.6, 0.4 + 0.1 * len(campaigns))), t, datetime.now(timezone.utc))


# ============================================================================
# TIER 2 ORCHESTRATOR — SOC monitor
# ============================================================================

@dataclass
class SOCVerdict:
    risk_score: float
    regime: OperationalRegime
    escalation_required: bool
    n_campaigns: int
    assets_critical: int
    assets_warning: int
    campaigns: List[Campaign]
    network_rules: List[str]
    per_engine: Dict[str, float]
    reserve_factor: float
    timestamp: datetime
    audit_hash: str = ""


class SOCMonitor:
    NET_ENGINES = {
        "attribution": NetworkAdapters.attribution,
        "lateral_spread": NetworkAdapters.lateral_spread,
        "capacity_saturation": NetworkAdapters.capacity_saturation,
        "adaptive_adversary": NetworkAdapters.adaptive_adversary,
    }
    def __init__(self, cfg: SOCConfig = SOCConfig()):
        self.cfg = cfg; self.audit_ledger = ImmutableAuditLedger()
        self.asset_engine = AssetEngine(self.audit_ledger)
        self.network_policy = EscalationPolicy()

    def evaluate(self, assets: List[AssetState], timestamp: datetime):
        states = {a.asset_id: a for a in assets}
        verdicts = {a.asset_id: self.asset_engine.evaluate(a) for a in assets}
        campaigns = _cluster_campaigns(states, verdicts)
        outputs = [fn(states, verdicts, campaigns, self.cfg) for fn in self.NET_ENGINES.values()]
        fused, entropy, probs, _ = BayesianFusion.fuse(outputs)
        candidate = OperationalRegime(max(probs, key=probs.get))

        rf = 1.0
        for a in assets:
            cap = a.context.get("soc_capacity_remaining"); tot = a.context.get("soc_capacity_total", 10.0)
            if cap is not None and tot > 0:
                rf = min(rf, max(0.0, min(1.0, cap / tot)))

        active = [o for o in outputs if not o.abstained]
        danger = any(r.startswith("DANGEROUS_PATTERN:") for o in active for r in o.triggered_rules)
        if danger and candidate.value in ("warning", "critical"):
            esc = self.network_policy.current_regime.value in ("stable", "caution")
            final = candidate; self.network_policy.current_regime = final
            self.network_policy.pending_regime = None; self.network_policy.dwell_count = 0
            if esc: self.network_policy.escalation_locked = True; self.network_policy.last_escalation_time = timestamp
        else:
            final, esc = self.network_policy.evaluate(candidate, timestamp, rf)

        rules = [r for o in outputs for r in o.triggered_rules if not r.startswith("No ") and "within SOC" not in r]
        sv = SOCVerdict(
            risk_score=fused, regime=final, escalation_required=esc, n_campaigns=len(campaigns),
            assets_critical=sum(1 for v in verdicts.values() if v.regime == OperationalRegime.CRITICAL),
            assets_warning=sum(1 for v in verdicts.values() if v.regime == OperationalRegime.WARNING),
            campaigns=campaigns, network_rules=rules,
            per_engine={o.engine_name: round(o.risk_score, 2) for o in outputs},
            reserve_factor=round(rf, 2), timestamp=datetime.now(timezone.utc))
        sv.audit_hash = self.audit_ledger.append("SOC", "soc_assessment",
            {"regime": final.value, "risk": fused, "campaigns": len(campaigns), "rules": rules})
        return sv, verdicts


# ============================================================================
# SMOKE TEST
# ============================================================================

if __name__ == "__main__":
    now = datetime.now(timezone.utc)

    def asset(aid, role, **kw):
        d = dict(timestamp=now, asset_type="host", role=role, failed_auth_rate=1.0, auth_geo_anomaly=0.0,
                 privilege_escalations=0, beaconing_score=0.05, bytes_egress_mb=20.0, lateral_connections=1,
                 file_modify_rate=1.0, process_anomaly=0.05, ioc_matches=0, telemetry_gap=0.0,
                 indicators=(), context={"soc_capacity_remaining": 8, "soc_capacity_total": 10})
        d.update(kw)
        return AssetState(asset_id=aid, **d)

    def show(label, sv, vd):
        print(f"\n=== {label} ===")
        print(f"SOC: risk={sv.risk_score:.3f} regime={sv.regime.value} escalate={sv.escalation_required} "
              f"reserve={sv.reserve_factor:.2f} | campaigns={sv.n_campaigns} "
              f"assets crit={sv.assets_critical} warn={sv.assets_warning}")
        print(f"  network engines: {sv.per_engine}")
        for r in sv.network_rules:
            print(f"   - {r}")

    # 1. Clean network -> stable.
    clean = [asset(f"WS{i}", "workstation") for i in range(5)] + [asset("DC1", "domain_controller", lateral_connections=18)]
    show("Clean network", *SOCMonitor().evaluate(clean, now))

    # 2. Single APT campaign (credential access -> lateral -> C2), shared infra "apt-X".
    single = [asset(f"WS{i}", "workstation") for i in range(3)]
    single += [
        asset("WS-A", "workstation", failed_auth_rate=40, auth_geo_anomaly=0.8, indicators=("c2:apt-X",)),
        asset("WS-B", "workstation", lateral_connections=22, process_anomaly=0.6, indicators=("c2:apt-X",)),
        asset("WS-C", "workstation", beaconing_score=0.85, indicators=("c2:apt-X",), lateral_connections=15),
    ]
    show("Single APT campaign", *SOCMonitor().evaluate(single, now))

    # 3. Ransomware on one host -> that host critical, blast radius noted.
    rw = [asset(f"WS{i}", "workstation") for i in range(4)]
    rw.append(asset("FS1", "db_server", file_modify_rate=300, ioc_matches=1, indicators=("ransom:lockbit-y",)))
    show("Ransomware detonation", *SOCMonitor().evaluate(rw, now))

    # 4. *** TWO SIMULTANEOUS STATE CAMPAIGNS, one a DIVERSION for the other ***
    two = [asset(f"WS{i}", "workstation") for i in range(2)]
    # Campaign A (LOUD): noisy brute force + lateral + ransomware on segment A (infra apt-A)
    two += [
        asset("A-1", "workstation", failed_auth_rate=60, auth_geo_anomaly=0.9, indicators=("c2:apt-A",)),
        asset("A-2", "workstation", lateral_connections=30, process_anomaly=0.7, indicators=("c2:apt-A",)),
        asset("A-3", "db_server", file_modify_rate=200, ioc_matches=1, indicators=("c2:apt-A",)),
    ]
    # Campaign B (QUIET): low-and-slow exfil staging on a DB server (infra apt-B), some log tampering
    two += [
        asset("B-1", "db_server", bytes_egress_mb=1600, process_anomaly=0.5, beaconing_score=0.55,
              telemetry_gap=0.35, indicators=("c2:apt-B",)),
        asset("B-2", "db_server", bytes_egress_mb=1400, process_anomaly=0.45, indicators=("c2:apt-B",)),
    ]
    mon4 = SOCMonitor()
    sv4, vd4 = mon4.evaluate(two, now)
    show("TWO simultaneous campaigns (A loud, B quiet exfil)", sv4, vd4)
    print("  campaign detail:")
    for c in sv4.campaigns:
        print(f"    {c.campaign_id}: hosts={c.members} ttps={c.ttps} noise={c.noise} objective={c.has_objective}")

    # 5. Telemetry tampering -> defenders being blinded, treated as suspicious.
    blind = [asset(f"WS{i}", "workstation") for i in range(4)]
    blind.append(asset("WS-X", "workstation", telemetry_gap=0.5, indicators=("c2:apt-Z",),
                       context={"edr_tamper": True, "log_clear_event": True,
                                "soc_capacity_remaining": 8, "soc_capacity_total": 10}))
    show("Telemetry tampering (being blinded)", *SOCMonitor().evaluate(blind, now))

    # 6. Same two-campaign load, but THIN SOC capacity -> escalates harder (reserve mod).
    thin = []
    for a in two:
        thin.append(AssetState(**{**asdict(a), "context": {**a.context, "soc_capacity_remaining": 2, "soc_capacity_total": 10}}))
    show("Two campaigns, thin SOC capacity (2 of 10)", *SOCMonitor(SOCConfig(responder_capacity=2)).evaluate(thin, now))

    _m = SOCMonitor(); _m.evaluate(clean, now)
    print(f"\nAudit chain integrity (clean run): {_m.audit_ledger.verify_integrity()} ({len(_m.audit_ledger.entries)} entries)")
