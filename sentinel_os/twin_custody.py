"""twin_custody -- key custody + envelope crypto for the customer-DR witness ("the twin").

Divergence Attestation Protocol (DAP) v1 reference implementation, crypto layer.

Custody models
--------------
  Option A: the customer generates an X25519 keypair and gives Sentinel ONLY the
            public half. Sentinel seals every replica entry to that public key.
            Decryption requires the private half, which Sentinel never possesses.
  Option D: identical mechanics, but the recipient keypair belongs to a neutral
            custodian (twin_custodian.py). Decryption happens only through the
            custodian's API, which logs and signs every request (attribution).

Envelope scheme (DAP-3)
-----------------------
ECIES-style, standard primitives only, via the `cryptography` library:

  per-seal ephemeral X25519 keypair
    -> ECDH(ephemeral_priv, recipient_pub)
    -> HKDF-SHA256(shared, info = b"twin-dap-v1|" + sha256(AAD)) -> 32-byte key
    -> AES-256-GCM(key, random 96-bit nonce, plaintext, associated_data=AAD)

The payload cipher is literally AES-256 (GCM), so the standing claim
"AES-256 at rest; customer holds keys; Sentinel holds zero key material"
remains true word-for-word under both custody models.

AAD slot-binding: the associated data is the canonical JSON of
{replica_id, primary_id, current_hash}. A validly sealed envelope therefore
authenticates ONLY in its own slot -- Sentinel cannot relocate a sealed blob to
a different entry, replica, or hash position without failing authentication.

Nothing here is hand-rolled: X25519, HKDF, AES-GCM, Ed25519 as shipped by
`cryptography`. This module composes them; it does not invent primitives.

Hash recomputation (DAP-2)
--------------------------
recompute_current_hash() reproduces, byte-for-byte, the canonicalization that
governance/ledger_postgres.py uses at append time, for both record kinds
(base rows via append(); governance decisions via append_decision()). The
customer/regulator uses it to confirm that a decrypted replica payload really
is the preimage of the clear-metadata current_hash.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any, Dict, Optional, Tuple

# Same contract the primary ledger uses to add optional fields to the hash.
# Importing it (rather than re-listing the fields here) is what guarantees the
# witness and the writer can never drift on which keys enter the canonical form.
from canonical_fields import apply_optional_hashed_fields

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

DAP_VERSION = 1
ENVELOPE_ALG = "X25519+HKDF-SHA256+AES-256-GCM"
_HKDF_INFO_PREFIX = b"twin-dap-v1|"


class CustodyError(Exception):
    """Raised when an envelope cannot be opened or a signature fails."""


# ---------------------------------------------------------------------------
# encoding helpers
# ---------------------------------------------------------------------------

def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def canonical_json(obj: Any) -> bytes:
    """The single canonical JSON rendering used everywhere in DAP v1."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()


def fingerprint(pub_b64: str) -> str:
    """Short stable identifier for a public key (sha256, first 16 hex)."""
    return hashlib.sha256(_b64d(pub_b64)).hexdigest()[:16]


# ---------------------------------------------------------------------------
# X25519 recipient keys (custody keys)
# ---------------------------------------------------------------------------

def generate_recipient_keypair() -> Tuple[str, str]:
    """Return (private_b64, public_b64) raw X25519 keys."""
    priv = X25519PrivateKey.generate()
    priv_raw = priv.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    pub_raw = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return _b64e(priv_raw), _b64e(pub_raw)


def _aad_bytes(aad: Dict[str, Any]) -> bytes:
    return canonical_json(aad)


def _derive_key(shared: bytes, aad_b: bytes) -> bytes:
    info = _HKDF_INFO_PREFIX + hashlib.sha256(aad_b).digest()
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(shared)


def seal(plaintext: bytes, recipient_pub_b64: str, aad: Dict[str, Any]) -> Dict[str, Any]:
    """Seal plaintext to the recipient public key, bound to the AAD slot."""
    aad_b = _aad_bytes(aad)
    recipient_pub = X25519PublicKey.from_public_bytes(_b64d(recipient_pub_b64))
    eph_priv = X25519PrivateKey.generate()
    eph_pub_raw = eph_priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    key = _derive_key(eph_priv.exchange(recipient_pub), aad_b)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, aad_b)
    return {
        "v": DAP_VERSION,
        "alg": ENVELOPE_ALG,
        "epk": _b64e(eph_pub_raw),
        "nonce": _b64e(nonce),
        "ct": _b64e(ct),
        "recipient_fp": fingerprint(recipient_pub_b64),
    }


def open_envelope(envelope: Dict[str, Any], recipient_priv_b64: str,
                  aad: Dict[str, Any]) -> bytes:
    """Open an envelope. Raises CustodyError on wrong key, wrong slot, or tamper."""
    try:
        if envelope.get("v") != DAP_VERSION or envelope.get("alg") != ENVELOPE_ALG:
            raise CustodyError(f"unsupported envelope: v={envelope.get('v')} alg={envelope.get('alg')}")
        aad_b = _aad_bytes(aad)
        priv = X25519PrivateKey.from_private_bytes(_b64d(recipient_priv_b64))
        eph_pub = X25519PublicKey.from_public_bytes(_b64d(envelope["epk"]))
        key = _derive_key(priv.exchange(eph_pub), aad_b)
        return AESGCM(key).decrypt(_b64d(envelope["nonce"]), _b64d(envelope["ct"]), aad_b)
    except CustodyError:
        raise
    except (InvalidTag, ValueError, KeyError, TypeError) as exc:
        raise CustodyError(f"envelope failed to open: {type(exc).__name__}") from exc


