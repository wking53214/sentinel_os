# DOCUMENT 8 — OPERATIONS RUNBOOK

**System:** Sentinel OS
**Repository:** `github.com/wking53214/sentinel_os`
**Documented baseline:** `origin/main` at commit `68cadfb`, July 29, 2026
**Documentation pass:** Pass 3, Round 2
**Source authority:** `SENTINEL_OS_REPOSITORY_INVENTORY.md`, `SENTINEL_OS_DIRECTORY_MAP.md`, `SENTINEL_OS_QUICK_REFERENCE.md`, `SENTINEL_OS_TECHNICAL_ARCHITECTURE_MAP.md`

**Classification:** `FACT` = stated in a source document · `DERIVED` = follows from two or more documented facts · `INTERPRETATION` = reasonable reading, not established · `UNKNOWN` = not in the sources.

**Scope discipline.** This runbook contains only procedures the source documents describe. Where an operational procedure a production system would require is absent, it is marked `UNKNOWN` and left empty. No procedure has been reconstructed, inferred from convention, or filled in from general practice. §10 is therefore a substantial part of this document and is the most operationally significant section in it.

**Correction notice (v3).** A subsequent verification pass against the live repository found that commit `d881bc0` moved the twin-replica tests into CI with native PostgreSQL and OS-identity provisioning. The error-diagnosis entry below describing `test_twin_live.py` failures is a still-valid local-environment procedure and is unchanged; the separate claim that these tests are excluded from CI has been corrected and marked `VERIFIED`.

---

## 0. READER FRAME

**AUDIENCE**
System operator, site reliability engineer, platform engineer taking on-call responsibility.

**READER QUESTIONS**
1. How do I start it, and what must already be running?
2. How do I know it is healthy?
3. What breaks, and what do I do when it does?
4. What am I not allowed to do?
5. What must a human do that nothing does automatically?
6. What happens at three in the morning when the alert fires?

**DECISION OBJECTIVE**
Determine whether this system can be accepted into an operational support model as documented — and identify what must be written before it can be.

**TRUST FAILURE**
This reader loses confidence, and should refuse the handover, if the following are not stated at the top:

- `UNKNOWN` There is no documented response procedure for the system's central control firing. Chain verification returns a clean-or-dirty result with findings; no source describes what an operator does when it comes back dirty.
- `UNKNOWN` No monitoring, alerting, or observability is documented. `FACT` The only dashboard-related code in the repository — a Grafana export path — was removed as dead code on July 25.
- `UNKNOWN` No backup or restore procedure is documented. The only recovery path described anywhere is to drop the primary ledger table and rebuild from the replica.
- `UNKNOWN` No on-call model, escalation path, incident procedure, capacity guidance, or rollback procedure is documented.
- `FACT` The obligation sweep, which produces the fairness evidence, is operator-invoked. No scheduler is documented.

`DERIVED` This is a development-grade operational document. Startup, configuration, and error diagnosis are covered. Steady-state operation, observation, recovery, and incident response are not.

---

## 1. RUNBOOK COVERAGE

Legend: 🟢 documented and usable · 🟡 partially documented · 🔴 not documented

| Operational area | Coverage |
|---|---|
| Required services and versions | 🟢 |
| Configuration and environment variables | 🟡 — inconsistent variable naming, see §3 |
| Startup commands | 🟢 |
| Local deployment | 🟢 |
| Twin provisioning | 🟢 |
| Health checks | 🟡 — manual commands only |
| Known error diagnosis | 🟢 — four documented errors with remedies |
| Known operational defects | 🟢 |
| Human intervention points | 🟢 |
| Operational boundaries and restrictions | 🟢 |
| Response to a failed integrity check | 🔴 |
| Monitoring and alerting | 🔴 |
| Backup and restore | 🔴 |
| Retention and archival | 🔴 |
| Secret management and key rotation | 🔴 |
| Scaling and capacity planning | 🔴 |
| Deployment, rollback, and release procedure | 🔴 |
| Shutdown and drain procedure | 🔴 |
| On-call, escalation, incident response | 🔴 |
| Disaster recovery beyond the replica | 🔴 |
| Scheduled job orchestration | 🔴 |

