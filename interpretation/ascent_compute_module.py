"""
ASCENT COMPUTE-LOAD Module - Plug-n-Play Adapter Set for a Launching Shuttle
============================================================================

Same OBSERVE platform again. The clinical/driving adapters are swapped for
adapters that manage the COMPUTE LOAD of the flight-control system during
ascent — the redundant flight computers, the real-time scheduler, the data
bus, memory, and the voting/redundancy state.

UNCHANGED (the platform):
  - the RiskOutput contract
  - regime_distribution() calibration (same thresholds)
  - BayesianFusion.fuse() with abstain-exclusion + the DETERMINISTIC floor
    (hard rule OR named pattern) that the driving build surfaced
  - the SHA-256 audit chain

This domain forced two things the other two domains did not, and BOTH look
like genuine platform refinements worth back-porting (flagged inline as
[BACK-PORT]):

  1. SECOND-ORDER dynamics. A compute-overload collapse is self-reinforcing:
     missed deadlines trigger retries that raise load that miss more deadlines.
     First-order momentum (all the trajectory engines do today) cannot tell a
     steady climb from an accelerating spiral. This module adds an acceleration
     term, and behavioral uses it to name a SATURATION_COLLAPSE. The clinical
     analogue is refractory/accelerating decompensation — same shape.

  2. RESERVE-MODULATED escalation. Redundancy depletion ("2 of 4 voting
     computers left") is a risk EVEN WHEN every deadline is currently being
     met — you are one failure from losing the vote. More importantly, when
     your safety margin is thin you should escalate on WEAKER evidence, because
     you have less room to be wrong. So the EscalationPolicy here takes a
     reserve_factor that shrinks the dwell when reserve is depleted. This
     generalizes: physiological reserve (clinical) and ODD margin (driving)
     should modulate escalation the same way.

Single-file, no third-party deps. Run it: `python3 ascent_compute_module.py`

NOTE: illustrative thresholds and phase envelopes. This is an architecture
demonstration, NOT certified flight software.
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

logger = logging.getLogger("ASCENT")
CONTROL_CYCLE_MS = 40.0  # 25 Hz flight-control loop; deadline margin is slack within it

# ============================================================================
# ENUMS & DATA CONTRACTS  (identical shape to OBSERVE)
# ============================================================================

class OperationalRegime(Enum):
    STABLE = "stable"      # compute load nominal
    CAUTION = "caution"    # headroom shrinking, worth watching
    WARNING = "warning"    # shed load / failover soon
    CRITICAL = "critical"  # deadlines missed or about to be; act now


@dataclass(frozen=True)
class ComputeState:
    """The 'vitals snapshot' of the flight-compute system at one cycle."""
    system_id: str
    timestamp: datetime
    phase: str                       # prelaunch, liftoff, first_stage, max_q, srb_sep, ...
    cpu_utilization: float           # 0..1
    memory_utilization: float        # 0..1
    scheduler_latency_ms: float      # how late the top-priority dispatch is
    deadline_margin_ms: float        # slack before next hard deadline (negative = MISSED)
    task_queue_depth: int            # ready tasks waiting
    io_bus_utilization: float        # 0..1 (e.g. MIL-STD-1553 traffic)
    # Optional redundancy / fault-tolerance telemetry (None => not reported).
    active_channels: Optional[int] = None     # healthy redundant flight computers
    total_channels: Optional[int] = None      # nominal redundancy (e.g. 4)
    voting_disagreements: Optional[int] = None # channels diverging in the vote this cycle
    watchdog_resets: Optional[int] = None      # recent watchdog timer resets
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskOutput:
    """IDENTICAL contract. abstained=True => excluded from fusion."""
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
    reserve_factor: float = 1.0  # 1.0 = full margin; <1.0 = depleted (tightened escalation)


# ============================================================================
# CALIBRATION  (IDENTICAL to OBSERVE)
# ============================================================================

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


# Per-phase expected compute envelope (the analogue of PEDIATRIC_NORMS / ROAD_NORMS).
# 80% CPU at max-Q is NORMAL; 80% CPU during coast is not. Context-relative, like age.
PHASE_NORMS = {
    "prelaunch":    {"cpu_mean": 0.30, "cpu_std": 0.10},
    "liftoff":      {"cpu_mean": 0.70, "cpu_std": 0.10},
    "first_stage":  {"cpu_mean": 0.65, "cpu_std": 0.10},
    "max_q":        {"cpu_mean": 0.80, "cpu_std": 0.08},
    "srb_sep":      {"cpu_mean": 0.75, "cpu_std": 0.10},
    "second_stage": {"cpu_mean": 0.55, "cpu_std": 0.10},
    "meco":         {"cpu_mean": 0.45, "cpu_std": 0.10},
    "generic":      {"cpu_mean": 0.60, "cpu_std": 0.15},
}


def get_phase_norms(phase: Optional[str]) -> Dict[str, float]:
    return PHASE_NORMS.get(phase or "generic", PHASE_NORMS["generic"])


def _load_kinematics(state: ComputeState):
    """
    Return (queue_vel, queue_accel, latency_vel, latency_accel) per second, or
    Nones where history is insufficient. Velocity needs >=1 prior sample;
    acceleration (the new second-order term) needs >=2.

    Expects context['history'] = oldest->newest list of dicts with keys
    'task_queue_depth' and 'scheduler_latency_ms'.
    """
    dt = state.context.get("time_delta_seconds", CONTROL_CYCLE_MS / 1000.0)
    hist = state.context.get("history") or []
    series = [(h.get("task_queue_depth"), h.get("scheduler_latency_ms")) for h in hist]
    series.append((state.task_queue_depth, state.scheduler_latency_ms))
    if len(series) < 2 or dt <= 0:
        return None, None, None, None
    (q1, l1), (q2, l2) = series[-2], series[-1]
    q_vel, l_vel = (q2 - q1) / dt, (l2 - l1) / dt
    q_acc = l_acc = None
    if len(series) >= 3:
        (q0, l0) = series[-3]
        q_vel_prev, l_vel_prev = (q1 - q0) / dt, (l1 - l0) / dt
        q_acc = (q_vel - q_vel_prev) / dt
        l_acc = (l_vel - l_vel_prev) / dt
    return q_vel, q_acc, l_vel, l_acc


# ============================================================================
# COMPUTE-LOAD RISK ADAPTERS  (the only domain-specific code)
# ============================================================================

class ComputeAdapters:

    # ------------------------------------------------------------------
    # 1. HEURISTIC — hard real-time thresholds. Always runs.
    # ------------------------------------------------------------------
    @staticmethod
    def heuristic(state: ComputeState) -> RiskOutput:
        triggered: List[str] = []
        score = 0.0

        # Hard deadline: a missed real-time deadline is the cardinal failure.
        if state.deadline_margin_ms <= 0.0:
            triggered.append(f"HARD_RULE: deadline_missed ({state.deadline_margin_ms:.1f}ms)")
            score += 0.6
        elif state.deadline_margin_ms < 0.1 * CONTROL_CYCLE_MS:
            triggered.append(f"WARNING_DEADLINE: {state.deadline_margin_ms:.1f}ms slack (<10% of cycle)")
            score += 0.3

        # CPU saturation.
        if state.cpu_utilization > 0.97:
            triggered.append(f"HARD_RULE: cpu_saturated ({state.cpu_utilization*100:.0f}%)")
            score += 0.5
        elif state.cpu_utilization > 0.90:
            triggered.append(f"HIGH_CPU: {state.cpu_utilization*100:.0f}%")
            score += 0.2

        # Memory exhaustion.
        if state.memory_utilization > 0.95:
            triggered.append(f"HARD_RULE: memory_exhausted ({state.memory_utilization*100:.0f}%)")
            score += 0.5
        elif state.memory_utilization > 0.85:
            triggered.append(f"HIGH_MEMORY: {state.memory_utilization*100:.0f}%")
            score += 0.15

        # Scheduler lag and bus saturation.
        if state.scheduler_latency_ms > 10.0:
            triggered.append(f"SCHEDULER_LAG: {state.scheduler_latency_ms:.1f}ms")
            score += 0.2
        if state.io_bus_utilization > 0.95:
            triggered.append(f"BUS_SATURATED: {state.io_bus_utilization*100:.0f}%")
            score += 0.2

        score = min(score, 1.0)
        have = sum(x is not None for x in [state.active_channels, state.context.get("phase")]) + 1
        confidence = min(0.95, 0.78 + 0.06 * have)
        return RiskOutput("heuristic", score, confidence,
                          regime_distribution(score), triggered,
                          datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # 2. BAYESIAN — deviation from the PHASE-expected compute envelope.
    # ------------------------------------------------------------------
    @staticmethod
    def bayesian(state: ComputeState) -> RiskOutput:
        triggered: List[str] = []
        debug: Dict[str, Any] = {}
        norms = get_phase_norms(state.phase)
        z_cpu = (state.cpu_utilization - norms["cpu_mean"]) / norms["cpu_std"]
        debug["z_cpu"] = round(z_cpu, 3)

        like = 1.0 - math.exp(-0.12 * (max(0.0, z_cpu) ** 2))
        score = 0.0
        if z_cpu > 2.0:
            triggered.append(f"CPU_DEVIATION: {abs(z_cpu):.1f} SD above {state.phase} norm")
            score += 0.4 * like

        score = min(score, 1.0)
        out = RiskOutput("bayesian", score, 0.85,
                         regime_distribution(score), triggered,
                         datetime.now(timezone.utc))
        out.debug_info = debug
        return out

    # ------------------------------------------------------------------
    # 3. TRAJECTORY — momentum AND acceleration. ABSTAINS without history.
    #    [BACK-PORT] The acceleration term is new here. Every other trajectory
    #    engine is first-order only; a self-reinforcing collapse needs the 2nd
    #    derivative to be seen coming.
    # ------------------------------------------------------------------
    @staticmethod
    def trajectory(state: ComputeState) -> RiskOutput:
        q_vel, q_acc, l_vel, l_acc = _load_kinematics(state)
        if q_vel is None:
            return RiskOutput("trajectory", 0.0, 0.2, {"stable": 1.0},
                              ["Insufficient history for trajectory analysis"],
                              datetime.now(timezone.utc), abstained=True)

        triggered: List[str] = []
        debug = {"queue_vel": round(q_vel, 1), "latency_vel": round(l_vel, 1)}
        score = 0.0

        # First-order: queue / latency climbing.
        if q_vel > 50.0:
            triggered.append(f"QUEUE_GROWTH: +{q_vel:.0f} tasks/s")
            score += 0.25
        if l_vel > 100.0:
            triggered.append(f"LATENCY_GROWTH: +{l_vel:.0f} ms/s")
            score += 0.25

        # Second-order: the climb is itself accelerating (spiral precursor).
        if q_acc is not None:
            debug["queue_accel"] = round(q_acc, 1)
            debug["latency_accel"] = round(l_acc, 1)
            if q_vel > 0 and q_acc > 0 and l_vel > 0:
                triggered.append(f"ACCELERATING_LOAD: queue accel +{q_acc:.0f} tasks/s^2 while rising")
                score += 0.3

        score = min(score, 1.0)
        out = RiskOutput("trajectory", score, 0.80,
                         regime_distribution(score), triggered,
                         datetime.now(timezone.utc))
        out.debug_info = debug
        return out

    # ------------------------------------------------------------------
    # 4. BEHAVIORAL — named hazard patterns; emit DANGEROUS_PATTERN (floor).
    #    Includes the new second-order SATURATION_COLLAPSE.
    # ------------------------------------------------------------------
    @staticmethod
    def behavioral(state: ComputeState) -> RiskOutput:
        triggered: List[str] = []
        score = 0.0
        dangerous: List[str] = []
        q_vel, q_acc, l_vel, l_acc = _load_kinematics(state)

        # SATURATION_COLLAPSE: queue rising, accelerating, latency rising => spiral.
        if (q_vel is not None and q_acc is not None
                and q_vel > 0 and q_acc > 0 and l_vel is not None and l_vel > 0):
            dangerous.append("SATURATION_COLLAPSE")
            triggered.append(
                f"DANGEROUS_PATTERN: saturation_collapse "
                f"(queue +{q_vel:.0f}/s accelerating +{q_acc:.0f}/s^2, latency +{l_vel:.0f}ms/s)")
            score = max(score, 0.92)

        # DEADLINE_CASCADE: a deadline already missed with load backing up.
        if state.deadline_margin_ms <= 0.0 and state.task_queue_depth > 12 and state.scheduler_latency_ms > 15.0:
            dangerous.append("DEADLINE_CASCADE")
            triggered.append(
                f"DANGEROUS_PATTERN: deadline_cascade "
                f"(missed, queue {state.task_queue_depth}, latency {state.scheduler_latency_ms:.0f}ms)")
            score = max(score, 0.90)

        # PRIORITY_INVERSION: top task starved despite spare CPU => blocking/lock.
        if state.scheduler_latency_ms > 15.0 and state.cpu_utilization < 0.70:
            dangerous.append("PRIORITY_INVERSION")
            triggered.append(
                f"DANGEROUS_PATTERN: priority_inversion "
                f"(latency {state.scheduler_latency_ms:.0f}ms at only {state.cpu_utilization*100:.0f}% CPU)")
            score = max(score, 0.85)

        # REDUNDANCY_EXHAUSTION: acute — down to a single channel (no vote left).
        if state.active_channels is not None and state.active_channels <= 1:
            dangerous.append("REDUNDANCY_EXHAUSTION")
            triggered.append(f"DANGEROUS_PATTERN: redundancy_exhaustion ({state.active_channels} channel left)")
            score = max(score, 0.88)

        if not dangerous:
            return RiskOutput("behavioral", 0.0, 0.78, {"stable": 1.0},
                              ["No named hazard pattern"],
                              datetime.now(timezone.utc), abstained=True)
        return RiskOutput("behavioral", min(score, 1.0), 0.92,
                          regime_distribution(min(score, 1.0)), triggered,
                          datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # 5. DRIFT — slow creep: memory leak, latency baseline drift.
    #    ABSTAINS without history.
    # ------------------------------------------------------------------
    @staticmethod
    def drift(state: ComputeState) -> RiskOutput:
        mem_hist = state.context.get("memory_history", [])
        if len(mem_hist) < 10:
            return RiskOutput("drift", 0.0, 0.3, {"stable": 1.0},
                              ["Insufficient history for drift detection"],
                              datetime.now(timezone.utc), abstained=True)

        triggered: List[str] = []
        score = 0.0
        # Least-squares slope over the window (memory fraction per sample).
        n = len(mem_hist)
        xs = list(range(n))
        mx = sum(xs) / n
        my = sum(mem_hist) / n
        denom = sum((x - mx) ** 2 for x in xs) or 1.0
        slope = sum((xs[i] - mx) * (mem_hist[i] - my) for i in range(n)) / denom

        if slope > 0.005:  # memory rising steadily across the window
            triggered.append(f"MEMORY_LEAK: +{slope*100:.2f}%/sample sustained")
            score += 0.3
            # Heading toward exhaustion AND already high => sharper.
            if state.memory_utilization > 0.85:
                triggered.append(f"LEAK_NEAR_LIMIT: {state.memory_utilization*100:.0f}% and climbing")
                score += 0.2

        score = min(score, 1.0)
        if score < 0.01:
            return RiskOutput("drift", 0.0, 0.5, {"stable": 1.0},
                              ["No sustained drift"], datetime.now(timezone.utc), abstained=True)
        conf = 0.85 if n >= 50 else 0.70
        return RiskOutput("drift", score, conf,
                          regime_distribution(score, critical_floor=0.02),
                          triggered, datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # 6. ADVERSARIAL — telemetry faults in the MONITORING, not real overload.
    # ------------------------------------------------------------------
    @staticmethod
    def adversarial(state: ComputeState) -> RiskOutput:
        triggered: List[str] = []
        score = 0.0

        recent = state.context.get("recent_cpu_readings", [])
        if len(recent) >= 5 and len(set(recent[-5:])) == 1 and state.cpu_utilization > 0.0:
            triggered.append(f"STUCK_TELEMETRY: {len(recent[-5:])} identical CPU readings")
            score += 0.3

        prev = state.context.get("previous") or {}
        if "cpu_utilization" in prev:
            jump = abs(state.cpu_utilization - prev["cpu_utilization"])
            if jump > 0.5:  # >50% CPU step in one 40ms cycle is not physical
                triggered.append(f"IMPLAUSIBLE_JUMP: CPU moved {jump*100:.0f}% in one cycle")
                score += 0.35

        skew = state.context.get("channel_clock_skew_ms")
        if skew is not None and abs(skew) > 5.0:
            triggered.append(f"CLOCK_SKEW: {skew:.1f}ms across channels")
            score += 0.25

        if not (0.0 <= state.cpu_utilization <= 1.0):
            triggered.append(f"OUT_OF_RANGE_CPU: {state.cpu_utilization}")
            score += 0.3

        score = min(score, 1.0)
        if score < 0.01:
            return RiskOutput("adversarial", 0.0, 0.70, {"stable": 1.0},
                              ["No telemetry-fault signature"],
                              datetime.now(timezone.utc), abstained=True)
        return RiskOutput("adversarial", score, 0.70,
                          regime_distribution(score), triggered,
                          datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # 7. REDUNDANCY RESERVE — defense-in-depth margin. Gated on redundancy
    #    telemetry. Can score HIGH while everything else reads nominal, and
    #    exports a reserve_factor that tightens escalation.
    #    [BACK-PORT] generalizes physiological_reserve / ODD-margin: depleted
    #    reserve => escalate on weaker evidence.
    # ------------------------------------------------------------------
    @staticmethod
    def redundancy_reserve(state: ComputeState) -> RiskOutput:
        if state.active_channels is None or state.total_channels is None or state.total_channels <= 0:
            return RiskOutput("redundancy_reserve", 0.0, 0.25, {"stable": 1.0},
                              ["No redundancy telemetry; engine abstains"],
                              datetime.now(timezone.utc), abstained=True)

        triggered: List[str] = []
        active = max(0, state.active_channels)
        total = state.total_channels
        reserve_factor = max(0.0, min(1.0, active / total))
        depletion = 1.0 - reserve_factor
        score = 0.0

        if depletion > 0.0:
            triggered.append(f"REDUNDANCY_DEPLETED: {active}/{total} channels healthy")
            # Quadratic-ish: losing the last channels matters far more than the first.
            score += min(0.7, depletion ** 0.7)

        if state.voting_disagreements:
            triggered.append(f"VOTE_DISAGREEMENT: {state.voting_disagreements} channel(s) diverging")
            score += min(0.3, 0.15 * state.voting_disagreements)

        if state.watchdog_resets:
            triggered.append(f"WATCHDOG_RESETS: {state.watchdog_resets} recent")
            score += min(0.3, 0.15 * state.watchdog_resets)

        score = min(score, 1.0)
        debug = {"reserve_factor": round(reserve_factor, 3), "depletion": round(depletion, 3)}

        if score < 0.01:
            out = RiskOutput("redundancy_reserve", 0.0, 0.30, {"stable": 1.0},
                             ["Full redundancy; engine abstains"],
                             datetime.now(timezone.utc), abstained=True)
            out.debug_info = debug  # still expose reserve_factor=1.0 for the policy
            return out

        out = RiskOutput("redundancy_reserve", score, 0.82,
                         regime_distribution(score), triggered,
                         datetime.now(timezone.utc))
        out.debug_info = debug
        return out


# ============================================================================
# FUSION  (IDENTICAL to the refined fusion: abstain-exclusion + deterministic floor)
# ============================================================================

class BayesianFusion:
    @staticmethod
    def fuse(outputs: List[RiskOutput]):
        if not outputs:
            return 0.0, 0.0, {"stable": 1.0, "caution": 0.0, "warning": 0.0, "critical": 0.0}, "No outputs"

        active = [o for o in outputs if not o.abstained]
        if not active:
            active = list(outputs)

        total_conf = sum(o.confidence for o in active)
        fused_risk = (sum(o.risk_score * o.confidence for o in active) / total_conf
                      if total_conf > 0 else 0.0)

        def _deterministic(o: RiskOutput) -> bool:
            return any(r.startswith("DANGEROUS_PATTERN:") or r.startswith("HARD_RULE:")
                       for r in o.triggered_rules)

        for o in active:
            if _deterministic(o):
                fused_risk = max(fused_risk, o.risk_score)

        regime_probs = regime_distribution(fused_risk)
        entropy = -sum(p * math.log2(p) for p in regime_probs.values() if p > 0)
        names = ", ".join(o.engine_name for o in active)
        return fused_risk, entropy, regime_probs, f"Fused {len(active)} active engines ({names})"


# ============================================================================
# ESCALATION POLICY  (per-system; dwell; bypass) + RESERVE MODULATION [BACK-PORT]
# ============================================================================

class EscalationPolicy:
    def __init__(self, dwell_threshold: int = 2, lock_seconds: float = 5.0):
        self.dwell_threshold = dwell_threshold
        self.lock_seconds = lock_seconds
        self.current_regime = OperationalRegime.STABLE
        self.pending_regime: Optional[OperationalRegime] = None
        self.dwell_count = 0
        self.escalation_locked = False
        self.last_escalation_time: Optional[datetime] = None

    def evaluate(self, new_regime: OperationalRegime, timestamp: datetime,
                 reserve_factor: float = 1.0):
        """
        reserve_factor in (0, 1]: 1.0 reproduces OBSERVE exactly. Below 1.0
        (depleted reserve) shrinks the effective dwell, so escalation fires on
        weaker/shorter evidence when there is less margin to absorb being wrong.
        """
        effective_dwell = max(1, math.ceil(self.dwell_threshold * max(0.05, reserve_factor)))

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

        if self.dwell_count >= effective_dwell:
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
# AUDIT LEDGER  (SHA-256 chain — unchanged)
# ============================================================================

class ImmutableAuditLedger:
    def __init__(self):
        self.entries: List[Dict[str, Any]] = []
        self.chain_head = "0" * 64

    def append(self, system_id: str, action: str, data: Dict[str, Any]) -> str:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "system_id": system_id,
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
# ASCENT COMPUTE ENGINE  (orchestrator — same shape as ObserveClinicalEngine)
# ============================================================================

class AscentComputeEngine:
    ENGINE_MAP: Dict[str, Callable[[ComputeState], RiskOutput]] = {
        "heuristic": ComputeAdapters.heuristic,
        "bayesian": ComputeAdapters.bayesian,
        "trajectory": ComputeAdapters.trajectory,
        "drift": ComputeAdapters.drift,
        "behavioral": ComputeAdapters.behavioral,
        "adversarial": ComputeAdapters.adversarial,
        "redundancy_reserve": ComputeAdapters.redundancy_reserve,
    }

    def __init__(self, max_tracked_systems: int = 1000):
        self.audit_ledger = ImmutableAuditLedger()
        self._policies: "OrderedDict[str, EscalationPolicy]" = OrderedDict()
        self._entropy: "OrderedDict[str, float]" = OrderedDict()
        self.max_tracked_systems = max_tracked_systems

    def _touch(self, sid: str):
        for d in (self._policies, self._entropy):
            if sid in d:
                d.move_to_end(sid)
        while len(self._policies) > self.max_tracked_systems:
            old, _ = self._policies.popitem(last=False)
            self._entropy.pop(old, None)

    def _get_policy(self, sid: str) -> EscalationPolicy:
        if sid not in self._policies:
            self._policies[sid] = EscalationPolicy()
            self._entropy[sid] = 0.0
        self._touch(sid)
        return self._policies[sid]

    def select_engines(self, state: ComputeState) -> List[str]:
        engines = ["heuristic", "behavioral"]
        ent = self._entropy.get(state.system_id, 0.0)
        if ent > 0.6 or state.context.get("force_heavy"):
            engines += ["bayesian", "drift"]
        if state.context.get("history") or state.context.get("previous"):
            engines.append("trajectory")
        if (state.context.get("recent_cpu_readings") or state.context.get("previous")
                or state.context.get("channel_clock_skew_ms") is not None):
            engines.append("adversarial")
        if state.active_channels is not None and state.total_channels is not None:
            engines.append("redundancy_reserve")
        if state.context.get("memory_history"):
            engines.append("drift")
        seen, ordered = set(), []
        for e in engines:
            if e not in seen:
                seen.add(e); ordered.append(e)
        return ordered

    def evaluate(self, state: ComputeState) -> FusedVerdict:
        policy = self._get_policy(state.system_id)
        selected = self.select_engines(state)
        outputs = [self.ENGINE_MAP[name](state) for name in selected]

        fused_risk, entropy, regime_probs, _ = BayesianFusion.fuse(outputs)
        self._entropy[state.system_id] = entropy
        candidate = OperationalRegime(max(regime_probs, key=regime_probs.get))

        # Pull the reserve_factor out of the reserve engine (default 1.0 = full margin).
        reserve_factor = 1.0
        for o in outputs:
            if o.engine_name == "redundancy_reserve" and "reserve_factor" in o.debug_info:
                reserve_factor = o.debug_info["reserve_factor"]

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
            final_regime, escalation = policy.evaluate(candidate, state.timestamp, reserve_factor)

        all_triggered = [r for o in outputs for r in o.triggered_rules]
        avg_conf = sum(o.confidence for o in active) / len(active) if active else 0.0

        verdict = FusedVerdict(
            risk_score=fused_risk, regime=final_regime, confidence=avg_conf,
            entropy=entropy, active_engines=[o.engine_name for o in active],
            triggered_rules=all_triggered, timestamp=datetime.now(timezone.utc),
            escalation_required=escalation, reserve_factor=reserve_factor,
        )
        verdict.audit_hash = self.audit_ledger.append(
            state.system_id, "compute_assessment",
            {
                "state": asdict(state),
                "selected_engines": selected,
                "reserve_factor": reserve_factor,
                "outputs": [{"engine": o.engine_name, "risk": o.risk_score,
                             "confidence": o.confidence, "abstained": o.abstained,
                             "rules": o.triggered_rules} for o in outputs],
                "verdict": {"risk_score": fused_risk, "regime": final_regime.value,
                            "escalation_required": escalation, "entropy": entropy},
            },
        )
        if escalation:
            logger.info(f"ESCALATION system={state.system_id} regime={final_regime.value} "
                        f"risk={fused_risk:.2f} reserve={reserve_factor:.2f}")
        return verdict


# ============================================================================
# SMOKE TEST
# ============================================================================

if __name__ == "__main__":
    engine = AscentComputeEngine()
    now = datetime.now(timezone.utc)

    def show(label, v):
        print(f"\n=== {label} ===")
        print(f"risk={v.risk_score:.3f} regime={v.regime.value} escalate={v.escalation_required} "
              f"reserve={v.reserve_factor:.2f} entropy={v.entropy:.2f}")
        print(f"engines={v.active_engines}")
        print(f"rules={[r for r in v.triggered_rules if 'No ' not in r and 'Insufficient' not in r and 'Full ' not in r]}")

    # 1. Nominal max-Q: high CPU but expected for the phase, deadlines met, 4/4.
    show("Nominal max-Q ascent", engine.evaluate(ComputeState(
        system_id="GPC-nominal", timestamp=now, phase="max_q",
        cpu_utilization=0.80, memory_utilization=0.55, scheduler_latency_ms=3.0,
        deadline_margin_ms=18.0, task_queue_depth=4, io_bus_utilization=0.60,
        active_channels=4, total_channels=4, voting_disagreements=0, watchdog_resets=0)))

    # 2. 88% CPU at max-Q — within the phase envelope, so NOT alarming (phase-relative).
    show("Elevated-but-expected CPU at max-Q", engine.evaluate(ComputeState(
        system_id="GPC-phase", timestamp=now, phase="max_q",
        cpu_utilization=0.88, memory_utilization=0.60, scheduler_latency_ms=4.0,
        deadline_margin_ms=14.0, task_queue_depth=5, io_bus_utilization=0.65,
        active_channels=4, total_channels=4, context={"force_heavy": True})))

    # 3. SATURATION SPIRAL — queue & latency rising AND accelerating (NEW 2nd-order).
    show("Saturation spiral (accelerating)", engine.evaluate(ComputeState(
        system_id="GPC-spiral", timestamp=now, phase="first_stage",
        cpu_utilization=0.93, memory_utilization=0.70, scheduler_latency_ms=18.0,
        deadline_margin_ms=2.0, task_queue_depth=14, io_bus_utilization=0.80,
        active_channels=4, total_channels=4,
        context={"time_delta_seconds": 0.04,
                 "history": [{"task_queue_depth": 4, "scheduler_latency_ms": 5.0},
                             {"task_queue_depth": 7, "scheduler_latency_ms": 9.0}]})))

    # 4. DEADLINE MISSED — hard rule + cascade pattern.
    show("Deadline missed, load backing up", engine.evaluate(ComputeState(
        system_id="GPC-deadline", timestamp=now, phase="srb_sep",
        cpu_utilization=0.96, memory_utilization=0.80, scheduler_latency_ms=30.0,
        deadline_margin_ms=-3.0, task_queue_depth=20, io_bus_utilization=0.90,
        active_channels=4, total_channels=4)))

    # 5a. Borderline-warning load, FULL redundancy (4/4) — dwell holds at frame 1.
    borderline = dict(phase="first_stage", cpu_utilization=0.92, memory_utilization=0.70,
                      scheduler_latency_ms=12.0, deadline_margin_ms=3.5, task_queue_depth=8,
                      io_bus_utilization=0.80)
    show("Borderline load @ 4/4 channels (frame 1)", engine.evaluate(ComputeState(
        system_id="GPC-full", timestamp=now, active_channels=4, total_channels=4, **borderline)))

    # 5b. SAME load, DEPLETED redundancy (2/4) — reserve tightens dwell => escalates frame 1.
    show("Same load @ 2/4 channels (frame 1)", engine.evaluate(ComputeState(
        system_id="GPC-depleted", timestamp=now, active_channels=2, total_channels=4,
        voting_disagreements=1, **borderline)))

    # 6. Telemetry glitch — implausible CPU jump; elevated but NOT real overload.
    show("Telemetry glitch (CPU jump)", engine.evaluate(ComputeState(
        system_id="GPC-glitch", timestamp=now, phase="second_stage",
        cpu_utilization=0.95, memory_utilization=0.55, scheduler_latency_ms=4.0,
        deadline_margin_ms=16.0, task_queue_depth=3, io_bus_utilization=0.55,
        active_channels=4, total_channels=4,
        context={"previous": {"cpu_utilization": 0.30}})))

    # 7. Memory leak — slow sustained creep flagged early (before any hard limit).
    leak = [0.55, 0.58, 0.61, 0.64, 0.67, 0.70, 0.73, 0.76, 0.79, 0.82]
    show("Slow memory leak", engine.evaluate(ComputeState(
        system_id="GPC-leak", timestamp=now, phase="second_stage",
        cpu_utilization=0.58, memory_utilization=0.86, scheduler_latency_ms=4.0,
        deadline_margin_ms=15.0, task_queue_depth=4, io_bus_utilization=0.55,
        active_channels=4, total_channels=4, context={"memory_history": leak})))

    print(f"\nAudit chain valid: {engine.audit_ledger.verify_integrity()} "
          f"({len(engine.audit_ledger.entries)} entries)")
