"""Keyed attestation that a ledger row's ``authorized_by`` claim was written
by a component holding the service signing key.

WHAT THIS ESTABLISHES
---------------------
* The row was written by something that held ``ICEBERG_LEDGER_ATTESTATION_KEY``
  at write time.
* The ``authorized_by`` string on the row has not been altered since it was
  written -- even by an attacker who can also recompute the unkeyed SHA-256
  chain to stay self-consistent.

WHAT THIS DOES NOT ESTABLISH -- and every message here is written to say so
-------------------------------------------------------------------------
* It does NOT prove the named human or role authorized anything. The name in
  ``authorized_by`` is still an unverified claim about accountability.
* It does NOT identify WHICH holder of the key wrote the row. There is one
  shared service key (locked decision D1); two legitimate key holders are
  indistinguishable from each other and from any process that has read the
  key.
* It provides NO protection once the key is compromised. A leaked key forges
  every signature this module would accept.

So this is an integrity-plus-writer-authenticity check on a single string,
nothing more. It is deliberately NOT called identity verification,
authentication, or authorization anywhere in the codebase.

MECHANISM
---------
HMAC-SHA256 (standard library only) over a small, self-contained payload:

    b"sentinel_os.authorized_by.v1" + b"\\x00" + canonical_json({
        "authorized_by": <the claimed identity string>,
        "previous_hash": <this row's previous_hash -- pins it to one chain
                          position and transitively commits every earlier row>,
        "record_kind":   <the row's record_kind>,
    })

The payload is deliberately narrow -- three values that are each already a
dedicated column at all three hash-recompute sites (the writer,
ledger_postgres.verify_chain, and twin_custody.recompute_current_hash) -- so
recomputing the signature needs no per-record-kind reconstruction and adds no
new byte-exactness drift surface. ``previous_hash`` transitively commits every
earlier row and, because ``ledger_entries.current_hash`` is UNIQUE, pins the
signature to one chain position for every row after genesis (the genesis row's
``previous_hash`` is the literal string "genesis", so a signature on the very
first row of a table is not position-bound -- a non-issue in practice: writing
row 1 of a rebuilt table is already total compromise, and the immutability
triggers block a rebuild in place).

The resulting hex signature is stored in its own nullable column
(``authorized_by_sig``) and enters the SHA-256 hash chain through the same
``OPTIONAL_HASHED_FIELDS`` contract every other optional forensic field uses
(locked decision D5): a signature stripped from a row would change that row's
recomputed ``current_hash`` and break the chain.

CONFIGURATION (locked decision D3 / D4)
--------------------------------------
* ``ICEBERG_LEDGER_ATTESTATION_KEY`` -- the service signing key, supplied by
  the environment. No default. No fallback. If it is unset, rows are written
  with a NULL signature and are honestly reported as unattested.
* ``ICEBERG_LEDGER_REQUIRE_ATTESTATION`` -- opt-in enforcement, OFF unless set
  to a truthy value ("1"/"true"/"yes"/"on"). When ON and no key is configured,
  the ledger refuses to start (see PostgreSQLLedger.__init__). When ON and a
  key is configured, a writer that cannot produce a signature for a present
  ``authorized_by`` claim refuses the write rather than recording an
  unattested claim.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any, Dict, Optional, Tuple

# Environment configuration. Names mirror the existing ICEBERG_* convention
# (ICEBERG_LEDGER_RUNTIME_USER, ICEBERG_REQUIRE_API_KEYS).
ENV_KEY = "ICEBERG_LEDGER_ATTESTATION_KEY"
ENV_REQUIRE = "ICEBERG_LEDGER_REQUIRE_ATTESTATION"

# Domain-separation tag: keeps this HMAC from ever colliding with an HMAC
# computed for some other purpose under the same key. Versioned so the
# payload shape can change later without silently accepting old signatures.
_DOMAIN_TAG = b"sentinel_os.authorized_by.v1"

# Canonical column / canonical-form key name for the signature. Added to
# canonical_fields.OPTIONAL_HASHED_FIELDS so it enters the hash chain the
# same way as cassette_hash, authorized_by, ai_cost, etc.
SIGNATURE_FIELD = "authorized_by_sig"

_TRUTHY = {"1", "true", "yes", "on"}


def attestation_key() -> Optional[bytes]:
    """The configured service signing key as bytes, or None if unset.

    An empty string is treated as unset -- there is no such thing as a
    zero-length signing key here, and a blank env var must not be mistaken
    for a configured one.
    """
    raw = os.environ.get(ENV_KEY)
    if not raw:
        return None
    return raw.encode("utf-8")


def enforcement_required() -> bool:
    """True when ICEBERG_LEDGER_REQUIRE_ATTESTATION is set to a truthy value.

    OFF by default (locked decision D3): turning enforcement on by default
    would fail every existing writer on its first call.
    """
    return os.environ.get(ENV_REQUIRE, "").strip().lower() in _TRUTHY


def _payload(authorized_by: str, previous_hash: Optional[str],
             record_kind: Optional[str]) -> bytes:
    body = json.dumps(
        {
            "authorized_by": authorized_by,
            "previous_hash": previous_hash,
            "record_kind": record_kind,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return _DOMAIN_TAG + b"\x00" + body


def sign_authorized_by(authorized_by: Optional[str], previous_hash: Optional[str],
                       record_kind: Optional[str],
                       key: Optional[bytes]) -> Optional[str]:
    """Return the hex HMAC-SHA256 attestation for an ``authorized_by`` claim,
    or None when there is nothing to sign (no claim) or no key is configured.

    Never raises on a missing key. The caller decides whether a missing key
    is fatal (enforcement on -> refuse the write) or acceptable (enforcement
    off -> store NULL, the row is unattested).
    """
    if not authorized_by or not key:
        return None
    return hmac.new(key, _payload(authorized_by, previous_hash, record_kind),
                    hashlib.sha256).hexdigest()


# Verification outcomes returned by verify_authorized_by_signature().
STATUS_OK = "ok"                     # signature present and valid
STATUS_ABSENT = "absent"             # no authorized_by claim; nothing to attest
STATUS_UNATTESTED = "unattested"     # claim present, signature NULL -- NOT tampering
STATUS_UNVERIFIABLE = "unverifiable" # signature present, but no key to check it
STATUS_INVALID = "invalid"          # signature present and does NOT match


def verify_authorized_by_signature(row: Dict[str, Any],
                                   key: Optional[bytes]) -> Tuple[str, Optional[str]]:
    """Check the keyed attestation on one row dict. Returns (status, detail).

    ``row`` must carry ``authorized_by``, ``authorized_by_sig``,
    ``previous_hash`` and ``record_kind`` (the same names used as columns at
    every recompute site).

    ``STATUS_INVALID`` is the only outcome that means "something is wrong with
    this row": the ``authorized_by`` string was altered after signing, or the
    signature was written under a different key, or it is a forgery attempt.
    ``STATUS_UNATTESTED`` is a normal, honest state for any row written while
    no key was configured and for every row that predates this mechanism.

    Comparison is constant-time via ``hmac.compare_digest``.
    """
    authorized_by = row.get("authorized_by")
    sig = row.get(SIGNATURE_FIELD)
    if not authorized_by:
        return (STATUS_ABSENT, None)
    if not sig:
        return (STATUS_UNATTESTED, None)
    if not key:
        return (STATUS_UNVERIFIABLE, None)
    expected = hmac.new(
        key,
        _payload(authorized_by, row.get("previous_hash"), row.get("record_kind")),
        hashlib.sha256,
    ).hexdigest()
    if hmac.compare_digest(expected, str(sig)):
        return (STATUS_OK, None)
    return (STATUS_INVALID,
            "authorized_by signature does not match a fresh HMAC over the "
            "claimed identity -- the claim was altered after writing, or "
            "signed under a different key")
