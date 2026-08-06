"""The governed egress chokepoint -- one door, chained before it opens.

WHAT THIS IS
------------
A single call that must be made before customer data moves outside the
declared boundary. It checks the movement against the counterparty's
contract terms and any approval the movement claims, chains the result
BEFORE returning, and fails closed.

WHAT "FAILS CLOSED" MEANS HERE, PRECISELY
-----------------------------------------
No live approval, no contract permission, no registered contract, or a
ledger that will not accept the event: the call refuses. A refusal is
chained exactly as an authorization is, so the log is a record of
decisions, not a record of successes. The one case that does NOT get a
chained refusal is a ledger that is itself unavailable -- there is
nowhere to chain it -- and that case raises rather than returning, so
a caller cannot mistake a broken ledger for a granted authorization.
That is proven by test, not merely documented.

WHAT THIS DOES *NOT* DO, AND WHY
--------------------------------
It does not move the data and it does not stop the data moving. It
declines to issue an authorization; the operator's own export code is
what must honor that refusal.

That was a deliberate posture decision, and the argument for it is not
squeamishness. Making Sentinel the pipe would put it in the data
plane: touching customer bytes, becoming an availability dependency of
the operator's exports, and taking on custody liability for data it
previously only witnessed. And it would buy nothing against the threat
that actually matters, because an operator willing to bypass the
chokepoint bypasses a blocking one just as easily by calling a
different client. Same gap either way, at a much higher cost.

It is also consistent with the one meaning "block" already has in this
codebase: regulatory_cassette_interface.RegulatoryBlock refuses to
RETURN A JUDGMENT and never reaches into the acting system. A second,
stronger meaning of the same word in one repo would invite exactly the
misreading this feature exists to prevent.

HONEST SCOPE -- state this wherever egress data is shown
--------------------------------------------------------
This proves the egress log is complete RELATIVE TO THE CHOKEPOINT. It
cannot prove nothing left by a path that never called the chokepoint.
Nothing in this module, the ledger, or the twin closes that gap; only
the operator's own engineering discipline does. Every customer report
carries this sentence, and so does the README.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from contract_cassette import (
    RECIPIENT_CLASS_SUBCONTRACTOR,
    ContractCassette,
    contract_cassette_version_of,
)
from regulatory_cassette_interface import SCREENING_DISCLAIMER

# Decision vocabulary -- these strings ride in ledger rows.
EGRESS_AUTHORIZED = "authorized"
EGRESS_REFUSED = "refused"

# Refusal reason codes. Bounded and typed, in the same spirit as
# outcome_v1's OPEN_REASONS: "refused" with no reason code is the
# mushy answer an auditor gets tired of.
REFUSAL_NO_CONTRACT = "no_contract_registered"
REFUSAL_PURPOSE_PROHIBITED = "purpose_prohibited_by_term"
REFUSAL_PURPOSE_NOT_PERMITTED = "purpose_outside_permitted_list"
REFUSAL_APPROVAL_REQUIRED_MISSING = "approval_required_but_not_referenced"
REFUSAL_APPROVAL_NOT_FOUND = "referenced_approval_not_found"
REFUSAL_APPROVAL_NOT_LIVE = "referenced_approval_not_live_at_egress_time"

REFUSAL_REASONS: Tuple[str, ...] = (
    REFUSAL_NO_CONTRACT,
    REFUSAL_PURPOSE_PROHIBITED,
    REFUSAL_PURPOSE_NOT_PERMITTED,
    REFUSAL_APPROVAL_REQUIRED_MISSING,
    REFUSAL_APPROVAL_NOT_FOUND,
    REFUSAL_APPROVAL_NOT_LIVE,
)

# Approval-check findings (Part 4). PASS/FLAG/INDETERMINATE, the same
# three-state vocabulary the C2 rollup already uses -- not a fourth
# spelling of the same idea.
APPROVAL_PASS = "PASS"
APPROVAL_FLAG = "FLAG"
APPROVAL_INDETERMINATE = "INDETERMINATE"


class EgressLedgerUnavailable(RuntimeError):
    """The chokepoint could not chain its own decision.

    Raised, never returned as a refusal, because a refusal that was
    never written is indistinguishable from a call that never happened.
    A caller must not be able to read this as "authorized".
    """


@dataclass(frozen=True)
class EgressRequest:
    """One proposed movement of data outside the boundary."""

    counterparty_id: str
    data_scope: str
    recipient: str
    recipient_class: str
    purpose: str
    approval_reference: Optional[str] = None
    occurred_at: Optional[str] = None

    def when(self) -> str:
        return self.occurred_at or datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class EgressDecision:
    """The chokepoint's answer, after it has been chained."""

    decision: str
    counterparty_id: str
    reasons: Tuple[str, ...] = ()
    evidence: Dict[str, Any] = field(default_factory=dict)
    current_hash: Optional[str] = None

    @property
    def authorized(self) -> bool:
        return self.decision == EGRESS_AUTHORIZED


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp, returning None when it is absent or
    unparseable. None is never silently treated as 'fine' by callers
    here -- see approval_live_at, which returns INDETERMINATE rather
    than guessing."""
    if not value or not str(value).strip():
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def latest_approval_states(approval_rows: List[Dict[str, Any]]
                           ) -> Dict[str, Dict[str, Any]]:
    """Fold contract_approval rows into the current state per approval_id.

    Grants and revocations are separate chained rows; this is where
    they are read back together. Later rows win, which is well-defined
    because the ledger is append-only and ordered.
    """
    states: Dict[str, Dict[str, Any]] = {}
    for row in approval_rows:
        data = row.get("data") or {}
        approval_id = str(data.get("approval_id") or "")
        if not approval_id:
            continue
        current = states.setdefault(approval_id, {})
        current.update({k: v for k, v in data.items() if v not in (None, "")})
        current["approval_id"] = approval_id
        current["approver"] = row.get("authorized_by") or current.get("approver")
    return states


def approval_live_at(approval: Optional[Dict[str, Any]],
                     at_time: str) -> Tuple[str, Dict[str, Any]]:
    """Was this approval live and unrevoked at `at_time`?

    Returns (PASS | FLAG | INDETERMINATE, evidence).

    INDETERMINATE is returned whenever a timestamp needed for the
    comparison is missing or unparseable, never a lenient PASS. An
    approval whose grant time cannot be read is not an approval that
    was definitely live; it is one nobody can speak to.
    """
    if approval is None:
        return APPROVAL_FLAG, {"reason": REFUSAL_APPROVAL_NOT_FOUND}

    when = _parse_ts(at_time)
    granted = _parse_ts(approval.get("granted_at"))
    if when is None or granted is None:
        return APPROVAL_INDETERMINATE, {
            "reason": "unparseable_or_missing_timestamp",
            "egress_time": at_time,
            "granted_at": approval.get("granted_at"),
        }

    evidence: Dict[str, Any] = {
        "approval_id": approval.get("approval_id"),
        "approver": approval.get("approver"),
        "granted_at": approval.get("granted_at"),
        "expires_at": approval.get("expires_at") or None,
        "revoked_at": approval.get("revoked_at") or None,
        "egress_time": at_time,
    }

    if when < granted:
        evidence["reason"] = "egress_preceded_the_grant"
        return APPROVAL_FLAG, evidence

    expires = _parse_ts(approval.get("expires_at"))
    if approval.get("expires_at") and expires is None:
        evidence["reason"] = "unparseable_expires_at"
        return APPROVAL_INDETERMINATE, evidence
    if expires is not None and when > expires:
        evidence["reason"] = "approval_had_expired"
        return APPROVAL_FLAG, evidence

    revoked = _parse_ts(approval.get("revoked_at"))
    if approval.get("revoked_at") and revoked is None:
        evidence["reason"] = "unparseable_revoked_at"
        return APPROVAL_INDETERMINATE, evidence
    if revoked is not None and when >= revoked:
        evidence["reason"] = "approval_was_revoked_at_or_before_egress"
        return APPROVAL_FLAG, evidence

    if str(approval.get("state") or "") == "revoked" and revoked is None:
        evidence["reason"] = "revoked_without_a_revocation_time"
        return APPROVAL_INDETERMINATE, evidence

    return APPROVAL_PASS, evidence


def evaluate_egress(contract: Optional[ContractCassette],
                    request: EgressRequest,
                    approvals: Dict[str, Dict[str, Any]]
                    ) -> Tuple[str, Tuple[str, ...], Dict[str, Any]]:
    """Pure decision logic: may this egress be authorized?

    No I/O, no ledger, no clock beyond what the request carries -- so
    every branch below is unit-testable without Postgres. The caller
    (request_egress) does the chaining.

    Returns (decision, reason_codes, evidence).
    """
    reasons: List[str] = []
    evidence: Dict[str, Any] = {
        "recipient_class": request.recipient_class,
        "purpose": request.purpose,
        "disclaimer": SCREENING_DISCLAIMER,
    }

    if contract is None:
        return (EGRESS_REFUSED, (REFUSAL_NO_CONTRACT,), evidence)

    evidence["contract_reference"] = contract.get_contract_reference()

    if request.purpose in contract.prohibited_purposes():
        reasons.append(REFUSAL_PURPOSE_PROHIBITED)

    permitted = contract.permitted_purposes()
    if permitted is not None and request.purpose not in permitted:
        reasons.append(REFUSAL_PURPOSE_NOT_PERMITTED)
        evidence["permitted_purposes"] = list(permitted)

    if request.recipient_class in contract.approval_required_classes():
        evidence["approval_required"] = True
        if not request.approval_reference:
            reasons.append(REFUSAL_APPROVAL_REQUIRED_MISSING)
        else:
            approval = approvals.get(str(request.approval_reference))
            verdict, appr_evidence = approval_live_at(approval, request.when())
            evidence["approval_check"] = {"verdict": verdict, **appr_evidence}
            if verdict == APPROVAL_FLAG:
                reasons.append(
                    REFUSAL_APPROVAL_NOT_FOUND if approval is None
                    else REFUSAL_APPROVAL_NOT_LIVE)
            elif verdict == APPROVAL_INDETERMINATE:
                # Fail closed. An approval nobody can speak to is not an
                # approval, and "we could not tell" must never authorize.
                reasons.append(REFUSAL_APPROVAL_NOT_LIVE)

    if reasons:
        return (EGRESS_REFUSED, tuple(reasons), evidence)
    return (EGRESS_AUTHORIZED, (), evidence)


def request_egress(ledger, registry, request: EgressRequest,
                   authorized_by: Optional[str] = None,
                   cassette_hash: Optional[str] = None) -> EgressDecision:
    """THE chokepoint. Call this before data leaves the boundary.

    Chains the decision -- authorization or refusal -- before returning
    it, so the record exists whether or not the caller then behaves.
    Raises EgressLedgerUnavailable if the decision could not be chained
    at all; it never returns an authorization it failed to record.
    """
    contract: Optional[ContractCassette] = None
    try:
        contract = registry.for_counterparty(request.counterparty_id)
    except Exception:
        # A counterparty with no registered contract has no permission
        # granted by anything. Refused, and chained as such.
        contract = None

    approvals: Dict[str, Dict[str, Any]] = {}
    if contract is not None and request.approval_reference:
        rows = ledger.get_contract_rows(
            request.counterparty_id, record_kinds=("contract_approval",))
        approvals = latest_approval_states(rows)

    decision, reasons, evidence = evaluate_egress(contract, request, approvals)

    finding: Dict[str, Any] = {
        "decision": decision,
        "reasons": list(reasons),
        "evidence": evidence,
        "scope_note": EGRESS_SCOPE_NOTE,
    }
    contract_version = (contract_cassette_version_of(contract)
                        if contract is not None
                        else f"contract:{request.counterparty_id}:UNREGISTERED")

    try:
        written = ledger.record_contract_egress(
            contract_version=contract_version,
            counterparty=request.counterparty_id,
            decision=decision,
            data_scope=request.data_scope,
            recipient=request.recipient,
            recipient_class=request.recipient_class,
            purpose=request.purpose,
            occurred_at=request.when(),
            finding=finding,
            approval_reference=request.approval_reference,
            cassette_hash=cassette_hash,
            authorized_by=authorized_by,
        )
    except Exception as exc:
        raise EgressLedgerUnavailable(
            f"egress decision for counterparty '{request.counterparty_id}' could "
            f"not be chained ({exc}); refusing without recording is still a "
            f"refusal, and an authorization that was never written must never "
            f"be returned"
        ) from exc

    return EgressDecision(
        decision=decision,
        counterparty_id=request.counterparty_id,
        reasons=reasons,
        evidence=evidence,
        current_hash=written.get("current_hash"),
    )


EGRESS_SCOPE_NOTE = (
    "This record proves the egress log is complete relative to the governed "
    "chokepoint. It cannot prove that no data left by a path that never "
    "called the chokepoint."
)


def check_subcontractor_approvals(egress_rows: List[Dict[str, Any]],
                                  approval_rows: List[Dict[str, Any]]
                                  ) -> List[Dict[str, Any]]:
    """Part 4's standing check, run over history rather than at the door.

    Every AUTHORIZED egress whose recipient_class is subcontractor must
    reference an approval that was live and unrevoked at egress time.
    Refused egresses are skipped: nothing moved, so there is nothing to
    have been approved.

    Returns one finding per egress: PASS / FLAG / INDETERMINATE. Pure
    logic over rows, so it unit-tests without Postgres.
    """
    approvals = latest_approval_states(approval_rows)
    findings: List[Dict[str, Any]] = []
    for row in egress_rows:
        data = row.get("data") or {}
        if data.get("recipient_class") != RECIPIENT_CLASS_SUBCONTRACTOR:
            continue
        if data.get("decision") != EGRESS_AUTHORIZED:
            continue
        reference = str(data.get("approval_reference") or "")
        occurred_at = str(data.get("occurred_at") or "")
        if not reference:
            verdict, evidence = APPROVAL_FLAG, {
                "reason": REFUSAL_APPROVAL_REQUIRED_MISSING}
        elif not occurred_at:
            verdict, evidence = APPROVAL_INDETERMINATE, {
                "reason": "egress_has_no_recorded_time"}
        else:
            verdict, evidence = approval_live_at(
                approvals.get(reference), occurred_at)
        findings.append({
            "check": "contract_subcontractor_approval",
            "verdict": verdict,
            "egress_hash": row.get("current_hash"),
            "recipient": data.get("recipient"),
            "purpose": data.get("purpose"),
            "occurred_at": occurred_at or None,
            "approval_reference": reference or None,
            "evidence": evidence,
            "disclaimer": SCREENING_DISCLAIMER,
        })
    return findings
