"""One-time operator tool: give replicas registered before read tokens existed
a read token.

Default is a dry run that only reports how many replicas lack a token. With
--apply it writes a new random token to each replica that has none, and only to
those. Existing tokens are never changed. The new tokens go to an output file
created with owner-only permissions, to be handed to the customer out of band.
Tokens are never printed.
"""
import argparse
import os
import secrets
from typing import List, Tuple

import psycopg2


def find_unset(conn) -> List[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT replica_id FROM replica_meta "
                    "WHERE read_token IS NULL OR read_token = '' ORDER BY replica_id")
        return [r[0] for r in cur.fetchall()]


def issue(conn, replica_ids: List[str], out_path: str) -> List[Tuple[str, str]]:
    issued: List[Tuple[str, str]] = []
    with conn.cursor() as cur:
        for rid in replica_ids:
            token = secrets.token_urlsafe(32)
            # Guard on NULL again so a token set concurrently is never overwritten.
            cur.execute("UPDATE replica_meta SET read_token = %s "
                        "WHERE replica_id = %s AND (read_token IS NULL OR read_token = '')",
                        (token, rid))
            if cur.rowcount == 1:
                issued.append((rid, token))
    conn.commit()
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        for rid, token in issued:
            fh.write(f"{rid}\t{token}\n")
    os.chmod(out_path, 0o600)
    return issued


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dsn", default=os.getenv("TWIN_RECEIVER_DSN"), required=False)
    ap.add_argument("--output", help="owner-only file for the new tokens (required with --apply)")
    ap.add_argument("--apply", action="store_true", help="actually write the tokens")
    args = ap.parse_args(argv)
    if not args.dsn:
        ap.error("a database address is required (--dsn or TWIN_RECEIVER_DSN)")
    conn = psycopg2.connect(args.dsn)
    try:
        unset = find_unset(conn)
        if not args.apply:
            print(f"dry run: {len(unset)} replica(s) have no read token; nothing changed")
            return 0
        if not args.output:
            ap.error("--output is required with --apply")
        issued = issue(conn, unset, args.output)
        print(f"issued {len(issued)} token(s) to {args.output}; existing tokens untouched")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
