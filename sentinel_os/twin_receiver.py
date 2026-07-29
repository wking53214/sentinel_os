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
ALTER TABLE replica_entries ADD COLUMN IF NOT EXISTS outcome_obligation TEXT;
ALTER TABLE replica_entries ADD COLUMN IF NOT EXISTS decided_at DOUBLE PRECISION;
-- Cohort assembly needs to group obligations by domain, not just by
-- obligation_kind (two unrelated cassettes could legitimately pick the same
-- obligation_kind string). Shipped in the clear, same posture as
-- outcome_obligation/decided_at above: it says which business line a
-- decision belongs to, nothing about the subject, and the twin needs it
-- without decryption authority.
ALTER TABLE replica_entries ADD COLUMN IF NOT EXISTS domain TEXT;
CREATE INDEX IF NOT EXISTS idx_replica_entries_obligation
    ON replica_entries (replica_id, outcome_obligation)
    WHERE outcome_obligation IS NOT NULL;
CREATE TABLE IF NOT EXISTS obligation_ledger (
    id            BIGSERIAL PRIMARY KEY,
    replica_id    TEXT NOT NULL REFERENCES replica_meta(replica_id),
    seq           INTEGER NOT NULL,
    obligation_id TEXT NOT NULL,
    primary_id    BIGINT,
    decision_hash TEXT NOT NULL,
    declaration   TEXT NOT NULL,
    domain        TEXT NOT NULL DEFAULT 'unknown',
    obligation_kind TEXT NOT NULL,
    opened_at     DOUBLE PRECISION NOT NULL,
    expected_by   DOUBLE PRECISION NOT NULL,
    state         TEXT NOT NULL,
    reason_code   TEXT,
    resolved_at   DOUBLE PRECISION,
    resolved_value JSONB,
    resolution_provenance TEXT,
    resolution_method TEXT,
    favorable     BOOLEAN,
    detail        JSONB NOT NULL DEFAULT '{}'::jsonb,
    prev_hash     TEXT NOT NULL,
    curr_hash     TEXT NOT NULL,
    signature     TEXT,
    signer_pub    TEXT,
    at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (replica_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_obligation_ledger_oid
    ON obligation_ledger (replica_id, obligation_id, seq DESC);
-- Backfill for a table created before these columns existed (dev/CI reruns
-- against a persistent volume); a fresh CREATE TABLE above already has them.
ALTER TABLE obligation_ledger ADD COLUMN IF NOT EXISTS resolved_at DOUBLE PRECISION;
ALTER TABLE obligation_ledger ADD COLUMN IF NOT EXISTS resolved_value JSONB;
ALTER TABLE obligation_ledger ADD COLUMN IF NOT EXISTS resolution_provenance TEXT;
ALTER TABLE obligation_ledger ADD COLUMN IF NOT EXISTS resolution_method TEXT;
ALTER TABLE obligation_ledger ADD COLUMN IF NOT EXISTS favorable BOOLEAN;
ALTER TABLE obligation_ledger ADD COLUMN IF NOT EXISTS domain TEXT NOT NULL DEFAULT 'unknown';
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
                    """SELECT primary_id, call_sid, previous_hash, current_hash,
                              envelope, outcome_obligation, decided_at, domain
                       FROM replica_entries WHERE replica_id=%s AND primary_id=%s""",
                    (replica_id, int(body["primary_id"])))
                existing = cur.fetchone()
                if existing:
                    same = (
                        existing["previous_hash"] == body["previous_hash"]
                        and existing["current_hash"] == body["current_hash"]
                        and existing["call_sid"] == body.get("call_sid")
                        and existing["outcome_obligation"] == body.get("outcome_obligation")
                        and existing["domain"] == body.get("domain")
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
                         (replica_id, primary_id, call_sid, previous_hash,
                          current_hash, envelope, outcome_obligation, decided_at,
                          domain)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (replica_id, int(body["primary_id"]), body.get("call_sid"),
                     body["previous_hash"], body["current_hash"],
                     json.dumps(body["envelope"]),
                     body.get("outcome_obligation"), body.get("decided_at"),
                     body.get("domain")))
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

    # ---------------- OutcomeV1: independently derived obligations -------------
    #
    # Decision 5's whole point lives here. The twin does NOT wait to be told
    # what is owed; it computes the open set from the decision feed it already
    # holds, using the maturation declaration each decision carried in the
    # clear. An operator who wants an obligation gone has to make the DECISION
    # gone, and a missing decision is already a MISSING verdict on the primary
    # cross-check. Independence without asking the primary to sign an
    # obligation-open event for every decision it makes.
    #
    # Own table, own vocabulary (OPEN/RESOLVED/ABANDONED), deliberately not
    # replica_entries and deliberately not the twin's transport verdicts:
    # twin_detector's PENDING means "inside the transport SLA" and its EXTRA
    # means "wiped from the primary". Outcome lag is unbounded and is neither
    # of those things; reusing either word would make a regulator read two
    # unrelated conditions as one.

    def _append_obligation(conn, replica_id: str, obligation, *,
                           primary_id=None, declaration: str,
                           signature=None, signer_pub=None):
        """Append one obligation state to the per-replica hash chain.

        Same shape as custody_log: seq + prev_hash + curr_hash computed by the
        receiver, signature optional and verified by whoever reads the chain.
        Append-only -- a transition never updates a prior row, so the history
        of what was owed and when is itself evidence.
        """
        from outcome_v1 import validate_obligation
        validate_obligation(obligation)
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('obligation_ledger_' || %s))",
                        (replica_id,))
            cur.execute("""SELECT seq, curr_hash FROM obligation_ledger
                           WHERE replica_id=%s ORDER BY seq DESC LIMIT 1""",
                        (replica_id,))
            row = cur.fetchone()
            seq = (row[0] + 1) if row else 1
            prev_hash = row[1] if row else "genesis"
            payload = {"replica_id": replica_id, "seq": seq,
                       "obligation_id": obligation.obligation_id,
                       "decision_hash": obligation.decision_hash,
                       "declaration": declaration,
                       "domain": obligation.domain,
                       "obligation_kind": obligation.obligation_kind,
                       "opened_at": obligation.opened_at,
                       "expected_by": obligation.expected_by,
                       "state": obligation.state,
                       "reason_code": obligation.reason_code,
                       "prev_hash": prev_hash}
            # Resolution fields enter the hashed payload ONLY when present --
            # same "optional hashed field" discipline used for cassette_hash
            # etc. in ledger_postgres.py. An OPEN-state row (all five None)
            # hashes exactly as it did before this fix; only a RESOLVED row
            # picks up the new fields, so nothing already on a chain is
            # invalidated by adding the capability to record them.
            resolution_fields = {
                "resolved_at": obligation.resolved_at,
                "resolved_value": obligation.resolved_value,
                "resolution_provenance": obligation.resolution_provenance,
                "resolution_method": obligation.resolution_method,
                "favorable": obligation.favorable,
            }
            for key, value in resolution_fields.items():
                if value is not None:
                    payload[key] = value
            curr_hash = hashlib.sha256(canonical_json(payload)).hexdigest()
            cur.execute(
                """INSERT INTO obligation_ledger
                     (replica_id, seq, obligation_id, primary_id, decision_hash,
                      declaration, domain, obligation_kind, opened_at, expected_by,
                      state, reason_code, resolved_at, resolved_value,
                      resolution_provenance, resolution_method, favorable,
                      detail, prev_hash, curr_hash, signature, signer_pub)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (replica_id, seq, obligation.obligation_id, primary_id,
                 obligation.decision_hash, declaration, obligation.domain,
                 obligation.obligation_kind,
                 obligation.opened_at, obligation.expected_by, obligation.state,
                 obligation.reason_code, obligation.resolved_at,
                 None if obligation.resolved_value is None
                 else json.dumps(obligation.resolved_value),
                 obligation.resolution_provenance, obligation.resolution_method,
                 obligation.favorable, json.dumps(obligation.detail),
                 prev_hash, curr_hash, signature, signer_pub))
        return seq, curr_hash

    def _latest_states(conn, replica_id: str) -> Dict[str, Dict[str, Any]]:
        """Current state per obligation: the highest-seq row for each id.
        The chain keeps every transition; this is the read view over it."""
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT DISTINCT ON (obligation_id)
                       obligation_id, primary_id, decision_hash, declaration,
                       domain, obligation_kind, opened_at, expected_by, state,
                       reason_code, resolved_at, resolved_value,
                       resolution_provenance, resolution_method, favorable,
                       detail, seq, curr_hash
                FROM obligation_ledger WHERE replica_id=%s
                ORDER BY obligation_id, seq DESC""", (replica_id,))
            return {row["obligation_id"]: dict(row) for row in cur.fetchall()}

    @app.post("/replica/{replica_id}/obligations/derive")
    def derive_obligations(replica_id: str):
        """Recompute the open-obligation set from the decision feed itself.

        Idempotent: an obligation already on the chain is not re-appended.
        Rows whose declaration will not parse are REPORTED, never skipped
        silently -- an unreadable declaration is a hole in the derivation, and
        a silent skip is how a hole becomes invisible.
        """
        from outcome_v1 import MaturationRule, open_obligation

        with db() as conn:
            _meta(conn, replica_id)
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT primary_id, current_hash, outcome_obligation, decided_at, domain
                    FROM replica_entries
                    WHERE replica_id=%s AND outcome_obligation IS NOT NULL
                    ORDER BY primary_id ASC""", (replica_id,))
                rows = [dict(x) for x in cur.fetchall()]

            known = set(_latest_states(conn, replica_id))
            derived, skipped, unreadable = 0, 0, []
            for row in rows:
                declaration = row["outcome_obligation"]
                try:
                    rule = MaturationRule.parse(declaration)
                except ValueError as exc:
                    unreadable.append({"primary_id": row["primary_id"],
                                       "declaration": declaration,
                                       "error": str(exc)})
                    continue
                if row["decided_at"] is None:
                    unreadable.append({"primary_id": row["primary_id"],
                                       "declaration": declaration,
                                       "error": "no decided_at shipped; a horizon "
                                                "cannot be derived without the time "
                                                "the clock started"})
                    continue
                obligation_id = f"{row['current_hash']}:{rule.kind}"
                if obligation_id in known:
                    skipped += 1
                    continue
                obligation = open_obligation(
                    obligation_id=obligation_id,
                    decision_hash=row["current_hash"],
                    domain=row.get("domain") or rule.kind,
                    rule=rule,
                    opened_at=float(row["decided_at"]),
                )
                _append_obligation(conn, replica_id, obligation,
                                   primary_id=row["primary_id"],
                                   declaration=declaration)
                known.add(obligation_id)
                derived += 1
        return {"status": "derived", "opened": derived, "already_known": skipped,
                "unreadable": unreadable, "decisions_declaring": len(rows)}

    @app.post("/replica/{replica_id}/obligations/{obligation_id}/transition")
    def transition_obligation(replica_id: str, obligation_id: str,
                              body: Dict[str, Any]):
        """Record a resolution, a restated open reason, or an abandonment.

        Signed by the customer in the same posture as custody_event: the
        receiver builds the chain, the signer attests to the content, and a
        regulator can verify both independently of Sentinel.
        """
        from outcome_v1 import (OutcomeObligation, abandon, resolve, stay_open,
                                OUTCOME_ABANDONED, OUTCOME_OPEN, OUTCOME_RESOLVED,
                                OutcomeIntegrityError)

        target_state = body.get("state")
        if target_state not in (OUTCOME_OPEN, OUTCOME_RESOLVED, OUTCOME_ABANDONED):
            raise HTTPException(status_code=422,
                                detail=f"state must be one of "
                                       f"{[OUTCOME_OPEN, OUTCOME_RESOLVED, OUTCOME_ABANDONED]}")
        with db() as conn:
            _meta(conn, replica_id)
            current = _latest_states(conn, replica_id).get(obligation_id)
            if not current:
                raise HTTPException(status_code=404,
                                    detail="no such obligation on this replica; "
                                           "derive the open set first")
            if current["state"] != OUTCOME_OPEN:
                raise HTTPException(
                    status_code=409,
                    detail=f"obligation is {current['state']}; a closed obligation is "
                           f"not reopened -- record a new one if a new fact arrived")
            existing = OutcomeObligation(
                obligation_id=current["obligation_id"],
                decision_hash=current["decision_hash"],
                domain=current["domain"],
                obligation_kind=current["obligation_kind"],
                opened_at=float(current["opened_at"]),
                expected_by=float(current["expected_by"]),
                state=OUTCOME_OPEN,
                reason_code=current["reason_code"],
                detail=dict(current["detail"] or {}),
            )
            try:
                if target_state == OUTCOME_RESOLVED:
                    updated = resolve(
                        existing,
                        resolved_at=float(body.get("resolved_at") or 0.0),
                        resolved_value=body.get("resolved_value") or {},
                        provenance=body.get("provenance") or "",
                        favorable=body.get("favorable"),
                        method=body.get("method"))
                elif target_state == OUTCOME_ABANDONED:
                    updated = abandon(existing, str(body.get("reason_code") or ""),
                                      at=float(body.get("at") or 0.0))
                else:
                    updated = stay_open(existing, str(body.get("reason_code") or ""))
            except OutcomeIntegrityError as exc:
                raise HTTPException(status_code=422,
                                    detail={"violations": exc.violations})
            seq, curr_hash = _append_obligation(
                conn, replica_id, updated,
                primary_id=current["primary_id"],
                declaration=current["declaration"],
                signature=body.get("signature"), signer_pub=body.get("signer_pub"))
        return {"status": "recorded", "seq": seq, "curr_hash": curr_hash,
                "state": updated.state, "reason_code": updated.reason_code}

    @app.get("/replica/{replica_id}/obligations")
    def list_obligations(replica_id: str, now: Optional[float] = None):
        """The examiner query: what is owed, what closed, what is late.

        `overdue` is COMPUTED here from opened_at/expected_by against the
        clock, never read from a column. A stored overdue flag is a number
        somebody can set to False; two timestamps and a comparison are not.
        """
        from outcome_v1 import OUTCOME_OPEN

        clock = float(now) if now is not None else __import__("time").time()
        with db() as conn:
            _meta(conn, replica_id)
            states = _latest_states(conn, replica_id)
        obligations, summary = [], {}
        for row in sorted(states.values(), key=lambda x: x["opened_at"]):
            overdue = row["state"] == OUTCOME_OPEN and clock > float(row["expected_by"])
            obligations.append({**row, "overdue": overdue})
            summary[row["state"]] = summary.get(row["state"], 0) + 1
            if overdue:
                summary["overdue"] = summary.get("overdue", 0) + 1
            if row["state"] == OUTCOME_OPEN and row["reason_code"]:
                key = f"open:{row['reason_code']}"
                summary[key] = summary.get(key, 0) + 1
        return {"replica_id": replica_id, "as_of": clock,
                "summary": summary, "obligations": obligations}

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
