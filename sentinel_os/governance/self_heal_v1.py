from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from drift_core_v1 import DriftSignal

class GovernanceViolation(Exception):
    pass

HEALABLE = frozenset({"expected_wait", "dwell_anomaly"})
GOVERNED = frozenset({"target_manifest", "reward_direction",
                      "load_to_probability", "lever_registry"})

@dataclass(frozen=True)
class HealBand:
    lo: float
    hi: float

    def clamp(self, x: float) -> Tuple[float, bool]:
        c = min(self.hi, max(self.lo, x))
        return c, (c != x)

@dataclass(frozen=True)
class HealRecord:
    kind: str
    node: str
    previous: float
    proposed: float
    applied: float
    clamped: bool
    rel_change: float
    head_hash: str

class InMemoryParameterStore:
    def __init__(self, initial: Optional[Dict[Tuple[str, str], float]] = None):
        self._d: Dict[Tuple[str, str], float] = dict(initial or {})

    def get(self, kind: str, node: str) -> float:
        return self._d.get((kind, node), 8.0)

    def set(self, kind: str, node: str, value: float) -> None:
        self._d[(kind, node)] = value

    def snapshot(self) -> Dict[Tuple[str, str], float]:
        return dict(self._d)

def heal(signals: List[DriftSignal],
         store,
         band: HealBand,
         ledger,
         kind: str = "expected_wait") -> List[HealRecord]:
    if kind in GOVERNED:
        raise GovernanceViolation(f"'{kind}' is governance-locked")
    if kind not in HEALABLE:
        raise GovernanceViolation(f"'{kind}' is not on the healable allow-list")

    records: List[HealRecord] = []
    for s in signals:
        if not s.breached:
            continue
        previous = store.get(kind, s.node)
        proposed = s.current_value
        applied, clamped = band.clamp(proposed)
        store.set(kind, s.node, applied)
        head = ledger.flush([{
            "action": "self_heal",
            "kind": kind,
            "node": s.node,
            "previous": round(previous, 4),
            "proposed": round(proposed, 4),
            "applied": round(applied, 4),
            "clamped": clamped,
            "rel_change": round(s.rel_change, 4),
        }])
        records.append(HealRecord(kind, s.node, previous, proposed, applied,
                                  clamped, s.rel_change, head))
    return records
