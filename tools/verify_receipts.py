#!/usr/bin/env python3
"""verify_receipts -- offline verification of a sentinel_os ledger export.

The artifact shown to a buyer or an auditor. Takes a ledger export file, the
head anchor file and a list of trusted key fingerprints, opens no database
connection, and prints exactly one of

    VERIFIED
    TAMPERED      the row's bytes are not what was hashed, or the chain is broken
    TRANSPLANTED  a verdict was moved onto content it was not issued for
    SEED_FORGED   the agent, not the server, chose the gate order
    TRUNCATED     the chain is shorter than, or diverges from, its anchor
    UNATTESTED    an accountable claim without a signature after enforcement
                  began, or signed by a key that is not trusted

followed, for a failure, by the first failing row id and the reason. Exit code
zero only on VERIFIED.

    python3 tools/verify_receipts.py --export ledger.json --anchor ledger.anchor \\
        --trusted-fingerprints 0123456789abcdef --key-file keys.txt

Key material: the attestation is HMAC-SHA256, so checking it needs the key,
not only its fingerprint. Keys come from --key-file (one per line) and from
the same ICEBERG_LEDGER_ATTESTATION_KEY* environment the ledger reads. A key
counts only if its fingerprint (governance/authorized_by_attestation
.key_fingerprint) is in --trusted-fingerprints; every other key is ignored.
With no trusted key material the tool refuses to run rather than print a
verdict it could not have checked.

Every check is the witness's own (sentinel_os/twin_custody.py): the same code
the twin runs, over the same shipped columns, so this tool adds no second
implementation that could drift from the ledger.

The export is the JSON written by sentinel_os/scripts/export_ledger.py:
{"format": "sentinel_os.ledger_export.v1", "columns": [...], "rows": [...]}
with every shipped column per row, in id order.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
_KERNEL = os.path.join(os.path.dirname(_HERE), "sentinel_os")
if _KERNEL not in sys.path:
    sys.path.insert(0, _KERNEL)

import twin_custody as tc  # noqa: E402
from governance import authorized_by_attestation as att  # noqa: E402

EXPORT_FORMAT = "sentinel_os.ledger_export.v1"
VERDICTS = ("VERIFIED", tc.VIOLATION_TAMPERED, tc.VIOLATION_TRANSPLANTED,
            tc.VIOLATION_SEED_FORGED, tc.VIOLATION_TRUNCATED, tc.VIOLATION_UNATTESTED)


def load_export(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        export = json.load(fh)
    if not isinstance(export, dict) or export.get("format") != EXPORT_FORMAT:
        raise SystemExit(f"{path}: not a {EXPORT_FORMAT} export")
    rows = export.get("rows")
    if not isinstance(rows, list):
        raise SystemExit(f"{path}: export carries no rows list")
    return sorted(rows, key=lambda r: int(r["id"]))


def trusted_keys(key_file: str | None, fingerprints: List[str]) -> att.KeySet:
    """Every key we hold whose fingerprint the auditor trusts, and no other."""
    held: List[bytes] = []
    if key_file:
        with open(key_file, "r", encoding="utf-8") as fh:
            held.extend(line.strip().encode("utf-8") for line in fh if line.strip())
    env = att.attestation_keyset()
    held.extend(env.trusted)
    held.extend(env.retired)
    wanted = {fp.strip() for fp in fingerprints if fp.strip()}
    return att.KeySet(None, previous=[k for k in held if att.key_fingerprint(k) in wanted])


def verify(rows: List[Dict[str, Any]], anchor: Dict[str, Any], keys: att.KeySet):
    """(verdict, row_id, reason, note). note is a second finding worth
    printing beside the verdict (the anchor, when a row failed first)."""
    failing = tc.verify_rows(rows, keys)
    anchored = tc.check_head_anchor(rows, anchor, keys)
    if failing is not None:
        row_id, kind, detail = failing
        note = None
        if anchored is not None:
            note = f"anchor mismatch as well: {anchored[2]}"
        return kind, row_id, detail, note
    if anchored is not None:
        row_id, kind, detail = anchored
        return kind, row_id, detail, None
    return "VERIFIED", None, None, None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export", required=True, help="ledger export JSON (scripts/export_ledger.py)")
    ap.add_argument("--anchor", required=True, help="head anchor file (ICEBERG_LEDGER_ANCHOR_PATH)")
    ap.add_argument("--trusted-fingerprints", required=True,
                    help="comma-separated key fingerprints the auditor trusts")
    ap.add_argument("--key-file", help="attestation keys, one per line; the environment is read too")
    args = ap.parse_args(argv)

    keys = trusted_keys(args.key_file, args.trusted_fingerprints.split(","))
    if keys.is_empty():
        print("no trusted key material: none of the held keys matches --trusted-fingerprints "
              "(supply --key-file or ICEBERG_LEDGER_ATTESTATION_KEY); refusing to print a "
              "verdict that could not be checked", file=sys.stderr)
        return 2
    try:
        rows = load_export(args.export)
        anchor = tc.read_head_anchor(args.anchor)
    except tc.CustodyError as exc:
        print(f"{tc.VIOLATION_TRUNCATED} row=- {exc}")
        return 1

    verdict, row_id, reason, note = verify(rows, anchor, keys)
    assert verdict in VERDICTS
    if verdict == "VERIFIED":
        print("VERIFIED")
        return 0
    print(f"{verdict} row={row_id if row_id is not None else '-'} {reason}")
    if note:
        print(f"  also: {note}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
