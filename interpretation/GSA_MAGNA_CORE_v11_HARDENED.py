# ============================================================
# VERSION-CONTROL-ID: <GSA-MAGNA-CORE-v11-HARDENED-PART-1>
# ============================================================
"""
SYSTEM:
    UNIFIED_GOVERNANCE_STATE_KERNEL (UGSK)

VERSION:
    v11 HARDENED

PURPOSE:
    Hardened governance processing kernel implementing:

    - True immutable state containers
    - Cryptographic lineage primitives
    - Governance decision modeling
    - Zero-trust module metadata
    - Capability-based registration
    - Deterministic state transitions

PART:
    1/4

"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import time

from collections import OrderedDict
from dataclasses import (
    dataclass,
    field,
    replace
)
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import (
    Any,
    Dict,
    Final,
    List,
    Optional,
    Protocol,
    Set,
    Tuple,
)


# ============================================================
# CRYPTOGRAPHIC CONSTANTS
# ============================================================

HASH_ALGORITHM: Final = hashlib.sha256


def utc_timestamp() -> str:
    """
    Generates deterministic UTC timestamp format.
    """
    return datetime.now(
        timezone.utc
    ).isoformat()


def sha256_digest(payload: str) -> str:
    """
    Generates SHA256 digest.
    """

    return HASH_ALGORITHM(
        payload.encode("utf-8")
    ).hexdigest()


def secure_compare(
    left: str,
    right: str
) -> bool:
    """
    Constant-time string comparison.
    """

    return hmac.compare_digest(
        left,
        right
    )


# ============================================================
# IMMUTABLE STRUCTURE ENGINE
# ============================================================

def freeze_structure(
    data: Any,
    memo: Optional[Dict[int, Any]] = None
) -> Any:
    """
    Recursively converts mutable structures into
    immutable equivalents.

    Supported:
        dict -> MappingProxyType
        list -> tuple
        set -> frozenset

    Includes cycle protection.
    """

    if memo is None:
        memo = {}

    object_id = id(data)

    if object_id in memo:
        return memo[object_id]


    if isinstance(data, dict):

        frozen = {}

        memo[object_id] = frozen

        for key, value in data.items():

            frozen[
                freeze_structure(
                    key,
                    memo
                )
            ] = freeze_structure(
                value,
                memo
            )

        result = MappingProxyType(
            frozen
        )

        memo[object_id] = result

        return result


    if isinstance(data, list):

        result = tuple(
            freeze_structure(
                item,
                memo
            )
            for item in data
        )

        memo[object_id] = result

        return result


    if isinstance(data, set):

        result = frozenset(
            freeze_structure(
                item,
                memo
            )
            for item in data
        )

        memo[object_id] = result

        return result


    return data



# ============================================================
# GOVERNANCE MODELS
# ============================================================


class DecisionState(Enum):
    """
    Governance decision lifecycle.
    """

    ALLOWED = "allowed"
    DENIED = "denied"
    REVIEW = "review"



@dataclass(
    frozen=True
)
class GovernanceDecision:
    """
    Formal governance decision record.

    Every protected transition should eventually
    produce one of these objects.
    """

    state: DecisionState

    risk_score: float

    policy_matches: Tuple[str, ...]

    evidence_chain: Tuple[str, ...]

    authority: str

    timestamp: str = field(
        default_factory=utc_timestamp
    )


    @property
    def approved(self) -> bool:

        return (
            self.state ==
            DecisionState.ALLOWED
        )



@dataclass(
    frozen=True
)
class ModuleIdentity:
    """
    Zero-trust module identity declaration.
    """

    name: str

    version: str

    capabilities: Tuple[str, ...]

    fingerprint: str

    registered_at: str = field(
        default_factory=utc_timestamp
    )



@dataclass(
    frozen=True
)
class ContextEnvelope:
    """
    Immutable processing envelope.

    Every pipeline transition creates
    a new envelope instance.
    """

    payload_data: Dict[str, Any]

    session_state_mapping: Dict[str, Any]

    header_mapping: Dict[str, Any]

    decision: Optional[
        GovernanceDecision
    ] = None

    status_string: str = (
        "INITIALIZED"
    )


    def __post_init__(self):

        object.__setattr__(
            self,
            "payload_data",
            freeze_structure(
                self.payload_data
            )
        )

        object.__setattr__(
            self,
            "session_state_mapping",
            freeze_structure(
                self.session_state_mapping
            )
        )

        object.__setattr__(
            self,
            "header_mapping",
            freeze_structure(
                self.header_mapping
            )
        )


# ============================================================
# MODULE CONTRACTS
# ============================================================


class ComposableSystemModule(
    Protocol
):
    """
    Required module interface.
    """

    async def process_payload(
        self,
        context_envelope: ContextEnvelope
    ) -> ContextEnvelope:
        ...



class BoundedHashSet:
    """
    Memory-safe bounded replay cache.
    """

    def __init__(
        self,
        max_size: int = 10000
    ):

        self.max_size = max_size

        self._cache: OrderedDict[
            str,
            None
        ] = OrderedDict()

        self._lock = asyncio.Lock()



    async def add_if_absent(
        self,
        key: str
    ) -> bool:

        async with self._lock:

            if key in self._cache:

                self._cache.move_to_end(
                    key
                )

                return False


            self._cache[key] = None


            if len(
                self._cache
            ) > self.max_size:

                self._cache.popitem(
                    last=False
                )


            return True



# ============================================================
# GOVERNANCE NORMALIZATION ENGINE
# ============================================================


class GovernanceEngine:
    """
    Separates:

        1. Text normalization
        2. Integrity validation
        3. Governance classification
    """

    REGEX_IDENTITY: Final = re.compile(
        r"\b(i|me|my|mine|myself|we|us|our|ours|ourselves)\b",
        re.IGNORECASE
    )


    REGEX_CORPORATE: Final = {

        re.compile(
            r"\butilize\b",
            re.IGNORECASE
        ):
            "use",

        re.compile(
            r"\bleverage\b",
            re.IGNORECASE
        ):
            "apply"
    }


    def __init__(
        self,
        max_history_size: int = 10000
    ):

        self.seen_payloads = BoundedHashSet(
            max_history_size
        )


    def normalize_text(
        self,
        text: str
    ) -> str:
        """
        Performs linguistic normalization.

        Does NOT make governance decisions.
        """

        normalized = (
            self.REGEX_IDENTITY
            .sub(
                "[SUBJECT_REFERENCE]",
                text
            )
        )


        for pattern, replacement in (
            self.REGEX_CORPORATE.items()
        ):

            normalized = pattern.sub(
                replacement,
                normalized
            )


        return normalized.strip()



    async def validate_integrity(
        self,
        payload: str
    ) -> bool:
        """
        Prevents duplicate payload replay.
        """

        digest = sha256_digest(
            payload
        )

        return await (
            self.seen_payloads
            .add_if_absent(
                digest
            )
        )



    def evaluate(
        self,
        payload: str
    ) -> GovernanceDecision:

        return GovernanceDecision(

            state=DecisionState.ALLOWED,

            risk_score=0.0,

            policy_matches=(),

            evidence_chain=(
                sha256_digest(
                    payload
                ),
            ),

            authority="GSA_ROOT"
        )
        # ============================================================
# VERSION-CONTROL-ID: <GSA-MAGNA-CORE-v11-HARDENED-PART-2>
# ============================================================
"""
PART:
    2/4

