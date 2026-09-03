# Troubleshooting Guide

Common problems getting the Sentinel OS kernel and its test suite running, and
how to fix them.

> The authoritative, always-current setup is [`.github/workflows/tests.yml`](.github/workflows/tests.yml)
> — it installs PostgreSQL and Redis, provisions the `iceberg` role/database and
> the twin OS identities, generates a TLS cert, and runs the suite. If a recipe
> here disagrees with that file, that file is right.
>
> The standalone IVR simulator (`iceberg_complete_simulator.py`) and its
> `Domain/` `Sim/` `Engines/` `Model/` `observe/` tree moved to the
> [GSA-815](https://github.com/wking53214/GSA-815) repo. Simulator recipes apply
> there, not here.

---

## Contents

1. [PostgreSQL connection issues](#postgresql-connection-issues)
2. [Python & dependency problems](#python--dependency-problems)
3. [TLS certificate issues](#tls-certificate-issues)
4. [Test failures](#test-failures)
5. [Docker issues](#docker-issues)
6. [Claude API key problems](#claude-api-key-problems)
7. [Logging & debug](#logging--debug)
8. [Getting help](#getting-help)

---

## PostgreSQL connection issues

The test suite connects as the OS-mapped role `iceberg` to a database `iceberg`,
using **peer authentication** over a Unix socket. Set that up once:

```bash
sudo -u postgres psql -c "CREATE ROLE iceberg WITH LOGIN SUPERUSER PASSWORD 'iceberg';"
sudo -u postgres psql -c "CREATE DATABASE iceberg OWNER iceberg;"
```

### "could not connect to server: Connection refused"

PostgreSQL is not running.

```bash
sudo systemctl start postgresql   # Linux (systemd)
sudo service postgresql start     # Linux (dev container / WSL)
brew services start postgresql    # macOS

pg_isready                        # should print "accepting connections"
```

### "FATAL: role 'iceberg' does not exist" / "database 'iceberg' does not exist"

Run the two `CREATE` statements above. Verify:

```bash
sudo -u postgres psql -c "\du"                       # role list
sudo -u postgres psql -c "\l" | grep iceberg         # database list
```

### "FATAL: Peer authentication failed for user 'iceberg'"

You are connecting as an OS user that Postgres cannot map to the `iceberg` role.
Either run the tests as an OS user named `iceberg`, or connect over TCP with the
password instead:

```bash
psql "host=localhost user=iceberg password=iceberg dbname=iceberg" -c "SELECT 1;"
```

### "could not translate host name 'redis'/'ledger' to address"

You are running outside Docker but pointing at Docker-internal hostnames. Use
`localhost` from the host machine; the `redis` / `ledger` names only resolve
inside the compose network.

---

## Python & dependency problems

### "No module named 'cassettes'" / "No module named 'episode'"

You are running from the wrong directory. The kernel modules are imported
flat — run from **inside `sentinel_os/`**:

```bash
cd sentinel_os
python3 -m pytest .
```

### "No module named 'conservation_kernel'"

`conservation_kernel` is a git-pinned dependency (not on PyPI). Reinstall:

```bash
pip install -r sentinel_os/requirements.txt
```

Subprocess-spawning tests (`test_sentinel_worker.py`,
`test_queue_identity_converter.py`) need it importable by a fresh interpreter,
not just on your shell's `PYTHONPATH` — a real `pip install` into the active
environment is required, a `--target` directory is not enough.

### "pip install fails" / dependency version mismatch

```bash
python3 --version            # 3.11+ (CI runs 3.12)
pip install --upgrade pip
pip install -r sentinel_os/requirements.txt

# anthropic's SDK currently needs httpx pinned below 0.28 (see the CI workflow)
pip install "httpx<0.28"
```

---

## TLS certificate issues

### "FileNotFoundError: ... 'certs/cert.pem'"

The TLS-dependent tests need a local cert. `certs/` is gitignored on purpose.

```bash
mkdir -p sentinel_os/certs
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout sentinel_os/certs/key.pem -out sentinel_os/certs/cert.pem \
  -days 1 -subj "/CN=sentinel-dev"
```

### "SSL: CERTIFICATE_VERIFY_FAILED" / expired cert

Regenerate with the command above. For a real deployment use a CA-issued
certificate and point `CERT_FILE` / `KEY_FILE` at it (see `sentinel_os/DEPLOYMENT.md`).

---

## Test failures

### Tests that `skip` with "requires PostgreSQL" / "requires Redis"

Expected when the infrastructure isn't reachable — the suite skips with a
reason rather than passing silently. Start PostgreSQL and Redis (see above), and
they run.

### `test_twin_live.py` collection errors ("FileNotFoundError")

`test_twin_live.py` needs the three twin OS identities and their peer-auth roles.
Provision them (idempotent), then run as root:

```bash
sudo sentinel_os/scripts/twin_ensure_services.sh
cd sentinel_os && sudo -E env "PATH=$PATH" python3 -m pytest test_twin_live.py -v
```

To run the rest of the suite without them: `python3 -m pytest . --ignore=test_twin_live.py`.

### A single test fails with `AssertionError`

```bash
cd sentinel_os
python3 -m pytest Tests/test_file.py::test_name -vvs --tb=long
```

### Suite is flaky under load

One wall-clock latency assertion is known to be timing-sensitive and passes in
isolation. Suites that use persistent PostgreSQL/Redis can also fail if the
daemon is reaped mid-run — restart both and re-run.

---

## Docker issues

The governed lane is `docker compose up -d` from `sentinel_os/`. It starts four
services — `ledger` (PostgreSQL), `redis`, `ingress` (`api_server_v2.py`, port
**8000**), and `worker`.

### "ICEBERG_API_KEYS ... before starting the governed lane"

The `ingress` service publishes port 8000 to the host, so it requires API keys —
`docker compose up` stops before starting anything if they're unset:

```bash
export ICEBERG_API_KEYS="yourkey:yourname"
docker compose up -d
```

### "Cannot connect to Docker daemon"

```bash
sudo systemctl start docker      # Linux
open -a Docker                   # macOS
```

### "docker-compose: command not found"

Use the v2 subcommand: `docker compose` (space, not hyphen).

### "port 5432 is already allocated"

A host PostgreSQL is using the port. Either stop it (`sudo systemctl stop
postgresql`) or remap the `ledger` service's published port in
`docker-compose.yml`.

### A service won't start

```bash
docker compose logs ledger       # or redis / ingress / worker
docker compose down -v           # -v also drops the volumes
docker compose up -d
```

### Health check

```bash
curl -fs http://localhost:8000/health
```

---

## Claude API key problems

The Claude governor client lives in the [GSA-815](https://github.com/wking53214/GSA-815)
repo, not this kernel — `CLAUDE_API_KEY` is not read here directly. If you are
running the IVR application layer and its governor calls fail:

- A missing or invalid key makes the governor **fail closed** — it returns
  `approved=false`, `risk_level=critical`. That is working as designed, not a
  bug to route around.
- Check the key format (`sk-ant-...`) and that outbound HTTPS to
  `api.anthropic.com` works.

---

## Logging & debug

```bash
export LOG_LEVEL=DEBUG
python3 -m pytest Tests/test_file.py::test_name -vvs   # -s shows log output
```

The ledger is in PostgreSQL, not a log file. Inspect it directly:

```bash
psql "host=localhost user=iceberg password=iceberg dbname=iceberg" \
  -c "SELECT id, action_type, created_at FROM ledger_entries ORDER BY id DESC LIMIT 10;"
```

---

## Getting help

1. Search the [issues](https://github.com/wking53214/sentinel_os/issues).
2. Check [`README.md`](README.md) (overview) and
   [`sentinel_os/DEPLOYMENT.md`](sentinel_os/DEPLOYMENT.md) (env vars, TLS, ports).
3. Open an issue with: the problem, steps to reproduce, the full traceback, and
   your environment (OS, Python version, `main` commit).

### Debugging commands

```bash
uname -a && python3 --version && pip list
pg_isready && redis-cli ping
psql "host=localhost user=iceberg password=iceberg dbname=iceberg" -c "SELECT version();"
docker compose ps
```
