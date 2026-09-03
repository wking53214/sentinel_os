#!/usr/bin/env python3
"""Run the synthetic mortgage population through the full governed path.

For each customer row in sample_data/mortgage_cassette_synthetic_customers_v2.csv:
build an Episode, judge it, and -- when actual differs from what was requested
(issue_count >= the cassette's governance_trigger) -- send it through
GovernanceHarness.process(): governor consult -> conservation boundary ->
hash-chained Postgres ledger write.

Afterwards: verify the whole chain with ledger.verify_chain(), and (read-only)
classify every matured resolution with the cassette's classify_outcome().

    python3 run_mortgage_population.py [--limit N] [--force-all] [--keep-ledger]

Needs a local PostgreSQL (iceberg/iceberg@localhost:5432). Resets ledger_entries
by default so the chain runs genesis -> N and is fully verifiable; --keep-ledger
appends to whatever is there instead.
"""
import argparse
import collections
import csv
import os
import sys
import time

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CSV_PATH = os.path.join(HERE, "..", "sample_data",
                        "mortgage_cassette_synthetic_customers_v2.csv")
_OWNER = dict(host="localhost", port=5432, dbname="iceberg",
              user="iceberg", password="iceberg")
_RUNTIME_USER = "ledger_reader"
_RUNTIME_PASSWORD = "ledger_reader_test_pw"  # local test role; see conftest.py


def _prep_ledger(reset: bool) -> None:
    """Give the fail-closed ledger a restricted runtime role to connect as
    (same setup the pytest conftest does), and optionally start from a clean
    genesis so verify_chain() covers the whole run."""
    conn = psycopg2.connect(**_OWNER)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname=%(u)s) "
        "THEN EXECUTE format('CREATE ROLE %%I WITH LOGIN PASSWORD %%L', %(u)s, %(p)s); "
        "ELSE EXECUTE format('ALTER ROLE %%I WITH PASSWORD %%L', %(u)s, %(p)s); "
        "END IF; END $$;",
        {"u": _RUNTIME_USER, "p": _RUNTIME_PASSWORD},
    )
    if reset:
        cur.execute("ALTER TABLE IF EXISTS ledger_entries DISABLE TRIGGER USER;")
        cur.execute("DROP TABLE IF EXISTS ledger_entries CASCADE;")
    conn.close()
    os.environ["ICEBERG_LEDGER_RUNTIME_USER"] = _RUNTIME_USER
    os.environ["ICEBERG_LEDGER_RUNTIME_PASSWORD"] = _RUNTIME_PASSWORD


class DemoGovernor:
    """Deterministic stand-in for the Claude governor (this script has no API
    key). SYNTHETIC POLICY, demonstration only: withholds approval when a loan
    was APPROVED despite weak fundamentals (DTI > 0.50 or credit score < 600).
    A blocked decision is still written to the ledger (governance_blocked=True);
    nothing is hidden."""

    def __init__(self) -> None:
        self.row: dict = {}   # the caller sets this before each process()

    def safety_check(self, action, details):
        row = self.row
        weak = False
        if row.get("decision") == "approved":
            try:
                weak = (float(row.get("dti_ratio") or 0) > 0.50
                        or float(row.get("credit_score") or 999) < 600)
            except ValueError:
                weak = False
        if weak:
            return {"safe": False, "model_identity": "demo-governor", "cost": None,
                    "reasoning": "approved despite DTI>0.50 or score<600 -- flagged for review"}
        return {"safe": True, "model_identity": "demo-governor", "cost": None,
                "reasoning": "within synthetic policy bounds"}


