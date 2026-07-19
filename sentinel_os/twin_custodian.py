"""twin_custodian -- neutral key custodian for custody model D (DAP-7).

Runs under its OWN credentials (third OS user, key material 0700 in its home).
Neither Sentinel nor the customer holds the recipient private key; decryption
happens only here, and every attempt -- granted or refused -- lands in a
hash-chained audit log where each entry is Ed25519-signed by the custodian.

That log is the attribution layer: "Sentinel requested zero decrypts in the
audit window" is a signed, independently verifiable statement rather than a
promise. Refused attempts are logged too, with the claimed requester, so a
probe can show not only who read what, but who TRIED.

Authorization: a decrypt request must carry the CUSTOMER's Ed25519 signature
over {replica_id, primary_id, nonce, requester}. The custodian verifies it
against the customer signing key registered for that replica. Nonces are
single-use (replay of a captured authorization is refused and logged).

Reference implementation notes: registration is unauthenticated and state is
file-backed JSON/JSONL under ~/custodian; a production custodian fronts this
with its own customer authentication and durable storage. The wire contract
and log format are the normative parts (spec §7).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from typing import Any, Dict, Optional

import uvicorn
from fastapi import FastAPI, HTTPException

from twin_custody import (
    CustodyError,
    canonical_json,
    fingerprint,
    generate_recipient_keypair,
    generate_signing_keypair,
    open_envelope,
    sign,
    verify_signature,
)


class CustodianState:
    def __init__(self, home: str):
        self.home = home
        os.makedirs(home, mode=0o700, exist_ok=True)
        self._lock = threading.Lock()
        self.keys_path = os.path.join(home, "keys.json")
        self.replicas_path = os.path.join(home, "replicas.json")
        self.log_path = os.path.join(home, "audit_log.jsonl")
        self.nonces_path = os.path.join(home, "nonces.json")
        if os.path.exists(self.keys_path):
            self.keys = json.load(open(self.keys_path))
        else:
            rpriv, rpub = generate_recipient_keypair()
            spriv, spub = generate_signing_keypair()
            self.keys = {"recipient_priv": rpriv, "recipient_pub": rpub,
                         "log_sign_priv": spriv, "log_sign_pub": spub}
            fd = os.open(self.keys_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump(self.keys, fh)
        self.replicas: Dict[str, str] = (
            json.load(open(self.replicas_path)) if os.path.exists(self.replicas_path) else {})
        self.nonces = set(
            json.load(open(self.nonces_path)) if os.path.exists(self.nonces_path) else [])
        self.last_hash = "genesis"
        self.seq = 0
        if os.path.exists(self.log_path):
            for line in open(self.log_path):
                if line.strip():
                    rec = json.loads(line)
                    self.last_hash = rec["curr_hash"]
                    self.seq = rec["seq"]

    def _save(self, path: str, obj: Any) -> None:
        with open(path, "w") as fh:
            json.dump(obj, fh)

    def register(self, replica_id: str, customer_sign_pub: str) -> None:
        with self._lock:
            self.replicas[replica_id] = customer_sign_pub
            self._save(self.replicas_path, self.replicas)

    def log(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Append one hash-chained, custodian-signed audit record."""
        with self._lock:
            self.seq += 1
            core = {"seq": self.seq, "ts": time.time(), "prev_hash": self.last_hash, **event}
            curr_hash = hashlib.sha256(canonical_json(core)).hexdigest()
            record = {**core, "curr_hash": curr_hash,
                      "sig": sign({**core, "curr_hash": curr_hash}, self.keys["log_sign_priv"])}
            with open(self.log_path, "a") as fh:
                fh.write(json.dumps(record) + "\n")
            self.last_hash = curr_hash
            return record

    def consume_nonce(self, nonce: str) -> bool:
        with self._lock:
            if nonce in self.nonces:
                return False
            self.nonces.add(nonce)
            self._save(self.nonces_path, sorted(self.nonces))
            return True


