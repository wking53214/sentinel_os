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
from typing import Any, Dict, List, Optional, Tuple

# Same contract the primary ledger uses to add optional fields to the hash.
# Importing it (rather than re-listing the fields here) is what guarantees the
# witness and the writer can never drift on which keys enter the canonical form.
from canonical_fields import (
    ATTESTATION_POLICY_RECORD_KIND as _ATTESTATION_POLICY_RECORD_KIND,
    CONTRACT_CANONICAL_FIELDS as _CONTRACT_CANONICAL_FIELDS,
    CONTRACT_KINDS_WITH_FINDING as _CONTRACT_KINDS_WITH_FINDING,
    apply_optional_hashed_fields,
    attestation_policy_canonical as _attestation_policy_canonical,
    observed_event_canonical as _observed_event_canonical,
)
# TACK Layer 5 receipts. The witness recomputes the subject binding with the
# same CNS function the writer used (a digest each side derives its own way
# does not cross a repository boundary) and re-derives a shuffle seed through
# the one helper Layer 1 will derive it with.
from cns.gate import subject_digest as _cns_subject_digest
from governance.authorized_by_attestation import (
    KeySet as _KeySet,
    SIGNATURE_FIELD as _SIGNATURE_FIELD,
    STATUS_RETIRED_KEY as _ATT_RETIRED_KEY,
    STATUS_UNVERIFIABLE as _ATT_UNVERIFIABLE,
    _as_keyset,
    key_fingerprint as _key_fingerprint,
    STATUS_ABSENT as _SEED_ABSENT,
    STATUS_INVALID as _ATT_INVALID,
    STATUS_OK as _SEED_OK,
    STATUS_UNKNOWN_KEY as _ATT_UNKNOWN_KEY,
    content_prehash as _content_prehash,
    verify_authorized_by_signature as _verify_authorized_by_signature,
    verify_shuffle_seed as _verify_shuffle_seed,
)

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
    "supersedes_id", "supersedes_hash", "replaces_hash",
    # OutcomeV1: the maturation rule the decision declared. Shipped so the
    # twin can derive the open-obligation set from the decision feed ITSELF
    # (outcome_v1.derive_open_obligations) rather than being told what is
    # owed by the party the obligation is owed by.
    "outcome_obligation",
    # AI cost tracking (2026-07-31, Item 8): shipped so the twin's own
    # recompute_current_hash can verify governance_decision rows that
    # carry it. Missed in this list when ai_cost was first added --
    # caught while adding shadow_run_hash below and fixed in the same
    # pass, rather than left for a future session to rediscover.
    "ai_cost",
    # Recommendation shadow-run/score (2026-07-31, Item 9): which shadow
    # run a shadow-score row is scoring. Its own field, not a reuse of
    # replaces_hash -- see canonical_fields.py.
    "shadow_run_hash",
    # F2 human-selection capture (2026-08-07): which governance_decision
    # row a human_selection row is reviewing. Its own field, same posture
    # as shadow_run_hash -- see canonical_fields.py.
    "decision_hash",
    # Keyed attestation over the row's authorized_by claim (hex
    # HMAC-SHA256). Shipped so the witness holds the customer's honest
    # copy and so recompute_current_hash can verify any row that carries
    # it -- it enters the hash via the shared OPTIONAL_HASHED_FIELDS
    # contract like every other optional field here (the shipped column
    # name already matches the canonical key). Legacy rows carry NULL and
    # recompute exactly as before. See
    # governance/authorized_by_attestation.py.
    "authorized_by_sig",
    # TACK Layer 5 verdict receipts. Both enter the hash via the shared
    # OPTIONAL_HASHED_FIELDS contract (column name == canonical key, like
    # every optional field above), so recompute_current_hash covers them
    # with no change here. subject_digest binds a decision to its stored
    # input_data; shuffle_seed is reserved for Layer 1 and NULL today.
    "subject_digest",
    "shuffle_seed",
]


