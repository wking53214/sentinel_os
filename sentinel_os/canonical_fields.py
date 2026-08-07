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

# Node-role vocabulary (2026-07-31, relocated here so the kernel doesn't
# reach into an IVR-side file for one shared constant): a governance path
# outside telephony -- sentinel_core.py's outcome/journey classification --
# needs to recognize a "queue" stop without importing anything IVR-specific.
# twilio_log_ingestion.py imports this back rather than redefining it, so
# there is exactly one place this string is spelled.
NODE_ROLE_QUEUE = "queue"

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
    "outcome_obligation",  # OutcomeV1: the maturation rule in force at decision time
    # Distinct from supersedes_hash on purpose: supersedes_hash is Item 6's
    # human-authored correction of an EXISTING decision's own verdict ("this
    # row's outcome was wrong; the corrected one is W"). replaces_hash is a
    # brand-new, independently-judged governance_decision that makes an
    # earlier decision's OUTCOME OBLIGATION moot (mortgage permanent
    # modification: a new loan number, a new decision, the old obligation
    # abandoned via outcome_v1.REASON_DECISION_SUPERSEDED -- never a
    # decision_supersession row). Conflating the two under one column would
    # make an examiner unable to tell "corrected" from "replaced" apart from
    # context. See cassettes/mortgage_cassette.py's module docstring and
    # obligation_supersession.py for where this is actually used.
    "replaces_hash",
    # AI cost tracking (2026-07-31): real token counts + computed dollar
    # cost from ai_cost_tracking.py, for whichever governance decision
    # actually called the Claude API (currently only safety_check, via
    # claude_governance_api.py -- see that module's docstring). A dict,
    # same as `output`, not a scalar -- everything about one call's cost
    # was computed together and belongs together. NULL/absent on every
    # decision that never called the API (nothing to disclose) and on
    # every row written before this field existed, same migration
    # guarantee every other optional field here already has.
    "ai_cost",
    # Item 9 (2026-07-31): which recommendation_shadow_run a
    # recommendation_shadow_score row is scoring. Its own field, not a
    # reuse of replaces_hash -- that field already has a specific,
    # fail-closed-enforced meaning (a NEW governance_decision replacing
    # an OLD one, see obligation_supersession.py) that this is not; a
    # shadow score isn't a decision at all, let alone one replacing
    # another, and overloading replaces_hash here would have made that
    # field mean two different things depending on record_kind with
    # nothing to warn a future reader which one they're looking at.
    "shadow_run_hash",
    # F2 (2026-08-07): which governance_decision row a human_selection
    # row is reviewing. Its own field for the same reason shadow_run_hash
    # is its own field and not a reuse of replaces_hash/supersedes_hash --
    # a human_selection isn't a decision, a correction, or a supersession,
    # it's a separate reviewer's verdict ON a decision, and needs a link
    # that means only that. See governance/human_selection_v1.py.
    "decision_hash",
)


# ---------------------------------------------------------------------------
# Contract compliance attestation: the four contract record kinds and the
# fields each one hashes, in the order the writer builds them.
#
# This lives HERE, next to OPTIONAL_HASHED_FIELDS, for exactly the reason
# that list does. Three sites recompute these hashes -- the writer
# (ledger_postgres._append_contract_row), the primary's own verifier
# (ledger_postgres.verify_chain) and the witness
# (twin_custody.recompute_current_hash) -- and the outcome_harm_event
# build already proved what happens when one of them is missed: honest
# rows fail their own verification and it looks exactly like tampering.
# One list, imported by all three, so there is no second copy to forget.
#
# sort_keys=True makes the order hash-irrelevant; it is fixed so the
# three call sites stay visually comparable.
CONTRACT_CANONICAL_FIELDS: Dict[str, tuple] = {
    # Data arrived under a contract. Starts the retention clock.
    "contract_ingest": ("counterparty", "ingest_id", "data_scope",
                        "received_at"),
    # One egress authorization decision, granted OR refused. `decision`
    # is hashed so a refusal cannot later read as an authorization.
    "contract_egress": ("counterparty", "decision", "data_scope", "recipient",
                        "recipient_class", "purpose", "approval_reference",
                        "occurred_at"),
    # An approval grant or its revocation. Revocation is a new row, never
    # an edit of the grant.
    "contract_approval": ("counterparty", "approval_id", "state", "recipient",
                          "recipient_class", "scope", "granted_at",
                          "expires_at", "revoked_at"),
    # A positive deletion event bound to the ingest it retires. `stamp` is
    # hashed and is always "attested" -- see record_contract_deletion.
    "contract_deletion": ("counterparty", "ingest_id", "deleted_at", "scope",
                          "method", "stamp"),
}

# Which contract kinds carry a finding body (stored in decision_output and
# hashed under "finding"). The other three hash no finding at all, and
# omitting the key entirely -- not passing None -- is what keeps the three
# sites byte-identical.
CONTRACT_KINDS_WITH_FINDING: tuple = ("contract_egress",)


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
