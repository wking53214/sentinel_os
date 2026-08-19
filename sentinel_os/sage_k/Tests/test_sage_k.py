import os
import sys
from dataclasses import replace
from types import MappingProxyType

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import asyncio

from sage_k.gsa_adapter import GsaContextEnvelope, GsaUniversalAdapter, compute_state_signature
from sage_k.graph_extractor import ExtractorGsaAdapterModule, extract_graph
from sage_k.kernel import Fortress


def test_kernel_run_cycle_returns_expected_keys():
    result = Fortress(operational_seed=1).run_cycle()
    assert set(result) == {"final_state", "regime", "distortion"}
    assert result["regime"] in {"STABLE", "UNSTABLE", "CRITICAL"}


def test_extract_graph_finds_function_and_call():
    graph = extract_graph("def f():\n    print('hi')\n", filename="t.py")
    assert "f" in graph.nodes
    assert any(e.dst == "print" for e in graph.edges)


def _seeded_envelope(payload):
    headers = {
        "gsa_chain_history": ["GENESIS_HASH_STUB_A01"],
        "gsa_loop_iteration": 0,
        "gsa_interlock_hash": "INITIAL_STUB_HASH",
    }
    envelope = GsaContextEnvelope(payload_data=payload, session_state_mapping={}, header_mapping=MappingProxyType(headers))
    headers["gsa_interlock_hash"] = compute_state_signature("GENESIS_HASH_STUB_A01", 0, envelope)
    return replace(envelope, header_mapping=MappingProxyType(headers))


def test_adapter_wraps_async_kernel():
    async def run():
        adapter = GsaUniversalAdapter(underlying_module=Fortress(operational_seed=1))
        out = await adapter.process_payload(_seeded_envelope({"system_status": "ONLINE"}))
        assert out.status_string.startswith("SAGE_KERNEL_COMPUTATION_SUCCESSFUL")
    asyncio.run(run())


def test_adapter_wraps_sync_extractor():
    async def run():
        adapter = GsaUniversalAdapter(underlying_module=ExtractorGsaAdapterModule())
        out = await adapter.process_payload(_seeded_envelope({"source_code_target": "x = 1"}))
        assert out.status_string == "AST_EXTRACTION_COMPLETED_SUCCESSFULLY"
    asyncio.run(run())
