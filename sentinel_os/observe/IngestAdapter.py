from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any, List, Tuple
import random

DEFAULT_EXPECTED_DWELL_SECONDS = 8.0

def generate_synthetic_call(
    call_id: str,
    journey: List[str],
    rng: random.Random,
    friction_profile: str = "clean",
) -> List[Dict[str, Any]]:
    t = 0.0
    events = [{"call_id": call_id, "type": "call_start", "timestamp": t}]
    path = list(journey)
    if friction_profile == "hangup":
        cut = max(1, len(path) - 1)
        path = path[:cut]
    visited: List[str] = []
    for i, node in enumerate(path):
        dwell = rng.uniform(3.0, 6.0)
        if friction_profile == "overrun" and i == len(path) // 2:
            dwell = DEFAULT_EXPECTED_DWELL_SECONDS * 4
        t += dwell
        events.append({"call_id": call_id, "type": "menu_reached",
                        "node": node, "timestamp": t})
        visited.append(node)
        if friction_profile == "revisit" and i == len(path) // 2 and len(visited) >= 2:
            back_node = visited[-2]
            t += rng.uniform(3.0, 5.0)
            events.append({"call_id": call_id, "type": "menu_reached",
                            "node": back_node, "timestamp": t})
            t += rng.uniform(3.0, 5.0)
            events.append({"call_id": call_id, "type": "menu_reached",
                            "node": node, "timestamp": t})
        if rng.random() < 0.3:
            hold = rng.uniform(2.0, 10.0)
            events.append({"call_id": call_id, "type": "hold_start", "timestamp": t})
            t += hold
            events.append({"call_id": call_id, "type": "hold_end", "timestamp": t})
    disposition = "hangup" if friction_profile == "hangup" else "completed"
    events.append({"call_id": call_id, "type": "call_end", "timestamp": t,
                    "disposition": disposition})
    return events

@dataclass(frozen=True)
class DerivedCall:
    call_id: str
    route: List[str]
    stimuli_by_hop: List[Dict[str, Any]] = field(default_factory=list)
    final_outcome_hint: str = "unknown"

def derive_stimuli(
    events: List[Dict[str, Any]],
    resolution_nodes: frozenset,
    handoff_nodes: frozenset,
    dwell_anomaly_seconds: float = DEFAULT_EXPECTED_DWELL_SECONDS,
    expected_wait_seconds: float = DEFAULT_EXPECTED_DWELL_SECONDS,
    expected_wait_by_node: Dict[str, float] | None = None,
) -> DerivedCall:
    call_id = events[0]["call_id"]
    menu_events = [e for e in events if e["type"] == "menu_reached"]
    route: List[str] = []
    stimuli: List[Dict[str, Any]] = []
    hold_segments: List[Tuple[float, float]] = []
    open_hold_start = None
    for e in sorted(events, key=lambda e: e["timestamp"]):
        if e["type"] == "hold_start":
            open_hold_start = e["timestamp"]
        elif e["type"] == "hold_end" and open_hold_start is not None:
            hold_segments.append((open_hold_start, e["timestamp"]))
            open_hold_start = None
    def hold_between(t0: float, t1: float) -> float:
        return sum(max(0.0, min(end, t1) - max(start, t0))
                   for start, end in hold_segments if end > t0 and start < t1)
    call_end_ts = events[-1]["timestamp"]
    prev_t = events[0]["timestamp"]
    for i, e in enumerate(menu_events):
        node = e["node"]
        ts = e["timestamp"]
        next_ts = (menu_events[i + 1]["timestamp"]
                   if i + 1 < len(menu_events) else call_end_ts)
        dwell_net = (ts - prev_t) - hold_between(prev_t, ts)
        hold_here = hold_between(ts, next_ts)
        revisit = node in route
        overrun = dwell_net > dwell_anomaly_seconds
        friction_event = 1 if (revisit or overrun) else 0
        expected_here = (
            expected_wait_by_node.get(node, expected_wait_seconds)
            if expected_wait_by_node else expected_wait_seconds
        )
        route.append(node)
        stimuli.append({
            "node": node,
            "timestamp": ts,
            "friction_event": friction_event,
            "actual_wait": hold_here,
            "expected_wait": expected_here,
            "resolved": node in resolution_nodes or node in handoff_nodes,
        })
        prev_t = ts
    last_node = route[-1] if route else None
    if last_node in resolution_nodes or last_node in handoff_nodes:
        outcome_hint = "success"
    else:
        outcome_hint = "abandonment"
    return DerivedCall(call_id=call_id, route=route,
                        stimuli_by_hop=stimuli, final_outcome_hint=outcome_hint)
