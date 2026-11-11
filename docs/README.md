# Documentation index

Start with the [repository root README](../README.md). It is the one maintained description of what Sentinel OS is, where it fits in STACK, and what is and is not done. Where any other document here disagrees with the root README or the code, the root README and the code win.

Last checked against the code on 2026-11-10.

## Current documents (kept in step with the code)

| Document | What it is for |
|---|---|
| [../README.md](../README.md) | What this repo is, how it fits in STACK, status and gaps |
| [../STACK.md](../STACK.md) | One-page statement of this repo's role in STACK |
| [GLOSSARY_v1.md](GLOSSARY_v1.md) | Plain-English meanings of the terms and codenames |
| [../sentinel_os/README.md](../sentinel_os/README.md) | Orientation for the kernel package and how to run it |
| [../sentinel_os/DEPLOYMENT.md](../sentinel_os/DEPLOYMENT.md) | Environment variables, TLS and database setup |
| [../sentinel_os/COMPLIANCE.md](../sentinel_os/COMPLIANCE.md) | What the system does and does not claim for regulators. Carries a `main @ <sha>` stamp that CI checks. Its legal-framework mappings have not had a lawyer's review. |
| [../sentinel_os/AUDIT_PLAYBOOK.md](../sentinel_os/AUDIT_PLAYBOOK.md) | How an auditor checks the ledger, including what the checks cannot prove |
| [../sentinel_os/MODEL_CARD.md](../sentinel_os/MODEL_CARD.md) | Model card for the intent-classification component. That component's code is not in this repo. |
| [../TROUBLESHOOTING.md](../TROUBLESHOOTING.md) and [../CONTRIBUTING.md](../CONTRIBUTING.md) | Setup problems and local test setup |
| [../sentinel_os/conservation/CONFORMANCE.md](../sentinel_os/conservation/CONFORMANCE.md) | How decisions pass the conservation boundary |
| [../sentinel_os/conservation/transport/PROVENANCE.md](../sentinel_os/conservation/transport/PROVENANCE.md) | Where the vendored transport code came from and how it has drifted |
| [architecture/ACTION_AUTHORIZATION_INTERFACE_v1.md](architecture/ACTION_AUTHORIZATION_INTERFACE_v1.md) | A contract that exists in code with no production consumer yet |
| Sub-READMEs in `sentinel_os/cassettes/`, `regulatory_cassettes/`, `contract_cassettes/`, `governance/` and `tools/wiring_verify/` | One directory each |

## Historical records (true when written, not maintained)

Each of these describes the tree on its own date. Read them for how and why something was decided, not for what is true today.

| Document | Note |
|---|---|
| [CHANGELOG.md](CHANGELOG.md) | Dated history of notable changes |
| [sentinel-os-status-v1.md](sentinel-os-status-v1.md) | Early status snapshot, carries a banner |
| [pass3/](pass3/) | Nine July 2026 briefs (executive, engineering, security, auditor, investor, runbook and more) plus [POST_SNAPSHOT_CORRECTIONS_2026-09-03.md](pass3/POST_SNAPSHOT_CORRECTIONS_2026-09-03.md). Each carries a banner. |
| [architecture/](architecture/) `SENTINEL_OS_*_v1.md` | Directory map, quick reference, inventory and technical map from the pre-extraction repo. They name files that no longer exist. |
| [architecture/](architecture/) `MORTGAGE_REGULATORY_LENS_FRAMEWORK_v1` to `v4` | Design record for the Reg B lens. v4 is the last version. The lens now exists as code. |
| [audit/HERALD_CAMPAIGN_2026-08-26.md](audit/HERALD_CAMPAIGN_2026-08-26.md) | Adversarial test campaign against HERALD, a separate repo. Its [provenance note](audit/HERALD_CAMPAIGN_2026-08-26.PROVENANCE.md) says how it got here. |
| [history-scrub-record-v2.md](history-scrub-record-v2.md) | The 2026-10-21 history rewrite and what it changed |
| `../sentinel_os/PHASE2_MIGRATION_NOTES.md` | Phase 2 database migration notes |
| `../sentinel_os/*_report_v1.md`, `twin_failure_taxonomy_v1.md`, `twin_w_reconciliation_v1.md` | July 18, 2026 verification reports for the twin and the rate limiter. Each carries a banner. |
| `../APPLY_*.md` | Delivery records, one per agent-run feature (attestation, conservation boundary, operator secret, registration pinning, twin read token, verdict receipts). They say what a run changed on its date. |

## Not documentation

[prompts/README_prompt.txt](prompts/README_prompt.txt) is the instruction text used to draft the README. It is not a description of the system and not a source of truth.
