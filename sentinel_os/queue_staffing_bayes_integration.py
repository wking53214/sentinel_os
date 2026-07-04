"""
Queue/Staffing/Bayes Integration - Operational Response Layer

Connects governance signals → staffing adjustments → queue dynamics → Bayes updates
"""

from dataclasses import dataclass
from typing import Dict, List, Optional
import numpy as np

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
        
        numerator = (traffic_intensity ** agents) / np.math.factorial(agents)
        denominator = numerator
        
        for i in range(agents):
            denominator += (traffic_intensity ** i) / np.math.factorial(i)
        
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
        min_agents = int(np.ceil(traffic_intensity)) + 1
        
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
    """Updates P(resolution | intent) based on call outcomes"""
    
    def __init__(self):
        self.intent_stats = {
            "billing": {"resolved": 0, "total": 0, "avg_handle": 5.0},
            "technical": {"resolved": 0, "total": 0, "avg_handle": 8.0},
            "sales": {"resolved": 0, "total": 0, "avg_handle": 10.0},
            "cancel": {"resolved": 0, "total": 0, "avg_handle": 6.0},
            "upgrade": {"resolved": 0, "total": 0, "avg_handle": 7.0},
            "complaint": {"resolved": 0, "total": 0, "avg_handle": 12.0},
            "general": {"resolved": 0, "total": 0, "avg_handle": 4.0},
        }
    
    def observe_outcome(self, intent: str, resolved: bool, handle_time: float):
        """Update beliefs based on call outcome"""
        
        if intent not in self.intent_stats:
            return
        
        self.intent_stats[intent]["total"] += 1
        if resolved:
            self.intent_stats[intent]["resolved"] += 1
        
        # Update avg handle time (exponential moving average)
        old_avg = self.intent_stats[intent]["avg_handle"]
        self.intent_stats[intent]["avg_handle"] = 0.9 * old_avg + 0.1 * handle_time
    
    def get_posterior(self, intent: str) -> BayesUpdate:
        """Get current belief about intent"""
        
        if intent not in self.intent_stats:
            return BayesUpdate(intent, 0.5, 5.0, 0.0)
        
        stats = self.intent_stats[intent]
        total = stats["total"]
        
        if total == 0:
            success_rate = 0.5
            confidence = 0.0
        else:
            success_rate = stats["resolved"] / total
            confidence = min(total / 100, 1.0)  # Confidence grows to 100 samples
        
        return BayesUpdate(
            intent=intent,
            success_rate=success_rate,
            avg_handling_time=stats["avg_handle"],
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
