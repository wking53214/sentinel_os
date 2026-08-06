# Contract Compliance Attestation

Lets each counterparty of a data processor independently verify that
their own signed data-use contract is being honored, without trusting
the operator's word and without seeing any other counterparty's data
or learning that other counterparties exist.

Sibling of `regulatory_cassettes/`, and built on the same machinery: a
contract lens is a `RegulatoryCassette` subclass, validated by the same
`validate_regulatory_cassette()`, inserted and removed through the same
`RegulatoryDeck`, riding the same `regulatory_cassette_inserted` /
`regulatory_cassette_removed` record kinds. The one difference is the
reserved identity slot, `contract:<counterparty_id>:<version>`.

## Why "counterparty" and not "customer"

`subject` is already taken in this codebase and means the person a
decision is *about* (`obligation_sweep.subject_of()` -- a loan
applicant). The party who signed the contract is a different identity,
and conflating them would silently mix a data subject into a cohort
keyed by contract counterparty. "Tenant" was rejected because it
implies an infrastructure isolation guarantee this repo does not make,
and these words end up in a document an auditor reads.

## Terms

Structured and typed, never free text. A checker can only be mechanical
if the thing it checks is mechanical.

| Term type | Parameters |
|---|---|
| `RETENTION_MAX_DAYS` | `max_days` (int), optional `backup_max_days` (int) |
| `EGRESS_PROHIBITED` | `purpose` (str) |
| `EGRESS_REQUIRES_APPROVAL` | `recipient_class` (str) |
| `PURPOSE_RESTRICTION` | `permitted_purposes` (list of str) |

Malformed terms are refused at authoring time, not discovered at check
time. An undeclared parameter is an error rather than being ignored: a
typo that silently does nothing is worse than a crash, because the
contract would read as enforced and enforce nothing.

**On `backup_max_days`.** Archived data is retained data -- a retention
clause turns on possession, not on how convenient a copy is to reach.
A contract silent on backups therefore gets the strict reading, one
clock governing every copy. The carve-out exists because purging one
record from immutable backup media is genuinely hard, so real
agreements that address the question give backups their own longer
clock; a term type that could not express that would force every
honest agreement to be authored as a lie. A carve-out *shorter* than
the main clock is refused as a drafting error.

## The checks

**`contract_egress_permission`** (`contract_egress.py`). One governed
door. Every movement of data outside the boundary must pass through
`request_egress()`, which chains its decision -- authorization *or*
refusal -- before returning. Refusal reasons are typed:
`no_contract_registered`, `purpose_prohibited_by_term`,
`purpose_outside_permitted_list`,
`approval_required_but_not_referenced`, `referenced_approval_not_found`,
`referenced_approval_not_live_at_egress_time`.

Fails closed. An approval nobody can speak to is not an approval: an
`INDETERMINATE` liveness result refuses rather than authorizing. The
only case with no chained refusal is a ledger that cannot be written
at all, which raises `EgressLedgerUnavailable` rather than returning,
so a caller can never read a broken ledger as a grant. Proven by test,
not just documented.

**`contract_retention_status`** (`contract_retention.py`). Per
counterparty, per record, per scope: `within_term`, `deleted_on_time`,
`overdue`, or `INDETERMINATE` with a typed reason. Deletion is a
positive chained event bound to the ingest record it retires. Absence
is never read as compliance in any direction -- a missing ingest, a
missing deletion, or an unparseable timestamp all produce
`INDETERMINATE` or `overdue`, never a pass. Reuses the existing sweep
pattern (pure logic, thin swappable fetch wrappers, a `main()` CLI on
the module) rather than adding a scheduler.

**`contract_subcontractor_approval`** (`contract_egress.py`). Every
*authorized* egress with `recipient_class = subcontractor` must
reference an approval that was live and unrevoked at egress time.
`PASS` / `FLAG` / `INDETERMINATE`. Revocation is its own chained row,
never an edit of the grant: the grant happened and the chain says so
permanently.

