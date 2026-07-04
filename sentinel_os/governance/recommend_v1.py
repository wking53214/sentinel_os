from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .drift_core_v1 import DriftSignal

def _role(node: str) -> str:
    if node.endswith("::auth"):
        return "auth"
    if "::menu_" in node:
        return "menu"
    if node == "intent_menu":
        return "intent"
    return "other"

_CATALOG = {
    "auth": [
        "investigate the auth/lookup backend for added latency",
        "offer a queue-back/callback option",
    ],
    "menu": [
        "review whether menu options match current intent",
        "shorten the menu to reduce decision time",
    ],
    "intent": [
        "re-order intent menu to reflect live distribution",
    ],
    "other": [
        "trace upstream dependency for source of wait",
    ],
}

@dataclass(frozen=True)
class Recommendation:
    node: str
    role: str
    baseline_value: float
    current_value: float
    rel_change: float
    status: str = "pending"

def recommend(signals: List[DriftSignal], ledger) -> List[Recommendation]:
    out: List[Recommendation] = []
    for s in signals:
        if not s.breached:
            continue
        role = _role(s.node)
        head = ledger.flush([{
            "action": "recommendation",
            "status": "pending",
            "node": s.node,
            "role": role,
            "baseline_p90": round(s.baseline_value, 4),
            "current_p90": round(s.current_value, 4),
            "rel_change": round(s.rel_change, 4),
        }])
        out.append(Recommendation(s.node, role, s.baseline_value, s.current_value,
                                  s.rel_change, status="pending"))
    return out
