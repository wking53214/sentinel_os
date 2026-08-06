"""Retention obligation checking -- age against the contract's own clock.

WHAT THIS CHECKS
----------------
Per counterparty, per ingested record: how old is it, what does the
contract's RETENTION_MAX_DAYS allow, and is there a positive deletion
event retiring it? Four statuses, and only four:

    within_term      -- not yet due, no deletion needed yet
    deleted_on_time  -- deletion attested at or before the horizon
    overdue          -- horizon passed, no deletion attested
    INDETERMINATE    -- something needed for the comparison is missing

ABSENCE IS NEVER COMPLIANCE
---------------------------
A missing deletion record does not mean the data is gone; it means
nobody said it was. That is why deletion is a positive chained event
bound to the ingest it retires, and why a missing ingest record makes
the whole comparison INDETERMINATE rather than defaulting either way.
Assuming compliance from absence is precisely the inference an
operator benefits from and an auditor should refuse.

PROVENANCE OF THE DELETION CLAIM
--------------------------------
Deletion events are ATTESTED, never verified. Sentinel does not delete
and does not watch the deletion happen; the processor deletes and then
says so. Every status this module returns for a deleted record carries
that stamp, so a reader never mistakes "the operator told us" for "we
saw it." What carries real weight here is not the stamp on a claim the
operator did make, it is the handling of the claim they did not: past
the horizon with nothing on file is `overdue`, and no amount of
silence turns that into a pass.

ARCHIVED DATA IS RETAINED DATA
------------------------------
A retention clause turns on possession, not on how convenient the copy
is to reach, so data sitting in an archive still counts as held. The
one exception is a contract that says otherwise: RETENTION_MAX_DAYS
may declare backup_max_days, and a deletion recorded with scope
"backup" is measured against that longer clock instead. A contract
silent on backups gets the strict reading -- one clock, governing every
copy. See contract_cassette.TERM_OPTIONAL_PARAMETERS for why that
carve-out has to exist at all.

REUSES THE EXISTING SWEEP PATTERN
---------------------------------
The shape here is obligation_sweep.py's: pure logic functions with no
I/O (`retention_status`, `assess_counterparty`), thin independently
swappable fetch wrappers, and a `main()` CLI on the module itself --
the same one-file-owns-its-CLI convention twin_migrate.py and
obligation_sweep.py already use. No new scheduler, and nothing that
needs a cron entry to be correct; whatever triggers it on a cadence
stays a deployment decision.
"""
import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from contract_cassette import ContractCassette
from regulatory_cassette_interface import SCREENING_DISCLAIMER

STATUS_WITHIN_TERM = "within_term"
STATUS_DELETED_ON_TIME = "deleted_on_time"
STATUS_OVERDUE = "overdue"
STATUS_INDETERMINATE = "INDETERMINATE"

RETENTION_STATUSES: Tuple[str, ...] = (
    STATUS_WITHIN_TERM,
    STATUS_DELETED_ON_TIME,
    STATUS_OVERDUE,
    STATUS_INDETERMINATE,
)

# Typed reasons for INDETERMINATE. Bounded vocabulary, same discipline
# as outcome_v1.OPEN_REASONS: "indeterminate" on its own is the mushy
# answer, and the whole point is not to give one.
INDET_NO_INGEST_RECORD = "no_ingest_record"
INDET_NO_RETENTION_TERM = "contract_declares_no_retention_term"
INDET_UNPARSEABLE_INGEST_TIME = "unparseable_ingest_time"
INDET_UNPARSEABLE_DELETION_TIME = "unparseable_deletion_time"

INDETERMINATE_REASONS: Tuple[str, ...] = (
    INDET_NO_INGEST_RECORD,
    INDET_NO_RETENTION_TERM,
    INDET_UNPARSEABLE_INGEST_TIME,
    INDET_UNPARSEABLE_DELETION_TIME,
)

# Scope of a deletion event.
SCOPE_ACTIVE = "active"
SCOPE_BACKUP = "backup"

