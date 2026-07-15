"""
Grafana Dashboard Configuration - Real-time Iceberg observability

Auto-generates Grafana dashboard JSON for visualization
"""

import json
from typing import Dict

class GrafanaDashboard:
    """Generate Grafana dashboard for Iceberg monitoring"""
    
    def __init__(self):
        self.dashboard = {
            "dashboard": {
                "title": "Iceberg IVR Platform - Real-Time Monitoring",
                "description": "Complete observability for self-healing IVR system",
                "timezone": "browser",
                "panels": [],
                "refresh": "5s",
                "time": {"from": "now-1h", "to": "now"},
                "uid": "iceberg-main",
            }
        }
        self.panel_id = 1
    
    def add_stat_panel(self, title: str, query: str, unit: str = "", x: int = 0, y: int = 0) -> None:
        """Add stat/gauge panel"""
        panel = {
            "id": self.panel_id,
            "title": title,
            "type": "stat",
            "gridPos": {"h": 8, "w": 6, "x": x, "y": y},
            "targets": [
                {
                    "expr": query,
                    "legendFormat": title,
                    "refId": "A"
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "unit": unit,
                    "color": {"mode": "thresholds"},
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"color": "red", "value": None},
                            {"color": "yellow", "value": 50},
                            {"color": "green", "value": 80}
                        ]
                    }
                }
            }
        }
        self.dashboard["dashboard"]["panels"].append(panel)
        self.panel_id += 1
    
    def add_graph_panel(self, title: str, queries: Dict[str, str], x: int = 0, y: int = 0) -> None:
        """Add time-series graph panel"""
        targets = []
        for legend, query in queries.items():
            targets.append({
                "expr": query,
                "legendFormat": legend,
                "refId": chr(65 + len(targets))  # A, B, C, ...
            })
        
        panel = {
            "id": self.panel_id,
            "title": title,
            "type": "timeseries",
            "gridPos": {"h": 8, "w": 12, "x": x, "y": y},
            "targets": targets,
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "palette-classic"},
                    "custom": {"showPoints": "auto"}
                }
            },
            "options": {
                "legend": {"showLegend": True, "placement": "bottom"},
                "tooltip": {"mode": "multi"}
            }
        }
        self.dashboard["dashboard"]["panels"].append(panel)
        self.panel_id += 1
    
    def build(self) -> dict:
        """Build complete dashboard"""
        
        # Row 1: Key Metrics
        self.add_stat_panel(
            "Total Calls",
            "iceberg_calls_total",
            unit="short",
            x=0, y=0
        )
        
        self.add_stat_panel(
            "Abandonment Rate",
            "iceberg_abandonment_rate * 100",
            unit="percent",
            x=6, y=0
        )
        
        self.add_stat_panel(
            "Avg Wait Time",
            "iceberg_avg_wait_time",
            unit="s",
            x=12, y=0
        )
        
        self.add_stat_panel(
            "RL Loss",
            "iceberg_rl_loss",
            unit="short",
            x=18, y=0
        )
        
        # Row 2: Resolution & Abandonment Trend
        self.add_graph_panel(
            "Call Resolution vs Abandonment",
            {
                "Resolved": "rate(iceberg_calls_resolved[5m])",
                "Abandoned": "rate(iceberg_calls_abandoned[5m])",
            },
            x=0, y=8
        )
        
        # Row 3: Governance Actions
        self.add_graph_panel(
            "Governance Activity",
            {
                "Drift Detections": "rate(iceberg_drift_detections[5m])",
                "Governance Actions": "rate(iceberg_governance_actions[5m])",
                "Healing Actions": "rate(iceberg_healing_actions[5m])",
            },
            x=12, y=8
        )
        
        # Row 4: Queue Metrics
        self.add_graph_panel(
            "Queue Lengths by Queue",
            {
                "billing_queue": "iceberg_queue_length{queue=\"billing_queue\"}",
                "tech_queue": "iceberg_queue_length{queue=\"tech_queue\"}",
                "sales_queue": "iceberg_queue_length{queue=\"sales_queue\"}",
            },
            x=0, y=16
        )
        
        # Row 5: Staffing
        self.add_graph_panel(
            "Staffed Agents by Queue",
            {
                "billing_queue": "iceberg_staffed_agents{queue=\"billing_queue\"}",
                "tech_queue": "iceberg_staffed_agents{queue=\"tech_queue\"}",
                "sales_queue": "iceberg_staffed_agents{queue=\"sales_queue\"}",
            },
            x=12, y=16
        )
        
        return self.dashboard

def generate_dashboard_json() -> str:
    """Generate complete dashboard JSON"""
    dashboard = GrafanaDashboard()
    config = dashboard.build()
    return json.dumps(config, indent=2)

# Example: Export dashboard provisioning YAML
GRAFANA_DATASOURCE_CONFIG = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-datasource
  namespace: iceberg
data:
  prometheus.yaml: |
    apiVersion: 1
    datasources:
    - name: Prometheus
      type: prometheus
      url: http://prometheus:9090
      access: proxy
      isDefault: true
"""

GRAFANA_DASHBOARD_CONFIG = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-dashboard
  namespace: iceberg
data:
  iceberg-dashboard.json: |
    {DASHBOARD_JSON}
"""

def export_grafana_kubernetes_configs(dashboard_json: str) -> tuple:
    """Export Kubernetes configs for Grafana deployment"""
    
    dashboard_config = GRAFANA_DASHBOARD_CONFIG.replace("{DASHBOARD_JSON}", 
                                                         dashboard_json.replace('"', '\\"'))
    
    return (GRAFANA_DATASOURCE_CONFIG, dashboard_config)
