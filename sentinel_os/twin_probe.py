"""twin_probe -- the regulator's red-button conformance probe (DAP-9).

One command, run by an auditor on customer-side inputs, no Sentinel
cooperation required, producing a machine-readable verdict Sentinel cannot
pre-cook:

  * the DAP-6 divergence report (match / diverge / missing, plus pending,
    extras, and chain order), including the Independent Completeness
    Cross-check against the customer's own submission record;
  * verification of the replica's custody log: hash chain intact and every
    event signed by the key owner who made it;
  * for custody model D: verification of the custodian audit log (chain +
    custodian signatures) and an attribution summary -- decrypt grants and
    refusals per requester -- so "Sentinel requested zero decrypts" is a
    checked statement, not a quote.

The probe is a thin, spec-pinned client: everything it reads travels over the
documented DAP interfaces, so an independent implementation of this file from
the spec alone must reach the same verdict on the same inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from typing import Any, Dict, Optional

import httpx

from twin_custodian import verify_audit_log
from twin_custody import canonical_json, verify_signature
from twin_detector import OptionADecryptor, OptionDDecryptor, detect


def verify_custody_log(receiver_url: str, replica_id: str) -> Dict[str, Any]:
    r = httpx.get(f"{receiver_url.rstrip('/')}/replica/{replica_id}/custody-log",
                  timeout=10.0)
    r.raise_for_status()
    events = r.json()["events"]
    prev = "genesis"
    for ev in events:
        payload = {"replica_id": replica_id, "seq": ev["seq"], "event": ev["event"],
                   "detail": ev["detail"], "actor": ev["actor"], "prev_hash": ev["prev_hash"]}
        if ev["prev_hash"] != prev:
            return {"ok": False, "at_seq": ev["seq"], "why": "prev_hash break"}
        if hashlib.sha256(canonical_json(payload)).hexdigest() != ev["curr_hash"]:
            return {"ok": False, "at_seq": ev["seq"], "why": "curr_hash mismatch"}
        content = {"replica_id": replica_id, "event": ev["event"],
                   "detail": ev["detail"], "actor": ev["actor"]}
        if ev.get("signer_pub") and not verify_signature(content, ev.get("signature", ""),
                                                         ev["signer_pub"]):
            return {"ok": False, "at_seq": ev["seq"], "why": "bad signer signature"}
        prev = ev["curr_hash"]
    return {"ok": True, "events": len(events),
            "kinds": sorted({e["event"] for e in events})}


def custodian_attestation(custodian_url: str, replica_id: str) -> Dict[str, Any]:
    r = httpx.get(f"{custodian_url.rstrip('/')}/audit-log",
                  params={"replica_id": replica_id}, timeout=10.0)
    r.raise_for_status()
    body = r.json()
    chain = verify_audit_log(body["events"], body["log_sign_pub"])
    grants: Counter = Counter()
    refusals: Counter = Counter()
    for ev in body["events"]:
        if ev.get("event") != "decrypt":
            continue
        (grants if ev.get("granted") else refusals)[ev.get("requester", "unknown")] += 1
    return {"log_verified": chain,
            "decrypt_grants_by_requester": dict(grants),
            "decrypt_refusals_by_requester": dict(refusals)}


def run_probe(receiver_url: str, replica_id: str, feed_dsn: str,
              submission_record: Optional[str], sla_seconds: Optional[int],
              key_file: Optional[str], custodian_url: Optional[str],
              sign_key_file: Optional[str], requester: str) -> Dict[str, Any]:
    decryptor = None
    if key_file:
        decryptor = OptionADecryptor(open(key_file).read().strip())
    elif custodian_url and sign_key_file:
        decryptor = OptionDDecryptor(custodian_url, replica_id,
                                     open(sign_key_file).read().strip(), requester)
    detection = detect(receiver_url, replica_id, feed_dsn, submission_record,
                       sla_seconds, decryptor)
    custody_log = verify_custody_log(receiver_url, replica_id)
    custodian = (custodian_attestation(custodian_url, replica_id)
                 if custodian_url else None)
    ok = (detection["verdict"] == "CLEAN" and custody_log.get("ok")
          and (custodian is None or custodian["log_verified"].get("ok")))
    return {"probe": "DAP-9", "dap_version": 1, "conformant": bool(ok),
            "detection": detection, "custody_log": custody_log,
            "custodian": custodian}


def main() -> None:
    ap = argparse.ArgumentParser(description="DAP v1 regulator conformance probe")
    ap.add_argument("--receiver-url", required=True)
    ap.add_argument("--replica-id", required=True)
    ap.add_argument("--feed-dsn", required=True)
    ap.add_argument("--submission-record", default=None)
    ap.add_argument("--sla-seconds", type=int, default=None)
    ap.add_argument("--key-file", default=None)
    ap.add_argument("--custodian-url", default=None)
    ap.add_argument("--sign-key-file", default=None)
    ap.add_argument("--requester", default="regulator-probe")
    args = ap.parse_args()
    report = run_probe(args.receiver_url, args.replica_id, args.feed_dsn,
                       args.submission_record, args.sla_seconds, args.key_file,
                       args.custodian_url, args.sign_key_file, args.requester)
    print(json.dumps(report, indent=2, default=str))
    sys.exit(0 if report["conformant"] else 2)


if __name__ == "__main__":
    main()
