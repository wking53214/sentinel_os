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

This package intentionally excludes the interpretation-framework files
(drift/generator/harness/realignment/report/scenarios) that were also
present in SAGE-K's sage_k/ directory: those are an earlier, unused
snapshot of the same regulatory drift-monitoring design that sentinel_os
already implements natively in sentinel_os.interpretation, with its own
integrated history and test suite. Only the kernel/adapter/extractor
trio was novel and under test here.
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
