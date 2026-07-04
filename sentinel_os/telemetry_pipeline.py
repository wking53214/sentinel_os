"""
Real Telemetry Pipeline - Live metrics flow through governance

Captures call metrics in real-time, feeds drift detection, triggers responses
"""

from dataclasses import dataclass, field
from typing import Dict, List
from collections import defaultdict
import time

@dataclass
class CallMetric:
    timestamp: float
    caller_id: str
    queue: str
    wait_time: float
    resolved: bool
    friction_count: int
    emotional_frustration: float

@dataclass
class TelemetrySnapshot:
    timestamp: float
    metrics_count: int
    waits_by_queue: Dict[str, List[float]]
    resolution_rates: Dict[str, float]
    abandonment_rates: Dict[str, float]
    avg_frustration: float

class RealTelemetryCollector:
    """Collects metrics in real-time during call processing"""
    
    def __init__(self):
        self.metrics: List[CallMetric] = []
        self.start_time = time.time()
    
    def record_call(self, caller_id: str, queue: str, wait_time: float,
                   resolved: bool, friction_count: int, frustration: float):
        """Record one call's metrics"""
        
        metric = CallMetric(
            timestamp=time.time() - self.start_time,
            caller_id=caller_id,
            queue=queue,
            wait_time=wait_time,
            resolved=resolved,
            friction_count=friction_count,
            emotional_frustration=frustration
        )
        self.metrics.append(metric)
    
    def get_snapshot(self) -> TelemetrySnapshot:
        """Create a snapshot of current metrics"""
        
        if not self.metrics:
            return TelemetrySnapshot(
                timestamp=time.time() - self.start_time,
                metrics_count=0,
                waits_by_queue={},
                resolution_rates={},
                abandonment_rates={},
                avg_frustration=0.0
            )
        
        waits = defaultdict(list)
        resolved_count = defaultdict(int)
        total_count = defaultdict(int)
        frustrations = []
        
        for m in self.metrics:
            waits[m.queue].append(m.wait_time)
            total_count[m.queue] += 1
            if m.resolved:
                resolved_count[m.queue] += 1
            frustrations.append(m.emotional_frustration)
        
        resolution_rates = {
            q: resolved_count[q] / total_count[q] if total_count[q] > 0 else 0.0
            for q in total_count.keys()
        }
        
        abandonment_rates = {
            q: 1.0 - resolution_rates[q]
            for q in resolution_rates.keys()
        }
        
        avg_frustration = sum(frustrations) / len(frustrations) if frustrations else 0.0
        
        return TelemetrySnapshot(
            timestamp=time.time() - self.start_time,
            metrics_count=len(self.metrics),
            waits_by_queue=dict(waits),
            resolution_rates=resolution_rates,
            abandonment_rates=abandonment_rates,
            avg_frustration=avg_frustration
        )

class GovernanceReactor:
    """Reacts to telemetry in real-time"""
    
    def __init__(self):
        self.reactions = []
    
    def react_to_snapshot(self, snapshot: TelemetrySnapshot,
                         baseline_waits: Dict[str, float]) -> List[str]:
        """Analyze snapshot and trigger governance responses"""
        
        reactions = []
        
        # Check each queue for drift
        for queue, waits in snapshot.waits_by_queue.items():
            if not waits:
                continue
            
            current_p90 = sorted(waits)[int(len(waits) * 0.9)] if len(waits) >= 10 else max(waits)
            baseline = baseline_waits.get(queue, 20.0)
            
            # Drift detection: 40% threshold
            rel_change = (current_p90 - baseline) / baseline if baseline > 0 else 0
            
            if rel_change > 0.4:
                reactions.append(f"DRIFT: {queue} wait {baseline:.1f}s → {current_p90:.1f}s (+{rel_change*100:.0f}%)")
            
            # High abandonment
            abandon = snapshot.abandonment_rates.get(queue, 0.0)
            if abandon > 0.15:
                reactions.append(f"ABANDON: {queue} abandonment rate {abandon*100:.1f}%")
        
        # Overall frustration
        if snapshot.avg_frustration > 0.6:
            reactions.append(f"FRUSTRATION: Average frustration {snapshot.avg_frustration:.2f}")
        
        self.reactions = reactions
        return reactions

def end_to_end_telemetry_flow(call_sequence: List[Dict],
                             baseline_waits: Dict[str, float]) -> Dict:
    """Simulate complete telemetry → governance → reaction flow"""
    
    collector = RealTelemetryCollector()
    reactor = GovernanceReactor()
    
    # 1. Collect metrics from calls
    for call in call_sequence:
        collector.record_call(
            caller_id=call["caller_id"],
            queue=call["queue"],
            wait_time=call["wait_time"],
            resolved=call["resolved"],
            friction_count=call["friction_count"],
            frustration=call["frustration"]
        )
    
    # 2. Get snapshot
    snapshot = collector.get_snapshot()
    
    # 3. React to snapshot
    reactions = reactor.react_to_snapshot(snapshot, baseline_waits)
    
    return {
        "metrics_collected": snapshot.metrics_count,
        "queues_monitored": len(snapshot.waits_by_queue),
        "avg_resolution_rate": sum(snapshot.resolution_rates.values()) / len(snapshot.resolution_rates) if snapshot.resolution_rates else 0.0,
        "avg_abandonment_rate": sum(snapshot.abandonment_rates.values()) / len(snapshot.abandonment_rates) if snapshot.abandonment_rates else 0.0,
        "avg_frustration": snapshot.avg_frustration,
        "governance_reactions": reactions,
        "reaction_count": len(reactions)
    }
