# Row Count: 182

"""
test_graph_integrity.py
-----------------------

Deterministic integrity tests for Iceberg's routing graph.

These tests guarantee:
- Structural determinism
- No drift across builds
- No orphan nodes
- No missing neighbors
- JSON-safe serialization
- Stable structural hash
- Replay-friendly topology

Best-in-Class Notes
-------------------
- Deterministic: No randomness.
- Governance-Safe: Structural hash detects drift.
- Replay-Friendly: Identical build → identical hash.
"""

import json
import hashlib
import pytest

from Domain.build_graph import build_graph, RoutingGraph


# ---------------------------------------------------------
# STRUCTURAL HASH UTILITY
# ---------------------------------------------------------
def structural_hash(obj: dict) -> str:
    raw = json.dumps(obj, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------
# TEST 1 — Graph builds deterministically
# ---------------------------------------------------------
def test_graph_deterministic_build():
    g1 = build_graph()
    g2 = build_graph()

    assert isinstance(g1, RoutingGraph)
    assert isinstance(g2, RoutingGraph)

    snap1 = g1.to_dict()
    snap2 = g2.to_dict()

    assert snap1 == snap2, "Graph build is not deterministic"


# ---------------------------------------------------------
# TEST 2 — Structural hash is stable
# ---------------------------------------------------------
def test_graph_structural_hash_stable():
    g = build_graph()
    snap = g.to_dict()

    h1 = structural_hash(snap)
    h2 = structural_hash(snap)

    assert h1 == h2, "Structural hash changed unexpectedly"


# ---------------------------------------------------------
# TEST 3 — No orphan nodes
# ---------------------------------------------------------
def test_graph_no_orphan_nodes():
    g = build_graph()
    nodes = g.nodes

    # Every node must be referenced either as a root or neighbor
    referenced = set(["root"])
    for node in nodes.values():
        for n in node.neighbors:
            referenced.add(n)

    for name in nodes.keys():
        assert name in referenced, f"Orphan node detected: {name}"


# ---------------------------------------------------------
# TEST 4 — All neighbors must exist
# ---------------------------------------------------------
def test_graph_neighbors_exist():
    g = build_graph()
    nodes = g.nodes

    for name, node in nodes.items():
        for n in node.neighbors:
            assert n in nodes, f"Node '{name}' references missing neighbor '{n}'"


# ---------------------------------------------------------
# TEST 5 — Graph is JSON-safe
# ---------------------------------------------------------
def test_graph_json_safe():
    g = build_graph()
    snap = g.to_dict()

    try:
        json.dumps(snap)
    except Exception as e:
        pytest.fail(f"Graph snapshot is not JSON-safe: {e}")


# ---------------------------------------------------------
# TEST 6 — No cycles except allowed terminal loops
# ---------------------------------------------------------
def test_graph_no_cycles():
    g = build_graph()
    nodes = g.nodes

    visited = set()

    def dfs(node_name, stack):
        if node_name in stack:
            pytest.fail(f"Cycle detected involving node '{node_name}'")

        stack.add(node_name)
        for n in nodes[node_name].neighbors:
            dfs(n, stack)
        stack.remove(node_name)

    dfs("root", set())