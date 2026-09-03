# Contributing to Sentinel OS

Thanks for wanting to contribute. This guide covers the setup, the checks CI
enforces, and the workflow for landing a change.

---

## Code of conduct

Be respectful, be constructive, assume good faith. If you see harassment or
inappropriate behaviour, contact the maintainer privately rather than posting
publicly.

---

## What Sentinel OS is (so your change lands in the right place)

`sentinel_os/` is a **domain-blind governance kernel**: it observes a decision,
judges its outcome against rules fixed and hashed before the outcome was known,
and records every step in a tamper-evident PostgreSQL hash-chained ledger with
an independent twin witness. A *cassette* supplies the domain knowledge; the
kernel refuses any cassette that asks for a capability it does not provide.

The IVR / contact-centre **application** — the standalone simulator, Twilio
ingestion, the Claude governor client, the queue/staffing/Bayes layer, the
resilient API server — is **not in this repo**. It lives in
[GSA-815](https://github.com/wking53214/GSA-815), which runs on this kernel via
`PYTHONPATH`. Changes to that layer go there.

---

## Development environment

### Prerequisites

- **Python 3.11+** (CI runs 3.12)
- **PostgreSQL 14+** — for the full suite. The tests use **peer authentication**
  over a Unix socket: a role named `iceberg` and a database named `iceberg`.
- **Redis 7+** — for the transmission-queue tests
- **Git**
- Docker + Docker Compose — optional, for the governed lane end to end
- `openssl` — the TLS-dependent tests need a local cert

### The executable spec is the CI workflow

`.github/workflows/tests.yml` is the authoritative, always-current setup: it
installs a native PostgreSQL, provisions the `iceberg` role/database, installs
Redis, generates an ephemeral TLS cert, provisions the three twin OS identities
via `scripts/twin_ensure_services.sh`, and runs the suite. When a setup step
here and that file disagree, that file is right — copy from it.

### Local setup (Linux / dev container)

```bash
# from the repo root
python3 -m venv .venv && source .venv/bin/activate
pip install -r sentinel_os/requirements.txt

# PostgreSQL role + database the tests expect (peer auth, no password needed
# for local socket connections as the matching OS user)
sudo -u postgres psql -c "CREATE ROLE iceberg WITH LOGIN SUPERUSER PASSWORD 'iceberg';"
sudo -u postgres psql -c "CREATE DATABASE iceberg OWNER iceberg;"

# twin live-suite OS identities (idempotent)
sudo sentinel_os/scripts/twin_ensure_services.sh

# ephemeral TLS cert for the TLS-dependent tests
mkdir -p sentinel_os/certs
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout sentinel_os/certs/key.pem -out sentinel_os/certs/cert.pem \
  -days 1 -subj "/CN=sentinel-dev"
```

See [`sentinel_os/DEPLOYMENT.md`](sentinel_os/DEPLOYMENT.md) for the full
environment-variable reference (attestation keys, TLS paths, ports).

---

## Running the tests

The suite runs from the `sentinel_os/` directory. `test_twin_live.py` needs the
OS identities provisioned above and generally needs to run as root (it uses
`runuser` internally to cross OS-identity boundaries).

```bash
cd sentinel_os

# most of the suite
python3 -m pytest . -v

# the whole suite the way CI runs it (twin live-suite included)
sudo -E env "PATH=$PATH" python3 -m pytest . -v
```

There is no dependency-free subset — the ledger, queue, and twin tests need
PostgreSQL and Redis, and they are most of the suite. Tests that cannot reach
their infrastructure `skip` with a reason rather than passing silently.

`load_test.py` and `load_test_live.py` are throughput checks against a local
ledger; run them if your change touches the decision hot path.

---

## Checks CI enforces (run these before you push)

Both are **hard gates** — a finding fails the build.

```bash
cd sentinel_os

# lint — pinned; do not `pip install ruff` unpinned, 0.16+ changes the default
# rule set and floods this tree with findings that are not real debt
pip install ruff==0.15.22
ruff check .

# security — whole tree, test code included, every severity level
pip install bandit==1.9.4
bandit -r . -c bandit.yaml
```

Keep both at zero. `bandit.yaml` holds the skip list — every entry is a check
verified to have no findings outside test code, with a one-line rationale. If a
new finding is a genuine false positive, add `# nosec BXXX` on the line with a
plain-comment justification directly above it — never a blanket suppression, and
don't widen `bandit.yaml` without the same verification.

There is also a mechanical duplication/dead-code baseline
(`.ghost_baseline.json` at the repo root, produced by the `ghost_buster` tool).
It is not a CI gate, but if your change legitimately shifts it, refresh and
commit it in the same PR.

---

## Code style

- PEP 8. The repo has no `ruff`/`pyproject` config, so the gate runs ruff's
  default rule set (`E4/E7/E9/F` — unused imports, undefined names, syntax,
  a few statement-level checks); it does **not** enforce line length. Keep
  lines to roughly 100 columns by convention anyway.
- `snake_case` functions, `PascalCase` classes, `UPPER_SNAKE_CASE` constants,
  `_leading_underscore` for private.
- Google-style docstrings on non-trivial functions.
- **No hardcoded governance values.** Thresholds, timeouts, and policies live in
  a cassette, never in code — the cassette loader's validation will refuse a
  parameter that isn't declared by an enabled capability.

---

## Workflow

1. **Branch** off `main`:
   ```bash
   git fetch origin && git checkout -b fix/short-description origin/main
   ```
   Prefixes: `fix/`, `feature/`, `docs/`, `refactor/`, `test/`.

2. **Make the change.** Keep the PR focused — one concern. Add or update tests
   for any behaviour change; a doc pointing at a file that no longer exists is a
   bug, so fix docs in the same PR when you move or rename something.

3. **Verify** (from `sentinel_os/`): `python3 -m pytest .`, `ruff check .`,
   `bandit -r . -c bandit.yaml`.

4. **Commit** with a `Type: summary` subject (`Fix:`, `Feature:`, `Docs:`,
   `Refactor:`, `Test:`, `Chore:`) and a body explaining *why*.

5. **Open a PR** against `main`. Describe what changed and why, link any issue,
   and say how you verified it. CI runs the full suite plus both gates.

6. **Squash-merge** once green and reviewed; delete the branch.

---

## Reporting bugs

Open an issue with: what you did, what you expected, what happened, the full
traceback, and your environment (OS, Python version, `main` commit). A failing
test that reproduces it is the best possible bug report.

Security issues: email the maintainer, don't open a public issue.

---

## License

By contributing you agree your contributions are licensed under **Apache-2.0**,
the same as the project ([`LICENSE`](LICENSE)).
