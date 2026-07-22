# Changelog

Dated, human-readable summary of notable changes. Git history has the
full detail; this is the skim version.

## 2026-07-22

- **Phase 2 merged** — closed 6 of 7 Known Limitations: cassette
  version binding, code-hash coverage, structural injection defense,
  model identity per decision, decision supersession, authorizing
  identity. See `COMPLIANCE.md` and `PHASE2_MIGRATION_NOTES.md`.
- **ICEBERG_LEDGER_RUNTIME_USER made fail-closed** — the app no longer
  boots with a privileged database credential, even by accident. See
  `governance/README.md`.
- **docker-compose fixed end-to-end** — the runtime-user fix above
  would have broken `docker-compose up` (no fallback credential to
  silently use); fixed via self-provisioning instead of just patching
  the compose file. Also fixed a separate, pre-existing startup race
  (`iceberg-main` could start before Postgres was actually ready to
  accept connections).
- **CI corrected** — `tests.yml` previously only ran the `Tests/`
  subdirectory (27 of 37 test files) and had no Redis service at all.
  Now runs the full suite; `test_twin_live.py` is explicitly excluded
  (needs infrastructure — 3 OS identities, real TLS PKI between them —
  not yet reconstructed in CI) rather than silently skipped.
- **Full stack verified live** — real Postgres ledger, real fail-closed
  credential behavior, a governed call correctly blocked with no
  governor configured, and an independently-verified 25-entry hash
  chain, all confirmed running end-to-end.
