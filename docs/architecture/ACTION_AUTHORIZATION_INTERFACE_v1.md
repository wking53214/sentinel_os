# Sentinel OS Action Authorization Interface

**Status:** Contract foundation implemented in
`sentinel_os/governance/action_authorization_v1.py`. No production consumer or
provider adapters are wired. This is not an authorization guarantee.

## Purpose and boundary

Sentinel OS currently evaluates and records governance results but has no
identified production business-action consumer. Consequently, there is no real
action boundary at which this interface is enforced today.

The interface is intended for the component immediately before a governed
effect. An HTTP response, queue acknowledgement, recorded human selection, or
caller-provided `review_required` flag is not authorization. Every route to an
eventual effect, including direct function calls and retries, must pass through
the same gate.

## Contract types

The Python API is `ActionAuthorizationGate.authorize(target, action)`, which
returns an `AuthorizationResult` containing status, reason, target, action,
and optional review-evidence ID. The four statuses mean:

- `PERMITTED`: configured policy permits this exact request and, when review
  is required, exact-target evidence passed configured integrity, reviewer
  authentication, and reviewer-authority verification. This is an evaluation
  result, not a capability token or proof that an effect occurred.
- `DENIED`: trusted state or evidence affirmatively fails a required check, or
  policy says this action is not allowed.
- `REVIEW_REQUIRED`: policy requires review but no evidence is available.
- `INDETERMINATE`: a required lookup, policy result, or verification failed,
  was malformed, or was ambiguous. It is never a permit.

`AuthorizationTarget` distinguishes `GOVERNANCE_DECISION` from
`REGULATORY_DISCLOSURE_FINDING` and carries a stable record identifier and
lowercase SHA-256 evidence hash. Target type, identifier, and hash must all
match the state and review evidence returned by trusted providers. A regulatory
disclosure finding is never treated as a `governance_decision`.

## Provider trust boundary

The gate receives authoritative decision state, policy assessment, review
evidence, and verification through configured provider interfaces. Its
`authorize` method accepts no approval boolean, review object, or client
principal. Provider construction and wiring are part of the trust boundary; a
future consumer must not accept untrusted provider implementations from a
request.

The policy provider determines whether review is required from loaded state.
`NOT_REQUIRED` preserves the existing non-review path unless the target is an
`ACTION_BLOCK`, which remains denied. `REQUIRED` permits only selections
explicitly listed by policy. The supported vocabulary is limited to the
existing lowercase ledger values `concur`, `override`, and `escalate`; their
meaning remains scoped to the existing governor-verdict contract.

The review provider loads evidence by exact target. The verifier must confirm
ledger integrity and attestation, authenticated individual reviewer identity,
and reviewer authority for this exact target, action, and selection. All three
verifications must explicitly succeed. A ledger attestation of an API-key
label alone is insufficient. No production provider currently implements
these checks for action authorization.

The gate fails closed for missing targets, target/hash mismatches, invalid
policy selection vocabulary, missing or malformed evidence, provider
exceptions, failed integrity/identity/authority checks, and ambiguous verifier
results. A recorded human selection alone cannot grant authorization.

## Policy decisions intentionally unresolved

Existing `CONCUR`, `OVERRIDE`, and `ESCALATE` semantics apply to the governor's
verdict on a `governance_decision`. For a regulatory `ACTION_BLOCK`, `CONCUR`
and `ESCALATE` always preserve the block. Only `OVERRIDE` can proceed to
verification, and then only when an explicit target-specific policy assessment
permits override, identifies its policy and version, permits the selection,
and the verifier confirms the specific reviewer may override that exact
finding and action. No such release policy or reviewer authority is currently
established, so the current outcome is denial.

Before any regulatory block can be released, policy owners must specify which
findings require review, who may review them, how reviewer identity and roles
are established, the exact finding-specific OVERRIDE rule, and how
supersession or revocation works. The current API-key authentication does not
provide individual human identity or reviewer roles. These decisions are not
inferred by this interface.

## Relationship to existing components

- `RegulatoryDeck.judge()` raises `RegulatoryBlock` after disclosure of an
  `ACTION_BLOCK`; it does not grant release authority.
- `PostgreSQLLedger.record_regulatory_disclosure()` stores a disclosure row
  whose finding is covered by the ledger hash. There is no existing human
  release record for that finding type.
- `PostgreSQLLedger.record_human_selection()` records selections against
  `governance_decision` rows. It does not establish reviewer authority or
  authorize an action.
- `api_server_v2.py` human-review routes are not action boundaries, and their
  API-key name is not individual reviewer identity.
- `contract_egress.request_egress()` has no identified production caller and
  relies on callers honoring its result.

The new gate does not yet adapt these ledger methods to the review-evidence
contract, nor does it verify PostgreSQL, chain, attestation, cassette, or twin
integrity itself. Implementations must use the established ledger write and
verification paths rather than creating a parallel path.

## Integration and guarantees

A future consumer must load target state from a trusted source, apply the
authoritative policy, call this gate immediately before the effect, and perform
no effect unless the result is `PERMITTED`. The current module supplies no
consumer wrapper, action token, durable intent, or transaction spanning an
external effect. For external actions, the eventual integration must specify
an outbox/idempotency strategy or document the remaining time-of-check to
time-of-use limitation.

## Acceptance tests

The focused tests in `sentinel_os/Tests/test_action_authorization_v1.py` use
in-memory provider doubles. They cover target and hash binding, malformed or
missing state/evidence, verifier exceptions and ambiguous results, policy
selection semantics, regulatory-block denial, API-key-label rejection by the
verifier contract, and rejection of caller-supplied approval/evidence
arguments. They do not prove PostgreSQL durability, ledger-chain integrity,
twin verification, concurrency safety, or real action-boundary enforcement.
