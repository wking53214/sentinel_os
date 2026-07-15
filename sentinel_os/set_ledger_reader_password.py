"""Set or rotate the ledger_reader role's password.

Run once after applying ledger_immutability.sql, and again any time the
password rotates. Reads the target password from ICEBERG_LEDGER_RUNTIME_PASSWORD
(the same env var the app's PostgreSQLLedger reads at startup) so there is a
single source of truth for the credential -- it is never hardcoded in SQL
that might get committed.

Requires schema-owner/superuser credentials to run (POSTGRES_USER/PASSWORD),
same as applying ledger_immutability.sql itself does.
"""
import os
import sys
import psycopg2


def main():
    password = os.getenv("ICEBERG_LEDGER_RUNTIME_PASSWORD")
    if not password:
        print("ICEBERG_LEDGER_RUNTIME_PASSWORD is not set -- nothing to do.", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "iceberg"),
        user=os.getenv("POSTGRES_USER", "iceberg"),
        password=os.getenv("POSTGRES_PASSWORD", "iceberg"),
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("ALTER ROLE ledger_reader WITH PASSWORD %s;", (password,))
        print("ledger_reader password set.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
