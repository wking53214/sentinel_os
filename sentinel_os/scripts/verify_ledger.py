#!/usr/bin/env python3
"""Run the ledger's own integrity check against a database and report.

Thin CLI over `PostgreSQLLedger.verify_chain()` -- the same recomputation an
auditor runs (AUDIT_PLAYBOOK.md section 1) and the one
`scripts/ledger_backup_verify.sh` runs against a restored copy. Reports every
violation rather than stopping at the first, and exits non-zero if any.

    python3 scripts/verify_ledger.py                 # POSTGRES_* env or defaults
    python3 scripts/verify_ledger.py --db ledger_verify_123

Connection: POSTGRES_HOST/PORT/DB/USER/PASSWORD (defaults localhost:5432
iceberg/iceberg/iceberg); --db overrides POSTGRES_DB. The ledger opens as the
restricted runtime role -- ICEBERG_LEDGER_RUNTIME_USER (default `ledger_reader`)
/ ICEBERG_LEDGER_RUNTIME_PASSWORD -- because it refuses to start as a superuser.
For a local dev database that role's password is typically `ledger_reader_test_pw`.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", help="database name (overrides POSTGRES_DB)")
    args = ap.parse_args()

    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    dbname = args.db or os.environ.get("POSTGRES_DB", "iceberg")
    user = os.environ.get("POSTGRES_USER", "iceberg")
    password = os.environ.get("POSTGRES_PASSWORD", "iceberg")
    os.environ.setdefault("ICEBERG_LEDGER_RUNTIME_USER", "ledger_reader")
    if not os.environ.get("ICEBERG_LEDGER_RUNTIME_PASSWORD"):
        os.environ["ICEBERG_LEDGER_RUNTIME_PASSWORD"] = "ledger_reader_test_pw"

    from governance.ledger_postgres import PostgreSQLLedger

    ledger = PostgreSQLLedger(host=host, port=port, dbname=dbname,
                              user=user, password=password)
    try:
        # non-strict: collect every violation instead of raising on the first
        result = ledger.verify_chain(mode="audit")
    finally:
        ledger.pool.closeall()

    print(f"database ......... {host}:{port}/{dbname}")
    print(f"entries .......... {result['entries']}")
    print(f"verify_chain ..... {'OK' if result['ok'] else 'FAILED'}")
    for v in result["violations"]:
        print(f"  ! {v}")

    if not result["ok"]:
        print("\nA violation means the stored chain does not match a fresh "
              "recomputation of its contents. See AUDIT_PLAYBOOK.md and "
              "DEPLOYMENT.md ('Ledger integrity incidents').")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
