"""
obligation_supersession.py -- ABANDON-on-modification orchestration.

Closes the gap documented in cassettes/mortgage_cassette.py's module
docstring: when a mortgage gets a permanent modification, a new loan
number means a brand-new, independently-judged governance_decision --
and the ORIGINAL loan's outcome obligation must be abandoned
(outcome_v1.REASON_DECISION_SUPERSEDED), not left open forever and not
silently resolved. The cassette itself deliberately does not perform
that abandon() call (see its docstring: "that is orchestration, the
same layer obligation_sweep.py lives at, not cassette logic") -- this
module is that orchestration.

HOW A REPLACEMENT IS IDENTIFIED
--------------------------------
Explicit declaration, never inference (no matching by address, loan
number, or date -- all guessable). The new decision, appended through
the normal cassette pipeline like any other decision, sets
GovernanceDecisionRecord.replaces_hash to the OLD decision's
current_hash. governance.ledger_postgres.PostgreSQLLedger.append_decision
refuses that write outright if the referenced hash isn't a real
governance_decision already on the chain (fail-closed) -- so by the
time this module ever sees a replaces_hash, its target is guaranteed
to exist. See canonical_fields.OPTIONAL_HASHED_FIELDS and
GovernanceDecisionRecord.replaces_hash for why this is a DIFFERENT
field from supersedes_hash (Item 6's human-corrects-a-decision case,
not this one).

WHERE THIS RUNS
-----------------
On the PRIMARY, same posture as obligation_sweep.py: the primary's own
ledger has plaintext read access to replaces_hash and
outcome_obligation (needed to derive the OLD obligation's id). The
twin holds the obligation's actual OPEN/RESOLVED/ABANDONED state --
outcome_v1's own design, obligations are the twin's independent
record of what is owed and how it turned out, not the primary's.

IDEMPOTENT, NOT STATEFUL
--------------------------
No separate "already processed" table to maintain. Each run re-derives
the old obligation_id from replaces_hash and reads its CURRENT state
from the twin: already ABANDONED for REASON_DECISION_SUPERSEDED is a
no-op (already done), not re-appended -- same idempotency posture as
twin_receiver.derive_obligations ("an obligation already on the chain
is not re-appended").

NO SILENT SKIPS
------------------
An old obligation that's RESOLVED (not OPEN) when a new decision
claims to replace it is a genuine anomaly -- a loan that already paid
off or foreclosed should never also be "modified." Reported as a
conflict for a human to look at, never silently abandoned and never
silently ignored. Same posture obligation_sweep.py takes on every
obligation it can't cleanly place: reported with why, not dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import psycopg2.extras

from outcome_v1 import (
    MaturationRule,
    OUTCOME_ABANDONED,
    OUTCOME_OPEN,
    OUTCOME_RESOLVED,
    REASON_DECISION_SUPERSEDED,
)
from twin_custody import domain_from_cassette_version

# The four things that can happen when a declared replacement is
# checked against the old obligation's actual state. Bounded, same
# reason every other status vocabulary in this codebase is bounded --
# free text here would be a status nobody could count or alert on.
STATUS_ABANDONED = "abandoned"                  # newly abandoned this run
STATUS_ALREADY_ABANDONED = "already_abandoned"  # idempotent no-op
STATUS_CONFLICT = "conflict"                    # old obligation is RESOLVED, not OPEN
STATUS_UNRESOLVABLE = "unresolvable"            # old obligation not found on the twin at all

SUPERSESSION_STATUSES = (
    STATUS_ABANDONED, STATUS_ALREADY_ABANDONED, STATUS_CONFLICT, STATUS_UNRESOLVABLE,
)


@dataclass(frozen=True)
class ReplacementCandidate:
    """One governance_decision row on the primary ledger that declares
    a replaces_hash -- a raw candidate, not yet checked against the old
    obligation's actual state."""

    new_decision_hash: str
    replaces_hash: str
    domain: str
    decided_at: Optional[float]


@dataclass(frozen=True)
class SupersessionOutcome:
    """What happened when one ReplacementCandidate was resolved against
    the old obligation's current state on the twin."""

    new_decision_hash: str
    replaces_hash: str
    old_obligation_id: Optional[str]
    status: str
    detail: str
    decided_at: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "new_decision_hash": self.new_decision_hash,
            "replaces_hash": self.replaces_hash,
            "old_obligation_id": self.old_obligation_id,
            "status": self.status,
            "detail": self.detail,
            "decided_at": self.decided_at,
        }