# ---------------------------------------------------------------------------
# Ed25519 signing (custody log, custodian audit log, submission receipts)
# ---------------------------------------------------------------------------

def generate_signing_keypair() -> Tuple[str, str]:
    priv = Ed25519PrivateKey.generate()
    priv_raw = priv.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    pub_raw = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return _b64e(priv_raw), _b64e(pub_raw)


def sign(payload: Dict[str, Any], signing_priv_b64: str) -> str:
    priv = Ed25519PrivateKey.from_private_bytes(_b64d(signing_priv_b64))
    return _b64e(priv.sign(canonical_json(payload)))


def verify_signature(payload: Dict[str, Any], signature_b64: str, signer_pub_b64: str) -> bool:
    pub = Ed25519PublicKey.from_public_bytes(_b64d(signer_pub_b64))
    try:
        pub.verify(_b64d(signature_b64), canonical_json(payload))
        return True
    except InvalidSignature:
        return False


# ---------------------------------------------------------------------------
# Primary-hash recomputation (DAP-2) -- mirrors governance/ledger_postgres.py
# ---------------------------------------------------------------------------

#: columns a shipped payload must carry for full deep verification
SHIPPED_COLUMNS = [
    "id", "timestamp", "action_type", "node", "previous_value", "applied_value",
    "reason", "previous_hash", "current_hash", "data", "record_kind",
    "cassette_version", "input_data", "policy_parameters", "decision_output",
    "cassette_snapshot", "cassette_hash", "call_sid",
    # Phase-2 forensic columns. Shipped so the witness can (a) recompute the
    # hash of any new-format row and (b) hold the customer's honest copy of
    # cassette_code_hash / model_identity / authorizing identity. Legacy rows
    # carry NULL here and recompute exactly as before.
    "cassette_code_hash", "model_identity", "authorized_by",
    "supersedes_id", "supersedes_hash",
]


def _ledger_dumps(obj: Any) -> bytes:
    # Byte-for-byte the serialization ledger_postgres.py uses at append time:
    # json.dumps(canonical_entry, sort_keys=True, default=str).encode()
    # (note: default separators, NOT the compact separators of canonical_json)
    return json.dumps(obj, sort_keys=True, default=str).encode()


def recompute_current_hash(row: Dict[str, Any]) -> str:
    """Recompute what current_hash must be for a shipped/decrypted row.

    Mirrors ledger_postgres.append() for base rows and
    ledger_postgres.append_decision() for governance decisions.
    """
    if row.get("record_kind") == "governance_decision":
        canonical: Dict[str, Any] = {
            "record_kind": "governance_decision",
            "action_type": row["action_type"],
            "node": row["node"],
            "cassette_version": row["cassette_version"],
            "input_data": row["input_data"],
            "policy_parameters": row["policy_parameters"],
            "reasoning": row["reason"],
            "output": row["decision_output"],
            "previous_value": row["previous_value"],
            "applied_value": row["applied_value"],
            "parameter_changed": bool((row.get("data") or {}).get("parameter_changed")),
            "previous_hash": row["previous_hash"],
        }
        # Optional hashed fields via the SAME contract the writer uses. The
        # shipped-row column names already match the canonical keys for every
        # optional field (cassette_hash, cassette_code_hash, model_identity,
        # authorized_by, supersedes_hash), so the row itself is the source.
        # Legacy rows have these NULL -> omitted -> byte-identical to Phase-1.
        apply_optional_hashed_fields(canonical, row)
    elif row.get("record_kind") == "cassette_binding":
        # Mirrors ledger_postgres.bind_cassette_version(). Item 2.
        canonical = {
            "record_kind": "cassette_binding",
            "cassette_version": row["cassette_version"],
            "previous_hash": row["previous_hash"],
        }
        # cassette_hash + cassette_code_hash enter via the shared contract; the
        # shipped column names match the canonical keys.
        apply_optional_hashed_fields(canonical, row)
    elif row.get("record_kind") == "decision_supersession":
        # Mirrors ledger_postgres.supersede_decision(). Item 6. The writer
        # stored the authorizing identity in the authorized_by column but hashed
        # it under BOTH "authority" (explicit field) and "authorized_by" (via the
        # shared contract). corrected_output was stored in the decision_output
        # column. Reconstruct those mappings exactly.
        canonical = {
            "record_kind": "decision_supersession",
            "supersedes_id": row["supersedes_id"],
            "cassette_version": row["cassette_version"],
            "authority": row["authorized_by"],
            "reason": row["reason"],
            "corrected_output": row["decision_output"],
            "previous_hash": row["previous_hash"],
        }
        # supersedes_hash + authorized_by enter via the shared contract.
        apply_optional_hashed_fields(canonical, row)
    else:
        canonical = {
            "action_type": row["action_type"],
            "node": row["node"],
            "previous_value": row["previous_value"],
            "applied_value": row["applied_value"],
            "reason": row["reason"],
            "data": row["data"],
            "previous_hash": row["previous_hash"],
        }
    return hashlib.sha256(_ledger_dumps(canonical)).hexdigest()


def deep_verify_row(row: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """(ok, detail). ok=True when recomputed hash equals the row's current_hash."""
    try:
        recomputed = recompute_current_hash(row)
    except (KeyError, TypeError) as exc:
        return False, f"recompute-failed:{type(exc).__name__}:{exc}"
    if recomputed == row.get("current_hash"):
        return True, None
    return False, f"hash-mismatch: recomputed {recomputed[:16]}.. != stored {str(row.get('current_hash'))[:16]}.."