## Reports and anchoring

`contract_attestation.build_attestation()` produces one counterparty's
scoped report. Scoping is done by the SQL query
(`get_contract_rows` filters on `data->>'counterparty'`), not by
filtering a global result set afterwards.

The non-obvious leak surface is metadata, not identifiers.
`ledger_entries.id` is a **global** sequence, so handing a counterparty
their rows with ids 41, 87 and 214 tells them roughly 170 other rows
were written in between, by someone. It is stripped. For the same
reason the anchor receipt commits to the counterparty's **own
partition head**, never the global chain head.

Two completeness layers, and the report names which is which:

1. **Partition chain** (in-chain). Their rows are folded into a chain
   of their own; removing, altering or reordering any row changes the
   head. Compared against an anchor receipt they already hold.
2. **Independent Completeness Cross-check** (pre-chain). A partition
   chain cannot show a row dropped *before* it was ever chained. That
   gap is closed by `twin_attestation_spec_v1.md` §6.4's ICC, which
   anchors completeness in the counterparty's own submission record.
   Reused, not reinvented -- and it fits better here than where it was
   built, because here the counterparty *is* the sender and naturally
   holds that log. The duty is theirs, and the report says so: Sentinel
   cannot supply a record that is trustworthy precisely because
   Sentinel does not control it.

Anchor receipts are Ed25519-signed and timestamped, reusing
`twin_custody`'s existing signing primitives and `canonical_json`. No
second signing scheme.

**Per-counterparty replicas.** A single shared replica would fail the
isolation requirement structurally, not incidentally: DAP's divergence
detection requires reading `previous_hash`/`current_hash` across the
covered window, so a shared replica leaks other counterparties' row
counts, cadence, and existence to anyone doing the check correctly.

## Posture

Observer and read-only. Every contract lens declares
`MODES == ("observer",)` and validation refuses one that does not.

The egress chokepoint is the single exception, and it is narrower than
it sounds: it refuses to issue an **authorization**. It does not move
data and does not stop data moving; the operator's own export code must
honor the refusal. Making Sentinel the pipe would put it in the data
plane -- touching customer bytes, becoming an availability dependency
of exports, taking on custody liability -- and would buy nothing
against the real threat, because an operator willing to bypass the
chokepoint bypasses a blocking one just as easily by calling a
different client. It also preserves the single meaning "block" already
has here: `RegulatoryBlock` refuses to return a judgment and never
reaches into the acting system.

## Disclosed limitations

These are reproduced in `DISCLOSED_LIMITATIONS` and in every generated
report, not only here.

- **Chokepoint-relative completeness.** This proves the egress log is
  complete *relative to the chokepoint*. It cannot prove that no data
  left by a path that never called the chokepoint. Nothing in this
  module, the ledger, or the twin closes that gap; only the operator's
  own engineering discipline does.
- **Deletion is attested, never verified.** Sentinel does not delete
  and does not watch deletion happen. A storage read-back check would
  still be the operator's code reporting on the operator. Real
  verification needs a signed tombstone from the storage layer, which
  is outside instrumentation and out of scope. What carries weight is
  the handling of the claim the operator did *not* make: past the
  horizon with nothing on file is `overdue`, and silence never becomes
  a pass.
- **No downstream-use claim.** This cannot show whether data was used
  to train a model after it left. Not provable from inside this
  boundary. The log states what left and to whom, and nothing about
  what happened next.
- **No resale claim** beyond what the egress log records.
- **Pre-chain drops need the ICC**, which needs the counterparty's own
  submission record.
- **No auditor-facing, non-Python contract authoring.** Terms are
  authored as Python classes, same as regulatory lenses. A
  higher-level authoring surface for non-engineers is a real need,
  deliberately deferred.
- **Nothing here requires the recipient's own instrumentation**, and
  nothing here can say anything that would.
- **Screening, not adjudication.** Findings flag for human review and
  are never a legal determination of contract compliance.