# ---------------------------------------------------------------------------
# Assembly: pure, no I/O -- takes already-fetched lookups as plain dicts so
# this stays testable without a live ledger or twin, same posture as
# obligation_sweep.assemble_cohort.
# ---------------------------------------------------------------------------


def resolve_candidate(candidate: ReplacementCandidate,
                      old_decision_row: Optional[Dict[str, Any]],
                      old_obligation: Optional[Dict[str, Any]],
                      ) -> SupersessionOutcome:
    """Decide what should happen for ONE candidate, given the OLD
    decision's own ledger row (for its outcome_obligation declaration,
    needed to derive the obligation_id) and the OLD obligation's
    current state from the twin (None if the twin has never derived
    one for that decision at all).

    Does no I/O and raises nothing -- every outcome, including "this
    can't be resolved yet," is a returned SupersessionOutcome, never an
    exception a caller has to catch to find out something was wrong
    (matches obligation_sweep's "no silent skips, always reported").
    """
    def outcome(old_obligation_id: Optional[str], status: str, detail: str,
               ) -> SupersessionOutcome:
        return SupersessionOutcome(
            candidate.new_decision_hash, candidate.replaces_hash, old_obligation_id,
            status, detail, decided_at=candidate.decided_at)

    if old_decision_row is None:
        return outcome(
            None, STATUS_UNRESOLVABLE,
            "the decision named by replaces_hash was not found on the primary "
            "ledger -- this should be impossible (append_decision's fail-closed "
            "check refuses a replaces_hash that doesn't exist at write time), "
            "so this is a genuine integrity gap worth investigating, not a "
            "normal skip")

    declaration = old_decision_row.get("outcome_obligation")
    if not declaration:
        return outcome(
            None, STATUS_UNRESOLVABLE,
            "the old decision never declared an outcome_obligation -- there is "
            "no obligation for this replacement to abandon at all")
    try:
        rule = MaturationRule.parse(declaration)
    except ValueError as exc:
        return outcome(
            None, STATUS_UNRESOLVABLE,
            f"the old decision's outcome_obligation declaration does not parse: "
            f"{exc}")

    old_obligation_id = f"{candidate.replaces_hash}:{rule.kind}"

    if old_obligation is None:
        return outcome(
            old_obligation_id, STATUS_UNRESOLVABLE,
            "no obligation with this id exists on the twin yet -- derive the "
            "open-obligation set (twin_receiver.derive_obligations) before "
            "sweeping supersessions")

    state = old_obligation.get("state")
    if state == OUTCOME_OPEN:
        return outcome(
            old_obligation_id, STATUS_ABANDONED,
            "old obligation was OPEN -- abandoning with "
            "REASON_DECISION_SUPERSEDED")
    if state == OUTCOME_ABANDONED:
        if old_obligation.get("reason_code") == REASON_DECISION_SUPERSEDED:
            return outcome(
                old_obligation_id, STATUS_ALREADY_ABANDONED,
                "old obligation is already ABANDONED with "
                "REASON_DECISION_SUPERSEDED -- a previous sweep already "
                "processed this replacement, nothing to do")
        return outcome(
            old_obligation_id, STATUS_CONFLICT,
            f"old obligation is already ABANDONED, but for a different reason "
            f"({old_obligation.get('reason_code')!r}) -- a decision claims to "
            f"replace a loan that was abandoned for an unrelated cause; needs "
            f"a human to reconcile, not auto-resolved either way")
    if state == OUTCOME_RESOLVED:
        return outcome(
            old_obligation_id, STATUS_CONFLICT,
            "old obligation is already RESOLVED -- a loan that already paid "
            "off or closed involuntarily should never also be \"modified\"; "
            "this is a genuine data conflict, not something to abandon over")
    return outcome(
        old_obligation_id, STATUS_CONFLICT,
        f"old obligation is in an unrecognized state {state!r} -- reporting "
        f"as a conflict rather than guessing what to do")


# ---------------------------------------------------------------------------
# I/O wrappers -- thin, and each independently swappable in a test, same
# posture as obligation_sweep.py's fetch_* functions.
# ---------------------------------------------------------------------------


