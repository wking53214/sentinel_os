"""
Queue/Staffing/Bayes Integration - Operational Response Layer

Connects governance signals → staffing adjustments → queue dynamics → Bayes updates
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import math
import os

import redis

from operational_resilience import setup_logging
from circuit_breaker import CircuitBreaker

logger = setup_logging("BayesianIntentEngine")

# Same env-var convention every other Redis-backed module in this repo
# uses (sentinel_worker.py, twin_shipper.py, queue_schema.py, ...).
# BAYES_REDIS_URL is an override for cases where the Bayes store should
# live on a separate instance from the queue/twin traffic; falls back to
# the shared default like everything else.
_REDIS_URL = os.getenv(
    "BAYES_REDIS_URL", os.getenv("SENTINEL_REDIS_URL", "redis://localhost:6379/0")
)

@dataclass
class QueueState:
    queue_name: str
    waiting_count: int
    current_wait_p90: float
    staffed_agents: int
    abandonment_rate: float

@dataclass
class StaffingAdjustment:
    queue_name: str
    current_agents: int
    recommended_agents: int
    reason: str
    expected_wait_reduction: float

@dataclass
class BayesUpdate:
    intent: str
    success_rate: float  # P(resolution | intent)
    avg_handling_time: float
    confidence: float

class QueueDynamics:
    """Models queue behavior: Erlang C, wait times, abandonment"""
    
    def __init__(self):
        self.erlang_c_cache = {}
    
    def erlang_c(self, agents: int, traffic_intensity: float) -> float:
        """Erlang C formula: prob of waiting"""
        key = (agents, round(traffic_intensity, 2))
        if key in self.erlang_c_cache:
            return self.erlang_c_cache[key]
        
        if agents <= traffic_intensity:
            return 1.0
        
        numerator = (traffic_intensity ** agents) / math.factorial(agents)
        denominator = numerator
        
        for i in range(agents):
            denominator += (traffic_intensity ** i) / math.factorial(i)
        
        pw = numerator / denominator if denominator > 0 else 1.0
        self.erlang_c_cache[key] = pw
        return pw
    
    def predict_wait_time(self, agents: int, traffic_intensity: float, 
                         avg_handle_time: float) -> float:
        """Estimate p90 wait given staffing"""
        pw = self.erlang_c(agents, traffic_intensity)
        
        if pw == 0:
            return 0.0
        
        # Average wait in queue
        aw = (pw * traffic_intensity) / (agents - traffic_intensity) if agents > traffic_intensity else float('inf')
        
        # P90 wait (roughly 2.3x average for exponential)
        p90_wait = aw * avg_handle_time * 2.3
        
        return min(p90_wait, 999.0)
    
    def recommended_agents(self, traffic_intensity: float, target_wait: float,
                          avg_handle_time: float) -> int:
        """Find agent count to meet target wait"""
        
        if traffic_intensity <= 0:
            return 1
        
        # Start with Erlang formula + buffer
        min_agents = int(math.ceil(traffic_intensity)) + 1
        
        for agents in range(min_agents, min_agents + 10):
            predicted_wait = self.predict_wait_time(agents, traffic_intensity, avg_handle_time)
            if predicted_wait <= target_wait:
                return agents
        
        return min_agents + 10

class StaffingCoordinator:
    """Adjusts staffing based on governance signals"""
    
    def __init__(self):
        self.queue_dynamics = QueueDynamics()
        self.current_staffing = {}
    
    def propose_adjustment(self, queue_state: QueueState, 
                          governance_signal: Dict) -> Optional[StaffingAdjustment]:
        """Propose staffing change based on governance drift signal"""
        
        if governance_signal is None:
            return None
        
        # Extract governance recommendation
        healed_expected_wait = governance_signal.get("healed_expected_wait", queue_state.current_wait_p90)
        
        # Estimate traffic
        traffic_intensity = queue_state.waiting_count * 0.3  # Rough estimate
        
        # Find agents needed for healed wait target
        recommended = self.queue_dynamics.recommended_agents(
            traffic_intensity,
            target_wait=healed_expected_wait,
            avg_handle_time=5.0  # Assume 5min avg handle
        )
        
        if recommended == queue_state.staffed_agents:
            return None
        
        expected_reduction = queue_state.current_wait_p90 - healed_expected_wait
        
        return StaffingAdjustment(
            queue_name=queue_state.queue_name,
            current_agents=queue_state.staffed_agents,
            recommended_agents=recommended,
            reason=f"Governance signal: heal {queue_state.queue_name} wait from {queue_state.current_wait_p90:.1f}s to {healed_expected_wait:.1f}s",
            expected_wait_reduction=expected_reduction
        )

class BayesianIntentEngine:
    """Updates P(resolution | intent) based on call outcomes.

    State is durable and shared across worker processes when Redis is
    reachable: every observation is written to a per-intent Redis hash
    (best-effort, never blocking or raising into the caller), and a
    fresh instance hydrates its starting beliefs from whatever the
    other workers have already recorded instead of starting at zero.
    If Redis is unreachable at construction or degrades mid-flight,
    this falls back to the original in-memory-only behavior -- fails
    open, same as every other Redis-backed component in this repo
    (rate_limiter_v2, PostgreSQLLedger via its circuit breaker, etc.).
    """

    _DEFAULTS = {
        "billing": {"resolved": 0, "total": 0, "avg_handle": 5.0},
        "technical": {"resolved": 0, "total": 0, "avg_handle": 8.0},
        "sales": {"resolved": 0, "total": 0, "avg_handle": 10.0},
        "cancel": {"resolved": 0, "total": 0, "avg_handle": 6.0},
        "upgrade": {"resolved": 0, "total": 0, "avg_handle": 7.0},
        "complaint": {"resolved": 0, "total": 0, "avg_handle": 12.0},
        "general": {"resolved": 0, "total": 0, "avg_handle": 4.0},
    }

    def __init__(self, redis_url: Optional[str] = None):
        self.intent_stats = {
            intent: dict(stats) for intent, stats in self._DEFAULTS.items()
        }

        self._redis: Optional["redis.Redis"] = None
        self._breaker = CircuitBreaker(
            name="bayes_redis", failure_threshold=3, reset_timeout_s=15,
        )

        url = redis_url or _REDIS_URL
        try:
            client = redis.Redis.from_url(
                url, socket_timeout=2.0, socket_connect_timeout=2.0,
                decode_responses=True,
            )
            client.ping()
            self._redis = client
            self._hydrate_from_redis()
        except (redis.exceptions.RedisError, OSError) as exc:
            logger.warning(
                f"Bayes: Redis unavailable at startup ({exc}) -- "
                "running in-memory only, beliefs will not survive a restart"
            )
            self._redis = None

    @staticmethod
    def _redis_key(intent: str) -> str:
        return f"bayes:intent:{intent}"

    def _hydrate_from_redis(self) -> None:
        """Pull in whatever the collective belief already is, so a
        freshly started worker doesn't act like nothing has ever been
        learned about any intent."""
        for intent in self.intent_stats:
            raw = self._redis.hgetall(self._redis_key(intent))
            if not raw:
                continue
            total = int(raw.get("total", 0))
            resolved = int(raw.get("resolved", 0))
            handle_sum = float(raw.get("handle_sum", 0.0))
            self.intent_stats[intent]["total"] = total
            self.intent_stats[intent]["resolved"] = resolved
            if total > 0:
                self.intent_stats[intent]["avg_handle"] = handle_sum / total

    def _persist_observation(self, intent: str, resolved: bool, handle_time: float) -> None:
        key = self._redis_key(intent)
        pipe = self._redis.pipeline()
        pipe.hincrby(key, "total", 1)
        if resolved:
            pipe.hincrby(key, "resolved", 1)
        pipe.hincrbyfloat(key, "handle_sum", handle_time)
        pipe.execute()

    def _read_remote_stats(self, intent: str) -> Optional[Tuple[int, int, float]]:
        raw = self._redis.hgetall(self._redis_key(intent))
        if not raw:
            return None
        return (
            int(raw.get("total", 0)),
            int(raw.get("resolved", 0)),
            float(raw.get("handle_sum", 0.0)),
        )

    def observe_outcome(self, intent: str, resolved: bool, handle_time: float):
        """Update beliefs based on call outcome"""

        if intent not in self.intent_stats:
            # Previously a silent no-op -- a caller passing the wrong key
            # (e.g. a queue name instead of an intent label) lost the
            # observation with no trace anywhere. Now it's visible.
            logger.warning(
                f"Bayes: dropped outcome for unmapped intent '{intent}' -- "
                f"no matching entry in intent_stats (known: {sorted(self.intent_stats)})"
            )
            return

        # In-memory update -- always happens, regardless of Redis. This
        # is the only state used when Redis is unavailable, and is
        # exactly the pre-existing behavior (same exponential moving
        # average) so a single-process, no-Redis deployment behaves
        # identically to before this change.
        self.intent_stats[intent]["total"] += 1
        if resolved:
            self.intent_stats[intent]["resolved"] += 1
        old_avg = self.intent_stats[intent]["avg_handle"]
        self.intent_stats[intent]["avg_handle"] = 0.9 * old_avg + 0.1 * handle_time

        if self._redis is not None:
            try:
                self._breaker.call(self._persist_observation, intent, resolved, handle_time)
            except Exception as exc:
                logger.debug(
                    f"Bayes: Redis persistence skipped for this observation "
                    f"('{intent}'): {exc}. In-memory belief still updated."
                )

    def get_posterior(self, intent: str) -> BayesUpdate:
        """Get current belief about intent"""

        if intent not in self.intent_stats:
            return BayesUpdate(intent, 0.5, 5.0, 0.0)

        stats = self.intent_stats[intent]
        total = stats["total"]
        avg_handle = stats["avg_handle"]

        if self._redis is not None:
            try:
                remote = self._breaker.call(self._read_remote_stats, intent)
                if remote is not None:
                    remote_total, remote_resolved, handle_sum = remote
                    total = remote_total
                    stats["total"] = remote_total
                    stats["resolved"] = remote_resolved
                    if remote_total > 0:
                        # Plain running mean of what's actually been
                        # observed across every worker -- not the local
                        # EMA, which (by design) can't be correctly
                        # resumed after a restart or merged across
                        # processes. Only used when Redis is the shared
                        # source of truth; the in-memory EMA above is
                        # untouched and is exactly what's used when
                        # Redis is unavailable.
                        avg_handle = handle_sum / remote_total
                        stats["avg_handle"] = avg_handle
            except Exception as exc:
                logger.debug(f"Bayes: Redis read skipped for '{intent}': {exc}. Using local state.")

        if total == 0:
            success_rate = 0.5
            confidence = 0.0
        else:
            success_rate = stats["resolved"] / total
            confidence = min(total / 100, 1.0)  # Confidence grows to 100 samples

        return BayesUpdate(
            intent=intent,
            success_rate=success_rate,
            avg_handling_time=avg_handle,
            confidence=confidence
        )

def integrate_all_three(queue_states: List[QueueState],
                       governance_signals: Dict,
                       call_outcomes: List[Dict]) -> Dict:
    """Coordinate Queue + Staffing + Bayes"""
    
    coordinator = StaffingCoordinator()
    bayes = BayesianIntentEngine()
    
    # 1. Staffing adjustments from governance
    staffing_changes = []
    for queue_state in queue_states:
        sig = governance_signals.get(queue_state.queue_name)
        adjustment = coordinator.propose_adjustment(queue_state, sig)
        if adjustment:
            staffing_changes.append(adjustment)
    
    # 2. Update Bayes from call outcomes
    for outcome in call_outcomes:
        intent = outcome.get("intent", "general")
        resolved = outcome.get("resolved", False)
        handle_time = outcome.get("handle_time", 5.0)
        bayes.observe_outcome(intent, resolved, handle_time)
    
    # 3. Get current posteriors
    posteriors = {}
    for intent in bayes.intent_stats.keys():
        posteriors[intent] = bayes.get_posterior(intent)
    
    return {
        "staffing_adjustments": staffing_changes,
        "bayesian_posteriors": posteriors,
        "queue_count": len(queue_states),
    }
