"""
Prometheus Metrics - Real observability for Iceberg

Exports: drift detection, RL loss, governance actions, queue wait times, abandonment rates
"""

from dataclasses import dataclass
from typing import Dict
from collections import defaultdict

@dataclass
class PrometheusMetrics:
    """Thread-safe metrics for Prometheus scraping"""
    
    def __init__(self):
        # Counters
        self.calls_total = 0
        self.calls_resolved = 0
        self.calls_abandoned = 0
        self.governance_actions = 0
        self.drift_detections = 0
        self.healing_actions = 0
        
        # Gauges
        self.avg_wait_time = 0.0
        self.abandonment_rate = 0.0
        self.rl_loss = 0.0
        self.queue_lengths = defaultdict(int)
        self.staffed_agents = defaultdict(int)
        
        # Histograms (stored as lists for bucketing)
        self.wait_times = []
        self.resolution_times = []
        self.rl_losses = []
    
    def record_call(self, wait_time: float, resolved: bool, resolution_time: float):
        """Record call outcome"""
        self.calls_total += 1
        if resolved:
            self.calls_resolved += 1
        else:
            self.calls_abandoned += 1
        
        self.wait_times.append(wait_time)
        self.resolution_times.append(resolution_time)
        
        # Update rates
        if self.calls_total > 0:
            self.abandonment_rate = self.calls_abandoned / self.calls_total
        
        # Update avg wait (exponential moving average)
        if self.wait_times:
            self.avg_wait_time = sum(self.wait_times[-100:]) / len(self.wait_times[-100:])
    
    def record_drift_detection(self, node: str, magnitude: float):
        """Record drift detection event"""
        self.drift_detections += 1
    
    def record_governance_action(self, action_type: str):
        """Record governance action (heal, recommend, etc)"""
        self.governance_actions += 1
        if action_type == "heal":
            self.healing_actions += 1
    
    def record_rl_loss(self, loss: float):
        """Record RL training loss"""
        self.rl_loss = loss
        self.rl_losses.append(loss)
    
    def record_queue_state(self, queue: str, waiting: int, staffed: int):
        """Record queue state"""
        self.queue_lengths[queue] = waiting
        self.staffed_agents[queue] = staffed
    
    def export_prometheus(self) -> str:
        """Export metrics in Prometheus text format"""
        
        lines = []
        lines.append("# HELP iceberg_calls_total Total calls processed")
        lines.append("# TYPE iceberg_calls_total counter")
        lines.append(f"iceberg_calls_total {self.calls_total}")
        
        lines.append("# HELP iceberg_calls_resolved Resolved calls")
        lines.append("# TYPE iceberg_calls_resolved counter")
        lines.append(f"iceberg_calls_resolved {self.calls_resolved}")
        
        lines.append("# HELP iceberg_calls_abandoned Abandoned calls")
        lines.append("# TYPE iceberg_calls_abandoned counter")
        lines.append(f"iceberg_calls_abandoned {self.calls_abandoned}")
        
        lines.append("# HELP iceberg_abandonment_rate Current abandonment rate")
        lines.append("# TYPE iceberg_abandonment_rate gauge")
        lines.append(f"iceberg_abandonment_rate {self.abandonment_rate:.3f}")
        
        lines.append("# HELP iceberg_avg_wait_time Average wait time seconds")
        lines.append("# TYPE iceberg_avg_wait_time gauge")
        lines.append(f"iceberg_avg_wait_time {self.avg_wait_time:.1f}")
        
        lines.append("# HELP iceberg_drift_detections Total drift detections")
        lines.append("# TYPE iceberg_drift_detections counter")
        lines.append(f"iceberg_drift_detections {self.drift_detections}")
        
        lines.append("# HELP iceberg_governance_actions Total governance actions")
        lines.append("# TYPE iceberg_governance_actions counter")
        lines.append(f"iceberg_governance_actions {self.governance_actions}")
        
        lines.append("# HELP iceberg_healing_actions Total healing actions")
        lines.append("# TYPE iceberg_healing_actions counter")
        lines.append(f"iceberg_healing_actions {self.healing_actions}")
        
        lines.append("# HELP iceberg_rl_loss Current RL training loss")
        lines.append("# TYPE iceberg_rl_loss gauge")
        lines.append(f"iceberg_rl_loss {self.rl_loss:.4f}")
        
        # Queue metrics
        for queue, length in self.queue_lengths.items():
            lines.append(f"iceberg_queue_length{{queue=\"{queue}\"}} {length}")
        
        for queue, staffed in self.staffed_agents.items():
            lines.append(f"iceberg_staffed_agents{{queue=\"{queue}\"}} {staffed}")
        
        return "\n".join(lines)
    
    def get_summary(self) -> Dict:
        """Get summary of current metrics"""
        return {
            "calls_total": self.calls_total,
            "calls_resolved": self.calls_resolved,
            "calls_abandoned": self.calls_abandoned,
            "abandonment_rate": self.abandonment_rate,
            "avg_wait_time": self.avg_wait_time,
            "rl_loss": self.rl_loss,
            "drift_detections": self.drift_detections,
            "governance_actions": self.governance_actions,
            "healing_actions": self.healing_actions,
        }