def fetch_replacement_candidates(ledger_conn, domain: Optional[str] = None,
                                 ) -> List[ReplacementCandidate]:
    """Every governance_decision on the primary ledger that declares a
    replaces_hash. Uses idx_replaces_hash's partial index (WHERE
    replaces_hash IS NOT NULL), so this stays cheap as the ledger
    grows -- the vast majority of decisions will never set this field.

    domain is parsed from cassette_version, same convention as every
    other domain-derivation in this codebase (twin_custody.
    domain_from_cassette_version) -- filtering here is a pure Python
    post-filter, not a second index, since the column itself doesn't
    carry domain directly.
    """
    result: List[ReplacementCandidate] = []
    with ledger_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT current_hash, replaces_hash, cassette_version, timestamp
            FROM ledger_entries
            WHERE replaces_hash IS NOT NULL AND record_kind = 'governance_decision'
        """)
        for row in cur.fetchall():
            row_domain = domain_from_cassette_version(row.get("cassette_version"))
            if domain is not None and row_domain != domain:
                continue
            decided_at = (row["timestamp"].timestamp()
                         if row.get("timestamp") is not None else None)
            result.append(ReplacementCandidate(
                new_decision_hash=str(row["current_hash"]),
                replaces_hash=str(row["replaces_hash"]),
                domain=row_domain or "unknown",
                decided_at=decided_at,
            ))
    return result


def fetch_old_decision_rows(ledger_conn, decision_hashes: set,
                            ) -> Dict[str, Dict[str, Any]]:
    """Look up each replaces_hash target by its current_hash on the
    primary's own ledger, for its outcome_obligation declaration. A
    hash with no matching row is simply absent from the result -- see
    resolve_candidate's STATUS_UNRESOLVABLE handling for why that
    should be impossible in practice (append_decision's fail-closed
    check) but is still reported rather than assumed."""
    if not decision_hashes:
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    with ledger_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT current_hash, outcome_obligation
            FROM ledger_entries
            WHERE current_hash = ANY(%s) AND record_kind = 'governance_decision'
        """, (list(decision_hashes),))
        for row in cur.fetchall():
            result[row["current_hash"]] = dict(row)
    return result


def fetch_obligations_by_id(twin_client, replica_id: str, obligation_ids: set,
                            ) -> Dict[str, Dict[str, Any]]:
    """The current state of each named obligation, from the twin's own
    read API (the twin has no per-id lookup endpoint, so this fetches
    the full set once and filters -- same approach obligation_sweep.
    fetch_resolved_obligations already takes against the same
    endpoint). An id with no matching obligation is simply absent."""
    resp = twin_client.get(f"/replica/{replica_id}/obligations")
    resp.raise_for_status()
    all_obligations = resp.json()["obligations"]
    return {o["obligation_id"]: o for o in all_obligations
           if o["obligation_id"] in obligation_ids}


def sweep(ledger_conn, twin_client, replica_id: str,
         domain: Optional[str] = None) -> List[SupersessionOutcome]:
    """The whole sweep, wired to real I/O: find every declared
    replacement on the primary ledger, look up what each one's old
    obligation actually needs, and return one SupersessionOutcome per
    candidate -- computed, not yet recorded (see record_outcomes)."""
    candidates = fetch_replacement_candidates(ledger_conn, domain=domain)
    if not candidates:
        return []
    old_decision_rows = fetch_old_decision_rows(
        ledger_conn, {c.replaces_hash for c in candidates})

    # First pass to know which obligation_ids to even ask the twin about
    # (rule.kind isn't known until the old decision's own declaration is
    # read, so this can't be done in one shot).
    provisional: List[tuple] = []
    needed_ids = set()
    for candidate in candidates:
        old_row = old_decision_rows.get(candidate.replaces_hash)
        declaration = (old_row or {}).get("outcome_obligation")
        obligation_id = None
        if declaration:
            try:
                rule = MaturationRule.parse(declaration)
                obligation_id = f"{candidate.replaces_hash}:{rule.kind}"
            except ValueError:
                pass
        provisional.append((candidate, old_row, obligation_id))
        if obligation_id:
            needed_ids.add(obligation_id)

    obligations = fetch_obligations_by_id(twin_client, replica_id, needed_ids)

    outcomes: List[SupersessionOutcome] = []
    for candidate, old_row, obligation_id in provisional:
        old_obligation = obligations.get(obligation_id) if obligation_id else None
        outcomes.append(resolve_candidate(candidate, old_row, old_obligation))
    return outcomes