DELETION_STAMP = "attested"


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value or not str(value).strip():
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True)
class RetentionFinding:
    """One record's retention status under one contract."""

    ingest_id: str
    counterparty_id: str
    status: str
    scope: str
    max_days: Optional[int] = None
    age_days: Optional[int] = None
    due_at: Optional[str] = None
    deleted_at: Optional[str] = None
    stamp: Optional[str] = None
    reason: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "check": "contract_retention_status",
            "ingest_id": self.ingest_id,
            "counterparty_id": self.counterparty_id,
            "status": self.status,
            "scope": self.scope,
            "max_days": self.max_days,
            "age_days": self.age_days,
            "due_at": self.due_at,
            "deleted_at": self.deleted_at,
            "stamp": self.stamp,
            "reason": self.reason,
            "evidence": dict(self.evidence),
            "disclaimer": SCREENING_DISCLAIMER,
        }


def retention_status(ingest_row: Optional[Dict[str, Any]],
                     deletion_rows: List[Dict[str, Any]],
                     max_days: Optional[int],
                     backup_max_days: Optional[int],
                     now: datetime,
                     scope: str = SCOPE_ACTIVE) -> Dict[str, Any]:
    """Pure status computation for ONE record in ONE scope.

    `now` is passed in rather than read from the clock so every branch
    is deterministically testable, matching how outcome_v1 keeps
    `overdue` as arithmetic instead of stored state.
    """
    result: Dict[str, Any] = {"scope": scope}

    if ingest_row is None:
        result.update({"status": STATUS_INDETERMINATE,
                       "reason": INDET_NO_INGEST_RECORD})
        return result

    effective_max = max_days
    if scope == SCOPE_BACKUP and backup_max_days is not None:
        effective_max = backup_max_days
    if effective_max is None:
        result.update({"status": STATUS_INDETERMINATE,
                       "reason": INDET_NO_RETENTION_TERM})
        return result
    result["max_days"] = int(effective_max)

    received = _parse_ts((ingest_row.get("data") or {}).get("received_at"))
    if received is None:
        result.update({"status": STATUS_INDETERMINATE,
                       "reason": INDET_UNPARSEABLE_INGEST_TIME})
        return result

    due_at = received + timedelta(days=int(effective_max))
    result["due_at"] = due_at.isoformat()
    result["age_days"] = int((now - received).total_seconds() // 86400)

    relevant = [r for r in deletion_rows
                if (r.get("data") or {}).get("scope") == scope]
    if relevant:
        # Earliest attested deletion in this scope is the one that
        # retired the record; later ones are re-attestations.
        parsed = []
        for row in relevant:
            data = row.get("data") or {}
            when = _parse_ts(data.get("deleted_at"))
            if when is None:
                result.update({"status": STATUS_INDETERMINATE,
                               "reason": INDET_UNPARSEABLE_DELETION_TIME,
                               "evidence": {"deletion_hash": row.get("current_hash")}})
                return result
            parsed.append((when, data, row))
        parsed.sort(key=lambda p: p[0])
        when, data, row = parsed[0]
        result.update({
            "deleted_at": when.isoformat(),
            "stamp": data.get("stamp") or DELETION_STAMP,
            "evidence": {"method": data.get("method"),
                         "deletion_hash": row.get("current_hash")},
        })
        if when <= due_at:
            result["status"] = STATUS_DELETED_ON_TIME
        else:
            # Attested, but late. Not a pass: the horizon is the term.
            result["status"] = STATUS_OVERDUE
            result["reason"] = "deleted_after_the_contract_horizon"
        return result

    if now > due_at:
        result.update({"status": STATUS_OVERDUE,
                       "reason": "horizon_passed_with_no_deletion_attested"})
    else:
        result["status"] = STATUS_WITHIN_TERM
    return result


def assess_counterparty(contract: ContractCassette,
                        ingest_rows: List[Dict[str, Any]],
                        deletion_rows: List[Dict[str, Any]],
                        now: Optional[datetime] = None
                        ) -> List[RetentionFinding]:
    """Every ingested record for one counterparty, in both scopes.

    Pure logic over already-fetched rows. Deletion rows that reference
    an ingest_id with no ingest record are NOT dropped -- they surface
    as INDETERMINATE, because a deletion for something never recorded
    as received is exactly the discrepancy a silent skip would hide.
    """
    now = now or datetime.now(timezone.utc)
    max_days = contract.retention_max_days()
    backup_max_days = contract.backup_max_days()
    counterparty_id = contract.get_counterparty_id()

    by_ingest: Dict[str, Dict[str, Any]] = {}
    for row in ingest_rows:
        ingest_id = str((row.get("data") or {}).get("ingest_id") or "")
        if ingest_id:
            by_ingest[ingest_id] = row

    deletions_by_ingest: Dict[str, List[Dict[str, Any]]] = {}
    for row in deletion_rows:
        ingest_id = str((row.get("data") or {}).get("ingest_id") or "")
        if ingest_id:
            deletions_by_ingest.setdefault(ingest_id, []).append(row)

    scopes = [SCOPE_ACTIVE]
    if backup_max_days is not None:
        scopes.append(SCOPE_BACKUP)

    findings: List[RetentionFinding] = []
    for ingest_id in sorted(set(by_ingest) | set(deletions_by_ingest)):
        for scope in scopes:
            computed = retention_status(
                by_ingest.get(ingest_id),
                deletions_by_ingest.get(ingest_id, []),
                max_days, backup_max_days, now, scope)
            findings.append(RetentionFinding(
                ingest_id=ingest_id,
                counterparty_id=counterparty_id,
                status=computed["status"],
                scope=computed["scope"],
                max_days=computed.get("max_days"),
                age_days=computed.get("age_days"),
                due_at=computed.get("due_at"),
                deleted_at=computed.get("deleted_at"),
                stamp=computed.get("stamp"),
                reason=computed.get("reason"),
                evidence=computed.get("evidence", {}),
            ))
    return findings


def summarize(findings: List[RetentionFinding]) -> Dict[str, int]:
    """Counts per status. Every status appears, including zeros -- a
    missing key would read as 'none overdue' when it may mean 'never
    computed'."""
    counts = {status: 0 for status in RETENTION_STATUSES}
    for finding in findings:
        counts[finding.status] = counts.get(finding.status, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Thin I/O wrappers -- independently swappable in tests, same shape as
# obligation_sweep's fetch_* functions.
# ---------------------------------------------------------------------------

def fetch_ingest_rows(ledger, counterparty_id: str) -> List[Dict[str, Any]]:
    return ledger.get_contract_rows(counterparty_id,
                                    record_kinds=("contract_ingest",))


def fetch_deletion_rows(ledger, counterparty_id: str) -> List[Dict[str, Any]]:
    return ledger.get_contract_rows(counterparty_id,
                                    record_kinds=("contract_deletion",))


def sweep(ledger, contract: ContractCassette,
          now: Optional[datetime] = None) -> List[RetentionFinding]:
    """Fetch and assess one counterparty's retention obligations."""
    counterparty_id = contract.get_counterparty_id()
    return assess_counterparty(
        contract,
        fetch_ingest_rows(ledger, counterparty_id),
        fetch_deletion_rows(ledger, counterparty_id),
        now=now,
    )


def main() -> None:
    """CLI, matching obligation_sweep.py's convention. What triggers this
    on a cadence (cron, k8s CronJob, manual) stays a deployment
    decision, deliberately not encoded here."""
    parser = argparse.ArgumentParser(
        description="Assess contract retention obligations for one counterparty.")
    parser.add_argument("--ledger-dsn", required=True)
    parser.add_argument("--counterparty", required=True)
    parser.add_argument("--contract-module", required=True,
                        help="import path of the contract lens module, e.g. "
                             "contract_cassettes.reference_dpa")
    parser.add_argument("--contract-class", required=True)
    args = parser.parse_args()

    import importlib

    from governance.ledger_postgres import PostgreSQLLedger

    module = importlib.import_module(args.contract_module)
    contract = getattr(module, args.contract_class)()
    if contract.get_counterparty_id() != args.counterparty:
        print(f"contract lens is for counterparty "
              f"'{contract.get_counterparty_id()}', not '{args.counterparty}'",
              file=sys.stderr)
        raise SystemExit(2)

    ledger = PostgreSQLLedger(args.ledger_dsn)
    findings = sweep(ledger, contract)
    print(json.dumps({
        "counterparty_id": args.counterparty,
        "summary": summarize(findings),
        "findings": [f.as_dict() for f in findings],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
