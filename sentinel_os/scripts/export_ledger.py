#!/usr/bin/env python3
"""Export every ledger row, with every shipped column, as one JSON file.

The input to tools/verify_receipts.py, which then needs no database. Reads
the same POSTGRES_* connection settings as scripts/verify_ledger.py. The
rows are exactly what the twin is shipped (twin_custody.SHIPPED_COLUMNS),
in id order; timestamps are rendered ISO-8601 (they are not hashed).

    python3 scripts/export_ledger.py --out ledger-export.json
"""
import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

EXPORT_FORMAT = "sentinel_os.ledger_export.v1"


def export_rows(conn):
    """The export dict for an open psycopg2 connection."""
    from twin_custody import SHIPPED_COLUMNS
    cur = conn.cursor()
    cur.execute(  # nosec B608 -- SHIPPED_COLUMNS is a fixed, code-defined column list
        f"SELECT {', '.join(SHIPPED_COLUMNS)} FROM ledger_entries ORDER BY id ASC")
    rows = []
    for r in cur.fetchall():
        row = dict(zip(SHIPPED_COLUMNS, r))
        for k, v in row.items():
            if isinstance(v, dt.datetime):
                row[k] = v.isoformat()
        rows.append(row)
    return {"format": EXPORT_FORMAT, "columns": list(SHIPPED_COLUMNS), "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="path of the export file to write")
    ap.add_argument("--db", help="database name (overrides POSTGRES_DB)")
    args = ap.parse_args()

    import psycopg2
    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=args.db or os.environ.get("POSTGRES_DB", "iceberg"),
        user=os.environ.get("POSTGRES_USER", "iceberg"),
        password=os.environ.get("POSTGRES_PASSWORD", "iceberg"))
    try:
        export = export_rows(conn)
    finally:
        conn.close()
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(export, fh, indent=1, sort_keys=True, default=str)
        fh.write("\n")
    print(f"wrote {args.out}: {len(export['rows'])} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