---

## 2. REQUIRED SERVICES

`FACT`

| Service | Version | Purpose | Notes |
|---|---|---|---|
| PostgreSQL | 16 | Primary ledger and twin replica | Version pinned explicitly in CI and compose. Twin security uses Unix-socket peer authentication |
| Redis | 7 | Rate limiting and learning-loop belief state | Version pinned explicitly. Fail-open: falls back to in-memory if unreachable |
| TLS certificate | — | Sealed-envelope encryption for the twin | Ephemeral; generated at runtime if absent at `certs/cert.pem` and `certs/key.pem`; self-signed |

**Database roles** `FACT`

| Role | Purpose | Privileges |
|---|---|---|
| `iceberg` | Primary ledger owner | Owner; used by development and CI |
| `ledger_reader` | Production runtime identity | SELECT and INSERT only |
| `sentinelsvc` | Twin security domain | `UNKNOWN` — privileges not documented |
| `twincustodian` | Twin custody identity | `UNKNOWN` — privileges not documented |
| `twincustomer` | Twin security domain | `UNKNOWN` — privileges not documented |

**Ports** `FACT` API server default `8000` (configurable via `--port`). Twin receiver `7001` in all documented examples. `UNKNOWN` No network topology, firewall requirement, or ingress model is documented.

`DERIVED` **A topology question an operator must resolve before deployment.** The twin is reached over HTTP at `SENTINEL_TWIN_RECEIVER_URL`, implying network separation, while twin verification is documented as using Unix-socket peer authentication, implying same-host access. `UNKNOWN` Whether the twin runs on a separate host, and if so how peer authentication is satisfied, is not stated. This determines whether the independence control is real in a given deployment.

**Python dependencies** `FACT` Python 3.9+. `httpx` pinned below 0.28 due to an incompatibility with Anthropic SDK 0.116.0. Install with `pip install -r sentinel_os/requirements.txt` followed by `pip install "httpx<0.28"`. Other required packages: `cryptography`, `psycopg2-binary`, `redis`, `anthropic`, plus `pytest`, `ruff` pinned at 0.15.22, and `bandit` for verification.

---

## 3. CONFIGURATION

`FACT` Production environment variables:

```bash
ICEBERG_LEDGER_DSN="postgresql://ledger_reader:PASSWORD@postgres:5432/iceberg"
# Runtime identity: ledger_reader (SELECT + INSERT only)
# Fail-closed: RuntimeError if unset, or if the identity is the table owner or a superuser

ICEBERG_REDIS_URL="redis://redis:6379/0"
# Rate limiting and belief-state persistence
# Fail-open: in-memory fallback if unreachable

SENTINEL_TWIN_RECEIVER_URL="http://twin-replica:7001"
SENTINEL_TWIN_REPLICA_ID="sentinel-primary-1"
SENTINEL_TWIN_SHIP_TOKEN="<sealed-envelope-keymat>"
```

`FACT` **A naming inconsistency an operator will hit immediately.** The configuration section documents `ICEBERG_LEDGER_DSN`, while the documented startup error reads `RuntimeError: ICEBERG_LEDGER_RUNTIME_USER not set`. `UNKNOWN` Whether these are two separate variables, or one variable documented under two names. Both appear in the same source document.

`FACT` The twin ship token holds sealed-envelope key material and is supplied as an environment variable. `UNKNOWN` No key management, rotation procedure, or secret-store integration is documented.

`FACT` Documented command examples embed credentials inline, and the development and CI credentials are `iceberg` / `iceberg`.

---

## 4. SYSTEM STARTUP

### Production services

`FACT` **Ingestion worker** — processes call records, judges, records outcomes:

```bash
python sentinel_os/sentinel_worker.py \
  --ledger-dsn "postgresql://iceberg:iceberg@localhost/iceberg" \
  --redis-url "redis://localhost:6379"
```

`FACT` **API server** — judge, explain, and ledger-query endpoints:

```bash
python sentinel_os/api_server_resilient.py \
  --port 8000 \
  --ledger-dsn "postgresql://iceberg:iceberg@localhost/iceberg"
```