def domain_from_cassette_version(cassette_version: Optional[str]) -> Optional[str]:
    """Extract the domain segment from a cassette_version string.

    cassette_version is always "domain:name:version"
    (cassette_schema.cassette_version_of) -- a fixed, code-generated
    format, never free text -- so the domain is reliably everything
    before the first colon. Returns None when there is nothing to parse,
    same "not yet known" posture as outcome_obligation/decided_at when
    a row predates this field.
    """
    if not cassette_version:
        return None
    return cassette_version.split(":", 1)[0] or None


def _ledger_dumps(obj: Any) -> bytes:
    # Byte-for-byte the serialization ledger_postgres.py uses at append time:
    # json.dumps(canonical_entry, sort_keys=True, default=str).encode()
    # (note: default separators, NOT the compact separators of canonical_json)
    return json.dumps(obj, sort_keys=True, default=str).encode()


def canonical_form(row: Dict[str, Any]) -> Dict[str, Any]:
    """The canonical dict a shipped/decrypted row hashes to current_hash.

    Mirrors ledger_postgres.append() for base rows and
    ledger_postgres.append_decision() for governance decisions, kind by
    kind. recompute_current_hash hashes it; content_prehash_of drops the
    signature field from it for the abv3 attestation check.
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
    elif row.get("record_kind") in ("regulatory_cassette_inserted",
                                    "regulatory_cassette_removed"):
        # Mirrors ledger_postgres.record_regulatory_cassette_event().
        # mode + regulation ride in the data JSONB (shipped).
        d = row.get("data") or {}
        canonical = {
            "record_kind": row["record_kind"],
            "cassette_version": row["cassette_version"],
            "mode": d.get("mode"),
            "regulation": d.get("regulation"),
            "previous_hash": row["previous_hash"],
        }
        # cassette_hash + cassette_code_hash + authorized_by enter via the
        # shared contract; the shipped column names match the canonical keys.
        apply_optional_hashed_fields(canonical, row)
    elif row.get("record_kind") == "regulatory_disclosure":
        # Mirrors ledger_postgres.record_regulatory_disclosure(). The finding
        # body was stored in decision_output; regulation/check/action/subject
        # in data. NOTE: cassette_code_hash is never set on disclosure rows,
        # so applying the full shared contract stays byte-identical to the
        # writer (absent -> omitted).
        d = row.get("data") or {}
        canonical = {
            "record_kind": "regulatory_disclosure",
            "cassette_version": row["cassette_version"],
            "regulation": d.get("regulation"),
            "check": d.get("check"),
            "action": d.get("action"),
            "subject": d.get("subject"),
            "finding": row["decision_output"],
            "previous_hash": row["previous_hash"],
        }
        apply_optional_hashed_fields(canonical, row)
    elif row.get("record_kind") == "outcome_harm_event":
        # Mirrors ledger_postgres.record_outcome_harm_event(). OutcomeV1's
        # governance exception: the finding body was stored in decision_output,
        # the pointer + kind + subject + discovery time in data. Like a
        # disclosure row, cassette_code_hash is never set here, so the shared
        # contract stays byte-identical to the writer.
        d = row.get("data") or {}
        canonical = {
            "record_kind": "outcome_harm_event",
            "cassette_version": row["cassette_version"],
            "harmed_decision": d.get("harmed_decision"),
            "harm_kind": d.get("harm_kind"),
            "subject": d.get("subject"),
            "discovered_at": d.get("discovered_at"),
            "finding": row["decision_output"],
            "previous_hash": row["previous_hash"],
        }
        apply_optional_hashed_fields(canonical, row)
    elif row.get("record_kind") in _CONTRACT_CANONICAL_FIELDS:
        # Mirrors ledger_postgres._append_contract_row(). Contract
        # compliance attestation: every field the kind hashes was
        # stored in the data JSONB under its canonical name, so the
        # rebuild is a key copy in the declared order. Only
        # contract_egress carries a finding body (in decision_output);
        # the other three hash no finding, and omitting the key here
        # matches the writer exactly. Recompute site 3 of 3.
        d = row.get("data") or {}
        kind = row["record_kind"]
        canonical = {
            "record_kind": kind,
            "cassette_version": row["cassette_version"],
        }
        for key in _CONTRACT_CANONICAL_FIELDS[kind]:
            canonical[key] = d.get(key)
        if kind in _CONTRACT_KINDS_WITH_FINDING:
            canonical["finding"] = row["decision_output"]
        canonical["previous_hash"] = row["previous_hash"]
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
    elif row.get("record_kind") == "recommendation_shadow_run":
        # Mirrors ledger_postgres.record_recommendation_shadow_run().
        # recommendation_kind + subject rode in data; inputs in
        # input_data; the recommendation itself in decision_output.
        d = row.get("data") or {}
        canonical = {
            "record_kind": "recommendation_shadow_run",
            "cassette_version": row["cassette_version"],
            "recommendation_kind": d.get("recommendation_kind"),
            "subject": d.get("subject"),
            "inputs": row["input_data"],
            "recommendation": row["decision_output"],
            "previous_hash": row["previous_hash"],
        }
        apply_optional_hashed_fields(canonical, row)
    elif row.get("record_kind") == "recommendation_shadow_score":
        # Mirrors ledger_postgres.record_recommendation_shadow_score().
        # The actual outcome rode in input_data, the computed score in
        # decision_output; shadow_run_hash has its own shipped column,
        # entering via the shared contract like every other optional
        # field here (the shipped column name already matches the
        # canonical key).
        canonical = {
            "record_kind": "recommendation_shadow_score",
            "actual": row["input_data"],
            "score": row["decision_output"],
            "previous_hash": row["previous_hash"],
        }
        apply_optional_hashed_fields(canonical, row)
    elif row.get("record_kind") == "human_selection":
        # Mirrors ledger_postgres.record_human_selection(). F2. The
        # selection + rationale rode in data; recommendation_shown (looked
        # up from the parent governance_decision at write time) in
        # decision_output; decision_hash has its own shipped column,
        # entering via the shared contract like shadow_run_hash.
        d = row.get("data") or {}
        canonical = {
            "record_kind": "human_selection",
            "cassette_version": row["cassette_version"],
            "human_selection": d.get("human_selection"),
            "rationale": d.get("rationale"),
            "recommendation_shown": row["decision_output"],
            "previous_hash": row["previous_hash"],
        }
        apply_optional_hashed_fields(canonical, row)
    elif row.get("record_kind") == "observed_event":
        # Mirrors the observed_event loop in ledger_postgres.append_decision.
        # The EventV1 body rode verbatim in input_data; the FIXED canonical
        # form (no optional fields -- every observed_event row is new) is
        # built by the shared observed_event_canonical the writer and
        # verify_chain also call. Recompute site 3 of 3.
        body = row.get("input_data") or {}
        canonical = _observed_event_canonical(body, row["previous_hash"])
    elif row.get("record_kind") == _ATTESTATION_POLICY_RECORD_KIND:
        # Mirrors ledger_postgres.record_attestation_policy(). The marker
        # body rode in data; fixed canonical form from the shared builder.
        # Recompute site 3 of 3.
        canonical = _attestation_policy_canonical(
            row.get("data") or {}, row.get("reason"), row["previous_hash"])
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
    return canonical


def recompute_current_hash(row: Dict[str, Any]) -> str:
    """Recompute what current_hash must be for a shipped/decrypted row."""
    return hashlib.sha256(_ledger_dumps(canonical_form(row))).hexdigest()


def content_prehash_of(row: Dict[str, Any]) -> str:
    """The content pre-hash an abv3 signature on this row covers: its
    canonical form as it stood before the signature field was added."""
    return _content_prehash(canonical_form(row))


def verify_row_attestation(row: Dict[str, Any], keys: Any) -> Tuple[str, Optional[str]]:
    """The witness's own signature check: (status, detail) from
    authorized_by_attestation.verify_authorized_by_signature, with the
    content pre-hash rebuilt here so an abv3 receipt is checked against the
    content the witness holds, not against anything the writer says."""
    return _verify_authorized_by_signature(
        row, keys, content_prehash=content_prehash_of(row))


# ---------------------------------------------------------------------------
# Verdict receipts (TACK Layer 5): the six things an outside auditor can tell
# about a row. Each name is the word the offline verifier prints.
# ---------------------------------------------------------------------------

VIOLATION_TAMPERED = "TAMPERED"          # the row's bytes are not what was hashed
VIOLATION_TRANSPLANTED = "TRANSPLANTED"  # a verdict moved onto content it was not issued for
VIOLATION_SEED_FORGED = "SEED_FORGED"    # the agent, not the server, chose the gate order
VIOLATION_TRUNCATED = "TRUNCATED"        # the chain is shorter than, or diverges from, its anchor
VIOLATION_UNATTESTED = "UNATTESTED"      # an accountable claim with no signature, after enforcement began


def verify_subject_binding(row: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """TRANSPLANTED check: a decision's subject_digest must be the CNS digest
    of its stored input_data. Independent of the hash chain: an attacker who
    recomputes current_hash after moving a digest is still caught, because
    the digest is recomputed from the content, not read off the row. A row
    with no digest (written before receipts) has nothing to check."""
    stored = row.get("subject_digest")
    if not stored:
        return True, None
    try:
        expected = _cns_subject_digest(row.get("input_data") or {})
    except TypeError as exc:
        return False, (f"{VIOLATION_TRANSPLANTED}: stored input_data is not "
                       f"canonically encodable ({exc})")
    if expected != stored:
        return False, (f"{VIOLATION_TRANSPLANTED}: subject_digest {str(stored)[:16]}.. "
                       f"was not issued for this row's input_data "
                       f"(recomputed {expected[:16]}..)")
    return True, None


def verify_shuffle_seed_row(row: Dict[str, Any], keys: Any = None) -> Tuple[bool, Optional[str]]:
    """SEED_FORGED check: a row carrying a shuffle_seed must re-derive it by
    the server's rule (authorized_by_attestation.derive_shuffle_seed) under a
    held key. A seed the witness cannot re-derive, because it holds no key,
    fails closed: it is not a seed the witness can vouch for. A row with no
    seed (every row written today) has nothing to check."""
    status, detail = _verify_shuffle_seed(
        row.get("shuffle_seed"), row.get("previous_hash"), row.get("record_kind"), keys)
    if status in (_SEED_ABSENT, _SEED_OK):
        return True, None
    return False, f"{VIOLATION_SEED_FORGED}: {detail or status}"


def verify_rows(rows: List[Dict[str, Any]], keys: Any = None
                ) -> Optional[Tuple[Any, str, str]]:
    """The witness's walk over a whole chain, in id order: the first failing
    (row id, violation kind, detail), or None when every row holds.

    Per row, in this order: the link to the previous row and the hash
    recompute (TAMPERED), the subject binding (TRANSPLANTED), a present
    signature under the held keys (TAMPERED when invalid, UNATTESTED when
    its key is unknown), a present seed (SEED_FORGED), and, once an
    attestation_policy marker has been passed, an accountable claim with no
    signature (UNATTESTED). Rows before the marker are untouched.
    """
    prev = "genesis"
    policy_from = None
    for row in rows:
        row_id = row.get("id")
        if row.get("previous_hash") != prev:
            return (row_id, VIOLATION_TAMPERED,
                    f"chain broken: previous_hash {str(row.get('previous_hash'))[:16]}.. "
                    f"does not link to {str(prev)[:16]}..")
        ok, detail = deep_verify_row(row, keys)
        if not ok:
            kind = (detail or VIOLATION_TAMPERED).split(":", 1)[0]
            if kind not in (VIOLATION_TAMPERED, VIOLATION_TRANSPLANTED,
                            VIOLATION_SEED_FORGED, VIOLATION_UNATTESTED):
                kind = VIOLATION_TAMPERED
            return (row_id, kind, detail or "")
        if (policy_from is not None and row.get("authorized_by")
                and not row.get(_SIGNATURE_FIELD)):
            return (row_id, VIOLATION_UNATTESTED,
                    f"authorized_by {row.get('authorized_by')!r} carries no signature; "
                    f"attestation has been enforced since row {policy_from}")
        if (row.get("record_kind") == _ATTESTATION_POLICY_RECORD_KIND
                and policy_from is None):
            policy_from = row_id
        prev = row.get("current_hash")
    return None


# ---------------------------------------------------------------------------
# The external head anchor (TACK Layer 5, step 2.6). Borrowed from the
# Resume_OS seal: a chain verifies link by link, so editing or removing a
# middle row is caught, but a chain truncated at the tail and rebuilt from
# there is internally perfect. The one thing the database cannot supply is a
# record of what the head WAS, kept somewhere its writer role cannot reach
# and signed with a key the agent does not hold. Head hash, row count,
# sealed-at time and key fingerprint, and an HMAC over those four.
# ---------------------------------------------------------------------------

HEAD_ANCHOR_ENV = "ICEBERG_LEDGER_ANCHOR_PATH"
HEAD_ANCHOR_VERSION = 1
_ANCHOR_DOMAIN = b"sentinel_os.head_anchor.v1"
_ANCHOR_FIELDS = ("head", "entries", "sealed_at", "key_fingerprint")


def head_anchor_payload(head: str, entries: int, sealed_at: str,
                        key_fingerprint: str) -> bytes:
    body = canonical_json({"entries": int(entries), "head": head,
                           "key_fingerprint": key_fingerprint,
                           "sealed_at": sealed_at})
    return _ANCHOR_DOMAIN + b"\x00" + body


def build_head_anchor(head: str, entries: int, key: bytes,
                      sealed_at: Optional[str] = None) -> Dict[str, Any]:
    """The anchor for a chain whose head is `head` after `entries` rows,
    signed with `key` (the ledger attestation key). Never unsigned: an
    anchor anybody could write anchors nothing."""
    if not key:
        raise CustodyError("a head anchor needs the ledger attestation key")
    if sealed_at is None:
        from datetime import datetime, timezone
        sealed_at = datetime.now(timezone.utc).isoformat()
    fp = _key_fingerprint(bytes(key))
    import hmac as _hmac
    digest = _hmac.new(bytes(key), head_anchor_payload(head, entries, sealed_at, fp),
                       hashlib.sha256).hexdigest()
    return {"v": HEAD_ANCHOR_VERSION, "head": head, "entries": int(entries),
            "sealed_at": sealed_at, "key_fingerprint": fp, "hmac": digest}


def write_head_anchor(path: str, head: str, entries: int, key: bytes,
                      sealed_at: Optional[str] = None) -> Dict[str, Any]:
    """Write the anchor atomically (temp file, then rename) so a reader never
    sees a torn anchor. Returns the anchor as written."""
    anchor = build_head_anchor(head, entries, key, sealed_at)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(anchor, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    return anchor


def read_head_anchor(path: str) -> Dict[str, Any]:
    """Load an anchor file. Raises CustodyError when it is missing, unreadable
    or not an anchor: an absent anchor is a finding, never a pass."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            anchor = json.load(fh)
    except (OSError, ValueError) as exc:
        raise CustodyError(f"anchor {path} cannot be read: {type(exc).__name__}: {exc}") from exc
    if not isinstance(anchor, dict) or anchor.get("v") != HEAD_ANCHOR_VERSION \
            or any(k not in anchor for k in _ANCHOR_FIELDS + ("hmac",)):
        raise CustodyError(f"anchor {path} is not a v{HEAD_ANCHOR_VERSION} head anchor")
    return anchor