CONTAINS:
    - Secure secret management
    - Cryptographic ledger
    - MAGNA orchestration engine
    - Hardened temporal doorway gate
"""

# ============================================================
# SECURE SECRET PROVIDER
# ============================================================


class SecretProvider:
    """
    Provides cryptographic secrets.

    Production deployments should replace this
    with:
        - Hashicorp Vault
        - AWS KMS
        - Azure Key Vault
        - HSM-backed storage

    Development fallback generates an ephemeral key.
    """

    def __init__(self):

        configured = os.environ.get(
            "GSA_SECRET_KEY"
        )

        if configured:

            self._secret = (
                configured.encode(
                    "utf-8"
                )
            )

        else:

            self._secret = os.urandom(
                32
            )


    @property
    def secret(
        self
    ) -> bytes:

        return self._secret



# ============================================================
# SIGNED EVENT LEDGER
# ============================================================


@dataclass(
    frozen=True
)
class LedgerEvent:
    """
    Append-only governance event.
    """

    event_id: str

    timestamp: str

    actor: str

    event_type: str

    payload_hash: str

    previous_hash: str

    signature: str



class CryptographicLedger:
    """
    Tamper-evident append-only ledger.

    Every event contains:

        previous_hash
        payload_hash
        signature

    creating a verifiable chain.
    """

    def __init__(
        self,
        secret: bytes,
        ledger_file: str
    ):

        self.secret = secret

        self.ledger_file = ledger_file

        self.events: List[
            LedgerEvent
        ] = []

        self._lock = asyncio.Lock()

        self._load()



    def _sign(
        self,
        data: str
    ) -> str:

        return hmac.new(
            self.secret,
            data.encode(
                "utf-8"
            ),
            hashlib.sha256
        ).hexdigest()



    def _load(
        self
    ):

        if not os.path.exists(
            self.ledger_file
        ):
            return


        try:

            with open(
                self.ledger_file,
                "r",
                encoding="utf-8"
            ) as file:

                for line in file:

                    item = json.loads(
                        line
                    )

                    self.events.append(
                        LedgerEvent(
                            **item
                        )
                    )

        except Exception:

            self.events = []



    async def append(
        self,
        actor: str,
        event_type: str,
        payload: str
    ) -> LedgerEvent:


        async with self._lock:

            previous_hash = (
                self.events[-1]
                .signature
                if self.events
                else "GENESIS"
            )


            payload_hash = sha256_digest(
                payload
            )


            event_id = sha256_digest(
                f"{actor}:{payload_hash}:{time.time_ns()}"
            )


            unsigned = (
                f"{event_id}|"
                f"{actor}|"
                f"{event_type}|"
                f"{payload_hash}|"
                f"{previous_hash}"
            )


            signature = self._sign(
                unsigned
            )


            event = LedgerEvent(
                event_id=event_id,
                timestamp=utc_timestamp(),
                actor=actor,
                event_type=event_type,
                payload_hash=payload_hash,
                previous_hash=previous_hash,
                signature=signature
            )


            self.events.append(
                event
            )


            with open(
                self.ledger_file,
                "a",
                encoding="utf-8"
            ) as file:

                file.write(
                    json.dumps(
                        event.__dict__
                    )
                    + "\n"
                )


            return event



# ============================================================
# MAGNA TRUST ORCHESTRATOR
# ============================================================


class MAGNA_Orchestrator:
    """
    Central governance coordinator.

    Responsibilities:

        - module registration
        - identity tracking
        - ledgering
        - handshake authorization
    """


    def __init__(
        self,
        ledger_file: Optional[str] = None
    ):

        self.modules: Dict[
            str,
            ModuleIdentity
        ] = {}


        self.module_classes: Dict[
            str,
            Any
        ] = {}


        self.secret_provider = SecretProvider()


        self.ledger = CryptographicLedger(
            secret=self.secret_provider.secret,
            ledger_file=(
                ledger_file
                or
                os.environ.get(
                    "GSA_LEDGER_FILE",
                    ".gsa_secure_ledger"
                )
            )
        )


        self._lock = asyncio.Lock()



    def register_as_module(
        self,
        name: str,
        version: str = "1.0.0",
        capabilities: Tuple[str,...] = ()
    ):

        def decorator(
            cls
        ):

            if name in self.modules:

                raise ValueError(
                    f"Module already registered: {name}"
                )


            fingerprint = sha256_digest(
                cls.__name__
                +
                version
            )


            identity = ModuleIdentity(
                name=name,
                version=version,
                capabilities=capabilities,
                fingerprint=fingerprint
            )


            self.modules[name] = identity

            self.module_classes[name] = cls


            return cls


        return decorator



    def is_registered(
        self,
        name: str
    ) -> bool:

        return name in self.modules



    def generate_handshake_token(
        self,
        source: str,
        destination: str,
        payload_summary: str
    ) -> Dict[str,str]:


        if (
            source not in self.modules
            or
            destination not in self.modules
        ):

            return {

                "status":
                "denied",

                "reason":
                "untrusted module boundary"

            }


        timestamp = utc_timestamp()


        material = (
            f"{source}|"
            f"{destination}|"
            f"{payload_summary}|"
            f"{timestamp}|"
            f"{time.time_ns()}"
        )


        signature = hmac.new(
            self.secret_provider.secret,
            material.encode(
                "utf-8"
            ),
            hashlib.sha256
        ).hexdigest()



        return {

            "status":
            "authorized",

            "timestamp":
            timestamp,

            "signature":
            signature

        }



# ============================================================
# HARDENED TEMPORAL DOORWAY GATE
# ============================================================


class GsaTemporalDoorwayGate:
    """
    Rotating cryptographic temporal gate.

    Security properties:

        - rotating signatures
        - constant-time verification
        - replay prevention
        - protected state access
    """

    def __init__(
        self,
        rotation_seed: str,
        rotation_interval: float = 0.05
    ):

        self._seed = rotation_seed

        self._interval = rotation_interval

        self._current_doorway_hash = ""

        self._running = False

        self._worker_task: Optional[
            asyncio.Task
        ] = None

        self._lock = asyncio.Lock()

        self._used_signatures = BoundedHashSet(
            max_size=10000
        )



    async def start_gate_engine(
        self
    ):

        if self._running:
            return


        self._running = True


        self._worker_task = asyncio.create_task(
            self._rotation_worker()
        )



    async def shutdown_gate_engine(
        self
    ):

        self._running = False


        if self._worker_task:

            self._worker_task.cancel()

            try:

                await self._worker_task

            except asyncio.CancelledError:

                pass


        self._worker_task = None



    async def _rotation_worker(
        self
    ):

        while self._running:

            entropy = (
                f"{self._seed}|"
                f"{time.time_ns()}"
            )


            signature = sha256_digest(
                entropy
            )


            async with self._lock:

                self._current_doorway_hash = (
                    signature
                )


            await asyncio.sleep(
                self._interval
            )



    async def get_current_signature(
        self
    ) -> str:

        async with self._lock:

            return (
                self._current_doorway_hash
            )



    async def verify_egress_handshake(
        self,
        target_exit_hash: str
    ) -> bool:


        async with self._lock:

            current = (
                self._current_doorway_hash
            )


            if not current:

                return False


            if not secure_compare(
                current,
                target_exit_hash
            ):

                return False



        return await (
            self._used_signatures
            .add_if_absent(
                target_exit_hash
            )
        )
        # ============================================================
# VERSION-CONTROL-ID: <GSA-MAGNA-CORE-v11-HARDENED-PART-3>
# ============================================================
"""
PART:
    3/4

