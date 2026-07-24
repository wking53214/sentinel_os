"""twin_detector -- the customer/regulator divergence detector (DAP-6).

Runs entirely on customer-side credentials. Inputs:

  * the replica (receiver URL),
  * the primary hash feed (read-only ledger_reader credential: id, call_sid,
    previous_hash, current_hash, timestamp -- hashes and identifiers, not a
    Sentinel-authored verdict),
  * the customer's OWN submission record: a JSONL file of every 202-accepted
    job_id/sid the customer's submitter logged at submission time. This is the
    Independent Completeness Cross-check (ICC): a count of what MUST exist,
    created outside anything Sentinel's ledger or replica can edit,
  * optionally, decryption authority for deep verification: the private key
    (custody A) or the custodian's decrypt API plus the customer signing key
    (custody D).

Per-entry verdicts (DAP-6.2):
  MATCH    -- present on both sides; clear hashes agree; deep verification
              (when enabled) opens the envelope and recomputes current_hash
              from the decrypted payload successfully.
  DIVERGE  -- present on both sides but different: clear-hash mismatch,
              unopenable/tampered envelope, decrypted payload that does not
              recompute to the stored hash, or (deep verification only) a live
              primary cassette_snapshot that differs from the replica's
              witnessed copy despite an intact hash chain -- the H4 forgery
              class, since the raw snapshot body is not itself hashed into
              current_hash. Sub-cause recorded.
  MISSING  -- expected but absent from the replica after the SLA window:
              expected via the primary feed, via the ICC record, or both.
              Sub-cause records whether the entry exists on the primary
              (withheld/lagging ship) or nowhere at all (dropped before the
              ledger -- gravest).
Plus:
  PENDING  -- absent but younger than the replica's max-lag SLA; not a verdict.
  EXTRA    -- present on the replica but no longer on the primary feed. This is
              the wipe/rewrite evidence class (H5): the customer's copy holds
              history the primary no longer admits to.

Chain order (DAP-6.3): replica entries, ordered by primary_id, must reproduce
the primary's hash linkage; consecutive present entries must self-link
(previous_hash == prior current_hash). Wall clocks are never consulted, which
is why clock skew is structurally not a divergence source in DAP v1.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Dict, List, Optional

import httpx
import psycopg2

from twin_custody import CustodyError, deep_verify_row, open_envelope, sign


def _canon(obj: Any) -> str:
    """Order-stable serialization for comparing two snapshot bodies for equality.

    Both sides are plain dicts (JSONB decoded from the primary; the decrypted
    replica payload); key order and NULL/absent must not create a false
    divergence. default=str mirrors the ledger's own dump so datetimes etc.
    compare the same way on both sides.
    """
    return json.dumps(obj, sort_keys=True, default=str)


# ---------------------------------------------------------------- inputs --

def load_submission_record(path: Optional[str]) -> List[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def fetch_primary_feed(feed_dsn: str) -> List[Dict[str, Any]]:
    conn = psycopg2.connect(feed_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT id, call_sid, previous_hash, current_hash,
                                  EXTRACT(EPOCH FROM timestamp), cassette_snapshot
                           FROM ledger_entries ORDER BY id ASC""")
            return [{"id": int(r[0]), "call_sid": r[1], "previous_hash": r[2],
                     "current_hash": r[3], "t": float(r[4]) if r[4] is not None else None,
                     "cassette_snapshot": r[5]}
                    for r in cur.fetchall()]
    finally:
        conn.close()


