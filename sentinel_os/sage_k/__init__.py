"""
sage_k -- Fortress simulation kernel, GSA hash-chain adapter, and AST graph
extractor, reconstructed from the SAGE-K archive (a pasted Gemini
transcript) as a real, tested package rather than draft/chat material.

Components:

  - kernel.py: Fortress, a seeded regime/distortion simulation kernel.
  - gsa_adapter.py: GsaUniversalAdapter, a hash-chained envelope wrapper
    that can drive any async- or sync-payload module (Fortress included)
    through a signed request/response handshake.
  - graph_extractor.py: extract_graph/graph_to_dict, a deterministic
    AST-based call-graph extractor, plus ExtractorGsaAdapterModule which
    wraps it for use behind GsaUniversalAdapter.

This package now contains only the kernel, adapter and extractor. The
interpretation-framework files that were once here (drift, generator,
harness, realignment, report, scenarios) were retired. The regulatory
drift-monitoring design they came from lives in its own code path, and
the scenario type the mortgage cassette uses is in scenario_model.py.
"""

from .kernel import Fortress
from .gsa_adapter import GsaContextEnvelope, GsaUniversalAdapter, compute_state_signature
from .graph_extractor import Edge, ExtractorGsaAdapterModule, Graph, Node, extract_graph, graph_to_dict

__all__ = [
    "Fortress",
    "GsaContextEnvelope",
    "GsaUniversalAdapter",
    "compute_state_signature",
    "Edge",
    "ExtractorGsaAdapterModule",
    "Graph",
    "Node",
    "extract_graph",
    "graph_to_dict",
]
