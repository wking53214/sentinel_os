# governance/

Core governance and ledger logic — the tamper-evident audit trail and the
policy-enforcement layer that decides whether an action is approved,
blocked, or requires escalation.

## Key files

- `ledger_postgres.py` — the Postgres-backed, hash-chained ledger.
  Every governance decision is appended here as an immutable row, linked
  to the previous row's hash. See `ledger_immutability.sql` for the
  trigger-level protections (blocks UPDATE/DELETE/TRUNCATE).
- `ledger_immutability.sql` — creates the append-only triggers and the
  restricted `ledger_reader` role used as the app's runtime identity.
- `drift_core_v1.py` / `self_heal_v1.py` — drift detection and
  self-healing policy logic.

## Runtime credential (as of July 22, 2026)

`PostgreSQLLedger` refuses to start unless `ICEBERG_LEDGER_RUNTIME_USER`
is set to a non-owner, non-superuser role. There is no privileged
fallback — an unset variable is a hard startup failure, not a warning.

This exists because the application's own database credential was
previously privileged by default, meaning the app itself could rewrite
or wipe the ledger with no more warning than a startup log line. Two
independent checks now close that path:

1. **Unset → refuse to boot.** No fallback to the schema-owner
   credentials, ever.
2. **Set-but-privileged → refuse to boot.** Even when the variable is
   set, a startup check independently confirms (via `pg_roles.rolsuper`
   and the table's actual owner) that the resolved identity isn't
   privileged, and refuses to start if it is. This catches
   misconfiguration, not just omission.

The restricted role (`ledger_reader` by default: SELECT + INSERT only,
no ALTER/TRIGGER) is created automatically by `ledger_immutability.sql`.
Its password is **self-provisioned at every startup** — if
`ICEBERG_LEDGER_RUNTIME_PASSWORD` is set, `PostgreSQLLedger` sets that
role's password itself, using the still-open owner connection, right
after creating the role. This means a fresh deployment (docker-compose,
k8s, bare metal) works the moment both env vars are set — no separate
manual `set_ledger_reader_password.py` step required. That script still
exists for rotating the password on an already-running system without a
restart.

**What this closes:** the application's own credential can no longer
rewrite or wipe the ledger, even if the app is compromised or misused.

**What this does NOT close:** a human operator with direct, independent
database-owner or superuser access (e.g. a DBA) can still disable the
protective triggers. That threat is detected, not prevented — see the
customer-held witness twin (`twin_custody.py` et al.) and
`COMPLIANCE.md`'s Recordkeeping & Audit Trail section.
