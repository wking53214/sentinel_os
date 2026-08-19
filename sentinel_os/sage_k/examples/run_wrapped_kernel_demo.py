"""
Wraps a Fortress kernel instance in GsaUniversalAdapter and pushes one
envelope through it, showing the hash-chain handshake succeed.
Reconstructed from artifact_2.py's main_test_harness().
"""

import asyncio
from dataclasses import replace
from types import MappingProxyType

from sage_k.gsa_adapter import GsaContextEnvelope, GsaUniversalAdapter, compute_state_signature
from sage_k.kernel import Fortress


async def main() -> None:
    print("Initiating GSA-wrapped kernel integration check...")

    kernel_instance = Fortress(operational_seed=42)
    gsa_secured_adapter = GsaUniversalAdapter(underlying_module=kernel_instance)

    initial_payload = {"system_status": "ONLINE", "authorized_access": True}
    initial_headers = {
        "gsa_chain_history": ["GENESIS_HASH_STUB_A01"],
        "gsa_loop_iteration": 0,
        "gsa_interlock_hash": "INITIAL_STUB_HASH",
    }

    test_envelope = GsaContextEnvelope(
        payload_data=initial_payload,
        session_state_mapping={"user_session_token": "TOK-99X"},
        header_mapping=MappingProxyType(initial_headers),
    )

    # The chain-continuity check requires the inbound hash to match what the
    # adapter itself would compute, so we compute it the same way here.
    correct_initial_hash = compute_state_signature("GENESIS_HASH_STUB_A01", 0, test_envelope)
    initial_headers["gsa_interlock_hash"] = correct_initial_hash
    test_envelope = replace(test_envelope, header_mapping=MappingProxyType(initial_headers))

    output_envelope = await gsa_secured_adapter.process_payload(test_envelope)

    print(f"Status: {output_envelope.status_string}")
    print(f"Payload keys: {list(output_envelope.payload_data.keys())}")
    print(f"Next iteration: {output_envelope.header_mapping.get('gsa_loop_iteration')}")


if __name__ == "__main__":
    asyncio.run(main())
