# Security Policy

## Reporting a vulnerability

Report privately via GitHub's **Security → Report a vulnerability** on this
repository. If that is not available to you, contact the maintainer
(**@wking53214**) directly — do not open a public issue for a suspected
vulnerability.

Please include: what you found, how to reproduce it, the affected file(s) and
`main` commit, and the impact you believe it has. A failing test or a minimal
script that demonstrates it is the most useful thing you can send.

Expect an acknowledgement within a few days. This is a small project — there is
no dedicated security team and no bug-bounty program. Fixes land on `main`;
there are no separate patch releases (see below).

## Supported versions

There are no tagged releases. **`main` is the only supported line.** Consumers
pin a commit (as GSA-815 does) and move the pin forward deliberately. A
security fix is a normal commit to `main` with a `CHANGELOG.md` entry.

## What this system is — and what it does not defend against

Sentinel OS is a governance **witness**: it records what a decision was, what
governance was in force, and the evidence to check it later, in a PostgreSQL
append-only hash-chained ledger with an independently-held twin replica.

**Read [`sentinel_os/AUDIT_PLAYBOOK.md`](sentinel_os/AUDIT_PLAYBOOK.md) before
relying on the ledger for anything adversarial.** It is the honest analysis of
what `verify_chain()` proves and does not. In short:

- `verify_chain()` reliably catches **accidental corruption and naive in-place
  edits** — it recomputes every row's content hash and every chain link.
- It **cannot prove an operator with database superuser access has not
  rewritten history.** Such an operator can disable the immutability triggers,
  produce a fully internally-consistent alternative chain, and `verify_chain()`
  reports zero violations. The playbook demonstrates this with runnable SQL
  (H3), and covers two related cases: `cassette_snapshot` is a real column but
  is not part of the hashed object (H4), and a full wipe verifies "clean"
  because nothing is left to check (H5).
- The control that closes this — the **customer-held-key twin** — is built and
  tested, but **regulatory/auditor acceptance of customer-held-key witnessing
  as a formal control has not been granted.** Until it is, the accurate claim
  is: *internally consistent and operator-attestable today; independently
  verifiable once the twin is accepted as a control.*

Other disclosed limitations are in the root README's **Known Limitations**
section and in `sentinel_os/COMPLIANCE.md`. A documented limitation is
acceptable; an undisclosed one is a governance failure.

## Scope

**In scope:** the kernel (`sentinel_os/`) — the ledger and its immutability
triggers, the twin custody / shipper / receiver, the keyed `authorized_by`
attestation, the cassette framework and its capability gate, the conservation
boundary (`conservation/`), the transmission-queue workers, the fail-closed
credential handling (`ICEBERG_LEDGER_RUNTIME_USER`), and the API ingress
(`api_server_v2.py`).

**Out of scope here:** the IVR/Iceberg application layer, which lives in the
separate **GSA-815** repository and consumes this kernel. Research-mode features
(e.g. BISG demographic inference behind `SENTINEL_BISG_RESEARCH_MODE`) are
disabled by default and gated deliberately — report issues in the gating
itself, not in a capability you have explicitly enabled.

Third-party dependency CVEs: the CI `deps` job runs `pip-audit` against
`requirements.txt` on every change. If you find one it misses, please report it.

## What CI enforces on every change

- `ruff check .` — zero findings (hard gate)
- `bandit -r . -c bandit.yaml` — whole tree, every severity (hard gate; the
  skip list in `bandit.yaml` is individually justified)
- `pip-audit -r requirements.txt` — known-CVE audit of the dependency tree
- `gitleaks` — secret scan of the working tree
- the full test suite against real PostgreSQL, Redis, and TLS between the twin
  identities — no mocks of the ledger, the crypto, or the transport
