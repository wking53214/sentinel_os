"""twin_shipper -- Sentinel-side ledger tailer (DAP-5, ship side).

Reads COMMITTED rows from the primary ledger and turns each into one sealed
sync job per replica target on the existing TransmissionQueue. Design points,
each load-bearing:

  * Not in the primary path. The shipper is a separate process doing read-only
    SELECTs on ledger_entries. The harness, worker, ingress, and ledger are
    untouched -- a dead shipper, a full sync queue, or an offline replica can
    slow NOTHING upstream (the no-new-F-A requirement is structural, not
    behavioral).
  * Survives the worker/harness changing shape. The integration point is the
    ledger table itself, not the worker seam, so it also captures rows that
    never traveled through the queue (self-heal writes, direct appends).
  * At-least-once, idempotent everywhere. Queue enqueue dedups on job_id
    ("{replica_id}|{primary_id}"); the receiver dedups on the same pair; the
    Redis cursor advances only after the batch is enqueued. Losing the cursor
    re-ships history harmlessly.
  * Fan-out is the routing table. One committed row -> one job per target in
    TWIN_TARGETS_FILE; multi-site customers list one target per site, with
    per-target custody keys.

Test hook: TWIN_SHIPPER_SKIP_SIDS (comma-separated call_sids) silently drops
matching rows from shipping -- the forced-omission scenario the divergence
detector must catch via the Independent Completeness Cross-check (ICC).
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Dict, List

import psycopg2
import psycopg2.extras
import redis

from queue_schema import TransmissionQueue
from twin_custody import SHIPPED_COLUMNS, canonical_json, seal

SYNC_QUEUE_NAME = "twin_sync"
CURSOR_KEY = "twin:cursor:{replica_id}"


def load_targets(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        targets = json.load(fh)
    required = ("replica_id", "receiver_url", "ship_token", "recipient_pub")
    for t in targets:
        missing = [f for f in required if not t.get(f)]
        if missing:
            raise ValueError(f"target {t.get('replica_id')} missing {missing}")
    return targets


def _ledger_conn():
    # Runs as the Sentinel service identity. Peer-auth socket by default;
    # falls back to the app's env-var credentials when set.
    if os.environ.get("POSTGRES_HOST"):
        return psycopg2.connect(
            host=os.environ["POSTGRES_HOST"],
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            dbname=os.environ.get("POSTGRES_DB", "iceberg"),
            user=os.environ.get("POSTGRES_USER", "iceberg"),
            password=os.environ.get("POSTGRES_PASSWORD", ""),
        )
    return psycopg2.connect(dbname=os.environ.get("POSTGRES_DB", "iceberg"))


def _row_plaintext(row: Dict[str, Any]) -> bytes:
    clean = dict(row)
    clean["timestamp"] = str(clean.get("timestamp"))
    return canonical_json(clean)


def _row_sid(row: Dict[str, Any]) -> Any:
    """call_sid lives in the column for governance decisions; base append() rows
    carry it only inside data/input_data. Resolve from whichever is present."""
    if row.get("call_sid"):
        return row["call_sid"]
    for src in ("input_data", "data"):
        val = row.get(src)
        if isinstance(val, dict) and val.get("call_sid"):
            return val["call_sid"]
    return None


def ship_available(queue: TransmissionQueue, r: redis.Redis,
                   targets: List[Dict[str, Any]], batch: int = 200) -> Dict[str, int]:
    """Ship every committed row past each target's cursor. Returns per-target counts."""
    skip_sids = {s for s in os.environ.get("TWIN_SHIPPER_SKIP_SIDS", "").split(",") if s}
    one_row = os.environ.get("TWIN_SHIPPER_ONEROW") == "1"
    if one_row:
        batch = 1
    shipped: Dict[str, int] = {}
    conn = _ledger_conn()
    try:
        for target in targets:
            rid = target["replica_id"]
            ckey = CURSOR_KEY.format(replica_id=rid)
            cursor_val = int(r.get(ckey) or 0)
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT {', '.join(SHIPPED_COLUMNS)} FROM ledger_entries "
                    f"WHERE id > %s ORDER BY id ASC LIMIT %s", (cursor_val, batch))
                rows = [dict(x) for x in cur.fetchall()]
            count = 0
            max_id = cursor_val
            for row in rows:
                max_id = max(max_id, int(row["id"]))
                row_sid = _row_sid(row)
                if row_sid in skip_sids:
                    continue  # test hook: forced omission
                aad = {"replica_id": rid, "primary_id": int(row["id"]),
                       "current_hash": row["current_hash"]}
                envelope = seal(_row_plaintext(row), target["recipient_pub"], aad)
                payload = {
                    "kind": "twin_sync",
                    "replica_id": rid,
                    "receiver_url": target["receiver_url"],
                    "ship_token": target["ship_token"],
                    "primary_id": int(row["id"]),
                    "call_sid": row_sid,
                    "previous_hash": row["previous_hash"],
                    "current_hash": row["current_hash"],
                    "envelope": envelope,
                }
                queue.enqueue(payload, job_id=f"{rid}|{row['id']}")
                count += 1
            if max_id > cursor_val:
                r.set(ckey, max_id)  # after enqueue: at-least-once by construction
            shipped[rid] = count
    finally:
        conn.close()
    return shipped


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", default=os.environ.get("TWIN_TARGETS_FILE", "twin_targets.json"))
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--batch", type=int, default=200)
    args = ap.parse_args()

    redis_url = os.environ.get("SENTINEL_REDIS_URL", "redis://localhost:6379/0")
    r = redis.Redis.from_url(redis_url, decode_responses=True)
    queue = TransmissionQueue(name=SYNC_QUEUE_NAME, redis_url=redis_url)
    targets = load_targets(args.targets)

    while True:
        counts = ship_available(queue, r, targets, batch=args.batch)
        if any(counts.values()):
            print(f"[shipper] enqueued {counts}", flush=True)
        if args.once:
            print(f"[shipper] once: {counts}", flush=True)
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