def build_app(state: CustodianState) -> FastAPI:
    app = FastAPI(title="twin-custodian", version="1.0")

    @app.get("/health")
    def health():
        return {"ok": True, "role": "custodian"}

    @app.get("/public-keys")
    def public_keys():
        return {"recipient_pub": state.keys["recipient_pub"],
                "recipient_fp": fingerprint(state.keys["recipient_pub"]),
                "log_sign_pub": state.keys["log_sign_pub"]}

    @app.post("/register-replica")
    def register(body: Dict[str, Any]):
        for f in ("replica_id", "customer_sign_pub"):
            if not body.get(f):
                raise HTTPException(status_code=422, detail=f"missing '{f}'")
        state.register(body["replica_id"], body["customer_sign_pub"])
        state.log({"event": "register_replica", "replica_id": body["replica_id"],
                   "requester": body.get("requester", "customer"), "granted": True})
        return {"registered": body["replica_id"]}

    @app.post("/decrypt")
    def decrypt(body: Dict[str, Any]):
        replica_id = body.get("replica_id")
        primary_id = body.get("primary_id")
        requester = str(body.get("requester", "unknown"))
        refuse: Optional[str] = None

        customer_pub = state.replicas.get(replica_id or "")
        if not customer_pub:
            refuse = "replica not registered with custodian"
        elif not all(k in body for k in ("envelope", "aad", "nonce", "auth_sig")):
            refuse = "missing envelope/aad/nonce/auth_sig"
        else:
            auth_payload = {"replica_id": replica_id, "primary_id": primary_id,
                            "nonce": body["nonce"], "requester": requester}
            if not verify_signature(auth_payload, body["auth_sig"], customer_pub):
                refuse = "customer authorization signature invalid"
            elif not state.consume_nonce(str(body["nonce"])):
                refuse = "nonce replay"

        if refuse:
            state.log({"event": "decrypt", "replica_id": replica_id,
                       "primary_id": primary_id, "requester": requester,
                       "granted": False, "reason": refuse})
            raise HTTPException(status_code=403, detail=refuse)

        try:
            plaintext = open_envelope(body["envelope"], state.keys["recipient_priv"],
                                      body["aad"])
        except CustodyError as exc:
            state.log({"event": "decrypt", "replica_id": replica_id,
                       "primary_id": primary_id, "requester": requester,
                       "granted": False, "reason": f"envelope failed: {exc}"})
            raise HTTPException(status_code=422, detail=str(exc))

        state.log({"event": "decrypt", "replica_id": replica_id,
                   "primary_id": primary_id, "requester": requester, "granted": True})
        return {"plaintext_b64": base64.b64encode(plaintext).decode("ascii")}

    @app.get("/audit-log")
    def audit_log(replica_id: Optional[str] = None):
        events = []
        if os.path.exists(state.log_path):
            for line in open(state.log_path):
                if line.strip():
                    rec = json.loads(line)
                    if replica_id is None or rec.get("replica_id") == replica_id:
                        events.append(rec)
        return {"events": events, "log_sign_pub": state.keys["log_sign_pub"]}

    return app


def verify_audit_log(events: list, log_sign_pub: str) -> Dict[str, Any]:
    """Customer/regulator-side verification of the custodian log: hash chain
    intact and every record custodian-signed. Importable; used by the probe."""
    prev = "genesis"
    for rec in events:
        core = {k: v for k, v in rec.items() if k not in ("curr_hash", "sig")}
        expected = hashlib.sha256(canonical_json(core)).hexdigest()
        if rec.get("prev_hash") != prev or rec.get("curr_hash") != expected:
            return {"ok": False, "at_seq": rec.get("seq"), "why": "hash chain break"}
        if not verify_signature({**core, "curr_hash": rec["curr_hash"]}, rec.get("sig", ""),
                                log_sign_pub):
            return {"ok": False, "at_seq": rec.get("seq"), "why": "bad custodian signature"}
        prev = rec["curr_hash"]
    return {"ok": True, "records": len(events)}


def main() -> None:
    home = os.environ.get("TWIN_CUSTODIAN_HOME", os.path.expanduser("~/custodian"))
    port = int(os.environ.get("TWIN_CUSTODIAN_PORT", "8400"))
    uvicorn.run(build_app(CustodianState(home)), host="127.0.0.1", port=port,
                log_level="warning")


if __name__ == "__main__":
    main()