`FACT` Both entry points run under the `ledger_reader` identity and are documented as fail-closed on identity misconfiguration.

`UNKNOWN` No process supervision, service unit, restart policy, startup ordering requirement, or readiness gate is documented for either service.

`UNKNOWN` No authentication or authorization for the API server is described in any source. An operator exposing port 8000 has no documented access-control model.

### Batch simulator

`FACT` Requires no database; uses an in-memory ledger:

```bash
python sentinel_os/Sim/iceberg_complete_simulator.py \
  --batch-size 1000 \
  --cassette cassettes.ivr_cassette
```

`FACT` Batch completion order is not guaranteed stable — `run_batch()` in `Sim/cluster_runner.py` is annotated as not stable-order.

### Local deployment

`FACT`

```bash
docker-compose up
# Provisions: postgres:16, redis:7, sentinel-api
# Healthcheck: pg_isready, 30s timeout
```

`FACT` **Known issue, pre-existing:** `depends_on` waits only for container start, not for database readiness. The documented mitigation is the `pg_isready` healthcheck.

### Twin provisioning

`FACT` Requires elevated privileges; provisions operating-system identities and PostgreSQL roles:

```bash
sudo scripts/twin_ensure_services.sh
```

`FACT` Twin schema migration:

```bash
python scripts/twin_migrate.py --action migrate
```

`FACT` Continuous replication of primary to twin:

```bash
python scripts/twin_sync_worker.py --ledger-dsn ... --receiver-url ...
```

`UNKNOWN` Whether the sync worker is intended to run continuously as a service or to be invoked periodically. No scheduling, supervision, or lag-monitoring guidance is documented.

### Cohort equity sweep

`FACT`

```bash
python sentinel_os/obligation_sweep.py \
  --ledger-dsn "postgresql://iceberg:iceberg@localhost/iceberg" \
  --receiver-url "http://twin:7001" \
  --replica-id sentinel-primary-1 \
  --domain lending:mortgage
```

`FACT` Supports a domain filter and a dry-run mode. Fetches resolved obligations, groups them by domain and obligation kind, runs compliance dimensions 4–6, and posts results to the twin for recording.

`UNKNOWN` What triggers this in a running deployment. The CLI is operator-invoked and no scheduler, cron entry, or orchestration is documented.

`DERIVED` This is the procedure that produces the system's fairness evidence, and its execution depends entirely on an operator remembering to run it. An examiner asking whether cohort reviews are current is asking about operator diligence, not about an automated control.

### Cassette deployment

`FACT` Three documented steps: tag and version the cassette (`git tag -a cassettes/mortgage-v1.0.0`); allow the harness to hash-check and refuse a mismatch at deployment time; then confirm binding in the ledger:

```sql
SELECT DISTINCT cassette_version FROM ledger_entries
WHERE cassette_version LIKE 'lending:mortgage:%';
```

`UNKNOWN` No rollback procedure for a cassette deployment is documented. `DERIVED` Given that records are bound to the code hash, a rollback has evidentiary consequences the sources do not address.

---

## 5. HEALTH SIGNALS

`FACT` Four documented manual checks:

```bash
# Ledger is reachable and accumulating
psql -U iceberg iceberg -c "SELECT COUNT(*) FROM ledger_entries;"

# Twin is synchronized and serving
curl http://twin:7001/replica/sentinel-primary-1/obligations | jq .

# Belief state is persisting
redis-cli GET "bayes:intent_stats:billing" | jq .

# Full governance verification suite
python -m pytest Tests/test_governance_verification.py -v
```

`FACT` Chain integrity can be verified programmatically:

```python
clean, findings = ledger.verify_chain()
```

`FACT` `docker-compose` provides a `pg_isready` healthcheck with a 30-second timeout.

`UNKNOWN` No metrics endpoint, structured logging specification, log destination, dashboard, alert threshold, service-level objective, or automated health probe for the application itself is documented. `FACT` The repository's only dashboard-related code — a Grafana export path — was removed as dead code on July 25.

`DERIVED` Every health signal above is a command a human runs. Nothing observes this system continuously.

---

## 6. FAILURE STATES AND DOCUMENTED RESPONSES