def record_outcomes(twin_client, replica_id: str,
                    outcomes: List[SupersessionOutcome],
                    fallback_at: Optional[float] = None,
                    ) -> List[Dict[str, Any]]:
    """Actually abandon each outcome whose status is STATUS_ABANDONED,
    via the twin's existing obligation-transition endpoint (the SAME
    endpoint any other abandon() caller uses -- this orchestration adds
    no new twin-side mechanism, just a new caller of the one that
    already exists). Every other status is a no-op here on purpose:
    STATUS_ALREADY_ABANDONED needs no write, and STATUS_CONFLICT /
    STATUS_UNRESOLVABLE must never be written -- recording an
    abandonment for either would be exactly the silent-guess failure
    mode this module exists to avoid.

    Each abandonment is timestamped with the REPLACEMENT decision's own
    decided_at -- the obligation was superseded as of when the new
    loan decision was actually made, not whenever this sweep happens
    to run. fallback_at is used only on the rare row that predates the
    `timestamp` column (decided_at is None); callers pass "now" for
    that case, same posture as every other honest-default-of-last-
    resort in this codebase.

    Deliberately separate from sweep() itself, same reasoning
    obligation_sweep.record_reviews gives: computing what should
    happen and actually recording it are two different steps with two
    different failure modes, and a caller may want to inspect
    conflicts before anything is written at all.
    """
    results = []
    for outcome in outcomes:
        if outcome.status != STATUS_ABANDONED:
            continue
        at = outcome.decided_at if outcome.decided_at is not None else fallback_at
        resp = twin_client.post(
            f"/replica/{replica_id}/obligations/{outcome.old_obligation_id}/transition",
            json={
                "state": OUTCOME_ABANDONED,
                "reason_code": REASON_DECISION_SUPERSEDED,
                "at": at,
            })
        resp.raise_for_status()
        results.append({"old_obligation_id": outcome.old_obligation_id,
                        **resp.json()})
    return results


# ---------------------------------------------------------------------------
# CLI entry point -- same posture as obligation_sweep.py's: runs on the
# PRIMARY, needs a real ledger connection and a real twin client. Added
# 2026-08-01: this module had no CLI at all (only sweep()/record_outcomes()
# called directly, from tests) -- meaning it could not actually run in
# production. Whatever triggers it on a schedule (cron, a k8s CronJob, a
# human running it by hand) is a deployment decision this script
# deliberately doesn't make for you, same as obligation_sweep.py.
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    import json as _json
    import os
    import time as _time

    import httpx
    import psycopg2 as _psycopg2

    ap = argparse.ArgumentParser(
        description="Find every declared loan-modification replacement on "
                    "the primary ledger and abandon the old obligation it "
                    "supersedes on the twin.")
    ap.add_argument("--ledger-dsn", required=True,
                    help="psycopg2 DSN for the primary's own ledger")
    ap.add_argument("--receiver-url", required=True,
                    help="Base URL of the twin's receiver API")
    ap.add_argument("--replica-id", required=True)
    ap.add_argument("--ship-token", default=os.environ.get("SENTINEL_SHIP_TOKEN"),
                    help="Bearer token for the twin's ship-token auth "
                         "(falls back to the SENTINEL_SHIP_TOKEN env var). "
                         "Required -- every twin route this script calls is "
                         "auth-gated (see AC-13).")
    ap.add_argument("--domain", default=None,
                    help="Restrict to one domain's replacement candidates "
                         "(default: every domain)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute and print outcomes but do not record any "
                         "abandonment on the twin")
    args = ap.parse_args()
    if not args.ship_token:
        ap.error("--ship-token is required (or set SENTINEL_SHIP_TOKEN) -- "
                 "every twin route this script calls is auth-gated (AC-13)")

    ledger_conn = _psycopg2.connect(args.ledger_dsn)
    twin_client = httpx.Client(
        base_url=args.receiver_url, timeout=30.0,
        headers={"Authorization": f"Bearer {args.ship_token}"})

    try:
        outcomes = sweep(ledger_conn, twin_client, args.replica_id,
                         domain=args.domain)
        summary = {
            "candidates_found": len(outcomes),
            "outcomes": [o.as_dict() for o in outcomes],
        }
        if not args.dry_run:
            recorded = record_outcomes(twin_client, args.replica_id, outcomes,
                                       fallback_at=_time.time())
            summary["recorded"] = recorded
        print(_json.dumps(summary, indent=2, default=str))
    finally:
        ledger_conn.close()
        twin_client.close()


if __name__ == "__main__":
    main()
