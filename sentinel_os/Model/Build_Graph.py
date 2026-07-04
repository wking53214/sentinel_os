"""
build_graph.py
--------------

Deterministic routing graph builder for Iceberg 3.x.

Best-in-Class Notes:
- Deterministic: Node and edge ordering is fixed via Fluent Builder.
- Governance-Safe: Built-in validation prevents orphaned edges.
- Replay-Friendly: Identical build logic ensures identical graph state.
- Scalable: Loop-based generation eliminates copy-paste risk.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Any


@dataclass
class GraphNode:
    """Deterministic graph node container."""
    name: str
    neighbors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "neighbors": list(self.neighbors)}


@dataclass
class RoutingGraph:
    """Canonical routing graph for Iceberg."""
    nodes: Dict[str, GraphNode]

    def validate(self) -> None:
        """Ensures all neighbors exist as nodes; prevents runtime traversal crashes."""
        for name, node in self.nodes.items():
            for neighbor in node.neighbors:
                if neighbor not in self.nodes:
                    raise ValueError(f"Integrity Error: {name} -> {neighbor} (Missing)")

    def to_dict(self) -> Dict[str, Any]:
        return {name: node.to_dict() for name, node in self.nodes.items()}


class GraphBuilder:
    """Fluent builder for deterministic Iceberg graph topology."""
    def __init__(self):
        self.nodes: Dict[str, GraphNode] = {}

    def add(self, name: str, neighbors: List[str]) -> GraphBuilder:
        self.nodes[name] = GraphNode(name, neighbors)
        return self

    def build(self) -> RoutingGraph:
        graph = RoutingGraph(self.nodes)
        graph.validate()
        return graph


def build_graph() -> RoutingGraph:
    """Constructs the deterministic Iceberg routing graph topology."""
    builder = GraphBuilder()

    # 1. Topology Root
    builder.add("root", ["intent_menu"])

    # 2. Queue Definitions
    queues = ["billing", "tech", "cancel", "upgrade", "complaint", "sales", "general"]
    builder.add("intent_menu", [f"{q}_queue" for q in queues])

    # 3. Deterministic Queue-Agent-Exit chains
    for q in queues:
        builder.add(f"{q}_queue", [f"{q}_agent"])
        builder.add(f"{q}_agent", ["exit"])

    # 4. Terminal State
    builder.add("exit", [])

    return builder.build()


# =========================================================
# FIXES / REVIEW NOTES (APPENDED — FROM PREVIOUS PASS)
# =========================================================

"""
1. DETERMINISM GAP (ORDERING RISK)
- Current neighbor lists are raw Python lists.
- Risk: insertion-order drift across refactors or edits.
- Impact: replay mismatch in simulator routing traversal.

Fix:
- Enforce sorted or immutable neighbor ordering:
  neighbors = tuple(sorted(neighbors))

------------------------------------------------------------

2. VALIDATION SCOPE LIMITATION
- validate() only checks forward existence:
  node -> neighbor exists
- Missing:
  - reverse-edge sanity validation
  - orphan detection (nodes never referenced)

Impact:
- silent topology asymmetry in RL routing behavior

------------------------------------------------------------

3. MUTABILITY RISK (GRAPHNODE)
- GraphNode.neighbors is mutable list
- Risk: runtime mutation by RL/simulator layers
- Impact: hidden state drift + non-replayable graphs

Fix:
- make GraphNode immutable:
  @dataclass(frozen=True)
- use tuple[str, ...] instead of List[str]

------------------------------------------------------------

4. NO TOPOLOGY VERSIONING
- No graph version identifier present
- Impact:
  cannot compare graph evolution across runs/debugs

Fix:
- add:
  graph_version: str = "iceberg-3.x"

------------------------------------------------------------

5. NO CYCLE DETECTION (OPTIONAL SAFETY GAP)
- Cycles allowed implicitly (valid for IVR, but unmonitored)
- Risk: accidental infinite routing loops if misconfigured

Fix (optional debug mode):
- add cycle detection utility for validation phase only

------------------------------------------------------------

6. RL / SIMULATOR CONTRACT GAP
- Simulator expects:
  routing_out["next_node"]
- Graph only provides adjacency structure
- Requires translation layer:
  Graph → Routing Engine → Simulator contract

------------------------------------------------------------

ARCHITECTURAL NOTE:

This module is structurally strong and already deterministic in construction.

Remaining issues are not correctness failures — they are:
- immutability guarantees
- strict ordering enforcement
- cross-module contract alignment
"""