"""twin_receiver -- the customer-side DR replica service (DAP-4).

Runs under the CUSTOMER's credentials (its own OS user, its own Postgres
database that Sentinel's roles cannot even connect to). In the self-storage
model this process may sit on Sentinel-owned hardware; the credential and
key boundaries are what make it the customer's, not the rack.

Properties enforced here, each covered by a live test:

  * Append-only + idempotent: (replica_id, primary_id) is unique. Re-delivery
    of an identical entry -> {"status":"duplicate"} (safe at-least-once
    transport). Re-delivery with DIFFERENT content -> 409 refused: the
    receiver never mutates a stored entry, so the shipper cannot rewrite
    history that has already reached the customer.
  * Structural validation: an envelope missing fields or carrying undecodable
    base64 is refused 422 at the door (torn/partial delivery surfaces
    immediately instead of rotting in storage).
  * Order independence: entries may arrive in any order; chain order is
    reconstructed from primary_id + hash linkage at verification time, never
    from arrival time or wall clocks.
  * Custody log: a hash-chained, signed record of custody events (creation,
    rotation, migration A->D, evidence designation) queryable by a regulator.

Auth: POST /entries requires the per-replica ship token (set at registration).
Read endpoints are unauthenticated in this reference implementation and the
service binds 127.0.0.1; the database itself is the customer-credential
boundary. Production deployments put customer authn in front (see spec §4.6).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from contextlib import contextmanager
from typing import Any, Dict, Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from twin_custody import canonical_json

SCHEMA = """
CREATE TABLE IF NOT EXISTS replica_meta (
    replica_id        TEXT PRIMARY KEY,
    site              TEXT NOT NULL,
    custody_model     TEXT NOT NULL CHECK (custody_model IN ('A','D')),
    recipient_pub     TEXT NOT NULL,
    recipient_fp      TEXT NOT NULL,
    customer_sign_pub TEXT NOT NULL,
    max_lag_seconds   INTEGER NOT NULL DEFAULT 30,
    retention_days    INTEGER NOT NULL DEFAULT 2557,
    is_primary_evidence BOOLEAN NOT NULL DEFAULT FALSE,
    ship_token        TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS replica_entries (
    id           BIGSERIAL PRIMARY KEY,
    replica_id   TEXT NOT NULL REFERENCES replica_meta(replica_id),
    primary_id   BIGINT NOT NULL,
    call_sid     TEXT,
    previous_hash TEXT NOT NULL,
    current_hash  TEXT NOT NULL,
    envelope     JSONB NOT NULL,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (replica_id, primary_id)
);
CREATE INDEX IF NOT EXISTS idx_replica_entries_sid ON replica_entries (replica_id, call_sid);
CREATE TABLE IF NOT EXISTS custody_log (
    id          BIGSERIAL PRIMARY KEY,
    replica_id  TEXT NOT NULL REFERENCES replica_meta(replica_id),
    seq         INTEGER NOT NULL,
    event       TEXT NOT NULL,
    detail      JSONB NOT NULL,
    actor       TEXT NOT NULL,
    prev_hash   TEXT NOT NULL,
    curr_hash   TEXT NOT NULL,
    signature   TEXT NOT NULL,
    signer_pub  TEXT NOT NULL,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (replica_id, seq)
);
"""

ENVELOPE_FIELDS = ("v", "alg", "epk", "nonce", "ct", "recipient_fp")


def _structurally_valid_envelope(env: Any) -> Optional[str]:
    if not isinstance(env, dict):
        return "envelope must be an object"
    for f in ENVELOPE_FIELDS:
        if f not in env:
            return f"envelope missing field '{f}'"
    for f in ("epk", "nonce", "ct"):
        try:
            raw = base64.b64decode(str(env[f]), validate=True)
        except Exception:
            return f"envelope field '{f}' is not valid base64"
        if f == "epk" and len(raw) != 32:
            return "envelope epk must decode to 32 bytes"
        if f == "nonce" and len(raw) != 12:
            return "envelope nonce must decode to 12 bytes"
        if f == "ct" and len(raw) < 17:  # >= 1 byte payload + 16-byte GCM tag
            return "envelope ct shorter than an AES-GCM tag"
    return None


def build_app(dsn: str, site: str) -> FastAPI:
    pool = psycopg2.pool.ThreadedConnectionPool(1, 8, dsn)

    @contextmanager
    def db():
        conn = pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            pool.putconn(conn)

    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)

    app = FastAPI(title="twin-receiver", version="1.0")

    def _meta(conn, replica_id: str) -> Dict[str, Any]:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM replica_meta WHERE replica_id=%s", (replica_id,))
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"replica '{replica_id}' not registered")
        return dict(row)

    @app.get("/health")
    def health():
        return {"ok": True, "site": site}

    @app.post("/replica/{replica_id}/register")
    def register(replica_id: str, body: Dict[str, Any]):
        required = ("custody_model", "recipient_pub", "recipient_fp",
                    "customer_sign_pub", "ship_token")
        missing = [f for f in required if not body.get(f)]
        if missing:
            raise HTTPException(status_code=422, detail=f"missing: {missing}")
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO replica_meta (replica_id, site, custody_model,
                         recipient_pub, recipient_fp, customer_sign_pub,
                         max_lag_seconds, retention_days, is_primary_evidence, ship_token)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (replica_id) DO NOTHING""",
                    (replica_id, site, body["custody_model"], body["recipient_pub"],
                     body["recipient_fp"], body["customer_sign_pub"],
                     int(body.get("max_lag_seconds", 30)),
                     int(body.get("retention_days", 2557)),
                     bool(body.get("is_primary_evidence", False)),
                     body["ship_token"]))
                created = cur.rowcount == 1
        return {"replica_id": replica_id, "site": site, "created": created}

    @app.get("/replica/{replica_id}/meta")
    def meta(replica_id: str):
        with db() as conn:
            m = _meta(conn, replica_id)
        m.pop("ship_token", None)
        m["created_at"] = str(m["created_at"])
        return m

    @app.post("/replica/{replica_id}/entries")
    def store_entry(replica_id: str, body: Dict[str, Any],
                    authorization: Optional[str] = Header(default=None)):
        with db() as conn:
            m = _meta(conn, replica_id)
            token = (authorization or "").removeprefix("Bearer ").strip()
            if token != m["ship_token"]:
                raise HTTPException(status_code=401, detail="bad ship token")
            for f in ("primary_id", "previous_hash", "current_hash", "envelope"):
                if f not in body:
                    raise HTTPException(status_code=422, detail=f"missing field '{f}'")
            err = _structurally_valid_envelope(body["envelope"])
            if err:
                raise HTTPException(status_code=422, detail=err)
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT primary_id, call_sid, previous_hash, current_hash, envelope
                       FROM replica_entries WHERE replica_id=%s AND primary_id=%s""",
                    (replica_id, int(body["primary_id"])))
                existing = cur.fetchone()
                if existing:
                    same = (
                        existing["previous_hash"] == body["previous_hash"]
                        and existing["current_hash"] == body["current_hash"]
                        and existing["call_sid"] == body.get("call_sid")
                        and canonical_json(existing["envelope"]) == canonical_json(body["envelope"])
                    )
                    if same:
                        return {"status": "duplicate", "primary_id": existing["primary_id"]}
                    # Immutability: a delivery that would CHANGE a stored entry is
                    # refused outright. History already in the customer's custody
                    # is not the shipper's to rewrite.
                    raise HTTPException(
                        status_code=409,
                        detail="entry already stored with different content; replica is append-only")
                cur.execute(
                    """INSERT INTO replica_entries
                         (replica_id, primary_id, call_sid, previous_hash, current_hash, envelope)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (replica_id, int(body["primary_id"]), body.get("call_sid"),
                     body["previous_hash"], body["current_hash"],
                     json.dumps(body["envelope"])))
        return {"status": "stored", "primary_id": int(body["primary_id"])}

    @app.get("/replica/{replica_id}/head")
    def head(replica_id: str):
        with db() as conn:
            _meta(conn, replica_id)
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT COALESCE(MAX(primary_id),0), COUNT(*)
                       FROM replica_entries WHERE replica_id=%s""", (replica_id,))
                max_id, count = cur.fetchone()
        return {"replica_id": replica_id, "max_primary_id": int(max_id), "count": int(count)}

    @app.get("/replica/{replica_id}/entries")
    def list_entries(replica_id: str, after_id: int = 0, limit: int = 500):
        with db() as conn:
            _meta(conn, replica_id)
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT primary_id, call_sid, previous_hash, current_hash,
                              envelope, received_at
                       FROM replica_entries
                       WHERE replica_id=%s AND primary_id > %s
                       ORDER BY primary_id ASC LIMIT %s""",
                    (replica_id, after_id, min(int(limit), 2000)))
                rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["received_at"] = str(r["received_at"])
        return {"replica_id": replica_id, "entries": rows}

    @app.post("/replica/{replica_id}/custody-event")
    def custody_event(replica_id: str, body: Dict[str, Any]):
        """Append a signed custody event (creation/rotation/migration/designation).

        The caller (customer tooling) supplies event, detail, actor, signer_pub and
        a signature over the canonical CONTENT payload {replica_id, event, detail,
        actor}. The receiver adds seq/prev_hash/curr_hash to form the hash chain;
        a regulator verifies the chain and the signer over the content payload.
        """
        for f in ("event", "detail", "actor", "signature", "signer_pub"):
            if f not in body:
                raise HTTPException(status_code=422, detail=f"missing field '{f}'")
        with db() as conn:
            _meta(conn, replica_id)
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext('custody_log_' || %s))",
                            (replica_id,))
                cur.execute(
                    """SELECT seq, curr_hash FROM custody_log
                       WHERE replica_id=%s ORDER BY seq DESC LIMIT 1""", (replica_id,))
                row = cur.fetchone()
                seq = (row[0] + 1) if row else 1
                prev_hash = row[1] if row else "genesis"
                if body.get("seq") not in (None, seq) or body.get("prev_hash") not in (None, prev_hash):
                    raise HTTPException(status_code=409,
                                        detail={"expected_seq": seq, "expected_prev_hash": prev_hash})
                payload = {"replica_id": replica_id, "seq": seq, "event": body["event"],
                           "detail": body["detail"], "actor": body["actor"],
                           "prev_hash": prev_hash}
                curr_hash = hashlib.sha256(canonical_json(payload)).hexdigest()
                cur.execute(
                    """INSERT INTO custody_log
                         (replica_id, seq, event, detail, actor, prev_hash, curr_hash,
                          signature, signer_pub)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (replica_id, seq, body["event"], json.dumps(body["detail"]),
                     body["actor"], prev_hash, curr_hash, body["signature"],
                     body["signer_pub"]))
        return {"status": "logged", "seq": seq, "curr_hash": curr_hash}

    @app.get("/replica/{replica_id}/custody-log")
    def custody_log(replica_id: str):
        with db() as conn:
            _meta(conn, replica_id)
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT seq, event, detail, actor, prev_hash, curr_hash,
                              signature, signer_pub, at
                       FROM custody_log WHERE replica_id=%s ORDER BY seq ASC""",
                    (replica_id,))
                rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["at"] = str(r["at"])
        return {"replica_id": replica_id, "events": rows}

    @app.exception_handler(Exception)
    def unhandled(_req: Request, exc: Exception):
        return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})

    return app


def main() -> None:
    dsn = os.environ.get("TWIN_RECEIVER_DSN", "dbname=twin_replica_a")
    port = int(os.environ.get("TWIN_RECEIVER_PORT", "8300"))
    site = os.environ.get("TWIN_RECEIVER_SITE", "site-a")
    uvicorn.run(build_app(dsn, site), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
