#!/usr/bin/env bash
# twin_ensure_services -- idempotent self-heal for the twin live-integration
# suite's infrastructure (test_twin_live.py).
#
# This is the script test_twin_live.py's `services` fixture calls at the
# start of every session (via /usr/local/bin/twin_ensure_services). It used
# to exist only as ad-hoc setup performed once, live, inside whichever dev
# container originally built the twin -- never captured as code, so every
# new environment (a fresh dev container, CI, another engineer's machine)
# hit the exact same "No such file or directory" / "role does not exist"
# wall the original build session never had to face. This script is that
# capture: run it once (as root) on any Debian/Ubuntu-family box with
# Postgres and Redis already installed, and it produces the same
# infrastructure the original build session had, byte-for-byte:
#
#   * three OS identities -- sentinelsvc (Sentinel's own service account),
#     twincustomer (the customer who owns the replica DB and its keys),
#     twincustodian (the neutral custodian for custody-model D) -- each
#     with a real home directory, default useradd permissions (0750,
#     unreadable by the other two identities -- this is what makes the
#     "sentinelsvc cannot read the customer's key file" test a genuine OS
#     permission denial rather than an assumption);
#   * one matching Postgres role per identity, LOGIN, no password -- peer
#     auth (`local all all peer` in pg_hba.conf, Postgres's own default for
#     Unix-socket connections) maps each OS user directly to the
#     same-named role with no credential to manage, which is what makes
#     the Sentinel-cannot-connect-to-the-customer-DB test a genuine
#     Postgres permission denial too;
#   * sentinelsvc granted membership in the existing ledger_reader role
#     (see ledger_immutability.sql) so it can read the primary ledger via
#     its own peer-auth identity when it ships rows -- reusing the read-only
#     role the rest of the repo already defines rather than duplicating
#     its grants;
#   * Postgres and Redis started if they aren't already running.
#
# Idempotent and safe to re-run: every step checks for existence first.
# Matches the "self-heals Postgres/Redis if the VM forked between calls"
# behavior test_twin_live.py's `services` fixture already documents.
#
# Install: copy (or symlink) this file to /usr/local/bin/twin_ensure_services
# and make it executable, e.g.:
#   sudo install -m 0755 scripts/twin_ensure_services.sh \
#       /usr/local/bin/twin_ensure_services
#
# Must run as root (useradd, service management, and the initial
# CREATE ROLE all require it -- consistent with the twin live suite's own
# documented assumption that the pytest process runs as root).

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "twin_ensure_services: must run as root" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 1. Postgres and Redis running
# ---------------------------------------------------------------------------

_service_running() {
  # Works whether the box uses systemd or sysv init scripts (both are seen
  # across the environments this has been run in: real systemd hosts and
  # container images where only `service` works).
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet "$1" 2>/dev/null; then
    return 0
  fi
  service "$1" status >/dev/null 2>&1
}

_service_start() {
  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -q "^$1"; then
    systemctl start "$1"
  else
    service "$1" start
  fi
}

_redis_reachable() {
  # True if something is already answering PING on 6379 -- whether that's
  # an OS-managed redis-server unit or (CI's actual topology) a separate
  # Docker `services: redis:` container already bound to the same host
  # port. What this script and test_twin_live.py need is a REACHABLE
  # Redis, not specifically an OS-managed one. Checking only the
  # redis-server unit's own active state (as _service_running does) misses
  # this case entirely: in CI the unit is genuinely inactive while the
  # Docker container already holds port 6379, so _service_start would try
  # to start a second redis-server on top of it. That second process fails
  # to bind ("Address already in use"), systemd reports "Main process
  # exited, code=exited, status=1/FAILURE" / "Start request repeated too
  # quickly", and -- because this script runs under `set -euo pipefail` --
  # the whole provisioning step aborts before ever reaching the OS
  # identities it exists to create. Reproduced locally byte-for-byte
  # against CI's own log by stopping the redis-server unit and manually
  # binding 6379 with a separate process first.
  #
  # redis-cli is preferred (a real PING/PONG, not just "something is
  # listening"); falls back to a raw TCP connect via bash's /dev/tcp for
  # boxes where only the server binary got installed, not the CLI.
  if command -v redis-cli >/dev/null 2>&1; then
    redis-cli -h 127.0.0.1 -p 6379 ping 2>/dev/null | grep -q PONG
    return $?
  fi
  timeout 2 bash -c 'exec 3<>/dev/tcp/127.0.0.1/6379' 2>/dev/null
}

if _service_running postgresql; then
  echo "twin_ensure_services: postgresql already running"
else
  echo "twin_ensure_services: starting postgresql"
  _service_start postgresql
fi

if _redis_reachable; then
  echo "twin_ensure_services: redis already reachable on 6379 (OS service or" \
       "external container) -- not starting redis-server"
elif _service_running redis-server; then
  echo "twin_ensure_services: redis-server already running"
else
  echo "twin_ensure_services: starting redis-server"
  _service_start redis-server
fi

# Wait for Postgres to actually accept connections (service start returning
# doesn't guarantee the socket is up yet).
for _ in $(seq 1 30); do
  if runuser -u postgres -- psql -tAc 'SELECT 1' >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

# ---------------------------------------------------------------------------
# 2. OS identities
# ---------------------------------------------------------------------------

for u in sentinelsvc twincustomer twincustodian; do
  if id "$u" >/dev/null 2>&1; then
    echo "twin_ensure_services: OS user $u already exists"
  else
    echo "twin_ensure_services: creating OS user $u"
    useradd --create-home --shell /bin/bash "$u"
  fi
done

# ---------------------------------------------------------------------------
# 3. Matching Postgres roles (peer auth, no password) + grants
# ---------------------------------------------------------------------------

runuser -u postgres -- psql -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sentinelsvc') THEN
    CREATE ROLE sentinelsvc WITH LOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'twincustomer') THEN
    -- CREATEDB: twincustomer needs to be able to own replica databases the
    -- test suite creates on its behalf (via the postgres identity), and the
    -- restore drill runs pg_dump/pg_restore as twincustomer directly.
    CREATE ROLE twincustomer WITH LOGIN CREATEDB;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'twincustodian') THEN
    CREATE ROLE twincustodian WITH LOGIN;
  END IF;
END
$$;
SQL

# sentinelsvc reads the primary ledger via its own peer-auth identity when
# shipping rows (twin_shipper.py connects as whoever the OS process runs
# as). Reuses ledger_immutability.sql's existing read-only role instead of
# duplicating its SELECT/INSERT grants here. Only meaningful once the
# `iceberg` database and ledger_reader role exist (they're created by the
# app's own ledger construction path, not this script) -- skip quietly if
# they don't exist yet rather than failing the whole self-heal over a
# database this script isn't responsible for provisioning.
if runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='iceberg'" | grep -q 1; then
  runuser -u postgres -- psql -d iceberg -v ON_ERROR_STOP=1 -c \
    "DO \$\$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname='ledger_reader') THEN GRANT ledger_reader TO sentinelsvc; END IF; END \$\$;"
else
  echo "twin_ensure_services: iceberg database not present yet -- skipping" \
       "the ledger_reader grant (run this again after the ledger has been" \
       "constructed once, e.g. via the normal test suite)"
fi

echo "twin_ensure_services: done"
