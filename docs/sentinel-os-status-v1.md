# Sentinel OS — Governance Status (v1)
*As of July 22, 2026 — repo: github.com/wking53214/sentinel_os, main @ 2880729*

## What Sentinel OS is
A domain-agnostic AI-governance platform — a hash-chained, tamper-evident ledger that records and verifies every decision an AI governor makes. IVR/contact-center was the original proving ground; domain-specific logic lives entirely in a swappable "cassette" layer, so the same governance core applies elsewhere.

## What's built, merged, and CI-verified

| Capability | Status |
|---|---|
| **Runtime credential can't rewrite or wipe its own ledger** | ✅ Merged. The app now refuses to boot unless given a non-privileged database identity — no fallback to a privileged one, ever. |
| **Cassette integrity hash covers the actual decision code**, not just its parameters | ✅ Merged. Two cassettes with identical parameters but different logic now hash differently. |
| **Model identity recorded per decision** | ✅ Merged. Every governed decision records which model version actually made the call. |
| **Structural defense against prompt injection** | ✅ Merged. Untrusted input is isolated from governor instructions at the protocol level, not just prompted around. |
| **Formal decision supersession** | ✅ Merged. A decision can be formally superseded by a later one without altering the original — the original stays immutable and provable. |
| **Authorizing identity recorded per decision** | ✅ Merged. Every decision records which service/role authorized it (never raw PII). |
| **Independent witness replica ("the twin")** | ✅ Live. A customer-controlled, encrypted replica that detects primary-ledger tampering (row rewrites, wipes) even if the primary's own credential is compromised. |
| **Full regression suite passing on real CI** | ✅ Confirmed on GitHub Actions — 270 tests passing against real Postgres + Redis, not mocked. |

## What's honestly still open

These are known, scoped, disclosed gaps — not blockers to internal review, but real work ahead of a customer-facing release:

- **Cassette-tamper enforcement isn't automatic yet.** The mechanism to detect a silently-modified cassette exists and is tested, but nothing calls it automatically when a cassette loads — so tampering is detectable on demand, not yet rejected outright.
- **The twin doesn't catch one specific forgery type** (a forged policy snapshot with an otherwise-valid hash chain) — a scoped, understood fix, not yet built.
- **No dedicated bias-testing mechanism.**
- **No dedicated adverse-action specificity mechanism** (the "why exactly was this decision made" requirement common in regulated-decision contexts).

## Where to look
- `COMPLIANCE.md` in the repo — the living list of what's closed vs. open, kept current with every change.
- `.github/workflows/tests.yml` — the CI pipeline; green runs are visible on the repo's Actions tab.