`FACT` Four documented errors with remedies:

| Error | Documented cause | Documented remedy |
|---|---|---|
| `RuntimeError: ICEBERG_LEDGER_RUNTIME_USER not set` | Runtime identity not configured; fail-closed by design | Set the runtime identity to the `ledger_reader` role |
| `cassette_version_conflict: governance_decision hash mismatch` | Running decision logic does not match the hash bound to stored records | Drop `ledger_entries` and rebuild from the twin. Characterized in the sources as binding enforcement working correctly |
| `permission denied for ledger_entries` | Runtime role lacks required grants | Verify the role holds SELECT and INSERT — not UPDATE or DELETE |
| `test_twin_live.py: psycopg2.OperationalError` | Twin identities not provisioned; requires Unix-socket peer authentication | Run `./scripts/twin_ensure_services.sh` |

`INTERPRETATION` The second remedy deserves an operator's full attention before it is ever needed. The documented response to a detected integrity conflict is destruction of the primary record set and reconstruction from the replica. That is a high-consequence operation with no documented pre-checks, no documented verification that the replica is complete first, no documented approval requirement, and no documented rollback.

**Fail-closed conditions** `FACT` The system refuses to operate — rather than degrading — when: the runtime identity is unset; the runtime identity is the table owner or a superuser; a cassette declares a parameter belonging to a disabled capability; or a cassette's code hash does not match its binding.

**Fail-open condition** `FACT` One exception: Redis. If unreachable, rate limiting and belief-state persistence fall back to in-memory behavior. `DERIVED` An operator should know that losing Redis is silent, not loud, and that the rate limiter's backing store can disappear while the system continues to serve.

---

## 7. KNOWN OPERATIONAL ISSUES

`FACT`

| Issue | Impact | Documented workaround |
|---|---|---|
| `docker-compose` `depends_on` does not wait for database readiness | Startup race | `pg_isready` healthcheck |
| PostgreSQL or Redis daemon reaped mid-container | Occasional test and runtime failures | Restart the daemon before running the suite |
| `test_api_server_v2.py::test_L11_frozen_redis_health_stays_alive` flaky | Wall-clock latency assertion fails under load; passes in isolation | None documented |
| PostgreSQL constructor lock optimization | Known issue, left unfixed by decision | None documented |
| `run_batch()` not stable-order | Batch completion order varies | None documented |
| Node-naming coupling — queue and agent detection by substring | Documented limitation, out of scope | None documented |
| ~~Twin tests excluded from CI~~ — resolved, `d881bc0` | `VERIFIED` (corrected in v3) No longer an open issue. Native PostgreSQL and OS-identity provisioning were added to the CI job specifically to run these tests; they now execute on every commit | None needed |
| Live ingestion unimplemented | No live call ingestion from the external provider | Use mock call records or a custom webhook |
| Geographic-equity dimension unwired | The sources state redlining is not prevented in real decisions | Apply the prepared patch, unapplied as of baseline |
| AI-explanation executor stubbed | No real explanation fallback | Use mock or cached explanations |

---

## 8. HUMAN INTERVENTION POINTS

`FACT` Operations that require a person, with no automation documented:

1. **Applying patches.** Three prepared patches await application by the single documented individual.
2. **Running the cohort equity sweep.** Operator-invoked; no scheduler documented.
3. **Provisioning twin identities.** Requires `sudo`.
4. **Twin schema migration.** Explicit `--action migrate` invocation.
5. **Running chain verification.** On demand only.
6. **All health checks.** Every documented signal is a manual command.
7. **Deciding cassette scope and configuration.** Which compliance dimensions are enabled is a configuration decision, and three of six are off or unwired by default.
8. **Confirming cassette binding after deployment.** A manual SQL query.
9. **Restarting reaped database or cache daemons.**

`FACT` One named individual is the only human role documented anywhere in the sources. `UNKNOWN` Whether any other operator exists, and whether any approval or separation-of-duties requirement applies to any of the nine operations above.

---

## 9. OPERATIONAL BOUNDARIES

`FACT` What the running system cannot do, by design:

