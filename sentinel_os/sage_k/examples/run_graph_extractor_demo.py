"""
Runs the AST graph extractor on a small snippet, both directly and wrapped
in GsaUniversalAdapter.
"""

import asyncio
import json
from dataclasses import replace
from types import MappingProxyType

from sage_k.gsa_adapter import GsaContextEnvelope, GsaUniversalAdapter, compute_state_signature
from sage_k.graph_extractor import ExtractorGsaAdapterModule, extract_graph, graph_to_dict

SAMPLE_SOURCE = """
import os

class Greeter:
    def hello(self, name):
        return os.path.join("hi", name)

def main():
    g = Greeter()
    print(g.hello("world"))
"""


def run_direct() -> None:
    graph = extract_graph(SAMPLE_SOURCE, filename="sample.py")
    print("Direct extraction:")
    print(json.dumps(graph_to_dict(graph), indent=2))


async def run_wrapped() -> None:
    adapter = GsaUniversalAdapter(underlying_module=ExtractorGsaAdapterModule())

    initial_headers = {
        "gsa_chain_history": ["GENESIS_HASH_STUB_A01"],
        "gsa_loop_iteration": 0,
        "gsa_interlock_hash": "INITIAL_STUB_HASH",
    }
    envelope = GsaContextEnvelope(
        payload_data={"source_code_target": SAMPLE_SOURCE},
        session_state_mapping={},
        header_mapping=MappingProxyType(initial_headers),
    )
    correct_hash = compute_state_signature("GENESIS_HASH_STUB_A01", 0, envelope)
    initial_headers["gsa_interlock_hash"] = correct_hash
    envelope = replace(envelope, header_mapping=MappingProxyType(initial_headers))

    output = await adapter.process_payload(envelope)
    print("\nWrapped extraction:")
    print(f"Status: {output.status_string}")
    print(json.dumps(output.payload_data, indent=2))


if __name__ == "__main__":
    run_direct()
    asyncio.run(run_wrapped())
