"""Canonical optional-field contract shared by the primary ledger and the twin.

WHY THIS FILE EXISTS
--------------------
Phase 1 added exactly one optional field to the SHA-256 canonical form
(`cassette_hash`) and did it in two places that must agree byte-for-byte:

  * governance/ledger_postgres.py::append_decision  (the writer)
  * twin_custody.py::recompute_current_hash          (the witness)

Phase 2 adds four more optional fields (cassette_code_hash, model_identity,
authorized_by, supersedes_hash). If the writer and the witness ever disagree
about *which* keys go into the canonical dict, or *in what form*, every honest
row silently starts failing verification on the twin -- a false DIVERGE that
looks exactly like tampering. That is the single highest-risk failure mode of
this whole phase.

To make divergence impossible-by-construction, both sides call the SAME
function here. There is no second list to forget to update.

THE CONTRACT (identical to the Phase-1 `cassette_hash` idiom)
------------------------------------------------------------
Each optional field is added to the canonical dict ONLY when it is present
and truthy on the row. Because `json.dumps(..., sort_keys=True)` omits absent
keys entirely, a row written before a field existed (value NULL) hashes to
EXACTLY the bytes it hashed to before -- so old rows stay verifiable with no
backfill, no re-hash, no chain break. New rows include the field. This is the
entire migration story for Items 3/5/7 and the supersession link in Item 6.

Fields are applied in a FIXED, documented order. sort_keys=True makes order
irrelevant to the hash, but a fixed order keeps the two call sites visually
identical and reviewable.

NOTHING here changes serialization. The primary uses default separators via
json.dumps(sort_keys=True, default=str); the twin's _ledger_dumps mirrors that
exactly. canonical_json's compact separators are a DIFFERENT serialization for
the envelope layer and are deliberately not touched.
"""
from typing import Any, Dict

# The optional governance-decision fields that enter the hash when present.
# Order is fixed for reviewability; sort_keys makes it hash-irrelevant.
# Adding a new optional hashed field is a ONE-LINE change here -- and it lands
# on the writer and the witness simultaneously because both import this.
OPTIONAL_HASHED_FIELDS = (
    "cassette_hash",       # Phase 1: parameter-snapshot integrity
    "cassette_code_hash",  # Item 3: decision-code integrity
    "model_identity",      # Item 5: which model produced the decision
    "authorized_by",       # Item 7: resolved authorizing identity (role/key name)
    "supersedes_hash",     # Item 6: link from a supersession row to the row it supersedes
)


def apply_optional_hashed_fields(canonical: Dict[str, Any],
                                 source: Dict[str, Any]) -> Dict[str, Any]:
    """Add each optional hashed field to `canonical` iff present-and-truthy in `source`.

    `source` is a plain dict of already-resolved values (the writer builds it
    from the record; the witness builds it from the shipped row). Mutates and
    returns `canonical` for call-site convenience.

    Truthiness gate (not just "is not None") deliberately mirrors the Phase-1
    `if cassette_hash:` guard: an empty string is treated as absent so a blank
    column can never shift the hash of an otherwise-identical row.
    """
    for field in OPTIONAL_HASHED_FIELDS:
        value = source.get(field)
        if value:
            canonical[field] = value
    return canonical
