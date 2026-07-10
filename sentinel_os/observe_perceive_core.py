"""
OBSERVE/PERCEIVE Core - Iceberg perception layer

OBSERVE: Detects state transitions, friction events, emotional dynamics
PERCEIVE: Infers outcomes, predicts next states, tracks world dynamics
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from enum import Enum

class CallOutcome(Enum):
    RESOLVED = "resolved"
    ABANDONED = "abandoned"
    ESCALATED = "escalated"
    IN_PROGRESS = "in_progress"

@dataclass
class FrictionEvent:
    node: str
    type: str  # "repeat", "long_wait", "denial", "transfer"
    severity: float  # 0-1
    timestamp: float

@dataclass
class EmotionalState:
    frustration: float  # 0-1
    patience: float  # 0-1, decreases as wait increases
    trust: float  # 0-1, decreases on denials/repeats
    
    def deteriorating(self) -> bool:
        return self.frustration > 0.7 or self.patience < 0.2

@dataclass
class CallPercept:
    caller_id: str
    journey: List[str]  # Sequence of nodes visited
    friction_events: List[FrictionEvent]
    emotional_state: EmotionalState
    outcome: CallOutcome
    abandonment_risk: float  # 0-1 probability
    next_action_distribution: Dict[str, float]  # node -> probability

class ObserveCore:
    """Detects friction and emotional dynamics during call"""
    
    def __init__(self):
        self.visited_nodes = {}  # caller_id -> [nodes...]
        self.wait_times = {}  # node -> total_wait
        self.repeats = {}  # caller_id -> {node -> count}
        
    def observe_transition(self, caller_id: str, from_node: str, to_node: str, 
                          wait_time: float) -> FrictionEvent:
        """Detect friction when caller moves to new node"""
        
        if caller_id not in self.visited_nodes:
            self.visited_nodes[caller_id] = []
        
        self.visited_nodes[caller_id].append(to_node)
        
        # Detect repeat (went to same queue twice)
        if to_node in self.visited_nodes[caller_id][:-1]:
            if caller_id not in self.repeats:
                self.repeats[caller_id] = {}
            self.repeats[caller_id][to_node] = self.repeats[caller_id].get(to_node, 0) + 1
            
            return FrictionEvent(
                node=to_node,
                type="repeat",
                severity=min(0.3 * self.repeats[caller_id][to_node], 1.0),
                timestamp=0.0
            )
        
        # Detect long wait
        if wait_time > 30.0:
            return FrictionEvent(
                node=to_node,
                type="long_wait",
                severity=min((wait_time - 30) / 60, 1.0),
                timestamp=0.0
            )
        
        return None
    
    def get_emotional_state(self, caller_id: str, friction_events: List[FrictionEvent],
                           elapsed_time: float) -> EmotionalState:
        """Infer emotional state from friction and time"""
        
        # Base state
        frustration = 0.0
        patience = 1.0
        trust = 1.0
        
        # Accumulate friction
        for event in friction_events:
            if event.type == "repeat":
                frustration += 0.2
                trust -= 0.15
            elif event.type == "long_wait":
                patience -= event.severity * 0.3
                frustration += event.severity * 0.2
            elif event.type == "denial":
                frustration += 0.3
                trust -= 0.2
        
        # Time decay on patience
        patience -= elapsed_time / 300  # Lose 1% patience per 3 seconds
        
        return EmotionalState(
            frustration=min(frustration, 1.0),
            patience=max(patience, 0.0),
            trust=max(trust, 0.0)
        )

class PerceiveCore:
    """Infers outcomes and predicts next states"""
    
    RESOLUTION_NODES = frozenset({"agent_a", "agent_b", "agent_c", "agent_d", 
                                  "agent_e", "agent_f", "agent_g"})
    ESCALATION_NODES = frozenset({"human_escalation"})
    
    def infer_outcome(self, journey: List[str], emotional_state: EmotionalState,
                     final_node: str) -> CallOutcome:
        """Determine call outcome from journey and state"""
        
        # Terminal outcome
        if final_node == "exit":
            if emotional_state.frustration > 0.7:
                return CallOutcome.ABANDONED
            elif final_node in self.ESCALATION_NODES:
                return CallOutcome.ESCALATED
            else:
                return CallOutcome.IN_PROGRESS
        
        # Check if resolved
        if any(node in self.RESOLUTION_NODES for node in journey):
            return CallOutcome.RESOLVED
        
        # Check if escalated
        if any(node in self.ESCALATION_NODES for node in journey):
            return CallOutcome.ESCALATED
        
        return CallOutcome.IN_PROGRESS
    
    def predict_abandonment_risk(self, emotional_state: EmotionalState,
                                 wait_time_remaining: float) -> float:
        """Predict likelihood of abandonment given current state"""
        
        # Risk increases with frustration, decreases with patience
        base_risk = emotional_state.frustration * 0.7 - emotional_state.patience * 0.4
        
        # High remaining wait increases risk
        wait_risk = min(wait_time_remaining / 120, 0.5)
        
        total_risk = base_risk + wait_risk
        return min(max(total_risk, 0.0), 1.0)
    
    def predict_next_action(self, current_node: str, caller_intent: str,
                           emotional_state: EmotionalState,
                           available_nodes: List[str]) -> Dict[str, float]:
        """Predict distribution over next nodes caller might go to"""
        
        if not available_nodes:
            return {}
        
        dist = {}
        base_prob = 1.0 / len(available_nodes)
        
        for node in available_nodes:
            prob = base_prob
            
            # If caller is deteriorating, increase exit probability
            if emotional_state.deteriorating() and node == "exit":
                prob *= 2.0
            
            # Resolution nodes attractive when frustrated
            if node in self.RESOLUTION_NODES and emotional_state.frustration > 0.5:
                prob *= 1.5
            
            dist[node] = prob
        
        # Normalize
        total = sum(dist.values())
        return {k: v/total for k, v in dist.items()}

def synthesize_percept(caller_id: str, journey: List[str], friction_events: List[FrictionEvent],
                       emotional_state: EmotionalState, final_node: str,
                       wait_time_remaining: float, available_next: List[str]) -> CallPercept:
    """Combine OBSERVE + PERCEIVE into unified percept"""
    
    perceive = PerceiveCore()
    
    outcome = perceive.infer_outcome(journey, emotional_state, final_node)
    abandonment_risk = perceive.predict_abandonment_risk(emotional_state, wait_time_remaining)
    next_actions = perceive.predict_next_action(final_node, "unknown", emotional_state, available_next)
    
    return CallPercept(
        caller_id=caller_id,
        journey=journey,
        friction_events=friction_events,
        emotional_state=emotional_state,
        outcome=outcome,
        abandonment_risk=abandonment_risk,
        next_action_distribution=next_actions
    )
