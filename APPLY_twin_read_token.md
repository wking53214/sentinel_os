# Delivery record: twin receiver read token (AC-13 follow-up)

Branch: fix/twin-read-token-2026-11-06

## Steps

1. Step 1 (commit d394da7): the receiver stores an optional read token per replica at registration. No behaviour change.
2. Step 2 (commit 28879cf): the detector and the regulator probe accept a read token (`--read-token`) and send it on the read calls. Without it, nothing changes.
3. Step 3: the receiver's four read routes (`/meta`, `/head`, `/entries`, `/custody-log`) require the read token. The ship token is refused on those routes. A replica registered without a read token refuses every read.
4. Step 4: COMPLIANCE.md and the attestation spec are updated to match.

## Pre-existing tests edited

Each edit is listed with the step that caused it.

- `Tests/test_twin_receiver_auth.py` (Step 3): the fixture now sends a read token at registration and provides a read-token header. No existing assertion changed. Five new tests cover the read routes.
- `test_twin_live.py` (Step 3): the registration helper sends a read token (derived as `read-` plus the ship token) and records it per replica. Each direct read call now sends that token. The two probe command runs now pass `--read-token`. No assertion changed.

## Not changed

- The register route still takes its own ship token from the request body. It is not gated (see COMPLIANCE.md, Known Limitations).
- Existing replicas in a live deployment have no read token. Their operator must set one (a one-time database update) before the detector or probe will work against them.