def verify_head_anchor(anchor: Dict[str, Any], keys: Any) -> Tuple[str, Optional[str]]:
    """(status, detail) for the anchor's HMAC under the key it names, using
    the same status vocabulary as the row attestation."""
    import hmac as _hmac
    ks = _as_keyset(keys)
    if ks.is_empty():
        return (_ATT_UNVERIFIABLE, "no key held to check the anchor")
    fp = str(anchor.get("key_fingerprint"))
    payload = head_anchor_payload(str(anchor.get("head")), int(anchor.get("entries") or 0),
                                  str(anchor.get("sealed_at")), fp)
    expected = str(anchor.get("hmac"))

    def matches(k: bytes) -> bool:
        return _hmac.compare_digest(_hmac.new(k, payload, hashlib.sha256).hexdigest(), expected)

    k = ks.trusted_key(fp)
    if k is not None:
        return (_SEED_OK, None) if matches(k) else (
            _ATT_INVALID, f"anchor HMAC does not verify under key {fp}: the anchor was altered")
    k = ks.retired_key(fp)
    if k is not None:
        return (_ATT_RETIRED_KEY, f"anchor is valid under retired key {fp}") if matches(k) else (
            _ATT_INVALID, f"anchor HMAC does not verify under retired key {fp}")
    return (_ATT_UNKNOWN_KEY, f"anchor signed by key {fp}, which this verifier does not hold")


