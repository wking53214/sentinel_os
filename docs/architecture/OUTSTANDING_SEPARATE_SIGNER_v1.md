# Outstanding item: a separate signer for row attestation and the head anchor (v1)

Status: OUTSTANDING. Nothing in this document is built. It records a design choice the owner has not yet made, so that the next run starts from the same facts. Written 2026-11-19 against `main`. Evidence labels: EXECUTED (run), INSPECTED (read in the code or config), REPORTED (stated elsewhere, not re-checked here), PROPOSED (not built).

## 1. The problem in plain words

The one process that writes the ledger also holds everything needed to make a rewrite look honest. If an attacker takes over that process, they can change history and then re-sign it so the checks pass.

What the writer service holds today (INSPECTED, `sentinel_os/docker-compose.yml`, worker service):

- The database owner account name and password (`POSTGRES_USER`, `POSTGRES_PASSWORD`).
- The restricted runtime account password.
- The attestation key (`ICEBERG_LEDGER_ATTESTATION_KEY`) and the previous keys.
- The path of the head anchor file, on a volume mounted on this service only.

What follows from that (EXECUTED 2026-11-16, recorded in the root README): with the anchor configured, a rewrite made by someone who had only the database was reported as `TRUNCATED`. After the anchor was re-sealed with the attestation key, `verify_chain` reported no violation. So the anchor detects a database-only attacker. It does not detect a compromised writer. For that case the customer-held twin is the only detection layer, and only for rows that reached it before the tampering.

Two separate weaknesses are mixed together here. They have different fixes:

1. **The owner password is in the writer.** Fixable on its own. See section 6, step 1.
2. **The signing key and the anchor are in the writer.** This document is about this one.

## 2. What this item is

Move signing and anchoring out of the writer into a small separate service (the signer). The writer would ask the signer to attest a row and to seal the head, and would never see the key or be able to write the anchor.

## 3. What the signer must do to be worth building

A signer that signs whatever it is handed only moves the secret. These properties are what make it a real control:

1. **It tracks the head itself.** It remembers the last head and row count it sealed. It seals a new head only if the new head extends the one it holds. A request that would shorten the chain, or branch it, is refused and logged.
2. **It keeps its own anchor.** The signer holds the anchor volume. The writer cannot reach it.
3. **It fails closed.** If the signer is down or refuses, the writer stops appending. It never writes unsigned rows.
4. **It signs only the narrow payload already defined.** For row attestation that is the claimed identity, the previous hash and the record kind (`governance/authorized_by_attestation.py`). For the anchor it is the head, the row count, the time and the optional name (`twin_custody.py`). No free text.
5. **Verification does not need the ability to forge.** Today one symmetric key both signs and verifies (INSPECTED, root README, "One symmetric key"). Anyone who can check a signature can also forge one. A signer only helps if the checkers hold something weaker than the signer. That means a key pair: the signer holds the private key and everyone else holds the public key. See open decision 1.
6. **Rotation still works.** Rows signed under earlier keys must keep verifying. The existing key-fingerprint label and previous-keys set carry over.

## 4. What it fixes and what it does not

Fixes (PROPOSED, not built):

- A compromised writer can no longer re-seal the anchor after rewriting history, because it cannot sign and cannot reach the anchor.
- A compromised writer can no longer forge a signature for a row it did not append through the signer.

Does not fix, and must not be described as fixing:

- **False rows through the normal path.** A compromised writer can still ask the signer to attest new rows that extend the chain, with a false identity claim. The signer cannot judge whether a claim is true. The attestation never established that (see "What this does not establish" in `authorized_by_attestation.py`). The customer-held twin remains the detector.
- **A compromised signer.** It becomes the new target and must be much smaller and more locked down than the writer.
- **Loss of availability.** Fail-closed means a signer outage stops writes. That is the intended behavior, and it has an operating cost.

## 5. Purple team: attacks and answers

| Attack | What stops it | Residual risk |
|---|---|---|
| Compromised writer rewrites old rows through the database, then re-seals the anchor | Writer has no signing capability and no anchor access. Signer refuses a head that does not extend its own | None for rewriting. See the false-rows row |
| Compromised writer asks the signer to seal a shorter or forked head | Signer tracks its own head and refuses non-extending requests | Requires the signer's head record to be trustworthy and backed up |
| Compromised writer replays an old valid signature | Payload pins the previous hash, so a signature is tied to one chain position. The signer also logs every request | Needs a test with a replayed request |
| Compromised writer floods the signer to cause an outage | Per-caller rate limit and an alert. Outage means writes stop, not forged data | Denial of service remains possible by design |
| Attacker steals the signer's private key | Key held in a place the writer cannot read. Rotation procedure. Public keys let rows signed earlier still verify | Everything signed afterwards under that key is suspect until rotation |
| Attacker rolls the signer's own state back (restore an old backup) | Signer state is append-only, and the customer-held twin holds an independent copy of earlier heads | The twin only covers rows it received |
| Attacker swaps the public key the verifier trusts | Public key fingerprint is recorded in each row and in the anchor, and the verifier pins it | Pinning needs an agreed place to publish the fingerprint |
| Writer appends false identity claims through the normal path | Not stopped by this item | Accepted and stated. Twin and human review remain the controls |

## 6. Suggested order of work

1. **Step 1, separate from this item: take the owner password out of the writer.** A one-off setup job does the table setup and the runtime password reset with the owner account, then exits. The writer starts with only the restricted account. This needs a change in the ledger start-up code, which today requires the owner password on every construction (INSPECTED, `governance/ledger_postgres.py`, constructor). It touches no hashed data. It does not fix the signing key weakness.
2. **Step 2: define the signer interface** (what the writer sends, what comes back, what is refused). Write the refusal cases as tests first.
3. **Step 3: build the signer** with its own head tracking, anchor volume and fail-closed behavior.
4. **Step 4: switch the writer** to call it, behind a setting that defaults to the current behavior until the tests pass.
5. **Step 5: move to a key pair**, if the owner approves open decision 1, with a migration path so rows signed under the shared key keep verifying.
6. **Step 6: prove it by attack.** Repeat the 2026-11-16 experiment: rewrite as the owner, re-seal the anchor from the writer, and show that `verify_chain` now reports a violation.

Every step follows the repository's standing rules in `CLAUDE.md`: a branch, minimal diffs, no new unhashed ledger columns, no hard-coded keys, the affected tests after each step.

## 7. Open decisions for the owner

1. **Move from one shared HMAC key to a key pair?** Without it, a signer protects against a compromised writer only if no verifier is also compromised, because every verifier can forge. The cost is a change to the attestation scheme that today uses the standard library only. The files that describe the current scheme are `governance/authorized_by_attestation.py` and `twin_custody.py`.
2. **Where does the signer run?** Another container in the same compose file is the cheapest. A separate host or a managed key service is stronger and costs more to operate.
3. **What should the writer do when the signer is down?** The proposal is to stop appending. Queueing rows unsigned and signing later would reopen the gap this item closes.

## 8. Not covered here

- Authenticating the people named in `authorized_by`. The attestation does not do that and this item does not change it.
- The twin's own key handling and the customer's side of custody.
- Anything in the other repositories of the library.
