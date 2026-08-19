"""
AST-based Python source graph extractor, reconstructed from artifact_3.py.

Per PROVENANCE.md this is unrelated to the S.A.G.E.-K. kernel: it was a
second, separate code sample pasted into the same chat and given the same
GSA wrapper treatment. It's kept in this package because that's how the
source repo bundled it, but it has no functional dependency on kernel.py.

What it does: parses a Python source string with `ast`, walks it, and
records module/function/class/import nodes plus CALL edges (caller ->
resolved callee name), so you can e.g. see what an unfamiliar script
touches. It's a plain AST visitor with straightforward name resolution;
"Graph Intermediate Representation" is naming, not a formal IR beyond
this dataclass structure. Call resolution is name-based, not type-aware,
so e.g. two different objects with the same method name resolve to the
same edge target.
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, List, Optional, Set

from .gsa_adapter import GsaContextEnvelope

__all__ = ["Node", "Edge", "Graph", "GraphExtractor", "extract_graph", "graph_to_dict", "ExtractorGsaAdapterModule"]


@dataclass(frozen=True)
class Node:
    """A code element: a module, function, async function, class, or import."""
    id: str
    kind: str
    file: str


@dataclass(frozen=True)
class Edge:
    """A directed reference between two nodes, with the source line as evidence."""
    src: str
    dst: str
    kind: str
    evidence: str


@dataclass
class Graph:
    nodes: Dict[str, Node]
    edges: List[Edge]


class GraphExtractor(ast.NodeVisitor):
    """Walks a parsed AST once, collecting nodes and CALL/import edges."""

    def __init__(self, filename: str = "<module>"):
        self.filename = filename
        self.nodes: Dict[str, Node] = {}
        self.edges: List[Edge] = []
        self.current_scope: List[str] = []
        self.defined: Set[str] = set()

    def add_node(self, name: str, kind: str) -> None:
        if name not in self.nodes:
            self.nodes[name] = Node(id=name, kind=kind, file=self.filename)

    def add_edge(self, src: str, dst: str, kind: str, evidence: str) -> None:
        self.edges.append(Edge(src, dst, kind, evidence))

    def current_qualname(self, name: str) -> str:
        if self.current_scope:
            return ".".join(self.current_scope + [name])
        return name

    def visit_Module(self, node: ast.Module) -> None:
        self.add_node(self.filename, "module")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        qname = self.current_qualname(node.name)
        self.add_node(qname, "function")
        self.defined.add(qname)
        self.current_scope.append(node.name)
        self.generic_visit(node)
        self.current_scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        qname = self.current_qualname(node.name)
        self.add_node(qname, "async_function")
        self.defined.add(qname)
        self.current_scope.append(node.name)
        self.generic_visit(node)
        self.current_scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qname = self.current_qualname(node.name)
        self.add_node(qname, "class")
        self.defined.add(qname)
        self.current_scope.append(node.name)
        self.generic_visit(node)
        self.current_scope.pop()

    def visit_Call(self, node: ast.Call) -> None:
        caller = ".".join(self.current_scope) if self.current_scope else self.filename
        callee = self.resolve_call(node.func)
        if callee:
            self.add_edge(src=caller, dst=callee, kind="CALL", evidence=ast.unparse(node))
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.add_node(alias.name, "import")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            full = f"{module}.{alias.name}" if module else alias.name
            self.add_node(full, "import")
        self.generic_visit(node)

    def resolve_call(self, func: ast.AST) -> Optional[str]:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return self.resolve_attr_chain(func)
        return None

    def resolve_attr_chain(self, node: ast.Attribute) -> str:
        parts = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))


def extract_graph(source: str, filename: str = "<module>") -> Graph:
    """Parse a Python source string and return its call/definition graph."""
    tree = ast.parse(source)
    extractor = GraphExtractor(filename=filename)
    extractor.visit(tree)
    return Graph(nodes=extractor.nodes, edges=extractor.edges)


def graph_to_dict(graph: Graph) -> dict:
    """JSON-serializable form of a Graph."""
    return {
        "nodes": [asdict(n) for n in graph.nodes.values()],
        "edges": [asdict(e) for e in graph.edges],
    }


class ExtractorGsaAdapterModule:
    """Sync bridge so GraphExtractor can be wrapped by GsaUniversalAdapter."""

    def execute_governance_logic(self, envelope: GsaContextEnvelope) -> GsaContextEnvelope:
        target_code = envelope.payload_data.get("source_code_target", "")
        extracted_graph = extract_graph(target_code, filename="gsa_target_buffer.py")
        dict_payload = graph_to_dict(extracted_graph)
        return replace(
            envelope,
            payload_data=dict_payload,
            status_string="AST_EXTRACTION_COMPLETED_SUCCESSFULLY",
        )