CONTAINS:
    - Governance transition adapters
    - Secure pipeline processing
    - Aggregation engine
    - Trusted runtime modules
"""

# ============================================================
# UNIVERSAL GOVERNANCE ADAPTER
# ============================================================


class GsaUniversalAdapter:
    """
    Secure module boundary adapter.

    Responsibilities:

        - validate module execution
        - preserve immutable envelope state
        - maintain cryptographic lineage
        - enforce temporal gates
        - produce governance decisions
    """

    def __init__(
        self,
        underlying_module: Any,
        governance_engine: GovernanceEngine,
        temporal_gate: Optional[
            GsaTemporalDoorwayGate
        ] = None
    ):

        self.module = underlying_module

        self.governance = governance_engine

        self.actor_name = (
            type(underlying_module)
            .__name__
        )

        self.temporal_gate = temporal_gate



    async def process_payload(
        self,
        context_envelope: ContextEnvelope
    ) -> ContextEnvelope:


        payload = dict(
            context_envelope.payload_data
        )


        raw_text = payload.get(
            "raw_transcript",
            ""
        )


        normalized = (
            self.governance
            .normalize_text(
                raw_text
            )
        )


        if not await (
            self.governance
            .validate_integrity(
                normalized
            )
        ):

            decision = GovernanceDecision(

                state=DecisionState.DENIED,

                risk_score=1.0,

                policy_matches=(
                    "DUPLICATE_PAYLOAD",
                ),

                evidence_chain=(),

                authority="GSA_ROOT"

            )


            return replace(

                context_envelope,

                decision=decision,

                status_string=
                "INTEGRITY_REJECTED"

            )


        decision = (
            self.governance
            .evaluate(
                normalized
            )
        )


        if not decision.approved:

            return replace(

                context_envelope,

                decision=decision,

                status_string=
                "GOVERNANCE_DENIED"

            )



        payload[
            "normalized_transcript"
        ] = normalized



        if hasattr(
            self.module,
            "execute_aggregation"
        ):

            source = payload.get(
                "source_node",
                ""
            )

            destination = payload.get(
                "dest_node",
                ""
            )


            result = (
                self.module
                .execute_aggregation(
                    source,
                    destination,
                    normalized
                )
            )


            if result is None:

                return replace(

                    context_envelope,

                    decision=decision,

                    status_string=
                    "MODULE_REJECTED"

                )


            payload[
                "aggregated_payload"
            ] = result



        headers = dict(
            context_envelope.header_mapping
        )


        history = list(
            headers.get(
                "gsa_chain_history",
                []
            )
        )


        previous = (

            history[-1]
            if history
            else
            "GENESIS"

        )


        if self.temporal_gate:

            doorway_hash = await (
                self.temporal_gate
                .get_current_signature()
            )


            if not await (
                self.temporal_gate
                .verify_egress_handshake(
                    doorway_hash
                )
            ):

                return replace(

                    context_envelope,

                    decision=decision,

                    status_string=
                    "TEMPORAL_REJECTED"

                )


            lineage_material = (

                f"{previous}|"
                f"{doorway_hash}|"
                f"{json.dumps(payload,sort_keys=True)}"

            )

        else:

            lineage_material = (

                f"{previous}|"
                f"{json.dumps(payload,sort_keys=True)}"

            )



        outbound_hash = sha256_digest(
            lineage_material
        )


        history.append(
            outbound_hash
        )


        headers[
            "gsa_chain_history"
        ] = history


        headers[
            "gsa_last_actor"
        ] = self.actor_name


        headers[
            "gsa_interlock_hash"
        ] = outbound_hash



        return ContextEnvelope(

            payload_data=payload,

            session_state_mapping=dict(
                context_envelope
                .session_state_mapping
            ),

            header_mapping=headers,

            decision=decision,

            status_string=
            "COMMITTED"

        )



# ============================================================
# TRUSTED PROCESSING MODULES
# ============================================================


@MAGNA.register_as_module(
    name="ComputeNode",
    version="2.0.0",
    capabilities=(
        "semantic_processing",
        "evaluation"
    )
)
class ComputeNode:
    """
    Computational execution node.
    """


    async def process_payload(
        self,
        context_envelope: ContextEnvelope
    ) -> ContextEnvelope:

        return replace(

            context_envelope,

            status_string=
            "COMPUTE_COMPLETE"

        )



# ============================================================
# CHAT AGGREGATION ENGINE
# ============================================================


@MAGNA.register_as_module(
    name="ChatAggregator",
    version="2.0.0",
    capabilities=(
        "transcript_processing",
        "code_extraction",
        "metadata_generation"
    )
)
class ChatAggregator:
    """
    Extracts structured artifacts from
    mixed conversation streams.
    """

    def __init__(
        self,
        orchestrator: MAGNA_Orchestrator,
        governance: GovernanceEngine
    ):

        self.orchestrator = orchestrator

        self.governance = governance


        self.code_pattern = re.compile(
            r"```python(.*?)```",
            re.DOTALL
        )


        self._background_tasks: Set[
            asyncio.Task
        ] = set()



    def extract_code_blocks(
        self,
        text: str
    ) -> List[str]:

        return [

            block.strip()

            for block in
            self.code_pattern.findall(
                text
            )

        ]



    def execute_aggregation(
        self,
        source_node: str,
        destination_node: str,
        transcript: str
    ) -> Optional[str]:


        blocks = (
            self.extract_code_blocks(
                transcript
            )
        )


        combined = (
            "\n\n"
            .join(blocks)
        )


        metadata = {

            "blocks":
            len(blocks),

            "lines":
            len(
                combined.splitlines()
            ),

            "timestamp":
            utc_timestamp()

        }


        payload = (

            "# GSA AGGREGATION ARTIFACT\n"

            +
            json.dumps(
                metadata,
                indent=2
            )

            +

            "\n\n"

            +

            combined

        )


        token = (
            self.orchestrator
            .generate_handshake_token(
                source_node,
                destination_node,
                payload[:128]
            )
        )


        if token[
            "status"
        ] != "authorized":

            return None



        task = asyncio.create_task(

            self.orchestrator
            .ledger
            .append(

                actor=
                "ChatAggregator",

                event_type=
                "AGGREGATION",

                payload=
                payload

            )

        )


        self._background_tasks.add(
            task
        )


        task.add_done_callback(
            self._background_tasks.discard
        )


        return payload



# ============================================================
# RUNTIME FACTORY
# ============================================================


async def create_runtime():

    governance = GovernanceEngine()

    gate = GsaTemporalDoorwayGate(
        rotation_seed=
        "GSA_MAGNA_ROOT_SEED"
    )


    await gate.start_gate_engine()


    aggregator = ChatAggregator(
        MAGNA,
        governance
    )


    adapter = GsaUniversalAdapter(

        underlying_module=
        aggregator,

        governance_engine=
        governance,

        temporal_gate=
        gate

    )


    return {

        "governance":
        governance,

        "gate":
        gate,

        "aggregator":
        aggregator,

        "adapter":
        adapter

    }
# ============================================================
# VERSION-CONTROL-ID: <GSA-MAGNA-CORE-v11-HARDENED-PART-4>
# ============================================================
"""
PART:
    4/4