def fetch_replica_entries(receiver_url: str, replica_id: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    after = 0
    while True:
        r = httpx.get(f"{receiver_url.rstrip('/')}/replica/{replica_id}/entries",
                      params={"after_id": after, "limit": 1000}, timeout=10.0)
        r.raise_for_status()
        batch = r.json()["entries"]
        if not batch:
            return entries
        entries.extend(batch)
        after = batch[-1]["primary_id"]


def fetch_replica_meta(receiver_url: str, replica_id: str) -> Dict[str, Any]:
    r = httpx.get(f"{receiver_url.rstrip('/')}/replica/{replica_id}/meta", timeout=10.0)
    r.raise_for_status()
    return r.json()


# ------------------------------------------------------------- decryptors --

class OptionADecryptor:
    via = "customer-held-key"

    def __init__(self, priv_b64: str):
        self._priv = priv_b64

    def open(self, envelope: Dict[str, Any], aad: Dict[str, Any]) -> bytes:
        return open_envelope(envelope, self._priv, aad)


class OptionDDecryptor:
    """Decrypts through the custodian; every call lands in the signed audit log."""
    via = "custodian"

    def __init__(self, custodian_url: str, replica_id: str,
                 customer_sign_priv_b64: str, requester: str):
        self.url = custodian_url.rstrip("/")
        self.replica_id = replica_id
        self.sign_priv = customer_sign_priv_b64
        self.requester = requester

    def open(self, envelope: Dict[str, Any], aad: Dict[str, Any]) -> bytes:
        import base64
        import uuid
        nonce = uuid.uuid4().hex
        auth_payload = {"replica_id": self.replica_id, "primary_id": aad["primary_id"],
                        "nonce": nonce, "requester": self.requester}
        body = {"replica_id": self.replica_id, "primary_id": aad["primary_id"],
                "envelope": envelope, "aad": aad, "requester": self.requester,
                "nonce": nonce, "auth_sig": sign(auth_payload, self.sign_priv)}
        r = httpx.post(f"{self.url}/decrypt", json=body, timeout=10.0)
        if r.status_code != 200:
            raise CustodyError(f"custodian refused decrypt: {r.status_code} {r.text[:120]}")
        return base64.b64decode(r.json()["plaintext_b64"])


# ---------------------------------------------------------------- verdict --

def run_detection(replica_entries: List[Dict[str, Any]],
                  primary_feed: List[Dict[str, Any]],
                  submission_record: List[Dict[str, Any]],
                  sla_seconds: int,
                  decryptor: Optional[Any] = None,
                  replica_id: str = "",
                  now: Optional[float] = None,
                  primary_id_range: Optional[tuple] = None) -> Dict[str, Any]:
    now = now if now is not None else time.time()
    by_pid = {e["primary_id"]: e for e in replica_entries}
    replica_sids = {e["call_sid"] for e in replica_entries if e.get("call_sid")}

    # Scope: a replica witnesses a defined window of the primary stream. It can
    # only be held accountable for rows within that window -- rows outside its
    # synced span are future/not-yet-covered, not missing. Callers pass an
    # explicit (lo, hi); by default the window is the replica's own min..max
    # span. Omissions at the very edges of the stream (before the first or after
    # the last synced row) fall outside this span by construction and are caught
    # instead by the Independent Completeness Cross-check against the customer's
    # submission record -- which is exactly why that second, feed-independent
    # layer exists.
    replica_pids = [e["primary_id"] for e in replica_entries]
    replica_max = max(replica_pids, default=0)
    replica_min = min(replica_pids, default=0)
    if primary_id_range is not None:
        lo, hi = primary_id_range
        lo = replica_min if lo is None else lo
        hi = replica_max if hi is None else hi
    else:
        lo, hi = replica_min, replica_max
    scoped_feed = [p for p in primary_feed if lo <= p["id"] <= hi]

    primary_by_id = {p["id"]: p for p in scoped_feed}
    primary_sids = {p["call_sid"] for p in scoped_feed if p.get("call_sid")}

    match: List[int] = []
    diverge: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []
    extra: List[Dict[str, Any]] = []
    deep_verified = 0

    # 1) primary feed vs replica (within the replica's covered window) ----
    for p in scoped_feed:
        rep = by_pid.get(p["id"])
        if rep is None:
            age = (now - p["t"]) if p["t"] else None
            bucket = pending if (age is not None and age < sla_seconds) else missing
            bucket.append({"primary_id": p["id"], "sid": p.get("call_sid"),
                           "sub": "present_on_primary_not_replica",
                           "age_s": None if age is None else round(age, 3)})
            continue
        if (rep["previous_hash"] != p["previous_hash"]
                or rep["current_hash"] != p["current_hash"]):
            diverge.append({"primary_id": p["id"], "sub": "clear_hash_mismatch",
                            "detail": f"replica {rep['current_hash'][:12]}.. vs primary {p['current_hash'][:12]}.."})
            continue
        if decryptor is not None:
            aad = {"replica_id": replica_id, "primary_id": rep["primary_id"],
                   "current_hash": rep["current_hash"]}
            try:
                row = json.loads(decryptor.open(rep["envelope"], aad))
            except CustodyError as exc:
                diverge.append({"primary_id": p["id"], "sub": "envelope_unopenable",
                                "detail": str(exc)})
                continue
            ok, detail = deep_verify_row(row)
            if not ok:
                diverge.append({"primary_id": p["id"], "sub": "payload_hash_mismatch",
                                "detail": detail})
                continue
            # H4 -- cassette-snapshot forgery. The raw snapshot body is NOT part
            # of current_hash (only its cassette_hash digest is), so a snapshot
            # edited on the primary while cassette_hash/current_hash are left
            # intact passes every hash check above and would otherwise score
            # MATCH. The replica holds the customer's witnessed honest copy;
            # cross-check the live primary snapshot against it. Requires deep
            # verification (a decryptor) -- without decryption authority the
            # honest body is unreadable and this class is structurally
            # invisible, same boundary as every other deep-verify check.
            if _canon(row.get("cassette_snapshot")) != _canon(p.get("cassette_snapshot")):
                diverge.append({"primary_id": p["id"], "sub": "cassette_snapshot_forgery",
                                "detail": "primary cassette_snapshot differs from the "
                                          "replica's witnessed copy (hash chain intact)"})
                continue
            deep_verified += 1
        match.append(p["id"])

    # 2) extras: replica holds what the primary no longer shows (wipe class)
    for e in replica_entries:
        if e["primary_id"] not in primary_by_id:
            extra.append({"primary_id": e["primary_id"], "sid": e.get("call_sid"),
                          "sub": "extra_on_replica_absent_on_primary"})

    # 3) ICC: the customer's own submission record ------------------------
    accounted_missing_sids = {m.get("sid") for m in missing} | {m.get("sid") for m in pending}
    for rec in submission_record:
        sid = rec.get("sid") or rec.get("job_id")
        if not sid or sid in replica_sids:
            continue
        age = now - float(rec.get("t", now))
        target = pending if age < sla_seconds else missing
        if sid in primary_sids:
            if sid not in accounted_missing_sids:
                target.append({"sid": sid, "sub": "present_on_primary_not_replica",
                               "icc": True, "age_s": round(age, 3)})
        else:
            target.append({"sid": sid, "sub": "absent_everywhere",
                           "icc": True, "age_s": round(age, 3)})

    # 4) chain order on the replica itself --------------------------------
    ordered = sorted(replica_entries, key=lambda e: e["primary_id"])
    chain_ok = True
    chain_breaks: List[Dict[str, Any]] = []
    for prev, cur in zip(ordered, ordered[1:]):
        if cur["primary_id"] == prev["primary_id"] + 1:
            if cur["previous_hash"] != prev["current_hash"]:
                chain_ok = False
                chain_breaks.append({"at_primary_id": cur["primary_id"],
                                     "sub": "replica_linkage_break"})

    verdict = "CLEAN" if not (diverge or missing or extra or not chain_ok) else "FINDINGS"
    return {
        "dap_version": 1,
        "replica_id": replica_id,
        "generated_at": now,
        "sla_seconds": sla_seconds,
        "verdict": verdict,
        "counts": {"match": len(match), "diverge": len(diverge), "missing": len(missing),
                   "pending": len(pending), "extra": len(extra),
                   "deep_verified": deep_verified,
                   "primary_feed": len(primary_feed),
                   "primary_feed_in_window": len(scoped_feed),
                   "replica_entries": len(replica_entries),
                   "submission_record": len(submission_record)},
        "covered_window": {"lo": lo, "hi": hi},
        "diverge": diverge, "missing": missing, "pending": pending, "extra": extra,
        "chain_ok": chain_ok, "chain_breaks": chain_breaks,
        "deep_verification": None if decryptor is None else decryptor.via,
    }


def detect(receiver_url: str, replica_id: str, feed_dsn: str,
           submission_record_path: Optional[str] = None,
           sla_seconds: Optional[int] = None,
           decryptor: Optional[Any] = None,
           primary_id_range: Optional[tuple] = None) -> Dict[str, Any]:
    meta = fetch_replica_meta(receiver_url, replica_id)
    sla = sla_seconds if sla_seconds is not None else int(meta.get("max_lag_seconds", 30))
    report = run_detection(
        replica_entries=fetch_replica_entries(receiver_url, replica_id),
        primary_feed=fetch_primary_feed(feed_dsn),
        submission_record=load_submission_record(submission_record_path),
        sla_seconds=sla, decryptor=decryptor, replica_id=replica_id,
        primary_id_range=primary_id_range)
    report["custody_model"] = meta.get("custody_model")
    report["site"] = meta.get("site")
    report["is_primary_evidence"] = meta.get("is_primary_evidence")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Twin divergence detector (DAP v1)")
    ap.add_argument("--receiver-url", required=True)
    ap.add_argument("--replica-id", required=True)
    ap.add_argument("--feed-dsn", required=True,
                    help="read-only primary hash feed, e.g. host=... user=ledger_reader ...")
    ap.add_argument("--submission-record", default=None)
    ap.add_argument("--sla-seconds", type=int, default=None)
    ap.add_argument("--key-file", default=None, help="custody A private key file")
    ap.add_argument("--custodian-url", default=None)
    ap.add_argument("--sign-key-file", default=None, help="customer signing key (custody D)")
    ap.add_argument("--requester", default="customer-audit")
    args = ap.parse_args()

    decryptor = None
    if args.key_file:
        decryptor = OptionADecryptor(open(args.key_file).read().strip())
    elif args.custodian_url and args.sign_key_file:
        decryptor = OptionDDecryptor(args.custodian_url, args.replica_id,
                                     open(args.sign_key_file).read().strip(),
                                     args.requester)

    report = detect(args.receiver_url, args.replica_id, args.feed_dsn,
                    args.submission_record, args.sla_seconds, decryptor)
    print(json.dumps(report, indent=2, default=str))
    raise SystemExit(0 if report["verdict"] == "CLEAN" else 2)


if __name__ == "__main__":
    main()