def check_head_anchor(rows: List[Dict[str, Any]], anchor: Dict[str, Any],
                      keys: Any) -> Optional[Tuple[Any, str, str]]:
    """Compare a chain (rows in id order, each with id and current_hash) to
    its anchor. None when the anchored head is a prefix of the chain; else
    (row id, TRUNCATED, detail). Rows appended after the anchor was sealed
    are expected; fewer rows than anchored, or a different hash at the
    anchored position, means the chain was cut or rebuilt. An anchor that
    does not verify cannot vouch for a head, so that fails closed too.
    """
    status, detail = verify_head_anchor(anchor, keys)
    if status not in (_SEED_OK, _ATT_RETIRED_KEY):
        return (None, VIOLATION_TRUNCATED, f"anchor cannot be trusted: {detail}")
    entries = int(anchor.get("entries") or 0)
    if entries <= 0:
        return None
    if len(rows) < entries:
        return (rows[-1].get("id") if rows else None, VIOLATION_TRUNCATED,
                f"the chain holds {len(rows)} row(s) but the anchor sealed "
                f"{entries}; {entries - len(rows)} row(s) are missing from the tail")
    at = rows[entries - 1]
    if at.get("current_hash") != anchor.get("head"):
        return (at.get("id"), VIOLATION_TRUNCATED,
                f"the anchored head {str(anchor.get('head'))[:16]}.. is not the hash at "
                f"row {entries} ({str(at.get('current_hash'))[:16]}..): the chain was "
                f"rebuilt after it was sealed")
    return None