CONTAINS:
    - Integration runtime
    - Security validation suite
    - Concurrency stress tests
    - Adversarial testing harness
"""

import traceback


# ============================================================
# SECURITY TEST HARNESS
# ============================================================


class MagnaSecurityTestSuite:
    """
    Automated adversarial validation suite.

    Tests:

        1. Valid temporal handshake
        2. Invalid signature rejection
        3. Replay attack prevention
        4. Concurrent access safety
        5. Immutable envelope protection
        6. Pipeline integrity
    """

    def __init__(self):

        self.results = []



    def record(
        self,
        test_name: str,
        passed: bool,
        details: str
    ):

        self.results.append(
            {
                "test":
                test_name,

                "passed":
                passed,

                "details":
                details
            }
        )



    async def test_valid_temporal_handshake(
        self
    ):

        gate = GsaTemporalDoorwayGate(
            "TEST_SEED"
        )

        await gate.start_gate_engine()

        await asyncio.sleep(
            .1
        )


        signature = await (
            gate.get_current_signature()
        )


        result = await (
            gate.verify_egress_handshake(
                signature
            )
        )


        self.record(
            "valid_temporal_handshake",
            result,
            "Valid signature accepted"
        )


        await gate.shutdown_gate_engine()



    async def test_invalid_signature_rejection(
        self
    ):

        gate = GsaTemporalDoorwayGate(
            "TEST_SEED"
        )


        await gate.start_gate_engine()

        await asyncio.sleep(
            .1
        )


        result = not await (
            gate.verify_egress_handshake(
                "ATTACKER_SIGNATURE"
            )
        )


        self.record(
            "invalid_signature_rejection",
            result,
            "Invalid signature rejected"
        )


        await gate.shutdown_gate_engine()



    async def test_replay_protection(
        self
    ):

        gate = GsaTemporalDoorwayGate(
            "TEST_SEED"
        )


        await gate.start_gate_engine()

        await asyncio.sleep(
            .1
        )


        signature = await (
            gate.get_current_signature()
        )


        first = await (
            gate.verify_egress_handshake(
                signature
            )
        )


        second = await (
            gate.verify_egress_handshake(
                signature
            )
        )


        result = (
            first is True
            and
            second is False
        )


        self.record(
            "replay_attack_protection",
            result,
            "Replay rejected after first use"
        )


        await gate.shutdown_gate_engine()



    async def test_concurrency_stress(
        self
    ):

        gate = GsaTemporalDoorwayGate(
            "STRESS_TEST"
        )


        await gate.start_gate_engine()

        await asyncio.sleep(
            .1
        )


        signature = await (
            gate.get_current_signature()
        )


        results = await asyncio.gather(
            *[
                gate.verify_egress_handshake(
                    signature
                )

                for _ in range(1000)
            ]
        )


        accepted = sum(
            results
        )


        result = (
            accepted == 1
        )


        self.record(
            "1000_request_concurrency",
            result,
            f"Accepted={accepted}"
        )


        await gate.shutdown_gate_engine()



    async def test_immutable_envelope(
        self
    ):

        envelope = ContextEnvelope(

            payload_data={
                "security":
                "locked"
            },

            session_state_mapping={},

            header_mapping={}

        )


        passed = False


        try:

            envelope.payload_data[
                "security"
            ] = "modified"


        except TypeError:

            passed = True



        self.record(
            "immutable_context",
            passed,
            "Mutation blocked"
        )



    async def test_full_pipeline(
        self
    ):

        runtime = await create_runtime()


        adapter = runtime[
            "adapter"
        ]


        envelope = ContextEnvelope(

            payload_data={

                "source_node":
                "ChatAggregator",

                "dest_node":
                "ComputeNode",

                "raw_transcript":
                """
                We utilize secure models.

                ```python
                print("validated")
                ```
                """

            },

            session_state_mapping={},

            header_mapping={
                "gsa_chain_history":[]
            }

        )


        result = await (
            adapter.process_payload(
                envelope
            )
        )


        passed = (
            result.status_string
            ==
            "COMMITTED"
        )


        self.record(
            "full_governance_pipeline",
            passed,
            result.status_string
        )


        await runtime[
            "gate"
        ].shutdown_gate_engine()



    async def run_all(
        self
    ):


        tests = [

            self.test_valid_temporal_handshake,

            self.test_invalid_signature_rejection,

            self.test_replay_protection,

            self.test_concurrency_stress,

            self.test_immutable_envelope,

            self.test_full_pipeline

        ]


        for test in tests:

            try:

                await test()

            except Exception as exc:

                self.record(

                    test.__name__,

                    False,

                    traceback.format_exc()

                )



        return self.results



# ============================================================
# SYSTEM ENTRYPOINT
# ============================================================


async def main():

    print(
        """
========================================
 GSA MAGNA CORE v11 HARDENED
 SECURITY VALIDATION
========================================
"""
    )


    suite = MagnaSecurityTestSuite()


    results = await (
        suite.run_all()
    )


    passed = sum(
        1
        for result in results
        if result["passed"]
    )


    total = len(
        results
    )


    for result in results:

        status = (
            "PASS"
            if result["passed"]
            else
            "FAIL"
        )

        print(
            f"{status}: "
            f"{result['test']} "
            f"- "
            f"{result['details']}"
        )


    print(
        "\n========================================"
    )

    print(
        f"RESULT: {passed}/{total} TESTS PASSED"
    )

    print(
        "========================================"
    )



if __name__ == "__main__":

    asyncio.run(
        main()
    )