def build_episode(row, make_episode):
    """CSV row -> (Episode, issue_count). issue_count is the number of fields
    where the recorded outcome differs from what was requested -- the same
    discrepancy count the cassette's own judge() scores on."""
    approved = row["decision"] == "approved"
    requested = {"outcome": "approved", "amount": float(row["requested_loan_amount"])}
    actual = {
        "outcome": row["decision"],
        "amount": float(row["approved_loan_amount"]) if approved and row["approved_loan_amount"] else 0.0,
    }
    reasons = [r.strip() for r in (row["adverse_action_reason"],
                                   row["approval_amount_change_reason"]) if r.strip()]
    issue_count = sum(1 for k in requested if requested[k] != actual[k])

    ep = make_episode(
        row["loan_number"], "mortgage",
        requested=requested, actual=actual, outcome_reasons=tuple(reasons),
        attributes={"property_address": row["loan_property_address"],
                    "property_state": row["property_state"],
                    "loan_purpose": row["loan_purpose"],
                    "credit_score": row["credit_score"],
                    "dti_ratio": row["dti_ratio"]},
    )
    return ep, issue_count


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=0, help="only the first N rows")
    ap.add_argument("--force-all", action="store_true",
                    help="govern every row (issue_count>=1), clean approvals included")
    ap.add_argument("--keep-ledger", action="store_true",
                    help="append to the existing ledger instead of resetting it")
    args = ap.parse_args()

    os.chdir(HERE)
    _prep_ledger(reset=not args.keep_ledger)

    from cassette_schema import cassette_version_of
    from cassettes.mortgage_cassette import MortgageCassette
    from episode import EpisodeIntegrityError, make_episode
    from governance_harness import GovernanceHarness

    with open(CSV_PATH, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if args.limit:
        rows = rows[:args.limit]
    print(f"loaded {len(rows)} rows from {os.path.relpath(CSV_PATH, HERE)}")

    cassette = MortgageCassette()
    pg = {"postgres_host": "localhost", "postgres_port": 5432, "postgres_db": "iceberg",
          "postgres_user": "iceberg", "postgres_password": "iceberg", "claude_api_key": None}
    harness = GovernanceHarness(pg, cassette)
    gov = DemoGovernor()
    harness.decider = gov
    print(f"harness up; cassette bound as {cassette_version_of(cassette)}\n")

    tiers = collections.Counter()
    n_judged = n_governed = n_approved = n_blocked = n_integrity_err = 0
    t0 = time.time()

    for i, row in enumerate(rows, 1):
        try:
            ep, issue_count = build_episode(row, make_episode)
            if args.force_all:
                issue_count = max(issue_count, 1)
            gov.row = row
            result = harness.process(ep, issue_count)
        except EpisodeIntegrityError:
            n_integrity_err += 1
            continue

        n_judged += 1
        if result["quality"] is not None:
            tiers[result["quality"].tier] += 1
        if result["governed"]:
            n_governed += 1
            n_approved += int(result["governance_approved"])
            n_blocked += int(not result["governance_approved"])
        if i % 2000 == 0:
            print(f"  ... {i}/{len(rows)}  ({time.time() - t0:.0f}s)")

    dt = time.time() - t0
    print(f"\ndone in {dt:.1f}s  ({len(rows) / dt:.0f} rows/s)")
    print(f"  judged ................ {n_judged}")
    print(f"  governed (-> ledger) .. {n_governed}")
    print(f"      governor approved . {n_approved}")
    print(f"      governor blocked .. {n_blocked}   (written anyway, governance_blocked=True)")
    print(f"  integrity-rejected .... {n_integrity_err}   (mismatch with no reason on file)")
    print(f"  quality tiers ......... {dict(tiers)}")

    chain = harness.ledger.verify_chain()
    print(f"\nledger.verify_chain(): ok={chain['ok']}  entries={chain['entries']}  "
          f"violations={len(chain['violations'])}")
    for v in chain["violations"][:5]:
        print(f"   ! {v}")

    outcomes = collections.Counter()
    for row in rows:
        rt = row["resolution_type"].strip()
        if not rt:
            continue
        verdict = cassette.classify_outcome({"resolution_type": rt})
        outcomes[{True: "favorable", False: "unfavorable", None: "ambiguous"}[verdict]] += 1
    mods = sum(1 for r in rows if r["loan_status"] == "modified")
    print(f"\nclassify_outcome over matured resolutions: {dict(outcomes)}")
    print(f"  (+ {mods} 'modified' -> not a resolution; the original obligation is abandoned "
          f"as DECISION_SUPERSEDED and a fresh decision opens under a new loan number)")

    harness.shutdown()


if __name__ == "__main__":
    main()
