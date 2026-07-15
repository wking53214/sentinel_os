from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

# Percentile statistic
def percentile(xs: List[float], q: float) -> float:
    if not xs:
        return float("nan")
    ss = sorted(xs)
    k = max(0, min(len(ss) - 1, int(round(q / 100.0 * (len(ss) - 1)))))
    return ss[k]

@dataclass(frozen=True)
class DriftPolicy:
    metric_q: float = 90.0
    rel_threshold: float = 0.40
    min_samples: int = 20

@dataclass(frozen=True)
class DriftSignal:
    node: str
    baseline_value: float
    current_value: float
    rel_change: float
    breached: bool
    n_current: int
    reason: str

    def human(self) -> str:
        pct = f"{self.rel_change:+.0%}"
        tag = "BREACH" if self.breached else "ok"
        return (f"[{tag}] {self.node}: {self.baseline_value:.1f}s -> "
                f"{self.current_value:.1f}s ({pct}, n={self.n_current})")

def baseline_from_holds(holds: Dict[str, List[float]],
                        policy: DriftPolicy = DriftPolicy()) -> Dict[str, float]:
    return {node: percentile(v, policy.metric_q)
            for node, v in holds.items() if v}

def detect_drift(baseline: Dict[str, float],
                 current_holds: Dict[str, List[float]],
                 policy: DriftPolicy = DriftPolicy()) -> List[DriftSignal]:
    signals: List[DriftSignal] = []
    for node in sorted(set(baseline) | set(current_holds)):
        base = baseline.get(node)
        obs = current_holds.get(node, [])
        n = len(obs)

        if base is None:
            signals.append(DriftSignal(node, float("nan"), percentile(obs, policy.metric_q),
                                       float("nan"), False, n,
                                       reason="no baseline for this node (new node)"))
            continue
        if n < policy.min_samples:
            signals.append(DriftSignal(node, base, percentile(obs, policy.metric_q),
                                       float("nan"), False, n,
                                       reason=f"insufficient samples (<{policy.min_samples})"))
            continue

        cur = percentile(obs, policy.metric_q)
        rel = (cur - base) / base if base else float("inf")
        breached = abs(rel) > policy.rel_threshold
        signals.append(DriftSignal(node, base, cur, rel, breached, n,
                                   reason="" if breached else "within band"))
    return signals