- Cannot update or delete ledger rows. The privilege is not held and a database trigger forbids it.
- Cannot start with an unset, owner, or superuser identity.
- Cannot load a cassette with undeclared parameters or a mismatched code hash.
- Cannot let a regulatory lens make an outside call during judgment.
- Cannot take effect before a compliance disclosure is written.
- Cannot let the governance verdict influence routing behavior.

`FACT` What an operator must not do, derived from documented constraints:

- Do not grant the runtime role UPDATE or DELETE. `DERIVED` It would defeat the immutability control that the entire evidentiary claim rests on.
- Do not run production as the `iceberg` owner. `FACT` Startup refuses this.
- Do not modify governance code without bumping the cassette version. `FACT` A hash change without a version bump produces a conflict whose only documented remedy destroys and rebuilds the primary table.
- Do not treat the simulator as a production path. `FACT` It has no ledger at all.

`UNKNOWN` No documented guidance exists on maximum throughput, connection pooling, concurrent worker count, ledger table growth expectations, or partitioning for a table that is append-only and never pruned.

`DERIVED` That last gap compounds over time. An append-only table with no documented retention, archival, or partitioning strategy, holding records whose obligations run for years, has a growth trajectory nobody has written down.

---

## 10. NOT DOCUMENTED — OPERATIONAL GAP REGISTER

`UNKNOWN` for every item. Recorded as absences in the source material, not as recommendations.

**Observation**
No monitoring, alerting, metrics endpoint, log aggregation, structured-logging specification, dashboard, service-level objective, or automated health probe. `FACT` The only dashboard-related code was removed as dead code.

**Integrity response**
No procedure for a chain verification that returns dirty. No definition of who is notified, what is preserved, what is halted, or what the escalation path is. No requirement to record that a verification occurred, or its result.

**Data protection**
No backup procedure. No restore procedure. No restore testing. No retention policy. No archival strategy. No encryption at rest. No read-access logging.

**Secrets**
No key management, no rotation procedure for the twin ship token or the TLS material, no secret-store integration. `FACT` Key material is supplied by environment variable and the TLS certificate is self-signed.

**Availability**
No high-availability model, no failover procedure, no disaster-recovery plan beyond the twin, no recovery-time or recovery-point objective. `DERIVED` The twin is documented as both the tamper detector and the sole recovery source, and no procedure verifies its completeness before it is relied upon.

**Change management**
No deployment procedure beyond `docker-compose up`. No release process, staging environment, rollback procedure, canary or blue-green approach, or migration path for the append-only ledger.

**Capacity**
No throughput figures, load-test results, capacity planning guidance, resource sizing, or growth projections. `FACT` The only load-related datum in the sources is that one latency test is flaky under load.

**Lifecycle**
No graceful shutdown or drain procedure. No documented startup ordering between the worker, the API, and the sync worker.

**Access control**
No authentication or authorization model for the API. No role-based access control above the database layer. No audit trail of who read what.

**Support model**
No on-call rotation, escalation path, incident classification, severity definitions, post-incident process, or runbook for any outage scenario.

**Scheduling**
No orchestration for the cohort sweep or the twin sync worker, both of which are documented as manually invoked and both of which produce evidence the system's claims depend on.

---

## 11. THE MISSING PROCEDURE THAT MATTERS MOST

`DERIVED` One gap stands apart from the rest of §10, and an operator should not accept a handover without it.

The system's purpose is to detect tampering. It can detect tampering — through hash chaining, three independent recomputations, and an independently held replica. `FACT` Verification returns a clean-or-dirty result together with findings.

`UNKNOWN` No source document describes what happens next. There is no documented notification path, no preservation procedure, no requirement to halt writes, no forensic capture step, no escalation, no decision authority, and no record that a verification took place at all.

`INTERPRETATION` A detection capability with no documented response is a capability that produces an alarm nobody has decided how to answer. Every other gap in §10 is a normal maturity gap for a system of this age. This one sits directly on the system's reason for existing.

---

**End of Document 8.**

`FACT` Grounded solely in the four source documents at repository state `68cadfb`. No procedure has been invented, inferred from convention, or reconstructed. Sections marked `UNKNOWN` are empty because the source material is silent, and are listed rather than omitted so that the silence is visible.