def deep_verify_row(row: Dict[str, Any], keys: Any = None) -> Tuple[bool, Optional[str]]:
    """(ok, detail). ok=True when the recomputed hash equals the row's
    current_hash AND the row's receipt fields hold: its subject_digest
    recomputes from its input_data (else TRANSPLANTED) and any shuffle_seed
    re-derives under a held key (else SEED_FORGED). `keys` is what the
    witness holds for the seed check; rows without a seed need none."""
    try:
        recomputed = recompute_current_hash(row)
    except (KeyError, TypeError) as exc:
        return False, f"recompute-failed:{type(exc).__name__}:{exc}"
    if recomputed != row.get("current_hash"):
        return False, (f"{VIOLATION_TAMPERED}: hash-mismatch: recomputed {recomputed[:16]}.. "
                       f"!= stored {str(row.get('current_hash'))[:16]}..")
    ok, detail = verify_subject_binding(row)
    if not ok:
        return ok, detail
    # The seed check runs before the signature check on purpose: an abv3
    # signature covers the whole row, so a forged seed also breaks it, and
    # the specific finding (SEED_FORGED) must name the attack rather than
    # be reported as its consequence (TAMPERED). Same reason the subject
    # binding runs first.
    ok, detail = verify_shuffle_seed_row(row, keys)
    if not ok:
        return ok, detail
    if keys is not None and row.get(_SIGNATURE_FIELD):
        status, why = verify_row_attestation(row, keys)
        if status == _ATT_INVALID:
            return False, f"{VIOLATION_TAMPERED}: attestation invalid: {why}"
        if status in (_ATT_UNKNOWN_KEY, _ATT_UNVERIFIABLE):
            # A caller that hands over keys means to verify; a signature
            # none of them can check is not attested to anyone it trusts.
            return False, f"{VIOLATION_UNATTESTED}: {why}"
    return True, None
