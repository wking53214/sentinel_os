"""Keyed attestation that a ledger row's ``authorized_by`` claim was written
by a component holding the service signing key.

WHAT THIS ESTABLISHES
---------------------
* The row was written by something that held a configured signing key at write
  time.
* The ``authorized_by`` string on the row has not been altered since it was
  written -- even by an attacker who can also recompute the unkeyed SHA-256
  chain to stay self-consistent.
* Which key signed the row (from a short fingerprint carried in the
  signature), so a key rotation does not make honest history read as forged.

WHAT THIS DOES NOT ESTABLISH -- and every message here is written to say so
-------------------------------------------------------------------------
* It does NOT prove the named human or role authorized anything. The name in
  ``authorized_by`` is still an unverified claim about accountability.
* It does NOT identify WHICH holder of a key wrote the row. Keys are
  service-level, not per-identity (locked decision D1); every holder of a key
  -- and any process that has read it -- is indistinguishable.
* It provides NO protection once a key is compromised. A leaked key forges
  every signature this module would accept under that key.

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

SIGNATURE FORMAT
----------------
Stored in the nullable ``authorized_by_sig`` column, which enters the SHA-256
hash chain through the ``OPTIONAL_HASHED_FIELDS`` contract like every other
optional forensic field (locked decision D5): a signature stripped from a row
changes that row's recomputed ``current_hash`` and breaks the chain.

Two on-disk forms coexist; verification accepts both:

  * v1 (legacy) -- a bare 64-hex HMAC digest. Every row written before key
    rotation support. It carries no indication of which key signed it.
  * v2 -- ``abv2.<keyfp>.<digest>`` where ``keyfp`` is a 16-hex fingerprint of
    the signing key (``key_fingerprint``) and ``digest`` is the same 64-hex
    HMAC. Every row written from now on. The fingerprint lets verification
    pick the right key after a rotation, and lets an operator see -- per row
    -- which key was in use (``... WHERE authorized_by_sig LIKE 'abv2.<fp>.%'``).

The fingerprint is ``sha256(domain || key)[:16]``. This is the JWK-thumbprint
idea, truncated: it reveals 64 bits of a hash of the key, which gives an
attacker no usable purchase on a properly generated key.

KEY ROLES (rotation)
--------------------
* ``ICEBERG_LEDGER_ATTESTATION_KEY`` (+ ``_KEY_FILE``) -- the ONE current
  signing key. New rows are always signed with this. If enforcement is on and
  this is unset, the ledger refuses to start.
* ``ICEBERG_LEDGER_ATTESTATION_KEYS_PREVIOUS`` (+ ``_PREVIOUS_FILE``) -- keys
  retired from signing but still fully trusted for verification. A row signed
  by one verifies as STATUS_OK. This is where a conservative deployment leaves
  every old key, forever -- keys are tiny and retaining them keeps all of
  history verifiable.
* ``ICEBERG_LEDGER_ATTESTATION_KEYS_RETIRED`` (+ ``_RETIRED_FILE``) -- keys the
  operator has deliberately stopped trusting (a suspected compromise, a policy
  sunset) but still recognises. A row signed by one whose HMAC still checks out
  verifies as STATUS_RETIRED_KEY -- a deliberate, operator-chosen state, which
  ``verify_chain`` treats as a violation only when enforcement is on.

A row whose fingerprint matches NONE of current / previous / retired verifies
as STATUS_UNKNOWN_KEY: something signed the ledger with a key this deployment
has never been told about. ``verify_chain`` treats that as a violation
ALWAYS, in every configuration -- it is the one outcome that cannot be
configured away.

The PREVIOUS / RETIRED lists are comma-separated in the env var, or one key
per line in the ``_FILE`` form (for secret managers that project a multi-line
secret as a file). Keys themselves must not contain commas; use the file form
otherwise. A ``_FILE`` path that is set but unreadable raises -- a
named-but-broken key source is a misconfiguration, never silently "no keys".

CONFIGURATION (locked decision D3 / D4)
--------------------------------------
* ``ICEBERG_LEDGER_ATTESTATION_KEY`` / ``ICEBERG_LEDGER_ATTESTATION_KEY_FILE``
  -- the current signing key, supplied directly or as a file path (the idiom
  for Vault Agent, the Secrets Store CSI driver, Docker/Compose secrets). The
  file's surrounding whitespace is stripped. Re-read on every call, so
  rewriting the file rotates with no restart. No default, no placeholder
  fallback. A set-but-broken ``_KEY_FILE`` raises. If neither is set, rows are
  written with a NULL signature and honestly reported as unattested.
* ``ICEBERG_LEDGER_REQUIRE_ATTESTATION`` -- opt-in enforcement, OFF unless set
  to a truthy value ("1"/"true"/"yes"/"on"). When ON and no signing key is
  configured, the ledger refuses to start. When ON, a writer that cannot
  produce a signature for a present ``authorized_by`` claim refuses the write,
  and ``verify_chain`` treats a STATUS_RETIRED_KEY row as a violation.

Generate every key with 32 bytes of CSPRNG output, e.g. ``openssl rand -hex
32``, store it in exactly one system of record, never commit it, and use a
distinct key per environment so a leaked staging key cannot forge production
rows.

ROTATION RUNBOOK
----------------
1. Generate key B (``openssl rand -hex 32``). Add it to the secret store.
2. Move the current key A into ``ICEBERG_LEDGER_ATTESTATION_KEYS_PREVIOUS``
   (append; keep everything already there).
3. Set ``ICEBERG_LEDGER_ATTESTATION_KEY`` = B. Roll the fleet.
4. New rows now carry ``abv2.<fp(B)>.…``; old rows verify via A from the
   PREVIOUS list. Confirm the cutover:
       SELECT split_part(authorized_by_sig, '.', 2) AS keyfp,
              count(*), max(timestamp)
       FROM ledger_entries
       WHERE authorized_by_sig LIKE 'abv2.%'
       GROUP BY 1;
   -- once no rows with fp(A) appear after the cutover instant, every writer
   has moved.
5. Leave A in PREVIOUS indefinitely. Only if A is believed COMPROMISED, move
   it from PREVIOUS to ``ICEBERG_LEDGER_ATTESTATION_KEYS_RETIRED`` -- its old
   rows then read as STATUS_RETIRED_KEY (a violation under enforcement),
   flagging exactly the history that a compromised key could have forged.

There is intentionally no per-row re-signing: the ledger is append-only and
the immutability triggers forbid it. A key you fully drop (remove from both
lists) makes the rows it signed STATUS_UNKNOWN_KEY -- a deliberate governance
decision to stop being able to verify that slice of history.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

# Environment configuration. Names mirror the existing ICEBERG_* convention
# (ICEBERG_LEDGER_RUNTIME_USER, ICEBERG_REQUIRE_API_KEYS); the _FILE forms
# mirror CERT_FILE / KEY_FILE in api_server_resilient.py.
ENV_KEY = "ICEBERG_LEDGER_ATTESTATION_KEY"
ENV_KEY_FILE = "ICEBERG_LEDGER_ATTESTATION_KEY_FILE"
ENV_KEYS_PREVIOUS = "ICEBERG_LEDGER_ATTESTATION_KEYS_PREVIOUS"
ENV_KEYS_PREVIOUS_FILE = "ICEBERG_LEDGER_ATTESTATION_KEYS_PREVIOUS_FILE"
ENV_KEYS_RETIRED = "ICEBERG_LEDGER_ATTESTATION_KEYS_RETIRED"
ENV_KEYS_RETIRED_FILE = "ICEBERG_LEDGER_ATTESTATION_KEYS_RETIRED_FILE"
ENV_REQUIRE = "ICEBERG_LEDGER_REQUIRE_ATTESTATION"

# Domain-separation tag for the HMAC payload. Versioned so the payload shape
# can change later without silently accepting old signatures.
_DOMAIN_TAG = b"sentinel_os.authorized_by.v1"

# Domain-separation tag for the key fingerprint -- kept distinct from the HMAC
# payload tag so a fingerprint can never be confused with a signature digest.
_KEYID_DOMAIN = b"sentinel_os.authorized_by.keyid.v1"

# Prefix marking a v2 signature envelope: "abv2.<keyfp>.<digest>".
_ENVELOPE_TAG = "abv2"
_KEYFP_LEN = 16

# Canonical column / canonical-form key name for the signature. Added to
# canonical_fields.OPTIONAL_HASHED_FIELDS so it enters the hash chain the
# same way as cassette_hash, authorized_by, ai_cost, etc.
SIGNATURE_FIELD = "authorized_by_sig"

_TRUTHY = {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# key resolution
# ---------------------------------------------------------------------------

def attestation_key() -> Optional[bytes]:
    """The configured current signing key as bytes, or None if not configured.

    Resolution order:
      1. ``ICEBERG_LEDGER_ATTESTATION_KEY`` -- used verbatim if set and non-empty.
      2. ``ICEBERG_LEDGER_ATTESTATION_KEY_FILE`` -- a path; the file's contents,
         with surrounding whitespace stripped, are the key.
      3. otherwise None (no key configured -> rows written unattested).

    An empty value is treated as unset -- there is no zero-length signing key,
    and a blank env var or blank file must not be mistaken for a configured one.

    Raises RuntimeError if ``..._KEY_FILE`` is set but the file cannot be read
    or is empty. A named-but-broken key source is a misconfiguration, not the
    same thing as "no key" -- failing loudly here is deliberate, so a
    deployment that meant to sign does not silently write unattested rows.
    """
    raw = os.environ.get(ENV_KEY)
    if raw:
        return raw.encode("utf-8")

    path = os.environ.get(ENV_KEY_FILE)
    if path:
        try:
            contents = open(path, "r", encoding="utf-8").read().strip()
        except OSError as exc:
            raise RuntimeError(
                f"{ENV_KEY_FILE}={path!r} is set but the file could not be "
                f"read ({exc.__class__.__name__}: {exc}). Refusing to treat a "
                f"broken key source as 'no key'."
            ) from exc
        if not contents:
            raise RuntimeError(
                f"{ENV_KEY_FILE}={path!r} is set but the file is empty. "
                f"A zero-length signing key is not a key."
            )
        return contents.encode("utf-8")

    return None


def _keys_from(env_name: str, file_env_name: str) -> List[bytes]:
    """Zero or more keys from a comma-separated env var and/or a file
    (one key per line). Blank entries and surrounding whitespace are dropped.

    Raises RuntimeError if the ``_FILE`` path is set but unreadable -- same
    fail-loud posture as attestation_key().
    """
    items: List[str] = []
    raw = os.environ.get(env_name)
    if raw:
        items.extend(raw.split(","))
    path = os.environ.get(file_env_name)
    if path:
        try:
            items.extend(open(path, "r", encoding="utf-8").read().splitlines())
        except OSError as exc:
            raise RuntimeError(
                f"{file_env_name}={path!r} is set but the file could not be "
                f"read ({exc.__class__.__name__}: {exc})."
            ) from exc
    return [s.strip().encode("utf-8") for s in items if s.strip()]


def key_fingerprint(key: bytes) -> str:
    """Short, stable, non-reversible id for a key: 16 hex chars of
    ``sha256(domain || key)``. Used to tag a v2 signature with its key and to
    pick the right key at verify time.
    """
    return hashlib.sha256(_KEYID_DOMAIN + b"\x00" + bytes(key)).hexdigest()[:_KEYFP_LEN]


class KeySet:
    """The keys a verifier will accept, partitioned into ``trusted`` (current
    signing key + PREVIOUS list -- a match is STATUS_OK) and ``retired``
    (RETIRED list -- a match is STATUS_RETIRED_KEY). Order within ``trusted``
    is current-key-first. Duplicates are collapsed; a key present in both
    trusted and retired counts only as trusted.
    """

    __slots__ = ("trusted", "retired", "_trusted_by_fp", "_retired_by_fp")

    def __init__(self, current: Optional[bytes],
                 previous: Iterable[bytes] = (),
                 retired: Iterable[bytes] = ()):
        trusted: List[bytes] = []
        for k in ([current] if current else []) + [bytes(x) for x in previous]:
            if k and k not in trusted:
                trusted.append(k)
        trusted_set = set(trusted)
        ret: List[bytes] = []
        for k in (bytes(x) for x in retired):
            if k and k not in trusted_set and k not in ret:
                ret.append(k)
        self.trusted = trusted
        self.retired = ret
        self._trusted_by_fp = {key_fingerprint(k): k for k in trusted}
        self._retired_by_fp = {key_fingerprint(k): k for k in ret}

    def is_empty(self) -> bool:
        return not self.trusted and not self.retired

    def trusted_key(self, fp: str) -> Optional[bytes]:
        return self._trusted_by_fp.get(fp)

    def retired_key(self, fp: str) -> Optional[bytes]:
        return self._retired_by_fp.get(fp)


def attestation_keyset() -> KeySet:
    """The full set of keys this process will accept for verification:
    the current signing key, the PREVIOUS list, and the RETIRED list.

    Raises RuntimeError if any configured ``_FILE`` path is set but unreadable.
    """
    return KeySet(
        current=attestation_key(),
        previous=_keys_from(ENV_KEYS_PREVIOUS, ENV_KEYS_PREVIOUS_FILE),
        retired=_keys_from(ENV_KEYS_RETIRED, ENV_KEYS_RETIRED_FILE),
    )


def enforcement_required() -> bool:
    """True when ICEBERG_LEDGER_REQUIRE_ATTESTATION is set to a truthy value.

    OFF by default (locked decision D3): turning enforcement on by default
    would fail every existing writer on its first call.
    """
    return os.environ.get(ENV_REQUIRE, "").strip().lower() in _TRUTHY


# ---------------------------------------------------------------------------
# sign / verify
# ---------------------------------------------------------------------------

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


def _hmac_hex(key: bytes, payload: bytes) -> str:
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _split_envelope(sig: str) -> Tuple[Optional[str], str]:
    """(keyfp, digest). keyfp is None for a v1 legacy bare-digest signature."""
    parts = sig.split(".")
    if len(parts) == 3 and parts[0] == _ENVELOPE_TAG:
        return parts[1], parts[2]
    return None, sig


def sign_authorized_by(authorized_by: Optional[str], previous_hash: Optional[str],
                       record_kind: Optional[str],
                       key: Optional[bytes]) -> Optional[str]:
    """Return the v2 signature envelope ``abv2.<keyfp>.<digest>`` for an
    ``authorized_by`` claim, or None when there is nothing to sign (no claim)
    or no key is configured.

    Never raises on a missing key. The caller decides whether a missing key is
    fatal (enforcement on -> refuse the write) or acceptable (enforcement off
    -> store NULL, the row is unattested).
    """
    if not authorized_by or not key:
        return None
    digest = _hmac_hex(bytes(key), _payload(authorized_by, previous_hash, record_kind))
    return f"{_ENVELOPE_TAG}.{key_fingerprint(key)}.{digest}"


# Verification outcomes returned by verify_authorized_by_signature().
STATUS_OK = "ok"                     # signature present and valid under a trusted key
STATUS_ABSENT = "absent"             # no authorized_by claim; nothing to attest
STATUS_UNATTESTED = "unattested"     # claim present, signature NULL -- NOT tampering
STATUS_UNVERIFIABLE = "unverifiable" # signature present, but no keys held to check it
STATUS_INVALID = "invalid"          # signature present and does NOT match the key it names / any key
STATUS_RETIRED_KEY = "retired_key"  # HMAC checks out, but under a key the operator marked retired-and-distrusted
STATUS_UNKNOWN_KEY = "unknown_key"  # v2 signature names a key fingerprint matching NOTHING this deployment holds

_KeysArg = Union["KeySet", bytes, bytearray, Iterable[bytes], None]


def _as_keyset(keys: _KeysArg) -> KeySet:
    if isinstance(keys, KeySet):
        return keys
    if keys is None:
        return KeySet(None)
    if isinstance(keys, (bytes, bytearray)):
        return KeySet(bytes(keys))
    return KeySet(None, previous=[bytes(k) for k in keys])


def verify_authorized_by_signature(row: Dict[str, Any],
                                   keys: _KeysArg) -> Tuple[str, Optional[str]]:
    """Check the keyed attestation on one row dict. Returns (status, detail).

    ``row`` must carry ``authorized_by``, ``authorized_by_sig``,
    ``previous_hash`` and ``record_kind`` (the same names used as columns at
    every recompute site). ``keys`` may be a KeySet, a single key (bytes), an
    iterable of keys (all treated as trusted), or None.

    Outcomes -- only INVALID and UNKNOWN_KEY mean "something is wrong":
      OK           -- valid under the current key or a PREVIOUS key.
      RETIRED_KEY  -- HMAC valid, but under a key the operator marked retired.
                      A deliberate state; verify_chain flags it only under
                      enforcement.
      UNKNOWN_KEY  -- a v2 signature naming a key fingerprint this deployment
                      does not hold at all. verify_chain flags it ALWAYS.
      INVALID      -- the digest does not match under the key the signature
                      names (v2) or under any held key (v1). Tampering, or a
                      signature made with a key that was never configured
                      here in any role.
      UNATTESTED   -- claim present, signature NULL. Normal for pre-key rows.
      UNVERIFIABLE -- signature present but this process holds no keys.
      ABSENT       -- no authorized_by claim.

    Comparison is constant-time via ``hmac.compare_digest``.
    """
    authorized_by = row.get("authorized_by")
    sig = row.get(SIGNATURE_FIELD)
    if not authorized_by:
        return (STATUS_ABSENT, None)
    if not sig:
        return (STATUS_UNATTESTED, None)

    ks = _as_keyset(keys)
    if ks.is_empty():
        return (STATUS_UNVERIFIABLE, None)

    payload = _payload(authorized_by, row.get("previous_hash"),
                       row.get("record_kind"))
    keyfp, digest = _split_envelope(str(sig))

    def matches(key: bytes) -> bool:
        return hmac.compare_digest(_hmac_hex(key, payload), digest)

    if keyfp is not None:
        # v2: the signature names its key -- verify against exactly that one.
        k = ks.trusted_key(keyfp)
        if k is not None:
            if matches(k):
                return (STATUS_OK, None)
            return (STATUS_INVALID,
                    f"signature does not match a fresh HMAC under key {keyfp} "
                    f"-- the authorized_by claim was altered after writing")
        k = ks.retired_key(keyfp)
        if k is not None:
            if matches(k):
                return (STATUS_RETIRED_KEY,
                        f"signature is valid, but under retired key {keyfp}")
            return (STATUS_INVALID,
                    f"signature does not match under retired key {keyfp}")
        return (STATUS_UNKNOWN_KEY,
                f"signed by key {keyfp}, which this deployment holds as "
                f"neither the current key, a PREVIOUS key, nor a RETIRED key")

    # v1 legacy bare digest -- no key id. Try every key held.
    for k in ks.trusted:
        if matches(k):
            return (STATUS_OK, None)
    for k in ks.retired:
        if matches(k):
            return (STATUS_RETIRED_KEY,
                    "legacy signature matched a retired key")
    return (STATUS_INVALID,
            "legacy signature does not match any configured key -- the "
            "authorized_by claim was altered, or it was signed by a key "
            "never configured here")
