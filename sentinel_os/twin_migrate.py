"""twin_migrate -- customer-run custody migration (DAP-8), e.g. A -> D.

Custody migration re-seals the SEAL layer only. Content, clear chain metadata,
and AAD slots are untouched: every re-sealed envelope is bound to the same
(replica_id, primary_id, current_hash) slot, so the detector's deep
verification holds unchanged across the migration.

Runs under the CUSTOMER's credentials against the customer's own replica
database -- the receiver API deliberately has no envelope-mutation endpoint,
so Sentinel cannot perform this operation over the wire. The migration is
recorded as a customer-signed event in the replica's hash-chained custody log
(old fingerprint, new fingerprint, entry count), which a regulator reads as
"custody transitioned HERE, by the key owner, with continuity."

Old-key retirement is the customer's final step (delete/retire the old private
key once the migration verifies) -- after re-sealing, the old key no longer
opens any stored envelope anyway, which the tests prove.
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict

import httpx
import psycopg2
import psycopg2.extras

from twin_custody import (
    CustodyError,
    fingerprint,
    open_envelope,
    seal,
    sign,
)


def migrate(replica_dsn: str, receiver_url: str, replica_id: str,
            old_priv_b64: str, new_recipient_pub_b64: str,
            new_custody_model: str, actor: str,
            customer_sign_priv_b64: str, customer_sign_pub_b64: str,
            new_custodian_url: str = "") -> Dict[str, Any]:
    conn = psycopg2.connect(replica_dsn)
    migrated = 0
    old_fp = None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT recipient_fp, custody_model FROM replica_meta WHERE replica_id=%s",
                        (replica_id,))
            meta = cur.fetchone()
            if not meta:
                raise SystemExit(f"replica '{replica_id}' not found")
            old_fp = meta["recipient_fp"]
            cur.execute("""SELECT id, primary_id, current_hash, envelope
                           FROM replica_entries WHERE replica_id=%s ORDER BY primary_id""",
                        (replica_id,))
            rows = cur.fetchall()
        for row in rows:
            aad = {"replica_id": replica_id, "primary_id": int(row["primary_id"]),
                   "current_hash": row["current_hash"]}
            try:
                plaintext = open_envelope(row["envelope"], old_priv_b64, aad)
            except CustodyError as exc:
                raise SystemExit(
                    f"ABORT at primary_id={row['primary_id']}: old key failed to open "
                    f"({exc}); refusing a partial migration") from exc
            new_env = seal(plaintext, new_recipient_pub_b64, aad)
            with conn.cursor() as cur:
                cur.execute("UPDATE replica_entries SET envelope=%s WHERE id=%s",
                            (json.dumps(new_env), row["id"]))
            migrated += 1
        new_fp = fingerprint(new_recipient_pub_b64)
        with conn.cursor() as cur:
            cur.execute("""UPDATE replica_meta
                           SET custody_model=%s, recipient_pub=%s, recipient_fp=%s
                           WHERE replica_id=%s""",
                        (new_custody_model, new_recipient_pub_b64, new_fp, replica_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    detail = {"from_fp": old_fp, "to_fp": fingerprint(new_recipient_pub_b64),
              "to_model": new_custody_model, "entries_resealed": migrated,
              "custodian_url": new_custodian_url}
    event_payload = {"replica_id": replica_id, "event": "custody_migration",
                     "detail": detail, "actor": actor}
    resp = httpx.post(f"{receiver_url.rstrip('/')}/replica/{replica_id}/custody-event",
                      json={"event": "custody_migration", "detail": detail, "actor": actor,
                            "signature": sign(event_payload, customer_sign_priv_b64),
                            "signer_pub": customer_sign_pub_b64},
                      timeout=10.0)
    return {"migrated": migrated, "from_fp": old_fp,
            "to_fp": detail["to_fp"], "custody_event": resp.json()}


def main() -> None:
    ap = argparse.ArgumentParser(description="Customer custody migration (DAP-8)")
    ap.add_argument("--replica-dsn", required=True)
    ap.add_argument("--receiver-url", required=True)
    ap.add_argument("--replica-id", required=True)
    ap.add_argument("--old-key-file", required=True)
    ap.add_argument("--new-recipient-pub-file", required=True)
    ap.add_argument("--new-model", required=True, choices=["A", "D"])
    ap.add_argument("--actor", required=True)
    ap.add_argument("--sign-key-file", required=True)
    ap.add_argument("--sign-pub-file", required=True)
    ap.add_argument("--custodian-url", default="")
    args = ap.parse_args()
    out = migrate(args.replica_dsn, args.receiver_url, args.replica_id,
                  open(args.old_key_file).read().strip(),
                  open(args.new_recipient_pub_file).read().strip(),
                  args.new_model, args.actor,
                  open(args.sign_key_file).read().strip(),
                  open(args.sign_pub_file).read().strip(),
                  args.custodian_url)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
